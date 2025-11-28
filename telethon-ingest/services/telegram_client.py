"""Telegram клиент для парсинга каналов."""

import asyncio
import logging
import os
import uuid
from typing import List, Optional, Any, Dict, Set
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, AuthKeyError
from telethon.utils import get_peer_id
import structlog
import redis.asyncio as redis
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime, timezone, timedelta

from config import settings
from services.events import (
    publish_post_created,
    STREAM_POST_CREATED,
    STREAM_GROUP_MESSAGE_CREATED,
)

logger = structlog.get_logger()


class TelegramIngestionService:
    """Сервис для парсинга Telegram каналов.
    
    Context7: Обрабатывает real-time события NewMessage через event handlers.
    Используется для live-парсинга новых сообщений по мере их публикации в каналах.
    
    NOTE: Для historical/incremental парсинга используется ChannelParser через scheduler.
    Этот сервис дополняет ChannelParser для real-time обработки.
    """
    
    def __init__(self, client_manager=None, media_processor=None):
        self.client: Optional[TelegramClient] = None
        self.client_manager = client_manager  # Context7: TelegramClientManager
        self.media_processor = media_processor  # Context7: MediaProcessor для обработки медиа
        # Context7 best practice: используем async Redis клиент для неблокирующих операций
        self.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        self.db_connection = None
        self.is_running = False
        
    async def start(self):
        """Запуск сервиса."""
        try:
            # Context7: Используем TelegramClientManager вместо прямой инициализации
            if not self.client_manager:
                logger.error("TelegramClientManager not available")
                return
                
            # Context7 best practice: асинхронное подключение к БД через ThreadPoolExecutor
            logger.info("Connecting to database...")
            import asyncio
            loop = asyncio.get_event_loop()
            self.db_connection = await loop.run_in_executor(
                None, 
                lambda: psycopg2.connect(
                    settings.database_url,
                    connect_timeout=10  # таймаут 10 секунд
                )
            )
            self.db_connection.autocommit = False
            logger.info("Database connected")
            
            # Context7: Получаем клиент через TelegramClientManager
            # Сначала получаем telegram_id авторизованного пользователя с сессией из БД
            cursor = self.db_connection.cursor()
            cursor.execute("""
                SELECT telegram_id 
                FROM users 
                WHERE telegram_auth_status = 'authorized' 
                  AND telegram_session_enc IS NOT NULL
                ORDER BY telegram_auth_created_at DESC
                LIMIT 1
            """)
            result = cursor.fetchone()
            cursor.close()
            
            if not result:
                logger.error(
                    "No authorized user with session found in users table",
                    available_users_count=0
                )
                # Context7: Дополнительная диагностика - проверяем, сколько пользователей есть
                cursor = self.db_connection.cursor()
                cursor.execute("""
                    SELECT 
                        COUNT(*) FILTER (WHERE telegram_auth_status = 'authorized') as authorized_count,
                        COUNT(*) FILTER (WHERE telegram_auth_status = 'pending') as pending_count,
                        COUNT(*) FILTER (WHERE telegram_session_enc IS NOT NULL) as with_session_count
                    FROM users
                    WHERE telegram_id IS NOT NULL
                """)
                stats = cursor.fetchone()
                cursor.close()
                if stats:
                    logger.warning(
                        "User session statistics",
                        authorized_count=stats[0] or 0,
                        pending_count=stats[1] or 0,
                        with_session_count=stats[2] or 0,
                    )
                return
                
            telegram_id = result[0]
            self.telegram_id = telegram_id  # Context7: Сохраняем для использования в других методах
            logger.info("Selected authorized user for Telegram client", telegram_id=telegram_id)
            self.client = await self.client_manager.get_client(telegram_id)
            if not self.client:
                logger.error(
                    "No available Telegram client from manager",
                    telegram_id=telegram_id,
                    reason="Session may be invalid or expired"
                )
                return
                
            logger.info("Telegram client obtained from manager", telegram_id=telegram_id)
            
            # Регистрация обработчиков событий
            self._register_handlers()
            
            # Загрузка активных каналов (неблокирующая)
            asyncio.create_task(self._load_active_channels())
            # Загрузка активных групп
            asyncio.create_task(self._load_active_groups())
            # Синхронизация истории групп
            asyncio.create_task(self._group_sync_worker())
            
            # Context7 best practice: исторический парсинг для отладки (неблокирующий)
            asyncio.create_task(self._start_historical_parsing())
            asyncio.create_task(self._group_discovery_worker())
            
            self.is_running = True
            logger.info("Telegram ingestion service started with Context7 components")
            
        except Exception as e:
            logger.error("Failed to start telegram service", error=str(e))
            raise

    async def _start_client_async(self):
        """Асинхронный запуск Telegram клиента."""
        try:
            await self.client.start()
            me = await self.client.get_me()
            logger.info("Telegram client started", user_id=getattr(me, 'id', None))
        except Exception as e:
            logger.error("Failed to start Telegram client", error=str(e))

    async def _get_authorized_session(self) -> Optional[str]:
        """Context7 best practice: получение авторизованной сессии из БД."""
        try:
            if not self.db_connection:
                return None
            
            loop = asyncio.get_event_loop()
            session_data = await loop.run_in_executor(
                None,
                self._get_session_sync
            )
            
            if not session_data:
                return None
            
            # Расшифровываем сессию
            from crypto_utils import decrypt_session
            session_string = decrypt_session(session_data['session_string_enc'])
            
            logger.info("Found authorized session", user_id=session_data.get('telegram_user_id'))
            return session_string
            
        except Exception as e:
            logger.error("Failed to get authorized session", error=str(e))
            return None
    
    def _get_session_sync(self) -> Optional[dict]:
        """Синхронное получение авторизованной сессии из БД."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT 
                        u.telegram_session_enc as session_string_enc,
                        u.telegram_id as telegram_user_id,
                        u.telegram_session_key_id as key_id
                    FROM users u
                    WHERE u.telegram_auth_status = 'authorized'
                    AND u.telegram_session_enc IS NOT NULL
                    ORDER BY u.telegram_auth_created_at DESC
                    LIMIT 1
                """)
                
                result = cursor.fetchone()
                if result:
                    logger.info("Found session in DB", user_id=result.get('telegram_user_id'))
                    return dict(result)
                else:
                    logger.warning("No authorized session found in DB")
                    return None
        except Exception as e:
            logger.error("Error getting session from DB", error=str(e))
            return None
    
    def _register_handlers(self):
        """Context7 P2: Регистрация оптимизированных обработчиков событий.
        
        Best practices:
        - Фильтрация по активным каналам для снижения нагрузки
        - Обработка MessageEdited для обновления контента
        - Обработка MessageDeleted для удаления постов
        """
        # Context7 P2: Кэш активных каналов для фильтрации
        self._active_channel_ids: Set[int] = set()
        self._active_group_ids: Set[int] = set()
        
        async def _refresh_active_chats():
            """Обновление кэша активных чатов."""
            try:
                if not self.db_connection:
                    return
                
                with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    # Активные каналы
                    cursor.execute("""
                        SELECT tg_channel_id 
                        FROM channels 
                        WHERE is_active = true AND tg_channel_id IS NOT NULL
                    """)
                    self._active_channel_ids = {int(row['tg_channel_id']) for row in cursor.fetchall() if row['tg_channel_id']}
                    
                    # Активные группы
                    cursor.execute("""
                        SELECT tg_chat_id 
                        FROM groups 
                        WHERE is_active = true AND tg_chat_id IS NOT NULL
                    """)
                    self._active_group_ids = {int(row['tg_chat_id']) for row in cursor.fetchall() if row['tg_chat_id']}
                    
                    logger.info("Active chats cache refreshed",
                               channels_count=len(self._active_channel_ids),
                               groups_count=len(self._active_group_ids),
                               active_channel_ids=list(self._active_channel_ids)[:10],  # Первые 10 для диагностики
                               active_group_ids=list(self._active_group_ids)[:10])  # Первые 10 для диагностики
            except Exception as e:
                logger.warning("Failed to refresh active chats cache", error=str(e))
        
        # Context7 P2: Обновляем кэш при старте и периодически
        asyncio.create_task(_refresh_active_chats())
        
        # Context7 P2: Периодическое обновление кэша (каждые 5 минут)
        async def _periodic_refresh():
            while self.is_running:
                await asyncio.sleep(300)  # 5 минут
                await _refresh_active_chats()
        
        asyncio.create_task(_periodic_refresh())
        
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            """Context7 P2: Обработка новых сообщений с фильтрацией по активным каналам."""
            try:
                # Context7 P2: Фильтрация по активным каналам/группам
                chat_id = getattr(event.message.peer_id, 'channel_id', None) or getattr(event.message.peer_id, 'chat_id', None)
                if chat_id:
                    chat_id_int = int(chat_id)
                    is_channel = chat_id_int in self._active_channel_ids
                    is_group = chat_id_int in self._active_group_ids
                    
                    if not is_channel and not is_group:
                        # Context7: Детальное логирование для диагностики пропущенных сообщений
                        logger.debug(
                            "Message skipped - not in active chats",
                            chat_id=chat_id_int,
                            active_channels_count=len(self._active_channel_ids),
                            active_groups_count=len(self._active_group_ids),
                            is_channel=is_channel,
                            is_group=is_group,
                        )
                        return
                    
                    # Context7: Логирование успешной обработки для диагностики
                    logger.info(
                        "Processing new message",
                        chat_id=chat_id_int,
                        message_id=event.message.id,
                        is_channel=is_channel,
                        is_group=is_group,
                    )
                
                await self._process_message(event)
            except Exception as e:
                logger.error("Error processing message", error=str(e), message_id=event.message.id, exc_info=True)
        
        @self.client.on(events.MessageEdited)
        async def handle_message_edited(event):
            """Context7 P2: Обработка отредактированных сообщений."""
            try:
                # Context7 P2: Фильтрация по активным каналам/группам
                chat_id = getattr(event.message.peer_id, 'channel_id', None) or getattr(event.message.peer_id, 'chat_id', None)
                if chat_id:
                    chat_id_int = int(chat_id)
                    if chat_id_int not in self._active_channel_ids and chat_id_int not in self._active_group_ids:
                        return
                
                # Context7 P2: Обновление поста в БД
                await self._process_message_edited(event)
            except Exception as e:
                logger.error("Error processing edited message", error=str(e), message_id=event.message.id)
        
        @self.client.on(events.MessageDeleted)
        async def handle_message_deleted(event):
            """Context7 P2: Обработка удалённых сообщений."""
            try:
                # Context7 P2: Помечаем пост как удалённый в БД
                await self._process_message_deleted(event)
            except Exception as e:
                logger.error("Error processing deleted message", error=str(e), deleted_ids=event.deleted_ids)
    
    async def _load_active_channels(self):
        """Загрузка активных каналов из БД."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, tg_channel_id, username, title 
                    FROM channels 
                    WHERE is_active = true
                """)
                channels = cursor.fetchall()
                
                for channel in channels:
                    try:
                        # Получение объекта канала по username или tg_channel_id
                        # Context7: [C7-ID: username-normalization-002] Нормализация username перед использованием
                        # Убираем @ из начала username, так как Telethon ожидает username без @
                        if channel['username']:
                            clean_username = channel['username'].lstrip('@')
                            entity = await self.client.get_entity(clean_username)
                        elif channel['tg_channel_id']:
                            entity = await self.client.get_entity(int(channel['tg_channel_id']))
                        else:
                            logger.warning("No username or tg_channel_id for channel", 
                                         channel_id=channel['id'])
                            continue
                            
                        logger.info("Loaded channel", 
                                  channel_id=channel['id'], 
                                  username=channel['username'],
                                  title=channel['title'])
                    except Exception as e:
                        logger.warning("Failed to load channel", 
                                     channel_id=channel['id'], 
                                     error=str(e))
                        
        except Exception as e:
            logger.error("Failed to load channels", error=str(e))

    async def _load_active_groups(self):
        """Загрузка активных групп из БД для подписки на события.
        
        Context7: Использует клиент пользователя, подписанного на группу.
        Только группы с активными подписками в user_group загружаются.
        """
        try:
            # Context7: Получаем группы с информацией о подписанных пользователях
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                # Получаем только группы с активными подписками в user_group
                cursor.execute("""
                    SELECT 
                        g.id, 
                        g.tenant_id, 
                        g.tg_chat_id, 
                        g.username, 
                        g.title,
                        u.telegram_id as subscriber_telegram_id
                    FROM groups g
                    INNER JOIN user_group ug ON g.id = ug.group_id
                    INNER JOIN users u ON ug.user_id = u.id
                    WHERE g.is_active = true
                      AND ug.is_active = true
                      AND u.telegram_auth_status = 'authorized'
                      AND u.telegram_session_enc IS NOT NULL
                    ORDER BY ug.subscribed_at DESC
                """)
                groups_with_users = cursor.fetchall() or []

            if not groups_with_users:
                logger.warning(
                    "No groups with authorized users found",
                    total_groups=0
                )
                return

            for group_data in groups_with_users:
                try:
                    subscriber_telegram_id = group_data.get("subscriber_telegram_id")
                    if not subscriber_telegram_id or not self.client_manager:
                        logger.warning(
                            "Group has no subscriber or client manager unavailable",
                            group_id=group_data.get("id"),
                            subscriber_telegram_id=subscriber_telegram_id,
                        )
                        continue

                    # Context7: Получаем клиент пользователя, подписанного на группу
                    subscriber_telegram_id_int = int(subscriber_telegram_id)
                    group_client = await self.client_manager.get_client(subscriber_telegram_id_int)
                    if not group_client:
                        logger.warning(
                            "No client available for group subscriber",
                            group_id=group_data.get("id"),
                            subscriber_telegram_id=subscriber_telegram_id_int,
                        )
                        continue

                    entity = None
                    if group_data.get("username"):
                        clean_username = group_data["username"].lstrip("@")
                        entity = await group_client.get_entity(clean_username)
                    elif group_data.get("tg_chat_id"):
                        entity = await group_client.get_entity(int(group_data["tg_chat_id"]))

                    logger.info(
                        "Loaded group for real-time ingest",
                        group_id=group_data["id"],
                        tg_chat_id=group_data.get("tg_chat_id"),
                        username=group_data.get("username"),
                        title=group_data.get("title"),
                        subscriber_telegram_id=subscriber_telegram_id_int,
                        entity_type=type(entity).__name__ if entity else None,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to load group entity",
                        group_id=group_data.get("id"),
                        tg_chat_id=group_data.get("tg_chat_id"),
                        subscriber_telegram_id=group_data.get("subscriber_telegram_id"),
                        error=str(e),
                    )
        except Exception as e:
            logger.error("Failed to load groups", error=str(e))

    async def _group_sync_worker(self):
        """Периодическая синхронизация истории сообщений групп.
        
        Context7: Использует сессию пользователя, подписанного на группу.
        """
        poll_interval = int(os.getenv("GROUP_SYNC_INTERVAL_SEC", "180"))
        max_messages = int(os.getenv("GROUP_SYNC_LIMIT", "200"))
        lookback_hours = int(os.getenv("GROUP_SYNC_LOOKBACK_HOURS", "24"))

        while True:
            if not self.is_running or not self.client_manager or not self.db_connection:
                await asyncio.sleep(poll_interval)
                continue

            try:
                # Context7: Получаем группы с информацией о подписанных пользователях
                with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        """
                        SELECT 
                            g.id, 
                            g.tenant_id, 
                            g.tg_chat_id, 
                            g.username, 
                            g.title,
                            u.telegram_id as subscriber_telegram_id
                        FROM groups g
                        INNER JOIN user_group ug ON g.id = ug.group_id
                        INNER JOIN users u ON ug.user_id = u.id
                        WHERE g.is_active = true
                          AND ug.is_active = true
                          AND u.telegram_auth_status = 'authorized'
                          AND u.telegram_session_enc IS NOT NULL
                        ORDER BY ug.subscribed_at DESC
                        """
                    )
                    groups_with_users = cursor.fetchall() or []
            except Exception as fetch_err:
                logger.error("Failed to load groups for sync", error=str(fetch_err))
                try:
                    self.db_connection.rollback()
                except Exception:
                    pass
                await asyncio.sleep(poll_interval)
                continue

            lookback_threshold = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

            for group_data in groups_with_users:
                tg_chat_id = group_data.get("tg_chat_id")
                subscriber_telegram_id = group_data.get("subscriber_telegram_id")
                if tg_chat_id is None or subscriber_telegram_id is None:
                    continue

                # Context7: Получаем клиент пользователя, подписанного на группу
                try:
                    subscriber_telegram_id_int = int(subscriber_telegram_id)
                    group_client = await self.client_manager.get_client(subscriber_telegram_id_int)
                    if not group_client:
                        logger.warning(
                            "No client available for group subscriber",
                            group_id=group_data.get("id"),
                            subscriber_telegram_id=subscriber_telegram_id_int,
                        )
                        continue
                except Exception as client_err:
                    logger.warning(
                        "Failed to get client for group subscriber",
                        error=str(client_err),
                        group_id=group_data.get("id"),
                        subscriber_telegram_id=subscriber_telegram_id,
                    )
                    continue

                try:
                    entity = await group_client.get_entity(int(tg_chat_id))
                except Exception as entity_err:
                    logger.warning(
                        "Failed to resolve group entity for sync",
                        error=str(entity_err),
                        group_id=group_data.get("id"),
                        tg_chat_id=tg_chat_id,
                        subscriber_telegram_id=subscriber_telegram_id_int,
                    )
                    continue

                processed = 0
                try:
                    async for message in group_client.iter_messages(entity, limit=max_messages):
                        message_date = getattr(message, "date", None)
                        if isinstance(message_date, datetime):
                            msg_dt = (
                                message_date.replace(tzinfo=timezone.utc)
                                if message_date.tzinfo is None
                                else message_date.astimezone(timezone.utc)
                            )
                            if msg_dt < lookback_threshold:
                                break

                        await self._process_group_message(message, dict(group_data))
                        processed += 1
                except Exception as sync_err:
                    logger.warning(
                        "Group sync iteration failed",
                        error=str(sync_err),
                        group_id=group_data.get("id"),
                        tg_chat_id=tg_chat_id,
                        subscriber_telegram_id=subscriber_telegram_id_int,
                    )
                    continue

                if processed:
                    logger.info(
                        "Group history synced",
                        group_id=group_data.get("id"),
                        tg_chat_id=tg_chat_id,
                        subscriber_telegram_id=subscriber_telegram_id_int,
                        processed=processed,
                        lookback_hours=lookback_hours,
                    )

            await asyncio.sleep(poll_interval)

    async def _group_discovery_worker(self):
        """Обработка запросов на discovery групп из БД."""
        poll_interval = int(os.getenv("GROUP_DISCOVERY_POLL_INTERVAL_SEC", "20"))
        max_dialogs = int(os.getenv("GROUP_DISCOVERY_DIALOG_LIMIT", "300"))

        while True:
            if not self.is_running or not self.client or not self.db_connection:
                await asyncio.sleep(poll_interval)
                continue

            request_row = None
            try:
                with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        """
                        SELECT id, tenant_id, user_id
                        FROM group_discovery_requests
                        WHERE status = 'pending'
                        ORDER BY created_at
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """
                    )
                    request_row = cursor.fetchone()
                    if not request_row:
                        self.db_connection.rollback()
                        await asyncio.sleep(poll_interval)
                        continue

                    cursor.execute(
                        """
                        UPDATE group_discovery_requests
                        SET status = 'processing'
                        WHERE id = %s
                        """,
                        (request_row["id"],),
                    )
                    self.db_connection.commit()
            except Exception as fetch_err:
                logger.error("Failed to fetch discovery request", error=str(fetch_err))
                try:
                    self.db_connection.rollback()
                except Exception:
                    pass
                await asyncio.sleep(poll_interval)
                continue

            if not request_row:
                await asyncio.sleep(poll_interval)
                continue

            request_id = request_row["id"]
            tenant_id = request_row["tenant_id"]
            user_uuid = request_row.get("user_id")
            target_telegram_id: Optional[int] = None
            target_client: Optional[TelegramClient] = None

            try:
                with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        """
                        SELECT telegram_id
                        FROM users
                        WHERE id = %s
                        """,
                        (user_uuid,),
                    )
                    user_row = cursor.fetchone()

                if not user_row or user_row.get("telegram_id") is None:
                    with self.db_connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE group_discovery_requests
                            SET status = %s,
                                error = %s,
                                completed_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                "failed",
                                "telegram_id_not_found",
                                request_id,
                            ),
                        )
                        self.db_connection.commit()
                    continue

                target_telegram_id = int(user_row["telegram_id"])
                if not self.client_manager:
                    raise RuntimeError("client_manager_not_configured")

                target_client = await self.client_manager.get_client(target_telegram_id)
                if not target_client:
                    with self.db_connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE group_discovery_requests
                            SET status = %s,
                                error = %s,
                                completed_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                "failed",
                                "telegram_client_unavailable",
                                request_id,
                            ),
                        )
                        self.db_connection.commit()
                    continue

            except Exception as session_err:
                logger.error(
                    "Group discovery failed to acquire user session",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    user_id=user_uuid,
                    error=str(session_err),
                )
                try:
                    with self.db_connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE group_discovery_requests
                            SET status = %s,
                                error = %s,
                                completed_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                "failed",
                                "session_unavailable",
                                request_id,
                            ),
                        )
                        self.db_connection.commit()
                except Exception:
                    try:
                        self.db_connection.rollback()
                    except Exception:
                        pass
                continue

            try:
                existing_map: Dict[int, Dict[str, Any]] = {}
                with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        """
                        SELECT id, tg_chat_id
                        FROM groups
                        WHERE tenant_id = %s
                    """,
                        (tenant_id,),
                    )
                    for row in cursor.fetchall() or []:
                        if row.get("tg_chat_id") is not None:
                            existing_map[int(row["tg_chat_id"])] = {
                                "group_id": str(row["id"])
                            }

                results: List[Dict[str, Any]] = []
                seen_chat_ids: Set[int] = set()

                async def _collect_dialogs(dialog_iter):
                    async for dialog in dialog_iter:
                        entity = dialog.entity
                        is_group_dialog = bool(getattr(dialog, "is_group", False))
                        is_supergroup = bool(getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False))
                        is_broadcast = bool(getattr(entity, "broadcast", False))
                        if not (is_group_dialog or is_supergroup):
                            continue
                        if is_broadcast and not is_supergroup:
                            continue
                        if getattr(entity, "left", False):
                            continue
                        try:
                            tg_chat_id = int(get_peer_id(entity))
                        except Exception:
                            continue
                        if tg_chat_id in seen_chat_ids:
                            continue
                        seen_chat_ids.add(tg_chat_id)

                        username = getattr(entity, "username", None)
                        title = getattr(entity, "title", None) or getattr(entity, "first_name", "") or ""
                        is_megagroup = bool(getattr(entity, "megagroup", False))
                        is_gigagroup = bool(getattr(entity, "gigagroup", False))
                        participants_count = getattr(entity, "participants_count", None)
                        is_private = username is None
                        is_connected = tg_chat_id in existing_map
                        connected_group_id = existing_map.get(tg_chat_id, {}).get("group_id")
                        category = "supergroup" if is_supergroup else "group"

                        results.append(
                            {
                                "tg_chat_id": tg_chat_id,
                                "title": title,
                                "username": username,
                                "is_megagroup": is_megagroup,
                                "is_gigagroup": is_gigagroup,
                                "is_channel": False,
                                "is_broadcast": is_broadcast,
                                "category": category,
                                "is_private": is_private,
                                "participants_count": participants_count,
                                "is_connected": is_connected,
                                "connected_group_id": connected_group_id,
                                "invite_required": is_private and not username,
                            }
                        )

                await _collect_dialogs(target_client.iter_dialogs(limit=max_dialogs))

                include_archived = os.getenv("GROUP_DISCOVERY_INCLUDE_ARCHIVED", "true").lower() in {"1", "true", "yes"}
                if include_archived:
                    await _collect_dialogs(target_client.iter_dialogs(limit=max_dialogs, archived=True))

                total = len(results)
                connected_count = sum(1 for item in results if item.get("is_connected"))

                with self.db_connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE group_discovery_requests
                        SET status = %s,
                            total = %s,
                            connected_count = %s,
                            results = %s::jsonb,
                            error = NULL,
                            completed_at = NOW()
                        WHERE id = %s
                        """,
                        (
                            "completed",
                            total,
                            connected_count,
                            json.dumps(results, ensure_ascii=False),
                            request_id,
                        ),
                    )
                    self.db_connection.commit()

                logger.info(
                    "Group discovery completed",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    total=total,
                    connected_count=connected_count,
                )

            except FloodWaitError as flood_err:
                wait_seconds = int(getattr(flood_err, "seconds", 30))
                logger.warning(
                    "Group discovery hit FloodWait",
                    request_id=request_id,
                    wait_seconds=wait_seconds,
                )
                try:
                    with self.db_connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE group_discovery_requests
                            SET status = %s,
                                error = %s,
                                completed_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                "failed",
                                f"FloodWait: retry after {wait_seconds}s",
                                request_id,
                            ),
                        )
                        self.db_connection.commit()
                except Exception as update_err:
                    logger.error("Failed to update discovery request after FloodWait", error=str(update_err))
                    try:
                        self.db_connection.rollback()
                    except Exception:
                        pass
                await asyncio.sleep(wait_seconds)
            except Exception as discovery_err:
                logger.error(
                    "Group discovery failed",
                    request_id=request_id,
                    error=str(discovery_err),
                )
                try:
                    with self.db_connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE group_discovery_requests
                            SET status = %s,
                                error = %s,
                                completed_at = NOW()
                            WHERE id = %s
                            """,
                            (
                                "failed",
                                str(discovery_err),
                                request_id,
                            ),
                        )
                        self.db_connection.commit()
                except Exception as update_err:
                    logger.error("Failed to set discovery request failed status", error=str(update_err))
                    try:
                        self.db_connection.rollback()
                    except Exception:
                        pass
                await asyncio.sleep(poll_interval)
            finally:
                await asyncio.sleep(0.1)

    async def _start_historical_parsing(self):
        """Context7 best practice: исторический парсинг сообщений за последние 24 часа.
        
        DEPRECATED: Используется ChannelParser через scheduler для historical парсинга.
        Этот метод отключен по умолчанию, так как ChannelParser поддерживает MediaProcessor.
        """
        # Context7: Отключено - используем ChannelParser через scheduler для исторического парсинга
        historical_enabled = os.getenv("TELEGRAM_INGEST_HISTORICAL_ENABLED", "false").lower() == "true"
        if not historical_enabled:
            logger.info("Historical parsing disabled - using ChannelParser via scheduler instead")
            return
        
        try:
            logger.info("Starting historical parsing for last 24 hours...")
            
            # Получаем активные каналы
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, tg_channel_id, username, title 
                    FROM channels 
                    WHERE is_active = true
                """)
                channels = cursor.fetchall()
            
            for channel in channels:
                try:
                    # Получаем entity канала
                    if channel['username']:
                        entity = await self.client.get_entity(channel['username'])
                    elif channel['tg_channel_id']:
                        entity = await self.client.get_entity(int(channel['tg_channel_id']))
                    else:
                        continue
                    
                    logger.info("Parsing historical messages", 
                              channel_id=channel['id'], 
                              username=channel['username'])
                    
                    # Парсим сообщения за последние 24 часа
                    await self._parse_historical_messages(entity, channel['id'])
                    
                except Exception as e:
                    logger.error("Failed to parse historical messages", 
                               channel_id=channel['id'], 
                               error=str(e))
            
            logger.info("Historical parsing completed")
            
        except Exception as e:
            logger.error("Failed to start historical parsing", error=str(e))

    async def _parse_historical_messages(self, entity, channel_id: str):
        """Парсинг исторических сообщений за последние 24 часа."""
        try:
            from datetime import datetime, timedelta
            
            # Время 24 часа назад (с timezone)
            from datetime import timezone
            since_date = datetime.now(timezone.utc) - timedelta(hours=24)
            
            # Context7: Используем TelegramClientManager для получения стабильного клиента
            client_manager = getattr(self, 'client_manager', None)
            telegram_id = getattr(self, 'telegram_id', None)
            
            if not client_manager or not telegram_id:
                logger.error("TelegramClientManager or telegram_id not available")
                return
                
            # Получаем клиент через manager
            client = await client_manager.get_client(telegram_id)
            if not client:
                logger.error("No available Telegram client")
                return
            
            # Получаем сообщения за последние 24 часа
            messages = []
            
            # Context7: Используем retry wrapper для стабильного получения сообщений
            try:
                from services.telethon_retry import fetch_messages_with_retry
                messages = await fetch_messages_with_retry(
                    client,
                    entity,
                    limit=10000,
                    redis_client=self.redis_client
                )
                
                # Фильтруем по времени
                filtered_messages = []
                for message in messages:
                    if message.date < since_date:
                        logger.debug(f"Reached since_date, stopping. message.date={message.date}, since_date={since_date}")
                        break
                    filtered_messages.append(message)
                    
                    # Ограничиваем по времени, а не по количеству
                    if len(filtered_messages) >= 10000:  # Защита от бесконечного цикла
                        logger.warning(f"Reached maximum messages limit (10000) for channel {channel_id}")
                        break
                
                messages = filtered_messages
                
            except Exception as e:
                logger.error("Failed to fetch messages with retry, using fallback", error=str(e))
                # Fallback к старому методу с лимитом
                async for message in client.iter_messages(entity, offset_date=since_date, limit=5000):
                    # Клиентская фильтрация (Telethon не умеет >= серверно)
                    if message.date < since_date:
                        logger.debug(f"Reached since_date, stopping. message.date={message.date}, since_date={since_date}")
                        break
                        
                    messages.append(message)
                    
                    # Ограничиваем по времени, а не по количеству
                    if len(messages) >= 5000:  # Защита от бесконечного цикла
                        logger.warning(f"Reached maximum messages limit (5000) for channel {channel_id}")
                        break
            
            logger.info("Found historical messages", 
                       channel_id=channel_id, 
                       count=len(messages))
            
            # Обрабатываем сообщения
            for message in messages:
                try:
                    await self._process_historical_message(message, channel_id)
                except Exception as e:
                    logger.error("Failed to process historical message", 
                               message_id=message.id, 
                               error=str(e))
            
        except Exception as e:
            logger.error("Failed to parse historical messages", 
                       channel_id=channel_id, 
                       error=str(e))

    async def _process_historical_message(self, message, channel_id: str):
        """Обработка исторического сообщения."""
        try:
            # Context7 best practice: используем channel_id напрямую для исторических сообщений
            channel_info = await self._get_channel_info_by_id(channel_id)
            if not channel_info:
                logger.warning("Channel info not found for historical message", 
                             channel_id=channel_id, 
                             message_id=message.id)
                return
            
            # Context7 best practice: извлекаем все доступные поля из Telegram сообщения
            message_data = await self._extract_message_data(message, channel_info['id'])
            
            # Context7: Генерация post_id и trace_id (для совместимости)
            tenant_id = message_data.get('tenant_id', 'default')
            trace_id = f"{tenant_id}:{channel_info['id']}:{message.id}"
            
            # Context7: Обработка медиа через MediaProcessor (если доступен)
            # NOTE: Исторический парсинг отключен по умолчанию, но если используется - медиа обрабатывается
            media_files = []
            if self.media_processor and message.media and self.client:
                try:
                    post_id = str(uuid.uuid4())
                    message_data['id'] = post_id
                    self.media_processor.telegram_client = self.client
                    media_files = await self.media_processor.process_message_media(
                        message=message,
                        post_id=post_id,
                        trace_id=trace_id,
                        tenant_id=tenant_id
                    )
                    if media_files:
                        message_data['media_files'] = media_files
                        message_data['media_count'] = len(media_files)
                        message_data['media_sha256_list'] = [mf.sha256 for mf in media_files]
                        message_data['has_media'] = True
                except Exception as e:
                    logger.warning("Failed to process media in historical message", error=str(e), exc_info=True)
            
            # Сохранение в БД
            post_id = await self._save_message(message_data)
            
            # Context7: Сохранение медиа в CAS (для исторического парсинга)
            if media_files and post_id and self.media_processor:
                try:
                    await self._save_media_to_cas_sync(post_id, media_files, trace_id)
                except Exception as e:
                    logger.warning("Failed to save media to CAS in historical message", error=str(e))
            
            # Публикация события в Redis Streams в формате PostParsedEventV1
            event_data = {
                'schema_version': 'v1',
                'trace_id': trace_id,  # Context7: Используем созданный trace_id
                'occurred_at': datetime.now(timezone.utc).isoformat(),
                'idempotency_key': f"{tenant_id}:{channel_info['id']}:{message.id}",
                'user_id': tenant_id,  # Context7: Используем tenant_id
                'channel_id': str(channel_info['id']),
                'post_id': post_id,
                'tenant_id': tenant_id,
                'text': message_data.get('content', ''),
                'urls': json.loads(message_data.get('urls', '[]')),
                'posted_at': message_data['created_at'],
                'content_hash': message_data.get('content_hash', ''),
                # Context7: Добавляем media_sha256_list для связи с обработанными медиа
                'media_sha256_list': message_data.get('media_sha256_list', []),
                'link_count': len(json.loads(message_data.get('urls', '[]'))),
                'tg_message_id': message.id,
                'telegram_message_id': message.id,
                'tg_channel_id': message_data.get('tg_channel_id', 0),
                'telegram_post_url': f"https://t.me/{channel_info.get('username', '')}/{message.id}" if channel_info.get('username') else None,
                'has_media': bool(message_data.get('has_media', False)),
                'is_edited': bool(message_data.get('is_edited', False)),
                'views_count': message_data.get('views_count', 0),
                'forwards_count': message_data.get('forwards_count', 0),
                'reactions_count': message_data.get('reactions_count', 0)
            }
            
            # Публикация в правильном формате (сериализация в JSON)
            # Context7 best practice: используем async Redis операции
            event_data_json = {k: json.dumps(v) if isinstance(v, (list, dict)) else str(v) for k, v in event_data.items()}
            await self.redis_client.xadd(STREAM_POST_CREATED, event_data_json)
            
            # Context7: Эмиссия VisionUploadedEventV1 для медиа (если обработано в историческом парсинге)
            if self.media_processor and media_files:
                try:
                    await self.media_processor.emit_vision_uploaded_event(
                        post_id=post_id,
                        tenant_id=tenant_id,
                        media_files=media_files,
                        trace_id=trace_id
                    )
                    logger.debug(
                        "Vision uploaded event emitted for historical message",
                        post_id=post_id,
                        media_count=len(media_files)
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to emit vision uploaded event for historical message",
                        post_id=post_id,
                        error=str(e),
                        exc_info=True
                    )
            
            logger.info("Historical message processed", 
                       post_id=post_id, 
                       channel_id=channel_info['id'],
                       message_id=message.id,
                       has_media=len(media_files) > 0)
            
        except Exception as e:
            logger.error("Failed to process historical message", 
                       message_id=message.id, 
                       error=str(e))

    async def _save_message(self, message_data: dict) -> str:
        """Context7 best practice: сохранение всех данных сообщения в БД."""
        try:
            with self.db_connection.cursor() as cursor:
                # Context7 P1.1: Подготовка новых полей для forwards и replies
                forward_from_peer_id_json = json.dumps(message_data.get('forward_from_peer_id')) if message_data.get('forward_from_peer_id') else None
                forward_date_dt = None
                if message_data.get('forward_date'):
                    try:
                        from dateutil.parser import parse as parse_date
                        forward_date_dt = parse_date(message_data['forward_date'])
                    except:
                        forward_date_dt = None
                
                cursor.execute("""
                    INSERT INTO posts (
                        channel_id, telegram_message_id, content, media_urls, created_at, is_processed,
                        posted_at, url, has_media, views_count, forwards_count, reactions_count,
                        replies_count, is_pinned, is_edited, edited_at, post_author,
                        reply_to_message_id, reply_to_chat_id, via_bot_id, via_business_bot_id,
                        is_silent, is_legacy, noforwards, invert_media,
                        forward_from_peer_id, forward_from_chat_id, forward_from_message_id,
                        forward_date, forward_from_name, thread_id, forum_topic_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (channel_id, telegram_message_id)
                    DO UPDATE SET
                        content = EXCLUDED.content,
                        media_urls = EXCLUDED.media_urls,
                        posted_at = EXCLUDED.posted_at,
                        url = EXCLUDED.url,
                        has_media = EXCLUDED.has_media,
                        views_count = EXCLUDED.views_count,
                        forwards_count = EXCLUDED.forwards_count,
                        reactions_count = EXCLUDED.reactions_count,
                        replies_count = EXCLUDED.replies_count,
                        is_pinned = EXCLUDED.is_pinned,
                        is_edited = EXCLUDED.is_edited,
                        edited_at = EXCLUDED.edited_at,
                        post_author = EXCLUDED.post_author,
                        reply_to_message_id = EXCLUDED.reply_to_message_id,
                        reply_to_chat_id = EXCLUDED.reply_to_chat_id,
                        via_bot_id = EXCLUDED.via_bot_id,
                        via_business_bot_id = EXCLUDED.via_business_bot_id,
                        is_silent = EXCLUDED.is_silent,
                        is_legacy = EXCLUDED.is_legacy,
                        noforwards = EXCLUDED.noforwards,
                        invert_media = EXCLUDED.invert_media,
                        forward_from_peer_id = EXCLUDED.forward_from_peer_id,
                        forward_from_chat_id = EXCLUDED.forward_from_chat_id,
                        forward_from_message_id = EXCLUDED.forward_from_message_id,
                        forward_date = EXCLUDED.forward_date,
                        forward_from_name = EXCLUDED.forward_from_name,
                        thread_id = EXCLUDED.thread_id,
                        forum_topic_id = EXCLUDED.forum_topic_id
                    RETURNING id
                """, (
                    message_data['channel_id'],
                    message_data['telegram_message_id'],
                    message_data['content'],
                    json.dumps(message_data.get('media_urls', [])),
                    message_data['created_at'],
                    message_data['is_processed'],
                    message_data.get('posted_at'),
                    message_data.get('url'),
                    message_data.get('has_media', False),
                    message_data.get('views_count', 0),
                    message_data.get('forwards_count', 0),
                    message_data.get('reactions_count', 0),
                    message_data.get('replies_count', 0),
                    message_data.get('is_pinned', False),
                    message_data.get('is_edited', False),
                    message_data.get('edited_at'),
                    message_data.get('post_author'),
                    message_data.get('reply_to_message_id'),
                    message_data.get('reply_to_chat_id'),
                    message_data.get('via_bot_id'),
                    message_data.get('via_business_bot_id'),
                    message_data.get('is_silent', False),
                    message_data.get('is_legacy', False),
                    message_data.get('noforwards', False),
                    message_data.get('invert_media', False),
                    forward_from_peer_id_json,
                    message_data.get('forward_from_chat_id'),
                    message_data.get('forward_from_message_id'),
                    forward_date_dt,
                    message_data.get('forward_from_name'),
                    message_data.get('thread_id'),
                    message_data.get('forum_topic_id')
                ))
                
                post_id = cursor.fetchone()[0]
                self.db_connection.commit()
                
                logger.debug("Message saved successfully", 
                           post_id=post_id, 
                           message_id=message_data['telegram_message_id'],
                           has_url=bool(message_data.get('url')),
                           views=message_data.get('views_count', 0))
                
                return str(post_id)
                
        except Exception as e:
            logger.error("Failed to save message", error=str(e), message_data=message_data)
            raise
    
    async def _process_message(self, event):
        """Обработка нового сообщения.
        
        Context7: Для групп использует клиент пользователя, подписанного на группу.
        """
        try:
            message = event.message
            channel = await event.get_chat()
            
            # Получение информации о канале из БД
            channel_info = await self._get_channel_info(channel.id)
            if not channel_info:
                # Попытка найти группу
                group_info = await self._get_group_info(channel.id)
                if group_info:
                    # Context7: Для групп используем клиент подписанного пользователя
                    subscriber_telegram_id = group_info.get("subscriber_telegram_id")
                    if subscriber_telegram_id and self.client_manager:
                        try:
                            subscriber_telegram_id_int = int(subscriber_telegram_id)
                            group_client = await self.client_manager.get_client(subscriber_telegram_id_int)
                            if group_client:
                                # Сохраняем оригинальный клиент и временно используем клиент подписчика
                                original_client = self.client
                                self.client = group_client
                                try:
                                    await self._process_group_message(message, group_info)
                                finally:
                                    self.client = original_client
                            else:
                                logger.warning(
                                    "No client available for group subscriber",
                                    group_id=group_info.get("id"),
                                    subscriber_telegram_id=subscriber_telegram_id_int,
                                )
                        except Exception as client_err:
                            logger.warning(
                                "Failed to get client for group subscriber",
                                error=str(client_err),
                                group_id=group_info.get("id"),
                                subscriber_telegram_id=subscriber_telegram_id,
                            )
                    else:
                        # Context7: Пропускаем сообщение, если нет подписчика в user_group
                        logger.warning(
                            "Group message skipped - no subscriber in user_group",
                            group_id=group_info.get("id"),
                            tg_chat_id=channel.id,
                        )
                else:
                    logger.warning(
                        "Chat not registered as channel or group",
                        telegram_chat_id=channel.id
                    )
                return
            
            # Context7 best practice: извлекаем все данные из сообщения
            message_data = await self._extract_message_data(message, channel_info['id'])
            
            # Context7: Генерация post_id и trace_id для MediaProcessor
            post_id = str(uuid.uuid4())
            trace_id = f"{message_data.get('tenant_id', 'default')}:{channel_info['id']}:{message.id}"
            message_data['id'] = post_id
            
            # Context7: Обработка медиа через MediaProcessor (если доступен)
            media_files = []
            if self.media_processor and message.media and self.client:
                try:
                    # Обновляем telegram_client в MediaProcessor
                    self.media_processor.telegram_client = self.client
                    
                    # Обработка медиа
                    media_files = await self.media_processor.process_message_media(
                        message=message,
                        post_id=post_id,
                        trace_id=trace_id,
                        tenant_id=message_data.get('tenant_id', 'default')
                    )
                    
                    # Сохраняем информацию о медиа
                    if media_files:
                        message_data['media_files'] = media_files
                        message_data['media_count'] = len(media_files)
                        message_data['media_sha256_list'] = [mf.sha256 for mf in media_files]
                        message_data['has_media'] = True
                    
                    logger.debug(
                        "Media processed in real-time event",
                        post_id=post_id,
                        media_count=len(media_files),
                        channel_id=channel_info['id']
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to process media in real-time event",
                        post_id=post_id,
                        error=str(e),
                        channel_id=channel_info['id'],
                        exc_info=True
                    )
                    # Продолжаем обработку даже при ошибке медиа
            
            # Сохранение в БД
            post_id = await self._save_message(message_data)
            
            # Context7: Сохранение медиа в CAS таблицы (media_objects + post_media_map)
            # Используем синхронный подход, так как TelegramIngestionService работает с psycopg2
            if media_files and post_id and self.media_processor:
                try:
                    await self._save_media_to_cas_sync(post_id, media_files, trace_id)
                    logger.debug(
                        "Media saved to CAS for real-time message",
                        post_id=post_id,
                        media_count=len(media_files)
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to save media to CAS for real-time message",
                        post_id=post_id,
                        error=str(e),
                        exc_info=True
                    )
                    # Не прерываем обработку - медиа уже в S3, можно восстановить позже
            
            # Публикация события в Redis Streams в формате PostParsedEventV1
            tenant_id = message_data.get('tenant_id', 'default')
            event_data = {
                'schema_version': 'v1',
                'trace_id': trace_id,  # Context7: Используем уже созданный trace_id
                'occurred_at': datetime.now(timezone.utc).isoformat(),
                'idempotency_key': f"{tenant_id}:{channel_info['id']}:{message.id}",
                'user_id': tenant_id,  # Context7: Используем tenant_id как user_id
                'channel_id': str(channel_info['id']),
                'post_id': post_id,
                'tenant_id': tenant_id,
                'text': message_data.get('content', ''),
                'urls': json.loads(message_data.get('urls', '[]')),
                'posted_at': message_data['created_at'],
                'content_hash': message_data.get('content_hash', ''),
                # Context7: Добавляем media_sha256_list для связи с обработанными медиа (для TaggingTask)
                'media_sha256_list': message_data.get('media_sha256_list', []),
                'link_count': len(json.loads(message_data.get('urls', '[]'))),
                'tg_message_id': message.id,
                'telegram_message_id': message.id,
                'tg_channel_id': message_data.get('tg_channel_id', 0),
                'telegram_post_url': f"https://t.me/{channel_info.get('username', '')}/{message.id}" if channel_info.get('username') else None,
                'has_media': bool(message_data.get('has_media', False)),
                'is_edited': bool(message_data.get('is_edited', False)),
                'views_count': message_data.get('views_count', 0),
                'forwards_count': message_data.get('forwards_count', 0),
                'reactions_count': message_data.get('reactions_count', 0)
            }
            
            # Публикация в правильном формате (сериализация в JSON)
            # Context7 best practice: используем async Redis операции
            event_data_json = {k: json.dumps(v) if isinstance(v, (list, dict)) else str(v) for k, v in event_data.items()}
            await self.redis_client.xadd(STREAM_POST_CREATED, event_data_json)
            
            # Context7: Эмиссия VisionUploadedEventV1 для медиа (если обработано)
            if self.media_processor and media_files:
                try:
                    await self.media_processor.emit_vision_uploaded_event(
                        post_id=post_id,
                        tenant_id=message_data.get('tenant_id', 'default'),
                        media_files=media_files,
                        trace_id=trace_id
                    )
                    logger.debug(
                        "Vision uploaded event emitted for real-time message",
                        post_id=post_id,
                        media_count=len(media_files)
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to emit vision uploaded event for real-time message",
                        post_id=post_id,
                        error=str(e),
                        exc_info=True
                    )
            
            logger.info("Message processed", 
                       post_id=post_id, 
                       channel_id=channel_info['id'],
                       message_id=message.id,
                       has_media=len(media_files) > 0)
            
        except FloodWaitError as e:
            logger.warning("FloodWait error", seconds=e.seconds)
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error("Error processing message", error=str(e))
    
    async def _process_message_edited(self, event):
        """Context7 P2: Обработка отредактированных сообщений."""
        try:
            message = event.message
            channel = await event.get_chat()
            
            # Получение информации о канале из БД
            channel_info = await self._get_channel_info(channel.id)
            if not channel_info:
                logger.debug("Channel not found for edited message", telegram_chat_id=channel.id)
                return
            
            # Context7 P2: Обновление поста в БД
            with self.db_connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE posts
                    SET content = %s,
                        is_edited = true,
                        edited_at = NOW(),
                        updated_at = NOW()
                    WHERE channel_id = %s AND telegram_message_id = %s
                    RETURNING id
                """, (
                    message.text or message.message or '',
                    channel_info['id'],
                    message.id
                ))
                
                result = cursor.fetchone()
                if result:
                    post_id = str(result[0])
                    self.db_connection.commit()
                    
                    # Context7 P2: Публикация события об обновлении
                    event_data = {
                        'post_id': post_id,
                        'channel_id': str(channel_info['id']),
                        'telegram_message_id': message.id,
                        'is_edited': True,
                        'edited_at': datetime.now(timezone.utc).isoformat(),
                        'content': message.text or message.message or ''
                    }
                    
                    event_data_json = {k: json.dumps(v) if isinstance(v, (list, dict)) else str(v) for k, v in event_data.items()}
                    await self.redis_client.xadd("stream:posts:edited", event_data_json)
                    
                    logger.info("Message edited and updated",
                               post_id=post_id,
                               channel_id=channel_info['id'],
                               message_id=message.id)
                else:
                    logger.debug("Post not found for edited message",
                               channel_id=channel_info['id'],
                               message_id=message.id)
                    self.db_connection.rollback()
                    
        except Exception as e:
            logger.error("Error processing edited message", error=str(e), message_id=message.id)
            try:
                self.db_connection.rollback()
            except:
                pass
    
    async def _process_message_deleted(self, event):
        """Context7 P2: Обработка удалённых сообщений."""
        try:
            # Context7 P2: MessageDeleted содержит список deleted_ids
            deleted_ids = getattr(event, 'deleted_ids', [])
            if not deleted_ids:
                return
            
            channel = await event.get_chat()
            channel_info = await self._get_channel_info(channel.id)
            if not channel_info:
                logger.debug("Channel not found for deleted message", telegram_chat_id=channel.id)
                return
            
            # Context7 P2: Помечаем посты как удалённые в БД
            with self.db_connection.cursor() as cursor:
                for message_id in deleted_ids:
                    cursor.execute("""
                        UPDATE posts
                        SET is_deleted = true,
                            deleted_at = NOW(),
                            updated_at = NOW()
                        WHERE channel_id = %s AND telegram_message_id = %s
                        RETURNING id
                    """, (
                        channel_info['id'],
                        message_id
                    ))
                    
                    result = cursor.fetchone()
                    if result:
                        post_id = str(result[0])
                        
                        # Context7 P2: Публикация события об удалении
                        event_data = {
                            'post_id': post_id,
                            'channel_id': str(channel_info['id']),
                            'telegram_message_id': message_id,
                            'is_deleted': True,
                            'deleted_at': datetime.now(timezone.utc).isoformat()
                        }
                        
                        event_data_json = {k: json.dumps(v) if isinstance(v, (list, dict)) else str(v) for k, v in event_data.items()}
                        await self.redis_client.xadd("stream:posts:deleted", event_data_json)
                        
                        logger.info("Message deleted and marked",
                                   post_id=post_id,
                                   channel_id=channel_info['id'],
                                   message_id=message_id)
                
                self.db_connection.commit()
                    
        except Exception as e:
            logger.error("Error processing deleted message", error=str(e), deleted_ids=deleted_ids)
            try:
                self.db_connection.rollback()
            except:
                pass
    
    async def _get_channel_info(self, telegram_id: int) -> Optional[dict]:
        """Получение информации о канале из БД."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, tg_channel_id, username, title 
                    FROM channels 
                    WHERE tg_channel_id = %s AND is_active = true
                """, (telegram_id,))
                return cursor.fetchone()
        except Exception as e:
            logger.error("Failed to get channel info", error=str(e))
            # Context7 best practice: rollback абортированной транзакции
            try:
                self.db_connection.rollback()
                logger.info("Rolled back aborted transaction")
            except:
                pass
            return None

    async def _get_group_info(self, telegram_id: int) -> Optional[dict]:
        """Получение информации о группе из БД с информацией о подписанном пользователе.
        
        Context7: Возвращает группу и telegram_id пользователя, подписанного на неё.
        Если подписки нет, использует пользователя из того же tenant с авторизованной сессией.
        """
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                # Context7: Сначала ищем группу с подписанным пользователем
                cursor.execute("""
                    SELECT 
                        g.id, 
                        g.tenant_id, 
                        g.tg_chat_id, 
                        g.username, 
                        g.title, 
                        g.settings,
                        u.telegram_id as subscriber_telegram_id
                    FROM groups g
                    INNER JOIN user_group ug ON g.id = ug.group_id
                    INNER JOIN users u ON ug.user_id = u.id
                    WHERE g.tg_chat_id = %s 
                      AND g.is_active = true
                      AND ug.is_active = true
                      AND u.telegram_auth_status = 'authorized'
                      AND u.telegram_session_enc IS NOT NULL
                    ORDER BY ug.subscribed_at DESC
                    LIMIT 1
                """, (telegram_id,))
                row = cursor.fetchone()
                
                return row
        except Exception as e:
            logger.error("Failed to get group info", error=str(e))
            try:
                self.db_connection.rollback()
            except Exception:
                pass
            return None

    async def _process_group_message(self, message, group_info: dict):
        """Обработка сообщения из Telegram-группы."""
        group_message_uuid = str(uuid.uuid4())
        try:
            message_data = await self._extract_group_message_data(message, group_info)
            message_data["id"] = group_message_uuid

            media_files: List[Any] = []
            if self.media_processor and message.media and self.client:
                try:
                    self.media_processor.telegram_client = self.client
                    media_files = await self.media_processor.process_message_media(
                        message=message,
                        post_id=group_message_uuid,
                        trace_id=message_data["trace_id"],
                        tenant_id=str(group_info.get("tenant_id")),
                        channel_id=str(group_info.get("id")),
                    )
                    if media_files:
                        message_data["media_files"] = media_files
                        message_data["has_media"] = True
                except Exception as media_err:
                    logger.warning(
                        "Failed to process group media",
                        error=str(media_err),
                        group_id=group_info.get("id"),
                        trace_id=message_data["trace_id"],
                        exc_info=True,
                    )

            group_message_id = await self._save_group_message(message_data)

            if media_files:
                try:
                    await self._save_group_media_to_cas_sync(group_message_id, media_files, message_data["trace_id"])
                except Exception as cas_err:
                    logger.warning(
                        "Failed to save group media to CAS",
                        error=str(cas_err),
                        group_message_id=group_message_id,
                        exc_info=True,
                    )

            await self._publish_group_message_event(group_message_id, message_data, group_info)

            logger.info(
                "Group message processed",
                group_message_id=group_message_id,
                group_id=group_info.get("id"),
                tenant_id=group_info.get("tenant_id"),
                tg_message_id=message.id,
                has_media=bool(message_data.get("has_media")),
            )
        except Exception as e:
            logger.error(
                "Failed to process group message",
                error=str(e),
                group_id=group_info.get("id"),
                tg_message_id=message.id,
            )

    async def _extract_group_message_data(self, message, group_info: dict) -> dict:
        """Извлечение данных сообщения группы."""
        tenant_id = str(group_info.get("tenant_id"))
        group_id = str(group_info.get("id"))
        media_urls = await self._extract_media_urls(message)

        sender_tg_id = None
        sender_username = None

        try:
            if getattr(message, "from_id", None):
                sender_tg_id = getattr(message.from_id, "user_id", None) or getattr(message.from_id, "channel_id", None)
        except Exception:
            sender_tg_id = None

        try:
            sender = getattr(message, "sender", None)
            if sender:
                sender_username = getattr(sender, "username", None)
                if not sender_tg_id:
                    sender_tg_id = getattr(sender, "id", None)
            elif sender_tg_id and self.client:
                entity = await self.client.get_entity(sender_tg_id)
                sender_username = getattr(entity, "username", None)
        except Exception as e:
            logger.debug("Failed to resolve group sender entity", error=str(e))

        reply_to_info = None
        try:
            if getattr(message, "reply_to", None):
                reply_to_peer = getattr(message.reply_to, "reply_to_peer_id", None)
                reply_peer_id = None
                if reply_to_peer:
                    reply_peer_id = getattr(reply_to_peer, "chat_id", None) or getattr(reply_to_peer, "channel_id", None) or getattr(reply_to_peer, "user_id", None)
                reply_to_info = {
                    "message_id": getattr(message.reply_to, "reply_to_msg_id", None),
                    "chat_id": reply_peer_id,
                }
        except Exception:
            reply_to_info = None

        mentions = self._extract_mentions(message)
        indicators_stub = {
            "tone": "unknown",
            "conflict": None,
            "collaboration": None,
            "stress": None,
            "enthusiasm": None,
        }

        posted_at = message.date.replace(tzinfo=timezone.utc) if message.date and message.date.tzinfo is None else message.date or datetime.now(timezone.utc)
        if posted_at is None:
            posted_at = datetime.now(timezone.utc)
        created_at = datetime.now(timezone.utc)

        message_data = {
            "group_id": group_id,
            "tenant_id": tenant_id,
            "tg_message_id": message.id,
            "sender_tg_id": sender_tg_id,
            "sender_username": sender_username,
            "content": message.message or message.text or "",
            "media_urls": media_urls,
            "reply_to": reply_to_info,
            "mentions": mentions,
            "has_media": bool(media_urls),
            "is_service": bool(getattr(message, "action", None)),
            "action_type": getattr(getattr(message, "action", None), "__class__", type(None)).__name__,
            "posted_at": posted_at,
            "created_at": created_at,
            "indicators": indicators_stub,
            "trace_id": f"{tenant_id}:{group_id}:{message.id}",
        }

        return message_data

    def _extract_mentions(self, message) -> list[dict]:
        """Извлечение упоминаний пользователей из сообщения."""
        mentions: list[dict] = []
        try:
            entities = getattr(message, "entities", []) or []
            if not entities:
                return mentions
            for entity in entities:
                entity_type = entity.__class__.__name__
                if entity_type in {"MessageEntityMention", "MessageEntityMentionName"}:
                    mention_text = message.message[entity.offset : entity.offset + entity.length] if message.message else ""
                    mentions.append(
                        {
                            "type": entity_type,
                            "text": mention_text,
                            "user_id": getattr(entity, "user_id", None),
                        }
                    )
        except Exception as e:
            logger.debug("Failed to extract mentions", error=str(e))
        return mentions

    async def _save_group_message(self, message_data: dict) -> str:
        """Сохранение сообщения группы и связанных записей."""
        try:
            with self.db_connection.cursor() as cursor:
                # Context7: Устанавливаем tenant_id для RLS перед INSERT
                tenant_id = message_data.get("tenant_id")
                if tenant_id:
                    cursor.execute("SET LOCAL app.tenant_id = %s", (str(tenant_id),))
                
                cursor.execute(
                    """
                    INSERT INTO group_messages (
                        id,
                        group_id,
                        tenant_id,
                        tg_message_id,
                        sender_tg_id,
                        sender_username,
                        content,
                        media_urls,
                        reply_to,
                        posted_at,
                        created_at,
                        updated_at,
                        has_media,
                        is_service,
                        action_type
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s::jsonb, %s::jsonb, %s, %s,
                        NOW(), %s, %s, %s
                    )
                    ON CONFLICT (group_id, tg_message_id)
                    DO UPDATE SET
                        content = EXCLUDED.content,
                        media_urls = EXCLUDED.media_urls,
                        reply_to = EXCLUDED.reply_to,
                        sender_tg_id = EXCLUDED.sender_tg_id,
                        sender_username = EXCLUDED.sender_username,
                        has_media = EXCLUDED.has_media,
                        is_service = EXCLUDED.is_service,
                        action_type = EXCLUDED.action_type,
                        posted_at = EXCLUDED.posted_at,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        message_data.get("id"),
                        message_data["group_id"],
                        message_data["tenant_id"],
                        message_data["tg_message_id"],
                        message_data["sender_tg_id"],
                        message_data["sender_username"],
                        message_data["content"],
                        json.dumps(message_data.get("media_urls", [])),
                        json.dumps(message_data.get("reply_to")),
                        message_data.get("posted_at"),
                        message_data.get("created_at"),
                        message_data.get("has_media", False),
                        message_data.get("is_service", False),
                        message_data.get("action_type"),
                    ),
                )

                row = cursor.fetchone()
                group_message_id = row[0]

                # Mentions
                if message_data.get("mentions"):
                    for mention in message_data["mentions"]:
                        cursor.execute(
                            """
                            INSERT INTO group_mentions (
                                group_message_id,
                                mentioned_user_tg_id,
                                context_snippet,
                                is_processed,
                                created_at
                            ) VALUES (
                                %s, %s, %s, %s, NOW()
                            )
                            ON CONFLICT (group_message_id, mentioned_user_tg_id) DO NOTHING
                            """,
                            (
                                group_message_id,
                                mention.get("user_id"),
                                mention.get("text"),
                                False,
                            ),
                        )

                # Стартовая запись в analytics (placeholder)
                cursor.execute(
                    """
                    INSERT INTO group_message_analytics (
                        message_id,
                        embeddings,
                        tags,
                        entities,
                        sentiment_score,
                        emotions,
                        moderation_flags,
                        analysed_at,
                        metadata_payload
                    )
                    VALUES (
                        %s,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        %s::jsonb
                    )
                    ON CONFLICT (message_id) DO NOTHING
                    """,
                    (
                        group_message_id,
                        json.dumps({"status": "pending"}),
                    ),
                )

                self.db_connection.commit()
                return str(group_message_id)
        except Exception as e:
            try:
                self.db_connection.rollback()
            except Exception:
                pass
            logger.error(
                "Failed to save group message",
                error=str(e),
                group_id=message_data.get("group_id"),
                tg_message_id=message_data.get("tg_message_id"),
            )
            raise

    async def _publish_group_message_event(self, group_message_id: str, message_data: dict, group_info: dict):
        """Публикация события group.message.created в Redis Stream."""
        try:
            # Context7: Сериализация datetime для JSON
            posted_at = message_data.get("posted_at")
            if posted_at and isinstance(posted_at, datetime):
                posted_at_str = posted_at.isoformat()
            elif posted_at:
                posted_at_str = str(posted_at)
            else:
                posted_at_str = None
            
            payload = {
                "group_message_id": str(group_message_id),
                "group_id": str(group_info.get("id")),
                "tenant_id": str(group_info.get("tenant_id")),
                "tg_message_id": str(message_data.get("tg_message_id")),
                "content": message_data.get("content", ""),
                "posted_at": posted_at_str,
                "trace_id": message_data.get("trace_id"),
                "sender_tg_id": str(message_data.get("sender_tg_id") or ""),
                "sender_username": message_data.get("sender_username") or "",
                "has_media": str(message_data.get("has_media", False)),
                "mentions": json.dumps(message_data.get("mentions", [])),
            }

            event_data = {
                key: value if isinstance(value, str) else json.dumps(value)
                for key, value in payload.items()
                if value is not None
            }

            await self.redis_client.xadd(STREAM_GROUP_MESSAGE_CREATED, event_data)
        except Exception as e:
            logger.warning(
                "Failed to publish group message event",
                error=str(e),
                group_message_id=group_message_id,
            )

    async def _get_channel_info_by_id(self, channel_id: str) -> Optional[dict]:
        """Получение информации о канале по ID из БД."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, tg_channel_id, username, title 
                    FROM channels 
                    WHERE id = %s AND is_active = true
                """, (channel_id,))
                return cursor.fetchone()
        except Exception as e:
            logger.error("Failed to get channel info by ID", error=str(e))
            # Context7 best practice: rollback абортированной транзакции
            try:
                self.db_connection.rollback()
                logger.info("Rolled back aborted transaction")
            except:
                pass
            return None
    
    
    async def _extract_message_data(self, message, channel_id: str) -> dict:
        """Context7 best practice: извлечение всех данных из Telegram сообщения."""
        try:
            # Базовые поля
            message_data = {
                'channel_id': channel_id,
                'telegram_message_id': message.id,
                'content': message.text or '',
                'created_at': message.date.isoformat(),
                'is_processed': False
            }
            
            # URL сообщения (если есть)
            if hasattr(message, 'web_preview') and message.web_preview:
                message_data['url'] = getattr(message.web_preview, 'url', None)
            
            # Медиа
            media_urls = await self._extract_media_urls(message)
            message_data['media_urls'] = media_urls
            message_data['has_media'] = len(media_urls) > 0
            
            # Метаданные сообщения
            message_data['posted_at'] = message.date.isoformat() if message.date else None
            message_data['views_count'] = getattr(message, 'views', 0) or 0
            message_data['forwards_count'] = getattr(message, 'forwards', 0) or 0
            # Context7 best practice: безопасная обработка reactions
            reactions = getattr(message, 'reactions', None)
            if reactions and hasattr(reactions, '__len__'):
                message_data['reactions_count'] = len(reactions)
            else:
                message_data['reactions_count'] = 0
            # Context7 best practice: безопасная обработка replies
            replies = getattr(message, 'replies', None)
            if replies and hasattr(replies, 'replies'):
                message_data['replies_count'] = replies.replies
            else:
                message_data['replies_count'] = 0
            
            # Флаги
            message_data['is_pinned'] = getattr(message, 'pinned', False)
            message_data['is_edited'] = getattr(message, 'edit_date', None) is not None
            message_data['edited_at'] = message.edit_date.isoformat() if getattr(message, 'edit_date', None) else None
            message_data['is_silent'] = getattr(message, 'silent', False)
            message_data['is_legacy'] = getattr(message, 'legacy', False)
            message_data['noforwards'] = getattr(message, 'noforwards', False)
            message_data['invert_media'] = getattr(message, 'invert_media', False)
            
            # Автор и реплаи
            if hasattr(message, 'from_id') and message.from_id:
                if hasattr(message.from_id, 'user_id'):
                    message_data['post_author'] = str(message.from_id.user_id)
                elif hasattr(message.from_id, 'channel_id'):
                    message_data['post_author'] = f"channel_{message.from_id.channel_id}"
            
            # Context7 P1.1: Глубокое извлечение replies с поддержкой thread_id
            if hasattr(message, 'reply_to') and message.reply_to:
                reply_to = message.reply_to
                message_data['reply_to_message_id'] = getattr(reply_to, 'reply_to_msg_id', None)
                
                # Thread ID (для каналов с комментариями)
                if hasattr(reply_to, 'reply_to_top_id'):
                    message_data['thread_id'] = reply_to.reply_to_top_id
                elif hasattr(reply_to, 'reply_to_forum_top_id'):
                    message_data['forum_topic_id'] = reply_to.reply_to_forum_top_id
                
                # Peer ID для reply
                if hasattr(reply_to, 'reply_to_peer_id'):
                    peer_id = reply_to.reply_to_peer_id
                    if hasattr(peer_id, 'channel_id'):
                        message_data['reply_to_chat_id'] = peer_id.channel_id
                    elif hasattr(peer_id, 'chat_id'):
                        message_data['reply_to_chat_id'] = peer_id.chat_id
            
            # Context7 P1.1: Глубокое извлечение forwards из MessageFwdHeader
            if hasattr(message, 'fwd_from') and message.fwd_from:
                fwd_from = message.fwd_from
                
                # Быстрые поля в Post для прямого доступа
                if hasattr(fwd_from, 'from_id'):
                    from_id = fwd_from.from_id
                    # Сохраняем полный peer ID как JSONB
                    peer_id_data = {}
                    if hasattr(from_id, 'user_id'):
                        peer_id_data['user_id'] = from_id.user_id
                        message_data['forward_from_peer_id'] = {'user_id': from_id.user_id}
                    elif hasattr(from_id, 'channel_id'):
                        peer_id_data['channel_id'] = from_id.channel_id
                        message_data['forward_from_peer_id'] = {'channel_id': from_id.channel_id}
                        message_data['forward_from_chat_id'] = from_id.channel_id
                    elif hasattr(from_id, 'chat_id'):
                        peer_id_data['chat_id'] = from_id.chat_id
                        message_data['forward_from_peer_id'] = {'chat_id': from_id.chat_id}
                        message_data['forward_from_chat_id'] = from_id.chat_id
                
                # Message ID и дата
                if hasattr(fwd_from, 'channel_post'):
                    message_data['forward_from_message_id'] = fwd_from.channel_post
                
                if hasattr(fwd_from, 'date'):
                    message_data['forward_date'] = fwd_from.date.isoformat() if fwd_from.date else None
                
                # Имя автора (если доступно)
                if hasattr(fwd_from, 'from_name'):
                    message_data['forward_from_name'] = fwd_from.from_name
                
                # Дополнительные поля MessageFwdHeader
                if hasattr(fwd_from, 'post_author'):
                    message_data['forward_post_author_signature'] = fwd_from.post_author
                
                if hasattr(fwd_from, 'saved_from_peer'):
                    saved_peer = fwd_from.saved_from_peer
                    saved_peer_data = {}
                    if hasattr(saved_peer, 'user_id'):
                        saved_peer_data['user_id'] = saved_peer.user_id
                    elif hasattr(saved_peer, 'channel_id'):
                        saved_peer_data['channel_id'] = saved_peer.channel_id
                    elif hasattr(saved_peer, 'chat_id'):
                        saved_peer_data['chat_id'] = saved_peer.chat_id
                    message_data['forward_saved_from_peer'] = saved_peer_data
                
                if hasattr(fwd_from, 'saved_from_msg_id'):
                    message_data['forward_saved_from_msg_id'] = fwd_from.saved_from_msg_id
                
                if hasattr(fwd_from, 'psa_type'):
                    message_data['forward_psa_type'] = fwd_from.psa_type
            
            # Боты
            if hasattr(message, 'via_bot_id') and message.via_bot_id:
                message_data['via_bot_id'] = message.via_bot_id
            if hasattr(message, 'via_business_bot_id') and message.via_business_bot_id:
                message_data['via_business_bot_id'] = message.via_business_bot_id
            
            logger.debug("Extracted message data", 
                         message_id=message.id, 
                         has_url=bool(message_data.get('url')),
                         has_media=message_data.get('has_media', False),
                         views=message_data.get('views_count', 0))
            
            return message_data
            
        except Exception as e:
            logger.error("Failed to extract message data", error=str(e), message_id=message.id)
            # Возвращаем базовые данные в случае ошибки
            return {
                'channel_id': channel_id,
                'telegram_message_id': message.id,
                'content': getattr(message, 'text', '') or '',
                'media_urls': [],
                'created_at': message.date.isoformat() if message.date else None,
                'is_processed': False,
                'has_media': False
            }

    async def _extract_media_urls(self, message) -> List[str]:
        """Извлечение URL медиафайлов из сообщения."""
        media_urls = []
        if message.photo:
            media_urls.append(f"photo:{message.photo.id}")
        if message.video:
            media_urls.append(f"video:{message.video.id}")
        if message.document:
            media_urls.append(f"document:{message.document.id}")
        return media_urls
    
    # Публикация перенесена в services.events
    
    async def _save_media_to_cas_sync(self, post_id: str, media_files: List[Any], trace_id: str):
        """
        Context7: Сохранение медиа в CAS таблицы (media_objects + post_media_map).
        
        Использует синхронный psycopg2, так как TelegramIngestionService работает с синхронной БД.
        TODO: [C7-ID: ARCH-REFACTOR-001] Рефакторинг на async БД для использования AtomicDBSaver.save_media_to_cas
        
        Args:
            post_id: UUID поста
            media_files: Список MediaFile объектов (sha256, s3_key, mime_type, size_bytes)
            trace_id: Trace ID для логирования
        """
        if not media_files or not post_id:
            return
        
        try:
            s3_bucket = self.media_processor.s3_service.bucket_name if self.media_processor else None
            if not s3_bucket:
                logger.warning("S3 bucket not available for CAS save", post_id=post_id)
                return
            
            with self.db_connection.cursor() as cursor:
                # Context7: UPSERT в media_objects с инкрементом refs_count
                for media_file in media_files:
                    cursor.execute("""
                        INSERT INTO media_objects (
                            file_sha256, mime, size_bytes, s3_key, s3_bucket,
                            first_seen_at, last_seen_at, refs_count
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            NOW(), NOW(), 1
                        )
                        ON CONFLICT (file_sha256) DO UPDATE SET
                            last_seen_at = NOW(),
                            refs_count = media_objects.refs_count + 1,
                            s3_key = EXCLUDED.s3_key,
                            s3_bucket = EXCLUDED.s3_bucket
                    """, (
                        media_file.sha256,
                        media_file.mime_type,
                        media_file.size_bytes,
                        media_file.s3_key,
                        s3_bucket
                    ))
                
                # Context7: INSERT в post_media_map с ON CONFLICT DO NOTHING
                for idx, media_file in enumerate(media_files):
                    cursor.execute("""
                        INSERT INTO post_media_map (
                            post_id, file_sha256, position, meta
                        ) VALUES (
                            %s, %s, %s, %s::jsonb
                        )
                        ON CONFLICT (post_id, file_sha256) DO NOTHING
                    """, (
                        post_id,
                        media_file.sha256,
                        idx,
                        json.dumps({
                            "s3_key": media_file.s3_key,
                            "s3_bucket": s3_bucket,
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes
                        })
                    ))
                
                self.db_connection.commit()
                logger.debug(
                    "Media saved to CAS successfully",
                    post_id=post_id,
                    media_count=len(media_files),
                    trace_id=trace_id
                )
        except Exception as e:
            self.db_connection.rollback()
            logger.error(
                "Failed to save media to CAS",
                post_id=post_id,
                error=str(e),
                trace_id=trace_id,
                exc_info=True
            )
            raise
    
    async def _save_group_media_to_cas_sync(self, group_message_id: str, media_files: List[Any], trace_id: str):
        """Сохранение медиа для групповых сообщений в CAS (media_objects + group_media_map)."""
        if not media_files or not group_message_id:
            return
        try:
            s3_bucket = self.media_processor.s3_service.bucket_name if self.media_processor else None
            if not s3_bucket:
                logger.warning("S3 bucket not available for group CAS save", group_message_id=group_message_id)
                return

            with self.db_connection.cursor() as cursor:
                for media_file in media_files:
                    cursor.execute(
                        """
                        INSERT INTO media_objects (
                            file_sha256, mime, size_bytes, s3_key, s3_bucket,
                            first_seen_at, last_seen_at, refs_count
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            NOW(), NOW(), 1
                        )
                        ON CONFLICT (file_sha256) DO UPDATE SET
                            last_seen_at = NOW(),
                            refs_count = media_objects.refs_count + 1,
                            s3_key = EXCLUDED.s3_key,
                            s3_bucket = EXCLUDED.s3_bucket
                        """,
                        (
                            media_file.sha256,
                            media_file.mime_type,
                            media_file.size_bytes,
                            media_file.s3_key,
                            s3_bucket,
                        ),
                    )

                for idx, media_file in enumerate(media_files):
                    cursor.execute(
                        """
                        INSERT INTO group_media_map (
                            group_message_id, file_sha256, position, meta
                        ) VALUES (
                            %s, %s, %s, %s::jsonb
                        )
                        ON CONFLICT (group_message_id, file_sha256) DO NOTHING
                        """,
                        (
                            group_message_id,
                            media_file.sha256,
                            idx,
                            json.dumps(
                                {
                                    "s3_key": media_file.s3_key,
                                    "s3_bucket": s3_bucket,
                                    "mime_type": media_file.mime_type,
                                    "size_bytes": media_file.size_bytes,
                                }
                            ),
                        ),
                    )

                self.db_connection.commit()
        except Exception as e:
            self.db_connection.rollback()
            logger.error(
                "Failed to save group media to CAS",
                group_message_id=group_message_id,
                error=str(e),
                trace_id=trace_id,
                exc_info=True,
            )
            raise
    
    async def stop(self):
        """Остановка сервиса."""
        self.is_running = False
        if self.client:
            await self.client.disconnect()
        # Context7 best practice: закрываем async Redis клиент
        if self.redis_client:
            await self.redis_client.aclose()
        if self.db_connection:
            self.db_connection.close()
        logger.info("Telegram ingestion service stopped")
