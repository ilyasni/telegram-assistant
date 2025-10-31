"""
Telethon Channel Parser для парсинга каналов Telegram
Поддерживает FloodWait handling, bulk insert, идемпотентность и event publishing
"""

import asyncio
import hashlib
import logging
import os
import time
import uuid
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
import json

import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient, errors
from telethon.tl.types import Message, Channel, Chat
import structlog

# Context7: Импорт новых компонентов
from .telethon_retry import fetch_messages_with_retry, is_channel_in_cooldown
from .atomic_db_saver import AtomicDBSaver
from .rate_limiter import RateLimiter, check_parsing_rate_limit
from utils.time_utils import ensure_dt_utc

# WORKER IMPORT DISABLED - will be restored when worker module is available
# from worker.event_bus import EventPublisher, PostParsedEvent
# from worker.events.schemas.posts_parsed_v1 import PostParsedEventV1

logger = structlog.get_logger()

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

@dataclass
class ParserConfig:
    """Конфигурация парсера каналов."""
    # Режимы парсинга
    mode_override: str = os.getenv("PARSER_MODE_OVERRIDE", "auto")  # auto|historical|incremental
    historical_hours: int = int(os.getenv("PARSER_HISTORICAL_HOURS", "24"))
    incremental_minutes: int = int(os.getenv("PARSER_INCREMENTAL_MINUTES", "5"))
    lpa_max_age_hours: int = int(os.getenv("PARSER_LPA_MAX_AGE_HOURS", "48"))
    
    # Батчинг
    max_messages_per_batch: int = 50
    batch_delay_ms: int = 1000
    
    # FloodWait handling
    max_flood_wait: int = 60
    flood_wait_backoff: float = 1.5
    
    # Идемпотентность
    idempotency_window_hours: int = 24  # Окно для проверки дубликатов
    
    # Concurrency and retries
    max_concurrency: int = int(os.getenv("PARSER_MAX_CONCURRENCY", "4"))
    retry_max: int = int(os.getenv("PARSER_RETRY_MAX", "3"))
    
    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # База данных
    db_url: str = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

# ============================================================================
# CHANNEL PARSER
# ============================================================================

