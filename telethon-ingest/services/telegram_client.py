"""Telegram клиент для парсинга каналов."""

import asyncio
import logging
import os
import uuid
from typing import List, Optional, Any
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, AuthKeyError
import structlog
import redis.asyncio as redis
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime, timezone

from config import settings
from services.events import publish_post_created, STREAM_POST_CREATED

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
            logger.info("Database connected")
            
            # Context7: Получаем клиент через TelegramClientManager
            # Сначала получаем telegram_id из БД
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL LIMIT 1")
            result = cursor.fetchone()
            cursor.close()
            
            if not result:
                logger.error("No telegram_id found in users table")
                return
                
            telegram_id = result[0]
            self.telegram_id = telegram_id  # Context7: Сохраняем для использования в других методах
            self.client = await self.client_manager.get_client(telegram_id)
            if not self.client:
                logger.error("No available Telegram client from manager")
                return
                
            logger.info("Telegram client obtained from manager", telegram_id=telegram_id)
            
            # Регистрация обработчиков событий
            self._register_handlers()
            
            # Загрузка активных каналов (неблокирующая)
            asyncio.create_task(self._load_active_channels())
            
            # Context7 best practice: исторический парсинг для отладки (неблокирующий)
            asyncio.create_task(self._start_historical_parsing())
            
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
        """Регистрация обработчиков событий."""
        
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            """Обработка новых сообщений."""
            try:
                await self._process_message(event)
            except Exception as e:
                logger.error("Error processing message", error=str(e), message_id=event.message.id)
    
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
                cursor.execute("""
                    INSERT INTO posts (
                        channel_id, telegram_message_id, content, media_urls, created_at, is_processed,
                        posted_at, url, has_media, views_count, forwards_count, reactions_count,
                        replies_count, is_pinned, is_edited, edited_at, post_author,
                        reply_to_message_id, reply_to_chat_id, via_bot_id, via_business_bot_id,
                        is_silent, is_legacy, noforwards, invert_media
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                        invert_media = EXCLUDED.invert_media
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
                    message_data.get('invert_media', False)
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
        """Обработка нового сообщения."""
        try:
            message = event.message
            channel = await event.get_chat()
            
            # Получение информации о канале из БД
            channel_info = await self._get_channel_info(channel.id)
            if not channel_info:
                logger.warning("Channel not found in database", telegram_id=channel.id)
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
            
            # Реплаи
            if hasattr(message, 'reply_to') and message.reply_to:
                message_data['reply_to_message_id'] = getattr(message.reply_to, 'reply_to_msg_id', None)
                if hasattr(message.reply_to, 'reply_to_peer_id'):
                    if hasattr(message.reply_to.reply_to_peer_id, 'channel_id'):
                        message_data['reply_to_chat_id'] = message.reply_to.reply_to_peer_id.channel_id
                    elif hasattr(message.reply_to.reply_to_peer_id, 'chat_id'):
                        message_data['reply_to_chat_id'] = message.reply_to.reply_to_peer_id.chat_id
            
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