class ChannelParser:
    """Парсер каналов Telegram с поддержкой идемпотентности и событий."""
    
    def __init__(
        self,
        config: ParserConfig,
        db_session: AsyncSession,
        event_publisher: Any,  # EventPublisher - temporarily disabled
        redis_client: Optional[Any] = None,
        atomic_saver: Optional[AtomicDBSaver] = None,
        rate_limiter: Optional[RateLimiter] = None,
        telegram_client_manager: Optional[Any] = None
    ):
        self.config = config
        self.db_session = db_session
        self.event_publisher = event_publisher
        
        # Context7: Redis клиент (переданный или созданный)
        if redis_client:
            self.redis_client = redis_client
        else:
            redis_url = getattr(config, 'redis_url', os.getenv("REDIS_URL", "redis://redis:6379"))
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
        
        # Context7: Новые компоненты
        self.atomic_saver = atomic_saver or AtomicDBSaver()
        self.rate_limiter = rate_limiter
        self.telegram_client_manager = telegram_client_manager
        
        # Статистика
        self.stats = {
            'messages_parsed': 0,
            'messages_skipped': 0,
            'flood_wait_count': 0,
            'errors': 0,
            'rate_limited': 0,
            'cooldown_skipped': 0
        }
        
        logger.info("Channel parser initialized with Context7 components")
    
    async def parse_channel_messages(
        self,
        channel_id: str,
        user_id: str,
        tenant_id: str,
        mode: str = "historical"
    ) -> Dict[str, Any]:
        """
        Парсинг сообщений из канала.
        
        Args:
            channel_id: ID канала в БД
            user_id: ID пользователя
            tenant_id: ID арендатора
            mode: режим парсинга (historical/incremental)
            
        Returns:
            Статистика парсинга с max_message_date для HWM
        """
        start_time = time.time()
        max_message_date = None
        
        logger.info("parse_channel_messages started", 
                   channel_id=channel_id,
                   mode=mode,
                   user_id=user_id)
        
        try:
            # Context7: Получение Telegram клиента из менеджера
            if not self.telegram_client_manager:
                logger.error("TelegramClientManager not available")
                self.stats['errors'] += 1
                return {
                    'processed': 0,
                    'skipped': 0,
                    'max_date': None,
                    'error': 'no_client_manager'
                }
            
            telegram_client = await self.telegram_client_manager.get_client(user_id)
            if not telegram_client:
                logger.error("No telegram client available for user", user_id=user_id)
                self.stats['errors'] += 1
                return {
                    'processed': 0,
                    'skipped': 0,
                    'max_date': None,
                    'error': 'no_client'
                }
            
            # Context7: Получение tg_channel_id для проверки cooldown
            logger.info("Fetching tg_channel_id from DB", channel_id=channel_id)
            result = await self.db_session.execute(
                text("SELECT tg_channel_id FROM channels WHERE id = :channel_id"),
                {"channel_id": channel_id}
            )
            row = result.fetchone()
            logger.info("tg_channel_id query result", 
                       channel_id=channel_id,
                       has_row=row is not None,
                       tg_channel_id=row[0] if row else None)
            
            # Context7: Безопасная проверка на None
            if not row or row[0] is None:
                logger.warning("Channel has no tg_channel_id, skipping cooldown check", 
                              channel_id=channel_id)
            else:
                tg_channel_id = row[0]
                # Context7: Проверка cooldown канала
                # Безопасное преобразование в int
                try:
                    tg_channel_id_int = int(tg_channel_id)
                    cooldown_result = await is_channel_in_cooldown(self.redis_client, tg_channel_id_int)
                    logger.info("Cooldown check result", 
                                channel_id=channel_id,
                                tg_channel_id=tg_channel_id,
                                in_cooldown=cooldown_result)
                    if cooldown_result:
                        logger.info("Channel in cooldown, skipping", 
                                   channel_id=channel_id, tg_channel_id=tg_channel_id)
                        self.stats['cooldown_skipped'] += 1
                        # Context7: Обновляем last_parsed_at даже при cooldown для отслеживания попыток
                        try:
                            logger.info("Updating last_parsed_at after cooldown skip", channel_id=channel_id)
                            await self._update_last_parsed_at(channel_id, 0)
                            logger.info("Successfully updated last_parsed_at after cooldown skip", channel_id=channel_id)
                        except Exception as e:
                            logger.error("Failed to update last_parsed_at after cooldown skip", 
                                        channel_id=channel_id, error=str(e), exc_info=True)
                        return {
                            'processed': 0,
                            'skipped': 0,
                            'max_date': None,
                            'cooldown_skipped': True
                        }
                except (ValueError, TypeError) as e:
                    logger.warning("Invalid tg_channel_id, skipping cooldown check", 
                                 channel_id=channel_id, tg_channel_id=tg_channel_id, error=str(e))
            
            # Context7: Проверка rate limiting
            if self.rate_limiter:
                rate_result = await check_parsing_rate_limit(
                    self.rate_limiter,
                    int(user_id),
                    int(channel_id)
                )
                
                if not rate_result.get('allowed', True):
                    logger.warning("Rate limit exceeded", 
                                 channel_id=channel_id,
                                 user_id=user_id,
                                 blocked_by=rate_result.get('blocked_by', []))
                    self.stats['rate_limited'] += 1
                    return {
                        'processed': 0,
                        'skipped': 0,
                        'max_date': None,
                        'rate_limited': True
                    }
            
            # Получение entity канала и tg_channel_id
            channel_result = await self._get_channel_entity(telegram_client, channel_id)
            if not channel_result:
                raise ValueError(f"Channel {channel_id} not found")
            
            channel_entity, tg_channel_id = channel_result
            
            # Context7 best practice: Получение данных канала для определения since_date
            result = await self.db_session.execute(
                text("SELECT id, last_parsed_at FROM channels WHERE id = :channel_id"),
                {"channel_id": channel_id}
            )
            channel_row = result.fetchone()
            channel_data = {
                'id': str(channel_row.id) if channel_row else channel_id,
                'last_parsed_at': channel_row.last_parsed_at if channel_row and channel_row.last_parsed_at else None
            }
            
            # Определение since_date на основе режима
            since_date = await self._get_since_date(channel_data, mode)
            
            logger.info(
                f"Starting to parse channel: channel_title={channel_entity.title}, mode={mode}, since_date={since_date.isoformat()}"
            )
            
            # Парсинг сообщений батчами
            messages_processed = 0
            batch_count = 0
            
            async for message_batch in self._get_message_batches(
                telegram_client, channel_entity, since_date, mode
            ):
                batch_count += 1
                
                # Обработка батча с передачей mode и channel_entity
                batch_result = await self._process_message_batch(
                    message_batch, channel_id, user_id, tenant_id, tg_channel_id, channel_entity, mode
                )
                
                messages_processed += batch_result['processed']
                self.stats['messages_parsed'] += batch_result['processed']
                self.stats['messages_skipped'] += batch_result['skipped']
                
                # Track max_message_date across all batches
                if batch_result.get('max_date') and (max_message_date is None or batch_result['max_date'] > max_message_date):
                    max_message_date = batch_result['max_date']
                
                # Задержка между батчами
                if batch_count < (1000 // self.config.max_messages_per_batch):
                    await asyncio.sleep(self.config.batch_delay_ms / 1000.0)
            
            # Обновление статистики канала
            await self._update_channel_stats(channel_id, messages_processed)
            
            # Context7 best practice: Обновление last_parsed_at после успешного парсинга
            # ВСЕГДА обновляем для отслеживания последней попытки парсинга
            await self._update_last_parsed_at(channel_id, messages_processed)
            
            processing_time = time.time() - start_time
            
            result = {
                'channel_id': channel_id,
                'messages_processed': messages_processed,
                'batch_count': batch_count,
                'processing_time_seconds': processing_time,
                'mode': mode,
                'since_date': since_date.isoformat(),
                'max_message_date': max_message_date.isoformat() if max_message_date else None,  # For HWM
                'stats': self.stats.copy()
            }
            
            logger.info("Channel parsing completed", **result)
            return result
            
        except Exception as e:
            self.stats['errors'] += 1
            logger.error("Channel parsing failed", 
                        channel_id=channel_id, mode=mode, error=str(e))
            raise
    
    async def _get_channel_entity(
        self, 
        client: TelegramClient, 
        channel_id: str
    ) -> Optional[tuple[Channel, int]]:
        """
        Получение entity канала и tg_channel_id.
        Context7 best practice: Автоматическое заполнение tg_channel_id при отсутствии.
        """
        try:
            # Получение информации о канале из БД
            result = await self.db_session.execute(
                text("SELECT tg_channel_id, username, title FROM channels WHERE id = :channel_id"),
                {"channel_id": channel_id}
            )
            channel_info = result.fetchone()
            
            if not channel_info:
                logger.error("Channel not found in database", channel_id=channel_id)
                return None
            
            tg_channel_id_db = channel_info.tg_channel_id
            username = channel_info.username
            title = channel_info.title
            
            # Context7 best practice: Получаем entity по username или tg_channel_id
            entity = None
            tg_channel_id = None
            
            if username:
                # Приоритет: username (более надёжный способ)
                try:
                    entity = await client.get_entity(username)
                    # Context7: Для каналов (Channel) ID всегда отрицательный при сохранении в БД
                    # entity.id может быть положительным для приватных каналов, используем utils.get_peer_id
                    from telethon import utils
                    from telethon.tl.types import PeerChannel
                    if hasattr(entity, 'id') and entity.id is not None:
                        # Для каналов создаём PeerChannel и получаем правильный ID
                        if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
                            tg_channel_id = utils.get_peer_id(PeerChannel(entity.id))
                        else:
                            tg_channel_id = entity.id
                    else:
                        logger.error("Entity has no valid ID", 
                                   channel_id=channel_id, username=username)
                        raise ValueError("Entity has no valid ID")
                except Exception as e:
                    logger.warning("Failed to get entity by username, trying tg_channel_id", 
                                 channel_id=channel_id, username=username, error=str(e))
                    # Context7: Безопасная проверка tg_channel_id_db на None
                    if tg_channel_id_db is not None:
                        try:
                            entity = await client.get_entity(int(tg_channel_id_db))
                            tg_channel_id = int(tg_channel_id_db)
                        except Exception as e2:
                            logger.error("Failed to get entity by tg_channel_id", 
                                       channel_id=channel_id, tg_channel_id=tg_channel_id_db, error=str(e2))
                            return None
                    else:
                        logger.error("No username and no tg_channel_id", channel_id=channel_id, title=title)
                        return None
            elif tg_channel_id_db is not None:
                # Fallback: используем tg_channel_id если username отсутствует
                try:
                    entity = await client.get_entity(int(tg_channel_id_db))
                    tg_channel_id = int(tg_channel_id_db)
                except Exception as e:
                    logger.error("Failed to get entity by tg_channel_id", 
                               channel_id=channel_id, tg_channel_id=tg_channel_id_db, error=str(e))
                    return None
            else:
                logger.error("Channel has neither username nor tg_channel_id", 
                           channel_id=channel_id, title=title)
                return None
            
            # Context7 best practice: Автоматическое заполнение tg_channel_id в БД, если отсутствует
            if entity and not tg_channel_id_db and tg_channel_id:
                try:
                    await self.db_session.execute(
                        text("UPDATE channels SET tg_channel_id = :tg_id WHERE id = :channel_id"),
                        {"tg_id": tg_channel_id, "channel_id": channel_id}
                    )
                    await self.db_session.commit()
                    logger.info("Auto-populated tg_channel_id", 
                              channel_id=channel_id, 
                              username=username,
                              tg_channel_id=tg_channel_id)
                except Exception as e:
                    logger.warning("Failed to update tg_channel_id in DB", 
                                 channel_id=channel_id, error=str(e))
                    try:
                        await self.db_session.rollback()
                    except Exception:
                        pass
            
            if not entity or not tg_channel_id:
                logger.error("Failed to resolve channel entity", 
                           channel_id=channel_id, username=username)
                return None
            
            return entity, tg_channel_id
            
        except Exception as e:
            logger.error("Failed to get channel entity", 
                       channel_id=channel_id, error=str(e))
            try:
                await self.db_session.rollback()
            except Exception:
                pass
            return None
    
    async def _get_since_date(
        self,
        channel: Dict[str, Any],
        mode: str
    ) -> datetime:
        """
        Context7 best practice: Определение since_date с учётом режима и HWM.
        
        Args:
            channel: данные канала с last_parsed_at
            mode: "historical" или "incremental"
        
        Returns:
            datetime с timezone UTC
        """
        now = datetime.now(timezone.utc)
        
        # Получение HWM из Redis (устойчивость к сбоям)
        hwm_key = f"parse_hwm:{channel['id']}"
        # Context7: get() - асинхронная функция в redis.asyncio
        hwm_raw = await self.redis_client.get(hwm_key)
        redis_hwm = ensure_dt_utc(hwm_raw) if hwm_raw else None
        
        if mode == "incremental":
            # Опора на last_parsed_at или Redis HWM
            base = channel.get('last_parsed_at') or redis_hwm
            if base:
                # Safeguard: если last_parsed_at слишком старый, форсим historical
                age_hours = (now - base).total_seconds() / 3600
                if age_hours > self.config.lpa_max_age_hours:
                    logger.warning(
                        f"last_parsed_at too old, forcing historical mode: channel_id={channel['id']}, age_hours={age_hours}"
                    )
                    return now - timedelta(hours=self.config.historical_hours)
                
                # Инкрементальный режим: с последнего парсинга, но не больше incremental_minutes
                return max(base, now - timedelta(minutes=self.config.incremental_minutes))
            else:
                # Fallback: если нет last_parsed_at, берём incremental окно
                return now - timedelta(minutes=self.config.incremental_minutes)
        
        elif mode == "historical":
            return now - timedelta(hours=self.config.historical_hours)
        
        else:
            raise ValueError(f"Unknown parser mode: {mode}")
    
    async def _get_message_batches(
        self,
        client: TelegramClient,
        channel_entity: Channel,
        since_date: datetime,
        mode: str = "historical"
    ):
        """Генератор батчей с временной фильтрацией."""
        batch_size = self.config.max_messages_per_batch
        batch = []
        messages_yielded = 0
        
        # Context7: Использование retry обвязки вместо прямого iter_messages
        try:
            # Получаем сообщения через retry обвязку
            messages = await fetch_messages_with_retry(
                client,
                channel_entity,
                limit=batch_size,
                redis_client=self.redis_client
            )
            
            # Обрабатываем полученные сообщения
            for message in messages:
                # Клиентская фильтрация (Telethon не умеет >= серверно)
                if message.date < since_date:
                    logger.debug(f"Reached since_date, stopping. message.date={message.date}, since_date={since_date}")
                    break
                
                batch.append(message)
                messages_yielded += 1
                
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
                    await asyncio.sleep(self.config.batch_delay_ms / 1000.0)
            
            # Возвращаем последний неполный батч
            if batch:
                yield batch
                
        except Exception as e:
            logger.error("Failed to fetch messages with retry", 
                        channel_id=channel_entity.id,
                        error=str(e))
            # Fallback к старому методу при ошибке
            async for message in client.iter_messages(
                channel_entity,
                offset_date=since_date  # ← Временной лимит
            ):
                # Клиентская фильтрация (Telethon не умеет >= серверно)
                if message.date < since_date:
                    logger.debug(f"Reached since_date, stopping. message.date={message.date}, since_date={since_date}")
                    break
                
                batch.append(message)
                messages_yielded += 1
                
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
                    await asyncio.sleep(self.config.batch_delay_ms / 1000.0)
            
            # Возвращаем последний неполный батч
            if batch:
                yield batch
        
        logger.info(f"Parsed {messages_yielded} messages since {since_date}")
    
    async def _process_message_batch(
        self,
        messages: List[Message],
        channel_id: str,
        user_id: str,
        tenant_id: str,
        tg_channel_id: int,
        channel_entity: Any = None,  # [C7-ID: dev-mode-012] Для получения title/username канала
        mode: str = "historical"
    ) -> Dict[str, int]:
        """Обработка батча сообщений с обновлением HWM."""
        processed = 0
        skipped = 0
        max_date = None
        
        # Подготовка данных для bulk insert
        posts_data = []
        events_data = []
        
        for message in messages:
            try:
                # Track max date in batch
                if message.date:
                    if max_date is None or message.date > max_date:
                        max_date = message.date
                
                # Проверка идемпотентности
                if await self._is_duplicate_message(message, channel_id, tenant_id):
                    skipped += 1
                    continue
                
                # Извлечение данных сообщения
                post_data = await self._extract_message_data(message, channel_id, tenant_id, tg_channel_id)
                # [C7-ID: dev-mode-015] Context7 best practice: генерация idempotency_key для идемпотентности
                # Формат: {tenant_id}:{channel_id}:{telegram_message_id}
                telegram_message_id = post_data.get('telegram_message_id')
                if not post_data.get('idempotency_key'):
                    post_data['idempotency_key'] = f"{tenant_id}:{channel_id}:{telegram_message_id}"
                
                posts_data.append(post_data)
                
                # Подготовка события
                event_data = await self._prepare_parsed_event(
                    post_data, user_id, channel_id, tenant_id
                )
                events_data.append(event_data)
                
                processed += 1
                
            except Exception as e:
                logger.error(f"Failed to process message {message.id}: {e}")
                skipped += 1
        
        # Context7: Атомарное сохранение в БД с новыми компонентами
        if posts_data:
            # Подготовка данных пользователя и канала
            # [C7-ID: dev-mode-012] tenant_id из параметра функции (передается в parse_channel_messages)
            user_data = {
                'telegram_id': user_id,
                'tenant_id': tenant_id,  # Используем переданный tenant_id
                'first_name': '',  # TODO: Получить из Telegram API
                'last_name': '',
                'username': ''
            }
            
            # [C7-ID: dev-mode-012] channel_data использует tg_channel_id (bigint) для UPSERT, а не channel_id (UUID)
            # tg_channel_id уже получен из _get_channel_entity
            # Получаем title и username из channel_entity или из БД
            channel_title = channel_entity.title if channel_entity and hasattr(channel_entity, 'title') else ''
            channel_username = channel_entity.username if channel_entity and hasattr(channel_entity, 'username') else ''
            
            # Если не получили из entity - берем из БД
            if not channel_title or not channel_username:
                try:
                    result = await self.db_session.execute(
                        text("SELECT title, username FROM channels WHERE id = :channel_id"),
                        {"channel_id": channel_id}
                    )
                    row = result.fetchone()
                    if row:
                        channel_title = row.title or channel_title
                        channel_username = row.username or channel_username
                except Exception as e:
                    logger.warning("Failed to get channel title/username from DB", error=str(e))
            
            channel_data = {
                'telegram_id': tg_channel_id,  # Используем tg_channel_id (bigint), не channel_id (UUID)
                'title': channel_title,
                'username': channel_username
            }
            
            # Атомарное сохранение
            success, error, inserted_count = await self.atomic_saver.save_batch_atomic(
                self.db_session,
                user_data,
                channel_data,
                posts_data
            )
            
            if success:
                logger.info("Atomic batch save successful", 
                          channel_id=channel_id,
                          inserted_count=inserted_count)
                
                # HWM ТОЛЬКО после успешного commit
                if processed > 0 and max_date:
                    hwm_key = f"parse_hwm:{channel_id}"
                    # Context7: set() - асинхронная функция в redis.asyncio
                    await self.redis_client.set(
                        hwm_key,
                        max_date.isoformat(),
                        ex=86400  # TTL 24 hours
                    )
                
                # Публикация событий только после успешного сохранения
                if events_data:
                    await self._publish_parsed_events(events_data)
            else:
                logger.error("Atomic batch save failed", 
                           channel_id=channel_id,
                           error=error)
                self.stats['errors'] += 1
        
        return {
            'processed': processed,
            'skipped': skipped,
            'max_date': max_date
        }
    
    async def _is_duplicate_message(
        self,
        message: Message,
        channel_id: str,
        tenant_id: str
    ) -> bool:
        """
        Context7 best practice: Проверка дубликатов через уникальный индекс.
        Использует комбинацию channel_id + telegram_message_id для идемпотентности.
        """
        # Context7: Глобальные каналы - проверка по channel_id + telegram_message_id
        cache_key = f"parsed:{channel_id}:{message.id}"
        
        # Проверка в Redis (быстрая проверка)
        # Context7: exists() - асинхронная функция в redis.asyncio
        if await self.redis_client.exists(cache_key):
            return True
        
        # Проверка в БД (Context7: используем существующий уникальный индекс)
        result = await self.db_session.execute(
            text("""
                SELECT 1 FROM posts 
                WHERE channel_id = :channel_id 
                  AND telegram_message_id = :message_id
            """),
            {"channel_id": channel_id, "message_id": message.id}
        )
        
        if result.fetchone():
            # Кеширование результата
            # Context7: setex() - асинхронная функция в redis.asyncio
            await self.redis_client.setex(cache_key, 3600, "1")  # TTL 1 час
            return True
        
        return False
    
    async def _extract_message_data(
        self,
        message: Message,
        channel_id: str,
        tenant_id: str,
        tg_channel_id: int
    ) -> Dict[str, Any]:
        """Извлечение данных из сообщения."""
        # Извлечение текста
        text = message.text or ""
        
        # Извлечение URL
        urls = self._extract_urls(text)
        
        # Context7 best practice: безопасная генерация ID на клиенте
        post_id = str(uuid.uuid4())
        
        # Определение времени публикации
        posted_at = message.date if hasattr(message, 'date') and message.date else datetime.now(timezone.utc)
        
        # Context7: Использование yyyymm для партиционирования
        yyyymm = int(posted_at.strftime('%Y%m'))
        
        # Context7: Экстракция Telegram-специфичных метрик
        views_count = getattr(message, 'views', 0) or 0
        forwards_count = getattr(message, 'forwards', 0) or 0
        reactions_count = 0  # Будет заполнено отдельной таблицей
        replies_count = 0    # Будет заполнено отдельной таблицей
        
        # Context7: Генерация telegram_post_url
        telegram_post_url = f"https://t.me/c/{abs(tg_channel_id)}/{message.id}" if hasattr(message, 'id') else None
        
        return {
            'id': post_id,
            'channel_id': channel_id,  # Context7: глобальные каналы без tenant_id
            'telegram_message_id': message.id if hasattr(message, 'id') else int(time.time() * 1000),
            'content': text,
            'media_urls': json.dumps(urls) if urls else '[]',  # [C7-ID: dev-mode-014] Context7: JSONB формат (строка JSON, не список)
            'posted_at': posted_at,
            'created_at': datetime.now(timezone.utc),
            'is_processed': False,
            'has_media': bool(message.media if hasattr(message, 'media') else False),
            'yyyymm': yyyymm,
            'views_count': views_count,
            'forwards_count': forwards_count,
            'reactions_count': reactions_count,
            'replies_count': replies_count,
            'telegram_post_url': telegram_post_url
        }
    
    def _extract_urls(self, text: str) -> List[str]:
        """Извлечение URL из текста."""
        import re
        
        # Простой regex для URL
        url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        urls = re.findall(url_pattern, text)
        
        # Фильтрация и нормализация URL
        normalized_urls = []
        for url in urls:
            # Удаление трекинг-параметров
            clean_url = self._clean_url(url)
            if clean_url and len(clean_url) <= 2048:  # Ограничение длины
                normalized_urls.append(clean_url)
        
        return list(set(normalized_urls))  # Дедупликация
    
    def _clean_url(self, url: str) -> str:
        """Очистка URL от трекинг-параметров."""
        import urllib.parse
        
        try:
            parsed = urllib.parse.urlparse(url)
            
            # Удаление трекинг-параметров
            tracking_params = {
                'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
                'fbclid', 'gclid', 'ref', 'source', 'campaign'
            }
            
            query_params = urllib.parse.parse_qs(parsed.query)
            clean_params = {
                k: v for k, v in query_params.items() 
                if k.lower() not in tracking_params
            }
            
            clean_query = urllib.parse.urlencode(clean_params, doseq=True)
            
            return urllib.parse.urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, clean_query, parsed.fragment
            ))
        except Exception:
            return url
    
    def _create_content_hash(self, text: str) -> str:
        """Создание хеша контента для дедупликации."""
        # Нормализация текста
        normalized = text.lower().strip()
        normalized = ' '.join(normalized.split())  # Удаление лишних пробелов
        
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    
    async def _prepare_parsed_event(
        self,
        post_data: Dict[str, Any],
        user_id: str,
        channel_id: str,
        tenant_id: str
    ) -> Dict[str, Any]:  # PostParsedEventV1 - temporarily disabled
        """Подготовка события post.parsed (строгая схема v1, ISO8601)."""
        # Приведение posted_at к ISO и нормализация полей согласно PostParsedEventV1
        posted_at = post_data['posted_at']
        if hasattr(posted_at, 'isoformat'):
            posted_at_iso = posted_at.isoformat()
        else:
            posted_at_iso = str(posted_at)

        # [C7-ID: dev-mode-015] Context7 best practice: безопасный доступ к idempotency_key с fallback
        # Если idempotency_key отсутствует, генерируем его (должен быть уже сгенерирован в _extract_message_data)
        idempotency_key = post_data.get('idempotency_key')
        if not idempotency_key:
            telegram_message_id = post_data.get('telegram_message_id')
            idempotency_key = f"{tenant_id}:{channel_id}:{telegram_message_id}"
            logger.warning("idempotency_key missing, generated fallback", 
                         tenant_id=tenant_id, 
                         channel_id=channel_id, 
                         telegram_message_id=telegram_message_id)

        # PostParsedEventV1 temporarily disabled - returning dict instead
        return dict(
            idempotency_key=idempotency_key,
            user_id=user_id,
            channel_id=channel_id,
            post_id=post_data['id'],
            tenant_id=tenant_id,
            text=post_data['content'] or "",
            urls=post_data.get('urls', []),
            posted_at=posted_at_iso,
            content_hash=post_data.get('content_hash'),
            link_count=len(post_data.get('urls', [])),
            tg_message_id=post_data.get('tg_message_id') or post_data.get('telegram_message_id'),
            telegram_message_id=post_data.get('telegram_message_id') or post_data.get('tg_message_id'),
            tg_channel_id=post_data.get('tg_channel_id') or 0,
            telegram_post_url=post_data.get('telegram_post_url'),
            has_media=post_data.get('has_media', False),
            is_edited=post_data.get('is_edited', False),
            views_count=post_data.get('views_count', 0),
            forwards_count=post_data.get('forwards_count', 0),
            reactions_count=post_data.get('reactions_count', 0)
        )
    
    async def _bulk_insert_posts(self, posts_data: List[Dict[str, Any]]):
        """
        Context7: Делегирование к AtomicDBSaver для совместимости.
        Этот метод теперь deprecated, используется atomic_saver.save_batch_atomic.
        """
        if not posts_data:
            return
            
        logger.warning("_bulk_insert_posts is deprecated, use atomic_saver.save_batch_atomic instead")
        
        # Fallback к старому методу если atomic_saver недоступен
        if not self.atomic_saver:
            await self._legacy_bulk_insert_posts(posts_data)
            return
        
        # Подготовка данных для AtomicDBSaver
        user_data = {
            'telegram_id': posts_data[0].get('user_id', 0),
            'first_name': '',
            'last_name': '',
            'username': ''
        }
        
        channel_data = {
            'telegram_id': posts_data[0].get('channel_id', 0),
            'title': '',
            'username': '',
            'description': '',
            'participants_count': 0,
            'is_broadcast': False,
            'is_megagroup': False
        }
        
        # Используем AtomicDBSaver
        success, error, inserted_count = await self.atomic_saver.save_batch_atomic(
            self.db_session,
            user_data,
            channel_data,
            posts_data
        )
        
        if not success:
            logger.error("Atomic bulk insert failed", error=error)
            raise Exception(f"Atomic bulk insert failed: {error}")
    
    async def _legacy_bulk_insert_posts(self, posts_data: List[Dict[str, Any]]):
        """Legacy bulk insert для совместимости."""
        # Context7: Используем только существующие колонки
        required_columns = [
            'id', 'channel_id', 'telegram_message_id', 'content', 'media_urls',
            'posted_at', 'created_at', 'is_processed', 'has_media', 'yyyymm',
            'views_count', 'forwards_count', 'reactions_count', 'replies_count',
            'telegram_post_url'
        ]
        
        # Фильтруем только существующие поля
        filtered_data = [
            {k: v for k, v in post.items() if k in required_columns}
            for post in posts_data
        ]
        
        # Подготовка SQL запроса
        columns = list(filtered_data[0].keys())
        placeholders = ', '.join([f':{col}' for col in columns])
        
        # Context7: Идемпотентность через уникальный индекс ux_posts_chan_msg
        sql = f"""
        INSERT INTO posts ({', '.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT (channel_id, telegram_message_id) 
        DO UPDATE SET
            content = EXCLUDED.content,
            media_urls = EXCLUDED.media_urls,
            views_count = EXCLUDED.views_count,
            forwards_count = EXCLUDED.forwards_count,
            updated_at = NOW()
        """
        
        try:
            await self.db_session.execute(text(sql), filtered_data)
            await self.db_session.commit()
            
            logger.info(f"Legacy bulk inserted {len(posts_data)} posts")
            
        except Exception as e:
            await self.db_session.rollback()
            logger.error(f"Legacy bulk insert failed: {e}")
            raise
    
    async def _publish_parsed_events(self, events_data: List[Dict[str, Any]]):  # PostParsedEventV1 - temporarily disabled
        """Публикация событий post.parsed."""
        try:
            if not events_data:
                return
                
            # Context7: Если event_publisher=None, публикуем напрямую в Redis Streams
            if self.event_publisher is None:
                stream_key = "posts.parsed"
                for event in events_data:
                    # Context7: Публикация в Redis Streams через xadd()
                    # Конвертируем значения в строки для Redis
                    event_payload = {}
                    for key, value in event.items():
                        if value is None:
                            continue
                        elif isinstance(value, (dict, list)):
                            import json
                            event_payload[key] = json.dumps(value, ensure_ascii=False)
                        elif isinstance(value, datetime):
                            event_payload[key] = value.isoformat()
                        else:
                            event_payload[key] = str(value)
                    await self.redis_client.xadd(stream_key, event_payload, maxlen=10000)  # Context7: ограничиваем размер stream
                logger.info(f"Published {len(events_data)} post.parsed events to Redis Streams")
            else:
                # Используем event_publisher, если он доступен
                for event in events_data:
                    await self.event_publisher.publish_event('posts.parsed', event)
                logger.info(f"Published {len(events_data)} post.parsed events via event_publisher")
            
        except Exception as e:
            logger.error(f"Failed to publish events: {e}", exc_info=True)
            # Context7: не падаем, только логируем ошибку
    
    async def _update_last_parsed_at(self, channel_id: str, parsed_count: int):
        """
        Context7 best practice: Обновление last_parsed_at после парсинга.
        Обновляем ВСЕГДА для отслеживания последней попытки парсинга.
        
        Supabase best practice: Используем параметризованные запросы для безопасности.
        """
        
        try:
            now = datetime.now(timezone.utc)
            
            # Context7: Supabase best practice - параметризованные запросы, атомарное обновление
            result = await self.db_session.execute(
                text("UPDATE channels SET last_parsed_at = :now WHERE id = :channel_id"),
                {"now": now, "channel_id": channel_id}
            )
            
            # Context7: Проверяем, что обновление произошло
            rows_affected = result.rowcount
            if rows_affected == 0:
                logger.warning("No rows updated for last_parsed_at", 
                             channel_id=channel_id,
                             parsed_count=parsed_count)
            
            await self.db_session.commit()
            
            # Удаление HWM после успешного обновления
            hwm_key = f"parse_hwm:{channel_id}"
            # Context7: delete() - асинхронная функция в redis.asyncio
            await self.redis_client.delete(hwm_key)
            
            # Context7: stdlib logging syntax для совместимости
            logger.info("Updated last_parsed_at", 
                       channel_id=channel_id, 
                       timestamp=now.isoformat(), 
                       parsed_count=parsed_count,
                       rows_affected=rows_affected)
            
        except Exception as e:
            # Context7: stdlib logging syntax для совместимости, полная информация об ошибке
            logger.error("Failed to update last_parsed_at", 
                        channel_id=channel_id, 
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True)
            # Context7: Откатываем транзакцию при ошибке
            try:
                await self.db_session.rollback()
            except Exception as rollback_error:
                logger.error("Failed to rollback after last_parsed_at update error",
                            channel_id=channel_id,
                            error=str(rollback_error))
            raise
    
    async def _update_channel_stats(self, channel_id: str, messages_count: int):
        """Обновление статистики канала."""
        try:
            await self.db_session.execute(
                text("""
                    UPDATE channels 
                    SET last_message_at = NOW()
                    WHERE id = :channel_id
                """),
                {"channel_id": channel_id}
            )
            await self.db_session.commit()
            
        except Exception as e:
            logger.error(f"Failed to update channel stats: {e}")
    
    async def handle_flood_wait(self, error: errors.FloodWaitError):
        """Обработка FloodWait ошибки."""
        wait_time = min(error.seconds, self.config.max_flood_wait)
        
        logger.warning(f"FloodWait: waiting {wait_time} seconds")
        
        # Экспоненциальный backoff с джиттером
        base_delay = min(wait_time, 2 ** min(wait_time // 10, 6))
        jitter = 0.1 + (0.2 * (wait_time / self.config.max_flood_wait))
        actual_delay = base_delay + jitter
        
        await asyncio.sleep(actual_delay)
        
        self.stats['flood_wait_count'] += 1
    
    async def close(self):
        """Закрытие соединений."""
        # Context7: aclose() - асинхронная функция в redis.asyncio
        await self.redis_client.aclose()
        logger.info("Channel parser closed")

# ============================================================================
# FACTORY FUNCTION
# ============================================================================

async def create_channel_parser(
    db_session: AsyncSession,
    event_publisher: Any,  # EventPublisher - temporarily disabled
    config: Optional[ParserConfig] = None
) -> ChannelParser:
    """Создание парсера каналов."""
    if config is None:
        config = ParserConfig()
    
    return ChannelParser(config, db_session, event_publisher)

# ============================================================================
# EXAMPLE USAGE
# ============================================================================

async def example_parsing():
    """Пример использования парсера."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    # from worker.event_bus import create_publisher  # Temporarily disabled
    
    # Создание сессии БД
    engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/db")
    async with AsyncSession(engine) as session:
        # Создание event publisher
        # publisher = await create_publisher()  # Temporarily disabled
        publisher = None
        
        # Создание парсера
        parser = await create_channel_parser(session, publisher)
        
        # Создание Telethon клиента
        client = TelegramClient('session', api_id, api_hash)
        await client.start()
        
        try:
            # Парсинг канала
            result = await parser.parse_channel_messages(
                channel_id="channel-uuid",
                user_id="user-uuid", 
                tenant_id="tenant-uuid",
                telegram_client=client,
                limit=100
            )
            
            print(f"Parsing result: {result}")
            
        finally:
            await parser.close()
            await client.disconnect()

if __name__ == "__main__":
    asyncio.run(example_parsing())
