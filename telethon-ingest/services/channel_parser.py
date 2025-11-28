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
from .discussion_extractor import (
    get_discussion_message,
    extract_reply_chain,
    check_channel_has_comments
)
from utils.time_utils import ensure_dt_utc
from prometheus_client import Counter

# WORKER IMPORT DISABLED - will be restored when worker module is available
# from worker.event_bus import EventPublisher, PostParsedEvent
# from worker.events.schemas.posts_parsed_v1 import PostParsedEventV1

logger = structlog.get_logger()

# Context7: Метрики для отслеживания проблем с парсингом
# Context7: Используем проверку на существование метрики для предотвращения дублирования
from prometheus_client import REGISTRY

def _get_or_create_counter(name, description, labels):
    """Получить существующую метрику или создать новую."""
    try:
        # Пытаемся получить существующую метрику
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError):
        pass
    
    # Создаём новую метрику
    return Counter(name, description, labels)

channel_not_found_total = _get_or_create_counter(
    'channel_not_found_total',
    'Total channel not found errors',
    ['exists_in_db']  # exists_in_db: 'true' или 'false'
)

album_save_failures_total = _get_or_create_counter(
    'album_save_failures_total',
    'Total album save failures',
    ['error_type']
)

session_rollback_failures_total = _get_or_create_counter(
    'session_rollback_failures_total',
    'Total session rollback failures',
    ['operation']  # operation: 'before_parsing', 'before_entity', 'before_albums'
)

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
    max_concurrency: int = int(os.getenv("PARSER_MAX_CONCURRENCY", "8"))  # Увеличено с 4 до 8 для лучшей параллельности
    retry_max: int = int(os.getenv("PARSER_RETRY_MAX", "3"))
    
    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379")
    
    # База данных
    db_url: str = os.getenv("DATABASE_URL") or ""  # Обязательное поле, проверяется в __post_init__
    
    def __post_init__(self):
        """Валидация обязательных полей."""
        if not self.db_url:
            raise ValueError("DATABASE_URL must be set in environment variables")
    
    # Адаптивные пороги и статистика
    stats_window_days: int = int(os.getenv("PARSER_STATS_WINDOW_DAYS", "14"))
    adaptive_thresholds_enabled: bool = os.getenv("FEATURE_ADAPTIVE_THRESHOLDS_ENABLED", "false").lower() == "true"

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
        telegram_client_manager: Optional[Any] = None,
        media_processor: Optional[Any] = None  # MediaProcessor для обработки медиа
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
        self.media_processor = media_processor  # MediaProcessor для обработки медиа
        
        # Статистика
        self.stats = {
            'messages_parsed': 0,
            'messages_skipped': 0,
            'flood_wait_count': 0,
            'errors': 0,
            'rate_limited': 0,
            'cooldown_skipped': 0
        }
        
        logger.info("Channel parser initialized with Context7 components", 
                   has_media_processor=media_processor is not None)
    
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
        
        # Context7: Проверяем и откатываем активную транзакцию перед началом парсинга
        # Это предотвращает ошибки "A transaction is already begun on this Session"
        try:
            if self.db_session.in_transaction():
                await self.db_session.rollback()
                logger.debug("Rolled back active transaction before parsing",
                           channel_id=channel_id)
        except Exception as e:
            logger.warning("Failed to rollback transaction before parsing",
                         channel_id=channel_id, error=str(e), error_type=type(e).__name__)
            # Context7: Метрика для отслеживания проблем с rollback
            session_rollback_failures_total.labels(operation='before_parsing').inc()
        
        # Context7: Сброс статистики для каждого канала
        self.stats = {
            'messages_parsed': 0,
            'messages_skipped': 0,
            'flood_wait_count': 0,
            'errors': 0,
            'rate_limited': 0,
            'cooldown_skipped': 0
        }
        
        logger.info("parse_channel_messages started", 
                   channel_id=channel_id,
                   mode=mode,
                   user_id=user_id)
        
        # Context7: Проверка и очистка незакрытых транзакций перед началом парсинга
        try:
            if self.db_session.in_transaction():
                logger.warning("Found open transaction before parsing, rolling back", channel_id=channel_id)
                await self.db_session.rollback()
        except Exception as e:
            logger.warning("Failed to check/rollback session state before parsing", 
                         channel_id=channel_id, error=str(e))
        
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
            
            try:
                telegram_id_int = int(user_id)
            except (TypeError, ValueError):
                logger.error(
                    "Invalid telegram_id type for client acquisition",
                    user_id=user_id
                )
                self.stats['errors'] += 1
                return {
                    'processed': 0,
                    'skipped': 0,
                    'max_date': None,
                    'error': 'invalid_telegram_id'
                }

            telegram_client = await self.telegram_client_manager.get_client(telegram_id_int)
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
            # Context7: Используем retry логику для SELECT запросов, чтобы избежать проблем с транзакциями
            async def _execute_tg_channel_id_query(max_retries: int = 3) -> Optional[Any]:
                """Выполнение запроса tg_channel_id с retry логикой."""
                for attempt in range(max_retries):
                    try:
                        # Context7: Перед каждым запросом проверяем состояние сессии
                        if self.db_session.in_transaction():
                            await self.db_session.rollback()
                            logger.debug("Rolled back transaction before tg_channel_id query",
                                       channel_id=channel_id, attempt=attempt + 1)
                        
                        result = await self.db_session.execute(
                            text("SELECT tg_channel_id FROM channels WHERE id = :channel_id"),
                            {"channel_id": channel_id}
                        )
                        return result.fetchone()
                    except Exception as e:
                        error_str = str(e).lower()
                        is_transaction_error = (
                            "invalid transaction" in error_str or
                            "rollback" in error_str or
                            "transaction" in error_str
                        )
                        
                        if is_transaction_error and attempt < max_retries - 1:
                            logger.warning("Transaction error in tg_channel_id query, retrying",
                                         channel_id=channel_id,
                                         attempt=attempt + 1,
                                         max_retries=max_retries,
                                         error=str(e))
                            try:
                                await self.db_session.rollback()
                            except Exception:
                                pass
                            await asyncio.sleep(0.1 * (attempt + 1))
                            continue
                        else:
                            raise
                return None
            
            try:
                row = await _execute_tg_channel_id_query()
            except Exception as e:
                logger.error("Failed to fetch tg_channel_id after retries",
                           channel_id=channel_id, error=str(e))
                raise
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
            
            # Context7: Проверка rate limiting (используем tg_channel_id из БД)
            if self.rate_limiter and tg_channel_id:
                rate_result = await check_parsing_rate_limit(
                    self.rate_limiter,
                    int(user_id),
                    int(tg_channel_id)
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
            
            # Context7: Проверяем и откатываем активную транзакцию перед получением entity
            # Это предотвращает ошибки "A transaction is already begun on this Session"
            # Context7 best practice: Явная проверка и очистка состояния сессии
            try:
                if self.db_session.in_transaction():
                    await self.db_session.rollback()
                    logger.debug("Rolled back active transaction before getting channel entity",
                               channel_id=channel_id)
                    # Context7: Проверяем, что rollback прошел успешно
                    if self.db_session.in_transaction():
                        logger.error("Transaction still active after rollback - session may be in bad state",
                                   channel_id=channel_id)
                        # Context7: Метрика для отслеживания проблем с состоянием сессии
                        session_rollback_failures_total.labels(operation='before_entity').inc()
            except Exception as e:
                logger.warning("Failed to rollback transaction before getting channel entity",
                             channel_id=channel_id, error=str(e), error_type=type(e).__name__)
                # Context7: Метрика для отслеживания проблем с rollback
                session_rollback_failures_total.labels(operation='before_entity').inc()
            
            # Получение entity канала и tg_channel_id
            # Context7: Улучшенная обработка ошибок для каналов без tg_channel_id или с проблемами доступа
            channel_result = await self._get_channel_entity(telegram_client, channel_id)
            if not channel_result:
                # Context7: Логируем детальную информацию для диагностики
                # Получаем информацию о канале из БД для лучшей диагностики
                try:
                    channel_info_result = await self.db_session.execute(
                        text("SELECT title, username, tg_channel_id FROM channels WHERE id = :channel_id"),
                        {"channel_id": channel_id}
                    )
                    channel_info = channel_info_result.fetchone()
                    channel_title = channel_info.title if channel_info else None
                    channel_username = channel_info.username if channel_info else None
                    channel_tg_id = channel_info.tg_channel_id if channel_info else None
                except Exception as e:
                    logger.warning("Failed to get channel info for error logging", 
                                 channel_id=channel_id, error=str(e))
                    channel_title = None
                    channel_username = None
                    channel_tg_id = None
                
                logger.error(
                    "Failed to get channel entity - channel may be missing tg_channel_id or inaccessible",
                    channel_id=channel_id,
                    channel_title=channel_title,
                    channel_username=channel_username,
                    channel_tg_channel_id=channel_tg_id,
                    user_id=user_id,
                    mode=mode,
                    has_username=channel_username is not None,
                    has_tg_channel_id=channel_tg_id is not None
                )
                # Context7: Возвращаем результат с ошибкой вместо исключения для graceful degradation
                self.stats['errors'] += 1
                return {
                    'status': 'error',
                    'error': 'channel_not_found',
                    'processed': 0,
                    'skipped': 0,
                    'max_date': None,
                    'messages_processed': 0
                }
            
            channel_entity, tg_channel_id = channel_result
            
            # Context7 best practice: Получение данных канала для определения since_date
            # Context7: Используем retry логику для обработки проблем с транзакциями
            async def _execute_channel_data_query(max_retries: int = 3) -> Optional[Any]:
                """Выполнение запроса данных канала с retry логикой."""
                for attempt in range(max_retries):
                    try:
                        # Context7: Перед каждым запросом проверяем состояние сессии
                        if self.db_session.in_transaction():
                            await self.db_session.rollback()
                            logger.debug("Rolled back transaction before channel data query",
                                       channel_id=channel_id, attempt=attempt + 1)
                        
                        result = await self.db_session.execute(
                            text("SELECT id, last_parsed_at FROM channels WHERE id = :channel_id"),
                            {"channel_id": channel_id}
                        )
                        return result.fetchone()
                    except Exception as e:
                        error_str = str(e).lower()
                        is_transaction_error = (
                            "invalid transaction" in error_str or
                            "rollback" in error_str or
                            "transaction" in error_str
                        )
                        
                        if is_transaction_error and attempt < max_retries - 1:
                            logger.warning("Transaction error in channel data query, retrying",
                                         channel_id=channel_id,
                                         attempt=attempt + 1,
                                         max_retries=max_retries,
                                         error=str(e))
                            try:
                                await self.db_session.rollback()
                            except Exception:
                                pass
                            await asyncio.sleep(0.1 * (attempt + 1))
                            continue
                        else:
                            raise
                return None
            
            try:
                channel_row = await _execute_channel_data_query()
            except Exception as e:
                logger.error("Failed to fetch channel data after retries",
                           channel_id=channel_id, error=str(e))
                raise
            channel_data = {
                'id': str(channel_row.id) if channel_row else channel_id,
                'last_parsed_at': channel_row.last_parsed_at if channel_row and channel_row.last_parsed_at else None
            }
            
            # Определение since_date на основе режима
            since_date = await self._get_since_date(channel_data, mode)
            
            logger.info(
                "Starting to parse channel",
                channel_id=channel_id,
                channel_title=channel_entity.title,
                mode=mode,
                since_date=since_date.isoformat(),
                last_parsed_at=channel_data.get('last_parsed_at'),
                is_new_channel=channel_data.get('last_parsed_at') is None
            )
            
            # Парсинг сообщений батчами
            messages_processed = 0
            batch_count = 0
            has_successful_save = False  # Context7: Отслеживаем успешное сохранение хотя бы одного батча
            
            async for message_batch in self._get_message_batches(
                telegram_client, channel_entity, since_date, mode
            ):
                batch_count += 1
                
                # Обработка батча с передачей mode, channel_entity и telegram_client
                batch_result = await self._process_message_batch(
                    message_batch, channel_id, user_id, tenant_id, tg_channel_id, channel_entity, mode, telegram_client
                )
                
                # Context7: Безопасная обработка результата батча с проверкой наличия ключей
                if not isinstance(batch_result, dict):
                    logger.error("Unexpected batch_result type", 
                               channel_id=channel_id,
                               batch_result_type=type(batch_result),
                               batch_result=str(batch_result)[:200])
                    continue
                
                # Context7: Безопасное извлечение значений с fallback на 0
                batch_processed = batch_result.get('processed', 0)
                batch_skipped = batch_result.get('skipped', 0)
                
                messages_processed += batch_processed
                self.stats['messages_parsed'] += batch_processed
                self.stats['messages_skipped'] += batch_skipped
                
                # Context7: Отслеживаем успешное сохранение - если processed > 0, значит сохранение прошло успешно
                # (в _process_message_batch processed увеличивается только после успешного save_batch_atomic)
                if batch_processed > 0:
                    has_successful_save = True
                
                # Track max_message_date across all batches
                if batch_result.get('max_date') and (max_message_date is None or batch_result['max_date'] > max_message_date):
                    max_message_date = batch_result['max_date']
                
                # Задержка между батчами
                if batch_count < (1000 // self.config.max_messages_per_batch):
                    await asyncio.sleep(self.config.batch_delay_ms / 1000.0)
            
            # Обновление статистики канала
            await self._update_channel_stats(channel_id, messages_processed)
            
            # Context7 best practice: Обновление last_parsed_at ТОЛЬКО после успешного сохранения постов
            # КРИТИЧНО: Не обновляем last_parsed_at если все батчи завершились с ошибкой сохранения
            # Это предотвращает пропуск постов при следующем парсинге
            if has_successful_save or messages_processed == 0:
                # Обновляем если:
                # 1. Был хотя бы один успешный save (has_successful_save = True), ИЛИ
                # 2. Не было постов для сохранения (messages_processed = 0) - это нормальная ситуация
                await self._update_last_parsed_at(channel_id, messages_processed)
            else:
                # Context7: Все батчи завершились с ошибкой - НЕ обновляем last_parsed_at
                # Это гарантирует, что при следующем парсинге мы попытаемся сохранить те же посты
                logger.warning(
                    "Skipping last_parsed_at update - all batches failed to save",
                    channel_id=channel_id,
                    messages_processed=messages_processed,
                    batch_count=batch_count
                )
            
            # Context7: [C7-ID: monitoring-missing-posts-002] Мониторинг пропусков постов
            # Сравниваем last_parsed_at с реальным временем последнего поста
            await self._monitor_missing_posts(channel_id)
            
            # Context7: [C7-ID: adaptive-thresholds-003] Асинхронное обновление статистики интервалов
            # Не блокируем основной поток, запускаем в фоне
            if self.config.adaptive_thresholds_enabled and messages_processed > 0:
                # Инвалидируем кеш статистики для пересчета при следующем запросе
                # Это безопаснее, чем пересчитывать сразу, так как не блокирует парсинг
                cache_key = f"interarrival_stats:{channel_id}"
                try:
                    await self.redis_client.delete(cache_key)
                    logger.debug("Invalidated interarrival stats cache after parsing",
                               channel_id=channel_id,
                               messages_processed=messages_processed)
                except Exception as e:
                    logger.warning("Failed to invalidate stats cache",
                                 channel_id=channel_id,
                                 error=str(e))
            
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
            # Context7: Проверка и очистка транзакции перед запросом
            # Context7 best practice: Явно проверяем и очищаем состояние сессии
            try:
                if self.db_session.in_transaction():
                    await self.db_session.rollback()
                    logger.debug("Rolled back active transaction before _get_channel_entity",
                               channel_id=channel_id)
            except Exception as rollback_error:
                logger.warning("Failed to rollback transaction before _get_channel_entity",
                             channel_id=channel_id, error=str(rollback_error))
            
            # Context7: Функция для выполнения запроса с retry логикой
            async def _execute_channel_query(max_retries: int = 3) -> Optional[Any]:
                """Выполнение запроса с retry логикой для обработки проблем с транзакциями."""
                for attempt in range(max_retries):
                    try:
                        # Context7: Перед каждым запросом проверяем состояние сессии
                        if self.db_session.in_transaction():
                            await self.db_session.rollback()
                            logger.debug("Rolled back transaction before query attempt",
                                       channel_id=channel_id, attempt=attempt + 1)
                        
                        result = await self.db_session.execute(
                            text("SELECT tg_channel_id, username, title FROM channels WHERE id = :channel_id"),
                            {"channel_id": channel_id}
                        )
                        channel_info = result.fetchone()
                        return channel_info
                    except Exception as e:
                        error_str = str(e).lower()
                        is_transaction_error = (
                            "invalid transaction" in error_str or
                            "rollback" in error_str or
                            "transaction" in error_str
                        )
                        
                        if is_transaction_error and attempt < max_retries - 1:
                            logger.warning("Transaction error in channel query, retrying",
                                         channel_id=channel_id,
                                         attempt=attempt + 1,
                                         max_retries=max_retries,
                                         error=str(e),
                                         error_type=type(e).__name__)
                            try:
                                await self.db_session.rollback()
                            except Exception:
                                pass
                            # Небольшая задержка перед retry
                            await asyncio.sleep(0.1 * (attempt + 1))
                            continue
                        else:
                            logger.error("Failed to execute channel query",
                                       channel_id=channel_id,
                                       attempt=attempt + 1,
                                       error=str(e),
                                       error_type=type(e).__name__)
                            raise
                return None
            
            # Получение информации о канале из БД
            # Context7: Добавляем детальное логирование для диагностики
            logger.debug("Fetching channel info from DB", channel_id=channel_id)
            channel_info = await _execute_channel_query()
            
            if channel_info:
                logger.debug("Channel info query result", 
                           channel_id=channel_id,
                           has_result=True,
                           tg_channel_id=channel_info.tg_channel_id,
                           username=channel_info.username)
            else:
                logger.warning("Channel info query returned None", channel_id=channel_id)
            
            if not channel_info:
                # Context7: Дополнительная диагностика - проверяем, существует ли канал в БД
                logger.error("Channel not found in database", 
                           channel_id=channel_id,
                           query_executed=True)
                # Context7: Пытаемся проверить, существует ли канал через прямой запрос с retry
                try:
                    # Context7: Проверяем состояние сессии перед диагностическим запросом
                    if self.db_session.in_transaction():
                        await self.db_session.rollback()
                    
                    diagnostic_result = await self.db_session.execute(
                        text("SELECT COUNT(*) as count FROM channels WHERE id = :channel_id"),
                        {"channel_id": channel_id}
                    )
                    diagnostic_row = diagnostic_result.fetchone()
                    count = diagnostic_row.count if diagnostic_row else 0
                    logger.error("Channel diagnostic query", 
                               channel_id=channel_id,
                               exists_in_db=count > 0,
                               count=count)
                    
                    # Context7: Если канал существует, но не был найден - это проблема с сессией
                    if count > 0:
                        logger.error("Channel exists in DB but query returned None - possible session state issue",
                                   channel_id=channel_id)
                        # Context7: Метрика для отслеживания проблем с сессией
                        channel_not_found_total.labels(exists_in_db='true').inc()
                    else:
                        channel_not_found_total.labels(exists_in_db='false').inc()
                except Exception as diag_error:
                    logger.error("Failed to run diagnostic query", 
                               channel_id=channel_id, error=str(diag_error))
                return None
            
            tg_channel_id_db = channel_info.tg_channel_id
            username = channel_info.username
            title = channel_info.title
            
            # Context7 best practice: Получаем entity по username или tg_channel_id
            entity = None
            tg_channel_id = None
            
            if username:
                # Context7: Нормализация username - убираем @ из начала для корректного поиска
                clean_username = username.lstrip('@')
                # Приоритет: username (более надёжный способ)
                try:
                    entity = await client.get_entity(clean_username)
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
                        # Context7: Для каналов без username и tg_channel_id логируем предупреждение
                        logger.warning(
                            "Channel has no username and no tg_channel_id, cannot resolve entity",
                            channel_id=channel_id,
                            title=title,
                            username=username
                        )
                        # Context7: Метрика для отслеживания каналов без tg_channel_id
                        channel_not_found_total.labels(exists_in_db='true').inc()
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
                # Context7: Для каналов без username и tg_channel_id логируем предупреждение
                # Это может произойти для каналов, которые были созданы без полной информации
                logger.warning(
                    "Channel has neither username nor tg_channel_id, cannot resolve entity",
                    channel_id=channel_id,
                    title=title
                )
                # Context7: Метрика для отслеживания каналов без tg_channel_id
                channel_not_found_total.labels(exists_in_db='true').inc()
                return None
            
            # Context7 best practice: Автоматическое заполнение tg_channel_id в БД, если отсутствует
            if entity and not tg_channel_id_db and tg_channel_id:
                try:
                    # Context7: Проверяем и откатываем активную транзакцию перед началом новой
                    if self.db_session.in_transaction():
                        await self.db_session.rollback()
                    
                    # Context7: Используем транзакцию через async with для безопасной обработки ошибок
                    async with self.db_session.begin():
                        await self.db_session.execute(
                            text("UPDATE channels SET tg_channel_id = :tg_id WHERE id = :channel_id"),
                            {"tg_id": tg_channel_id, "channel_id": channel_id}
                        )
                    logger.info("Auto-populated tg_channel_id", 
                              channel_id=channel_id, 
                              username=username,
                              tg_channel_id=tg_channel_id)
                except Exception as e:
                    # Context7: Обрабатываем разные типы ошибок gracefully
                    error_str = str(e)
                    if "UniqueViolationError" in error_str or "duplicate key" in error_str.lower():
                        # tg_channel_id уже существует для другого канала - это нормально
                        logger.debug("tg_channel_id already exists for another channel, skipping update",
                                   channel_id=channel_id,
                                   tg_channel_id=tg_channel_id,
                                   error=error_str)
                    else:
                        logger.warning("Failed to update tg_channel_id in DB", 
                                     channel_id=channel_id, error=error_str)
                    # Context7: Rollback уже выполнен автоматически через async with begin()
                    # Но делаем дополнительный rollback на случай ошибки
                    try:
                        if self.db_session.in_transaction():
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
    
    async def _get_high_watermark(self, channel_id: str) -> Optional[datetime]:
        """
        Context7: High Watermark - максимальное время последнего поста из БД.
        
        Это источник истины о реальном времени последнего поста в канале.
        
        Args:
            channel_id: ID канала в БД
        
        Returns:
            datetime последнего поста в UTC или None, если постов нет
        """
        return await self._get_last_post_date(channel_id)
    
    async def _get_low_watermark(self, channel_id: str) -> Optional[datetime]:
        """
        Context7: Low Watermark - время, с которого гарантированно спарсили всё.
        
        Хранится в Redis как последний успешный since_date после backfill.
        Используется для определения границ гарантированно обработанного диапазона.
        
        Args:
            channel_id: ID канала в БД
        
        Returns:
            datetime low watermark в UTC или None, если не установлен
        """
        try:
            watermark_key = f"low_watermark:{channel_id}"
            watermark_raw = await self.redis_client.get(watermark_key)
            if watermark_raw:
                watermark_dt = ensure_dt_utc(watermark_raw)
                if watermark_dt:
                    logger.debug("Retrieved low watermark from Redis",
                               channel_id=channel_id,
                               low_watermark=watermark_dt.isoformat())
                    return watermark_dt
            return None
        except Exception as e:
            logger.warning("Failed to get low watermark from Redis",
                         channel_id=channel_id,
                         error=str(e))
            return None
    
    async def _update_low_watermark(self, channel_id: str, watermark_dt: datetime):
        """
        Context7: Обновление Low Watermark после успешного backfill.
        
        Args:
            channel_id: ID канала в БД
            watermark_dt: datetime для установки как low watermark (в UTC)
        """
        try:
            watermark_key = f"low_watermark:{channel_id}"
            await self.redis_client.setex(
                watermark_key,
                86400,  # TTL 24 часа
                watermark_dt.isoformat()
            )
            logger.debug("Updated low watermark",
                       channel_id=channel_id,
                       low_watermark=watermark_dt.isoformat())
        except Exception as e:
            logger.warning("Failed to update low watermark",
                         channel_id=channel_id,
                         error=str(e))
    
    async def _get_last_post_date(self, channel_id: str) -> Optional[datetime]:
        """
        Context7 best practice: Получение реального времени последнего поста из БД.
        
        Это более надёжный источник истины, чем last_parsed_at, так как отражает
        фактическое время последнего сохранённого поста.
        
        Args:
            channel_id: ID канала в БД
        
        Returns:
            datetime последнего поста в UTC или None, если постов нет
        """
        try:
            result = await self.db_session.execute(
                text("""
                    SELECT MAX(posted_at) as max_posted_at
                    FROM posts
                    WHERE channel_id = :channel_id
                """),
                {"channel_id": channel_id}
            )
            row = result.fetchone()
            if row and row.max_posted_at:
                # Конвертируем в UTC, если нужно
                max_posted = ensure_dt_utc(row.max_posted_at)
                if max_posted:
                    logger.debug("Retrieved last post date from DB",
                               channel_id=channel_id,
                               last_post_date=max_posted.isoformat())
                    return max_posted
            return None
        except Exception as e:
            logger.warning("Failed to get last post date from DB",
                         channel_id=channel_id,
                         error=str(e))
            return None
    
    async def _calculate_interarrival_stats(
        self, 
        channel_id: str, 
        window_days: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Context7: [C7-ID: adaptive-thresholds-001] Расчет статистики интервалов между постами.
        
        Гибридный подход: Redis кеш + PostgreSQL для долгосрочной истории.
        Вычисляет median, p95 и EWMA интервалов между постами за указанный период.
        
        Args:
            channel_id: ID канала в БД
            window_days: Период истории в днях (если None - из config)
        
        Returns:
            Dict с ключами: median, p95, ewma, sample_count, window_days, calculated_at
            или None при ошибке
        """
        if not self.config.adaptive_thresholds_enabled:
            return None
        
        window_days = window_days or self.config.stats_window_days
        cache_key = f"interarrival_stats:{channel_id}"
        
        try:
            # Проверка Redis кеша
            cached = await self.redis_client.get(cache_key)
            if cached:
                try:
                    stats = json.loads(cached)
                    logger.debug("Using cached interarrival stats",
                               channel_id=channel_id,
                               window_days=stats.get('window_days'))
                    return stats
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Failed to parse cached stats, recalculating",
                                 channel_id=channel_id,
                                 error=str(e))
            
            # Расчет из PostgreSQL
            # Используем f-string для window_days, так как это безопасное целое число из конфига
            result = await self.db_session.execute(
                text(f"""
                    WITH post_times AS (
                        SELECT 
                            posted_at,
                            LAG(posted_at) OVER (ORDER BY posted_at) as prev_posted_at
                        FROM posts
                        WHERE channel_id = :channel_id
                          AND posted_at >= NOW() - INTERVAL '{window_days} days'
                          AND posted_at IS NOT NULL
                        ORDER BY posted_at DESC
                        LIMIT 200
                    ),
                    intervals AS (
                        SELECT 
                            EXTRACT(EPOCH FROM (posted_at - prev_posted_at)) as interval_seconds
                        FROM post_times
                        WHERE prev_posted_at IS NOT NULL
                          AND posted_at > prev_posted_at
                    )
                    SELECT 
                        COUNT(*) as sample_count,
                        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY interval_seconds) as median,
                        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY interval_seconds) as p95,
                        AVG(interval_seconds) as avg_interval
                    FROM intervals
                """),
                {"channel_id": channel_id}
            )
            
            row = result.fetchone()
            if not row or not row.sample_count or row.sample_count < 3:
                # Недостаточно данных для статистики
                logger.debug("Insufficient data for interarrival stats",
                           channel_id=channel_id,
                           sample_count=row.sample_count if row else 0)
                return None
            
            # Расчет EWMA (Exponentially Weighted Moving Average)
            # Для простоты используем avg как приближение EWMA
            # В будущем можно добавить более точный расчет
            ewma = float(row.avg_interval) if row.avg_interval else float(row.median)
            
            stats = {
                "median": float(row.median) if row.median else None,
                "p95": float(row.p95) if row.p95 else None,
                "ewma": ewma,
                "sample_count": int(row.sample_count),
                "window_days": window_days,
                "calculated_at": datetime.now(timezone.utc).isoformat()
            }
            
            # Сохранение в Redis кеш с TTL 1 час
            try:
                await self.redis_client.setex(
                    cache_key,
                    3600,  # 1 час
                    json.dumps(stats)
                )
                logger.debug("Calculated and cached interarrival stats",
                           channel_id=channel_id,
                           median=stats["median"],
                           p95=stats["p95"],
                           sample_count=stats["sample_count"])
            except Exception as e:
                logger.warning("Failed to cache interarrival stats",
                             channel_id=channel_id,
                             error=str(e))
            
            # Context7: Обновление метрики interarrival_seconds histogram
            # Экспортируем p95 и median для наблюдаемости
            try:
                from tasks.parse_all_channels_task import interarrival_seconds
                # Обновляем histogram с медианным значением
                if stats.get("median"):
                    interarrival_seconds.labels(channel_id=channel_id).observe(stats["median"])
            except ImportError:
                pass  # Метрики недоступны в этом контексте
            except Exception as e:
                logger.warning("Failed to update interarrival_seconds metric",
                             channel_id=channel_id,
                             error=str(e))
            
            return stats
            
        except Exception as e:
            logger.warning("Failed to calculate interarrival stats",
                         channel_id=channel_id,
                         error=str(e))
            return None
    
    def _is_quiet_hours(self, dt: datetime) -> Tuple[bool, str]:
        """
        Context7: Определение "quiet hours" (выходные и ночное время).
        
        Args:
            dt: datetime в UTC
        
        Returns:
            (is_quiet, reason) где reason может быть "weekend", "night_hours" или "normal"
        """
        # Конвертация в MSK (UTC+3)
        msk_tz = timezone(timedelta(hours=3))
        msk_time = dt.astimezone(msk_tz)
        
        hour = msk_time.hour
        weekday = msk_time.weekday()  # 0=Monday, 6=Sunday
        
        # Выходные (суббота=5, воскресенье=6)
        if weekday >= 5:
            return (True, "weekend")
        
        # Ночные часы (22:00-08:00 MSK)
        if hour >= 22 or hour < 8:
            return (True, "night_hours")
        
        return (False, "normal")
    
    async def _compute_adaptive_threshold(self, channel_id: str) -> int:
        """
        Context7: [C7-ID: adaptive-thresholds-002] Вычисление адаптивного порога для определения пропусков.
        
        Использует статистику интервалов между постами канала и применяет коэффициенты
        для quiet hours (выходные/ночь).
        
        Args:
            channel_id: ID канала в БД
        
        Returns:
            Порог в секундах (int)
        """
        if not self.config.adaptive_thresholds_enabled:
            # Fallback: фиксированный порог 1 час
            return 3600
        
        # Коэффициенты для quiet hours
        HOURLY_COEF = {
            "night": 1.5,      # 22:00-08:00 MSK
            "day": 1.0,        # 08:00-22:00 MSK
            "evening": 1.3     # 22:00-23:00 MSK (уже учтено в night)
        }
        
        WEEKDAY_COEF = {
            "weekdays": 1.0,   # Понедельник-Пятница
            "weekend": 1.8     # Суббота-Воскресенье
        }
        
        try:
            # Получение статистики интервалов
            stats = await self._calculate_interarrival_stats(channel_id)
            
            if not stats or not stats.get('p95'):
                # Недостаточно данных - используем фиксированный порог
                logger.debug("Insufficient stats, using fixed threshold",
                           channel_id=channel_id)
                return 3600  # 1 час
            
            p95 = stats['p95']
            
            # Базовый порог: p95 * 1.5, ограниченный 30мин-12ч
            base_threshold = max(1800, min(43200, p95 * 1.5))  # clamp(p95 * 1.5, 30m, 12h)
            
            # Применение коэффициентов по времени (MSK)
            now = datetime.now(timezone.utc)
            is_quiet, quiet_reason = self._is_quiet_hours(now)
            
            # Определение коэффициента дня недели
            msk_tz = timezone(timedelta(hours=3))
            msk_time = now.astimezone(msk_tz)
            weekday_coef = WEEKDAY_COEF["weekend"] if msk_time.weekday() >= 5 else WEEKDAY_COEF["weekdays"]
            
            # Определение коэффициента часа
            hour = msk_time.hour
            if hour >= 22 or hour < 8:
                hour_coef = HOURLY_COEF["night"]
            else:
                hour_coef = HOURLY_COEF["day"]
            
            # Итоговый порог
            adaptive_threshold = int(base_threshold * weekday_coef * hour_coef)
            
            # Финальное ограничение (не больше 24 часов)
            adaptive_threshold = min(adaptive_threshold, 86400)
            
            logger.debug("Computed adaptive threshold",
                       channel_id=channel_id,
                       base_threshold=base_threshold,
                       weekday_coef=weekday_coef,
                       hour_coef=hour_coef,
                       final_threshold=adaptive_threshold,
                       quiet_reason=quiet_reason,
                       p95=p95)
            
            return adaptive_threshold
            
        except Exception as e:
            logger.warning("Failed to compute adaptive threshold, using fixed",
                         channel_id=channel_id,
                         error=str(e))
            return 3600  # Fallback: 1 час
    
    async def _get_since_date(
        self,
        channel: Dict[str, Any],
        mode: str
    ) -> datetime:
        """
        Context7 best practice: Определение since_date с учётом режима, HWM и адаптивного overlap.
        
        Улучшения:
        1. Использование MAX(posted_at) из БД как приоритетного источника (High Watermark)
        2. Адаптивный overlap на основе статистики канала (clamp(p95 * 0.5, 2m, 10m))
        3. Fallback на фиксированный overlap (5 минут) при отсутствии статистики
        4. Мониторинг пропусков через метрики
        
        Args:
            channel: данные канала с last_parsed_at
            mode: "historical" или "incremental"
        
        Returns:
            datetime с timezone UTC
        """
        now = datetime.now(timezone.utc)
        channel_id = channel['id']
        
        # Получение HWM из Redis (устойчивость к сбоям)
        hwm_key = f"parse_hwm:{channel_id}"
        # Context7: get() - асинхронная функция в redis.asyncio
        hwm_raw = await self.redis_client.get(hwm_key)
        redis_hwm = ensure_dt_utc(hwm_raw) if hwm_raw else None
        
        if mode == "incremental":
            # Context7: [C7-ID: incremental-since-date-fix-003] КРИТИЧНО - для incremental режима используем ТОЛЬКО MAX(posted_at) из БД
            # last_parsed_at может быть намного больше реального последнего поста, если парсинг не нашел новых постов
            # Это приводит к неправильному расчету since_date и пропуску постов
            last_post_date = await self._get_last_post_date(channel_id)
            
            if last_post_date:
                # Context7: Используем ТОЛЬКО last_post_date (реальный последний пост в БД)
                # НЕ используем last_parsed_at, так как он может быть неточным
                base_utc = last_post_date
                logger.debug("Using last_post_date as base for incremental mode",
                           channel_id=channel_id,
                           last_post_date=last_post_date.isoformat())
            else:
                # Fallback: если нет постов в БД, используем last_parsed_at или Redis HWM
                base = channel.get('last_parsed_at') or redis_hwm
                if base:
                    base_utc = ensure_dt_utc(base)
                    if not base_utc:
                        # Если не удалось нормализовать, используем incremental окно
                        return now - timedelta(minutes=self.config.incremental_minutes)
                    logger.debug("Using last_parsed_at/HWM as fallback (no posts in DB)",
                               channel_id=channel_id,
                               base_date=base_utc.isoformat())
                else:
                    # Fallback: если нет данных, берём incremental окно
                    return now - timedelta(minutes=self.config.incremental_minutes)
            
            # Проверка валидности
            if not base_utc:
                logger.warning("Failed to normalize base date to UTC, using fallback",
                             channel_id=channel_id)
                return now - timedelta(minutes=self.config.incremental_minutes)
            
            # Context7: [C7-ID: incremental-old-date-fix-001] КРИТИЧНО - если базовая дата слишком старая,
            # НЕ переключаемся на historical режим, а используем last_post_date как нижнюю границу
            # Это гарантирует, что мы не пропустим посты, опубликованные между last_post_date и now
            age_hours = (now - base_utc).total_seconds() / 3600
            if age_hours > self.config.lpa_max_age_hours:
                logger.warning(
                    "Base date too old, but using it as lower bound to avoid missing posts",
                    channel_id=channel_id,
                    age_hours=age_hours,
                    base_date=base_utc.isoformat()
                )
                # НЕ переключаемся на historical - используем last_post_date как есть
                # Это гарантирует, что мы найдем все посты после last_post_date
            
            # Проверка на будущее время
            if base_utc > now:
                logger.warning("Base date is in future, using now instead",
                             channel_id=channel_id,
                             base_date=base_utc.isoformat(),
                             now=now.isoformat())
                return now - timedelta(minutes=self.config.incremental_minutes)
            
            # Context7: [C7-ID: incremental-overlap-001] Адаптивный overlap на основе статистики
            # Предотвращает пропуски из-за:
            # - Race conditions (пост появился между парсингами)
            # - Временных расхождений между Telegram API и системным временем
            # - Задержек обработки сообщений в Telegram
            
            if self.config.adaptive_thresholds_enabled:
                # Получаем статистику для адаптивного overlap
                stats = await self._calculate_interarrival_stats(channel_id)
                if stats and stats.get('p95'):
                    # Overlap = clamp(p95 * 0.5, min=120, max=600) секунд
                    overlap_seconds = max(120, min(600, int(stats['p95'] * 0.5)))
                    overlap_minutes = overlap_seconds / 60.0
                    logger.debug("Using adaptive overlap",
                               channel_id=channel_id,
                               overlap_seconds=overlap_seconds,
                               p95=stats['p95'])
                else:
                    overlap_minutes = 5  # Fallback
            else:
                overlap_minutes = 5  # Фиксированный overlap
            
            since_date = base_utc - timedelta(seconds=int(overlap_minutes * 60))
            
            # Context7: [C7-ID: incremental-old-date-fix-002] КРИТИЧНО - НЕ ограничиваем since_date для старых дат
            # Если last_post_date старый, мы должны парсить все посты после него, даже если это больше 24 часов
            # Ограничение min_since_date приводит к пропуску постов между last_post_date и now - 24h
            # Убираем это ограничение для incremental режима, чтобы гарантировать полноту парсинга
            
            logger.debug("Calculated since_date with overlap",
                       channel_id=channel_id,
                       base_date=base_utc.isoformat(),
                       since_date=since_date.isoformat(),
                       overlap_minutes=overlap_minutes,
                       age_minutes=(now - base_utc).total_seconds() / 60)
            
            return since_date
        
        elif mode == "historical":
            # Context7: Для historical режима (новые каналы или старые с пропусками)
            # Парсим последние N часов (по умолчанию 24 часа)
            # Используем исторический диапазон для полного покрытия
            since_date = now - timedelta(hours=self.config.historical_hours)
            logger.debug("Calculated since_date for historical mode",
                       channel_id=channel_id,
                       since_date=since_date.isoformat(),
                       historical_hours=self.config.historical_hours,
                       now=now.isoformat())
            return since_date
        
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
            # [C7-ID: dev-mode-017] Context7 best practice: получаем достаточно сообщений
            # чтобы гарантированно захватить все новые посты за нужный период
            # - incremental: последние сообщения между last_parsed_at и now
            # - historical: последние 24 часа (для новых каналов)
            # Базовый лимит будет установлен ниже в зависимости от режима
            limit = batch_size * 100 if mode == "incremental" else batch_size * 200
            
            # Context7: КРИТИЧНО - offset_date в Telethon возвращает сообщения ПРЕДШЕСТВУЮЩИЕ дате, а не ПОСЛЕ!
            # Источник: Telethon docs: "offset_date: Offset date (messages *previous* to this date will be retrieved). Exclusive."
            # 
            # Для ОБОИХ режимов (incremental и historical) НЕ используем offset_date, потому что:
            # - incremental: нужны сообщения НОВЕЕ since_date (последние сообщения)
            # - historical: нужны сообщения НОВЕЕ since_date (последние 24 часа для новых каналов)
            # 
            # Используем подход: получаем последние сообщения БЕЗ offset_date, затем фильтруем локально
            # Это гарантирует, что мы получим ВСЕ новые сообщения независимо от режима
            offset_date_param = None
            
            # Context7: Для historical режима увеличиваем лимит, чтобы гарантированно захватить все сообщения за последние 24 часа
            if mode == "historical":
                limit = batch_size * 200  # Увеличиваем лимит для historical режима (новые каналы)
            
            logger.debug("Fetching messages batch",
                        channel_id=channel_entity.id,
                        mode=mode,
                        limit=limit,
                        since_date=since_date.isoformat(),
                        offset_date=offset_date_param.isoformat() if offset_date_param else None)
            
            messages = await fetch_messages_with_retry(
                client,
                channel_entity,
                limit=limit,
                redis_client=self.redis_client,
                offset_date=offset_date_param,  # Всегда None для обоих режимов (получаем последние сообщения)
                reverse=False  # Context7 P1.3: По умолчанию без reverse (для incremental/historical)
            )
            
            # Диагностика: логируем первое и последнее сообщение для понимания диапазона
            # Context7: Логируем для обоих режимов, чтобы отслеживать правильность работы
            if messages:
                first_msg = messages[0] if messages else None
                last_msg = messages[-1] if messages else None
                first_msg_date = ensure_dt_utc(first_msg.date) if first_msg and first_msg.date else None
                last_msg_date = ensure_dt_utc(last_msg.date) if last_msg and last_msg.date else None
                
                # Считаем сколько сообщений новее since_date
                newer_count = sum(1 for msg in messages 
                                if msg.date and ensure_dt_utc(msg.date) and ensure_dt_utc(msg.date) > since_date)
                
                # Context7: Для historical режима считаем сообщения >= since_date, для incremental > since_date
                now_for_log = datetime.now(timezone.utc)
                if mode == "historical":
                    matching_count = sum(1 for msg in messages 
                                       if msg.date and ensure_dt_utc(msg.date) and ensure_dt_utc(msg.date) >= since_date)
                    # Дополнительная диагностика для historical режима
                    age_hours = (now_for_log - since_date).total_seconds() / 3600 if since_date else None
                else:
                    matching_count = newer_count
                    age_hours = None
                
                logger.info(f"Fetched messages range for {mode} mode",
                          channel_id=channel_entity.id,
                          mode=mode,
                          count=len(messages),
                          first_message_date=first_msg_date.isoformat() if first_msg_date else None,
                          first_message_date_original=first_msg.date if first_msg else None,
                          last_message_date=last_msg_date.isoformat() if last_msg_date else None,
                          since_date=since_date.isoformat(),
                          messages_matching_since_date=matching_count,
                          historical_range_hours=age_hours,
                          now=now_for_log.isoformat())
            
            # Обрабатываем полученные сообщения
            messages_filtered = 0
            found_newer_messages = False  # Context7: Флаг для отслеживания наличия новых сообщений
            messages_checked = 0  # Context7: Счетчик проверенных сообщений для диагностики
            max_check_before_stop = 20  # Context7: Максимальное количество сообщений для проверки перед остановкой в incremental режиме
            
            for message in messages:
                # Context7: КРИТИЧНО - нормализуем message.date к UTC для корректного сравнения
                # Telethon может возвращать datetime с разными timezone или без timezone
                message_date_utc = ensure_dt_utc(message.date) if message.date else None
                messages_checked += 1
                
                if not message_date_utc:
                    logger.warning(f"Message {message.id} has no date, skipping",
                                 channel_id=channel_entity.id,
                                 message_id=message.id,
                                 message_index=messages_checked)
                    continue
                
                # [C7-ID: dev-mode-017] Context7: Унифицированная логика фильтрации с нормализацией timezone
                # Historical: включаем сообщения >= since_date (парсим от новых к старым, останавливаемся на < since_date)
                # Incremental: включаем только сообщения > since_date (строго новее last_parsed_at, останавливаемся на <= since_date)
                if mode == "historical":
                    # Historical: парсим назад, останавливаемся когда дошли до since_date
                    # Включаем сообщения с message_date_utc >= since_date
                    if message_date_utc < since_date:
                        logger.info(f"Reached since_date in historical mode, stopping. message_date_utc={message_date_utc}, since_date={since_date}, messages_yielded={messages_yielded}, messages_filtered={messages_filtered}")
                        break
                else:  # incremental
                    # Context7: КРИТИЧНО - для incremental режима проверяем, есть ли сообщения новее since_date
                    # Улучшенная логика: проверяем несколько сообщений перед остановкой, чтобы не пропустить новые
                    # если они не в начале списка (например, из-за задержек в Telegram API)
                    if message_date_utc > since_date:
                        found_newer_messages = True
                        # Включаем сообщение в batch
                        batch.append(message)
                        messages_yielded += 1
                        messages_filtered += 1
                    elif found_newer_messages:
                        # Мы уже нашли новые сообщения, но теперь встретили старое - останавливаемся
                        logger.info(f"Reached since_date in incremental mode after processing newer messages, stopping. message_date_utc={message_date_utc}, since_date={since_date}, messages_yielded={messages_yielded}, messages_filtered={messages_filtered}, messages_checked={messages_checked}")
                        break
                    else:
                        # Первое сообщение уже старше since_date
                        # Context7: Проверяем несколько сообщений перед остановкой, чтобы не пропустить новые
                        # которые могут быть не в начале списка из-за задержек или нехронологического порядка
                        if messages_checked < max_check_before_stop:
                            # Продолжаем проверку - возможно, новые сообщения дальше в списке
                            logger.debug(f"Message {messages_checked} is older than since_date, but checking more messages. message_date_utc={message_date_utc}, since_date={since_date}, messages_checked={messages_checked}/{max_check_before_stop}")
                            continue
                        else:
                            # Проверили достаточно сообщений - новых нет
                            logger.info(f"No newer messages found in incremental mode after checking {messages_checked} messages. Last checked message_date: {message_date_utc}, since_date: {since_date}, messages_yielded={messages_yielded}, messages_filtered={messages_filtered}")
                        break
                
                # Для historical режима добавляем сообщение в batch
                if mode == "historical":
                    batch.append(message)
                    messages_yielded += 1
                    messages_filtered += 1
                
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
            # [C7-ID: dev-mode-017] Context7 best practice: для ОБОИХ режимов НЕ используем offset_date
            # Получаем последние сообщения и фильтруем локально по date >= since_date (historical) или date > since_date (incremental)
            # Это гарантирует, что мы получим ВСЕ новые сообщения независимо от режима
            limit_fallback = batch_size * 200 if mode == "historical" else batch_size * 100
            iter_params = {"limit": limit_fallback}
            
            async for message in client.iter_messages(channel_entity, **iter_params):
                # [C7-ID: dev-mode-017] Context7: Унифицированная логика фильтрации (соответствует основному пути)
                # Historical: включаем сообщения >= since_date, останавливаемся на < since_date
                # Incremental: включаем только сообщения > since_date, останавливаемся на <= since_date
                # Context7: Нормализуем message.date к UTC для корректного сравнения
                message_date_utc = ensure_dt_utc(message.date) if message.date else None
                
                if not message_date_utc:
                    continue  # Пропускаем сообщения без даты
                
                if mode == "historical":
                    # Historical: останавливаемся когда дошли до since_date
                    # Включаем сообщения с message_date_utc >= since_date
                    if message_date_utc < since_date:
                        logger.debug(f"Reached since_date, stopping. message_date_utc={message_date_utc}, since_date={since_date}")
                        break
                else:  # incremental
                    # Incremental: сообщения приходят от новых к старым, останавливаемся при встрече <= since_date
                    # Это исключает сообщения на границе since_date (равные last_parsed_at), предотвращая дубликаты
                    if message_date_utc <= since_date:
                        logger.debug(f"Reached since_date in incremental mode (fallback), stopping. message_date_utc={message_date_utc}, since_date={since_date}, messages_yielded={messages_yielded}")
                        break
                
                # Добавляем сообщение в batch (для historical или incremental с message_date_utc > since_date)
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
        mode: str = "historical",
        telegram_client: Any = None  # Context7 P1: TelegramClient для DiscussionExtractor
    ) -> Dict[str, int]:
        """Обработка батча сообщений с обновлением HWM."""
        processed = 0
        skipped = 0
        max_date = None
        
        # Подготовка данных для bulk insert
        posts_data = []
        events_data = []
        # Context7: Сохраняем mapping между сообщениями и post_data для извлечения forwards/reactions/replies
        message_to_post_mapping = []
        
        logger.info(f"Processing batch of {len(messages)} messages", 
                   channel_id=channel_id, mode=mode)
        
        for message in messages:
            try:
                # Context7: КРИТИЧНО - нормализуем message.date к UTC для корректного сравнения
                message_date_utc = ensure_dt_utc(message.date) if message.date else None
                
                # Track max date in batch (нормализованная дата)
                if message_date_utc:
                    if max_date is None or message_date_utc > max_date:
                        max_date = message_date_utc
                
                # Диагностическое логирование для проверки фильтрации
                if message.id % 10 == 0:  # Логируем каждое 10-е сообщение для диагностики
                    logger.debug(f"Processing message", 
                               message_id=message.id,
                               message_date=message.date,
                               message_date_utc=message_date_utc,
                               has_text=bool(message.text),
                               has_media=bool(message.media))
                
                # Context7: Идемпотентность обеспечивается UNIQUE constraint (channel_id, telegram_message_id) в БД
                # Дедупликация по grouped_id удалена - она вызывала race conditions и потерю альбомов
                grouped_id = getattr(message, 'grouped_id', None)
                
                # Проверка идемпотентности
                is_duplicate = await self._is_duplicate_message(message, channel_id, tenant_id)
                if is_duplicate:
                    skipped += 1
                    logger.debug(f"Message {message.id} skipped as duplicate", 
                               channel_id=channel_id,
                               message_id=message.id)
                    continue
                
                # Извлечение данных сообщения
                post_data = await self._extract_message_data(message, channel_id, tenant_id, tg_channel_id)
                # [C7-ID: dev-mode-015] Context7 best practice: генерация idempotency_key для идемпотентности
                # Формат: {tenant_id}:{channel_id}:{telegram_message_id}
                telegram_message_id = post_data.get('telegram_message_id')
                if not post_data.get('idempotency_key'):
                    post_data['idempotency_key'] = f"{tenant_id}:{channel_id}:{telegram_message_id}"
                
                post_id = post_data.get('id')
                trace_id = post_data.get('idempotency_key', str(uuid.uuid4()))
                
                # Context7: Обработка медиа через MediaProcessor (если доступен и сообщение содержит медиа)
                media_files = []
                
                if self.media_processor and message.media:
                    try:
                        # Получаем TelegramClient из telegram_client_manager для MediaProcessor
                        if self.telegram_client_manager:
                            try:
                                telegram_id_for_media = int(user_id)
                            except (TypeError, ValueError):
                                logger.error(
                                    "Invalid telegram_id type for media processing",
                                    user_id=user_id
                                )
                                telegram_client = None
                            else:
                                telegram_client = await self.telegram_client_manager.get_client(telegram_id_for_media)
                            if telegram_client:
                                # Обновляем telegram_client в MediaProcessor
                                self.media_processor.telegram_client = telegram_client
                                
                                # Обработка медиа
                                # Передаем channel_entity и channel_id для обработки альбомов
                                media_files = await self.media_processor.process_message_media(
                                    message=message,
                                    post_id=post_id,
                                    trace_id=trace_id,
                                    tenant_id=tenant_id,
                                    channel_id=channel_id
                                )
                                
                                logger.info(
                                    "Media processed",
                                    post_id=post_id,
                                    media_count=len(media_files),
                                    is_album=bool(grouped_id and len(media_files) > 1),
                                    grouped_id=grouped_id,
                                    channel_id=channel_id,
                                    has_media=bool(message.media)
                                )
                            else:
                                logger.warning(
                                    "TelegramClient not available for media processing",
                                    post_id=post_id,
                                    user_id=user_id,
                                    channel_id=channel_id,
                                    has_media=bool(message.media)
                                )
                        else:
                            logger.warning(
                                "TelegramClientManager not available for media processing",
                                post_id=post_id,
                                channel_id=channel_id,
                                has_media=bool(message.media)
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to process media",
                            post_id=post_id,
                            error=str(e),
                            channel_id=channel_id,
                            exc_info=True
                        )
                        # Продолжаем обработку даже при ошибке медиа
                elif message.media:
                    # Context7: Логируем, почему медиа не обрабатывается
                    logger.debug(
                        "Media not processed - MediaProcessor or message.media check failed",
                        post_id=post_id,
                        has_media_processor=bool(self.media_processor),
                        has_message_media=bool(message.media),
                        channel_id=channel_id
                    )
                
                # Сохраняем информацию о медиа в post_data для последующего использования
                if media_files:
                    post_data['media_files'] = media_files
                    post_data['media_count'] = len(media_files)
                    # Context7: Извлекаем SHA256 для передачи в событие
                    post_data['media_sha256_list'] = [mf.sha256 for mf in media_files]
                
                # Context7: КРИТИЧНО - сохраняем grouped_id в post_data для обработки альбомов
                # grouped_id извлекается из сообщения выше (строка 1530), но может быть не в post_data
                if grouped_id is not None:
                    post_data['grouped_id'] = grouped_id
                    logger.debug(
                        "Grouped ID added to post_data",
                        post_id=post_id,
                        grouped_id=grouped_id,
                        channel_id=channel_id
                    )
                
                posts_data.append(post_data)
                
                # Context7: Сохраняем mapping для последующего извлечения forwards/reactions/replies
                message_to_post_mapping.append((message, post_data))
                
                # Подготовка события
                event_data = await self._prepare_parsed_event(
                    post_data, user_id, channel_id, tenant_id
                )
                events_data.append(event_data)
                
                # Context7: НЕ увеличиваем processed здесь - это будет сделано только после успешного сохранения в БД
                # processed будет обновлен после успешного save_batch_atomic
                
            except Exception as e:
                logger.error(f"Failed to process message {message.id}: {e}", 
                           channel_id=channel_id,
                           message_id=message.id,
                           error=str(e),
                           exc_info=True)
                skipped += 1
        
        # Context7: Атомарное сохранение в БД с новыми компонентами
        logger.info(f"Batch processing completed", 
                   channel_id=channel_id,
                   total_messages=len(messages),
                   posts_prepared=len(posts_data),
                   skipped=skipped,
                   posts_data_count=len(posts_data),
                   max_date=max_date)
        
        # Context7 best practice: Создаём user_channel даже если нет новых постов
        # Это необходимо для корректной работы сохранения альбомов для существующих постов
        if not posts_data:
            # Если нет новых постов, но есть сообщения (даже дубликаты),
            # создаём user_channel для обеспечения возможности создания альбомов
            try:
                # Context7: Проверяем состояние сессии перед созданием user_channel
                if self.db_session.in_transaction():
                    await self.db_session.rollback()
                    logger.debug("Rolled back existing transaction before ensuring user_channel",
                                channel_id=channel_id)
                
                # Создаём user_channel через atomic_saver
                # Context7 P2: Получение first_name из Telegram API (если доступен telegram_client)
                user_first_name = ''
                user_last_name = ''
                user_username = ''
                
                # Context7 P2: Получаем first_name из Telegram API через telegram_client (передается как параметр)
                if telegram_client and user_id:
                    try:
                        user_entity = await telegram_client.get_entity(int(user_id))
                        if user_entity:
                            user_first_name = getattr(user_entity, 'first_name', '') or ''
                            user_last_name = getattr(user_entity, 'last_name', '') or ''
                            user_username = getattr(user_entity, 'username', '') or ''
                            
                            logger.debug(
                                "User info retrieved from Telegram API (no new posts)",
                                user_id=user_id,
                                first_name=user_first_name[:50] if user_first_name else None,
                                username=user_username
                            )
                    except Exception as e:
                        logger.debug(
                            "Failed to get user entity from Telegram API (no new posts)",
                            user_id=user_id,
                            error=str(e)
                        )
                        # Продолжаем с пустыми значениями - не критично
                
                user_data = {
                    'telegram_id': user_id,
                    'tenant_id': tenant_id,
                    'first_name': user_first_name,
                    'last_name': user_last_name,
                    'username': user_username
                }
                
                channel_title = channel_entity.title if channel_entity and hasattr(channel_entity, 'title') else ''
                channel_username = channel_entity.username if channel_entity and hasattr(channel_entity, 'username') else ''
                
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
                    'id': channel_id,
                    'telegram_id': tg_channel_id,
                    'title': channel_title,
                    'username': channel_username
                }
                
                # Context7: Проверка подписки с поддержкой системного парсинга
                # Для системного парсинга (scheduler) разрешаем парсинг активных каналов,
                # так как AtomicDBSaver автоматически создаст/активирует подписку при сохранении постов
                # Context7: Преобразуем user_id в int для SQL запроса (telegram_id в БД - bigint)
                telegram_id_int = int(user_id) if isinstance(user_id, str) else user_id
                
                # Проверяем активность канала
                channel_active_check = await self.db_session.execute(
                    text("SELECT is_active FROM channels WHERE id = :channel_id LIMIT 1"),
                    {"channel_id": channel_id}
                )
                channel_active_row = channel_active_check.fetchone()
                is_channel_active = channel_active_row and channel_active_row.is_active
                
                # Проверяем подписку
                check_subscription = await self.db_session.execute(
                    text("""
                        SELECT user_id FROM user_channel 
                        WHERE user_id = (SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1)
                          AND channel_id = :channel_id
                          AND is_active = true
                        LIMIT 1
                    """),
                    {"telegram_id": telegram_id_int, "channel_id": channel_id}
                )
                
                subscription_exists = check_subscription.fetchone() is not None
                
                if not subscription_exists:
                    if is_channel_active:
                        # Context7: Канал активен - разрешаем парсинг для системного парсинга
                        # AtomicDBSaver автоматически создаст/активирует подписку при сохранении постов
                        logger.info("Channel is active, allowing parsing for system parsing (subscription will be created by AtomicDBSaver)",
                                  channel_id=channel_id,
                                  telegram_id=user_id)
                    else:
                        # Канал неактивен - блокируем парсинг
                        logger.warning("User not subscribed to inactive channel, skipping parsing",
                                      channel_id=channel_id,
                                      telegram_id=user_id)
                        return {"status": "skipped", "reason": "not_subscribed_inactive_channel", "parsed": 0, "max_message_date": None}
                else:
                    # Пользователь подписан - продолжаем парсинг
                    logger.info("User subscribed to channel, continuing parsing",
                           channel_id=channel_id,
                           telegram_id=user_id)
            except Exception as e:
                logger.warning("Failed to ensure user_channel when no new posts",
                             channel_id=channel_id,
                             error=str(e),
                             exc_info=True)
        
        if posts_data:
            # Подготовка данных пользователя и канала
            # [C7-ID: dev-mode-012] tenant_id из параметра функции (передается в parse_channel_messages)
            # Context7 P2: Получение first_name, last_name, username из Telegram API
            user_first_name = ''
            user_last_name = ''
            user_username = ''
            
            if telegram_client and user_id:
                try:
                    # Получаем entity пользователя через Telegram API
                    user_entity = await telegram_client.get_entity(int(user_id))
                    if user_entity:
                        user_first_name = getattr(user_entity, 'first_name', '') or ''
                        user_last_name = getattr(user_entity, 'last_name', '') or ''
                        user_username = getattr(user_entity, 'username', '') or ''
                        
                        logger.debug(
                            "User info retrieved from Telegram API",
                            user_id=user_id,
                            first_name=user_first_name[:50] if user_first_name else None,
                            username=user_username
                        )
                except Exception as e:
                    logger.debug(
                        "Failed to get user entity from Telegram API",
                        user_id=user_id,
                        error=str(e)
                    )
                    # Продолжаем с пустыми значениями - не критично
            
            user_data = {
                'telegram_id': user_id,
                'tenant_id': tenant_id,  # Используем переданный tenant_id
                'first_name': user_first_name,
                'last_name': user_last_name,
                'username': user_username
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
                'id': channel_id,  # Context7: Передаём channel_id для использования в upsert
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
                # Context7: КРИТИЧНО - увеличиваем processed ТОЛЬКО после успешного сохранения в БД
                # inserted_count - это количество реально сохраненных/обновленных постов
                processed = inserted_count
                
                logger.info("Atomic batch save successful", 
                          channel_id=channel_id,
                          inserted_count=inserted_count,
                          processed=processed)
                
                # Context7: Сохранение альбомов в media_groups/media_group_items
                # Context7 best practice: Собираем альбомы из БД, а не только из текущего batch
                # Это позволяет обрабатывать альбомы, разбитые на несколько batches
                # ВАЖНО: save_batch_atomic уже завершил транзакцию, используем новую транзакцию для альбомов
                try:
                    # Context7: Проверяем состояние сессии перед сохранением альбомов
                    if self.db_session.in_transaction():
                        # Если транзакция активна (не должно быть), откатываем
                        await self.db_session.rollback()
                        logger.warning("Found active transaction after save_batch_atomic, rolled back",
                                      channel_id=channel_id)
                    
                    from services.media_group_saver import save_media_group
                    
                    # Context7: Собираем уникальные grouped_id из текущего batch
                    grouped_ids_in_batch = set()
                    for post_data in posts_data:
                        grouped_id = post_data.get('grouped_id')
                        if grouped_id:
                            grouped_ids_in_batch.add(grouped_id)
                    
                    # Context7: Также проверяем grouped_id из обработанных сообщений (даже если они были пропущены)
                    # Это позволяет обрабатывать альбомы для существующих постов
                    # Context7 best practice: Проверяем отсутствующие альбомы, если:
                    # 1. Есть новые посты в batch (processed > 0), ИЛИ
                    # 2. Есть grouped_id в текущем batch, которые еще не имеют альбомов
                    should_check_missing = processed > 0 or len(grouped_ids_in_batch) > 0
                    
                    if should_check_missing:
                        # Получаем все grouped_id из канала, которые ещё не имеют альбомов
                        # Context7: Ограничиваем поиск только grouped_id из текущего batch + максимум 20 отсутствующих
                        try:
                            missing_albums_result = await self.db_session.execute(
                                text("""
                                    SELECT DISTINCT p.grouped_id
                                    FROM posts p
                                    LEFT JOIN media_groups mg ON mg.channel_id = p.channel_id 
                                                              AND mg.grouped_id = p.grouped_id
                                    WHERE p.channel_id = :channel_id
                                      AND p.grouped_id IS NOT NULL
                                      AND mg.id IS NULL
                                      AND (p.grouped_id = ANY(CAST(:grouped_ids AS bigint[])) OR :check_all = true)
                                    LIMIT 20
                                """),
                                {
                                    "channel_id": channel_id,
                                    "grouped_ids": list(grouped_ids_in_batch) if grouped_ids_in_batch else [0],
                                    "check_all": processed > 0  # Проверяем все отсутствующие только если есть новые посты
                                }
                            )
                            missing_albums = missing_albums_result.fetchall()
                            for row in missing_albums:
                                if row.grouped_id:
                                    grouped_ids_in_batch.add(row.grouped_id)
                            
                            if missing_albums:
                                logger.info("Found existing posts with missing albums",
                                           channel_id=channel_id,
                                           missing_albums_count=len(missing_albums),
                                           processed=processed,
                                           has_grouped_ids_in_batch=len(grouped_ids_in_batch) > 0)
                        except Exception as e:
                            logger.warning("Failed to check for missing albums",
                                         channel_id=channel_id,
                                         error=str(e))
                    
                    # Context7: КРИТИЧНО - также проверяем grouped_id из БД для всех постов с grouped_id без альбомов
                    # Это гарантирует, что альбомы будут созданы даже если grouped_id не попал в batch
                    if processed > 0:
                        try:
                            # Получаем все grouped_id из канала, которые ещё не имеют альбомов (без ограничения по batch)
                            all_missing_albums_result = await self.db_session.execute(
                                text("""
                                    SELECT DISTINCT p.grouped_id
                                    FROM posts p
                                    LEFT JOIN media_groups mg ON mg.channel_id = p.channel_id 
                                                              AND mg.grouped_id = p.grouped_id
                                    WHERE p.channel_id = :channel_id
                                      AND p.grouped_id IS NOT NULL
                                      AND mg.id IS NULL
                                    LIMIT 20
                                """),
                                {
                                    "channel_id": channel_id
                                }
                            )
                            all_missing_albums = all_missing_albums_result.fetchall()
                            for row in all_missing_albums:
                                if row.grouped_id:
                                    grouped_ids_in_batch.add(row.grouped_id)
                            
                            if all_missing_albums:
                                logger.info("Found all missing albums for channel",
                                           channel_id=channel_id,
                                           missing_albums_count=len(all_missing_albums),
                                           total_grouped_ids=len(grouped_ids_in_batch))
                        except Exception as e:
                            logger.warning("Failed to check for all missing albums",
                                         channel_id=channel_id,
                                         error=str(e))
                    
                    logger.info("Checking for albums in batch",
                               channel_id=channel_id,
                               grouped_ids_count=len(grouped_ids_in_batch),
                               grouped_ids=list(grouped_ids_in_batch)[:5] if grouped_ids_in_batch else [])
                    
                    if not grouped_ids_in_batch:
                        # Нет альбомов в текущем batch - пропускаем обработку альбомов
                        logger.debug("No media groups in batch, skipping album processing",
                                   channel_id=channel_id,
                                   processed=processed,
                                   posts_count=len(posts_data))
                    else:
                        # Context7: Используем отдельную транзакцию для сохранения альбомов
                        # Context7: Проверяем состояние транзакции перед началом новой
                        # Context7 best practice: Явная проверка и очистка состояния сессии
                        try:
                            if self.db_session.in_transaction():
                                await self.db_session.rollback()
                                logger.debug("Rolled back active transaction before saving albums",
                                           channel_id=channel_id)
                                # Context7: Проверяем, что rollback прошел успешно
                                if self.db_session.in_transaction():
                                    logger.error("Transaction still active after rollback before saving albums - session may be in bad state",
                                               channel_id=channel_id)
                                    # Context7: Метрика для отслеживания проблем с состоянием сессии
                                    session_rollback_failures_total.labels(operation='before_albums').inc()
                        except Exception as rollback_error:
                            logger.warning("Failed to rollback transaction before saving albums",
                                         channel_id=channel_id, error=str(rollback_error), error_type=type(rollback_error).__name__)
                            # Context7: Метрика для отслеживания проблем с rollback
                            session_rollback_failures_total.labels(operation='before_albums').inc()
                        
                        async with self.db_session.begin():
                            # Context7: Получаем UUID пользователя один раз (для всех альбомов канала)
                            user_uuid = None
                            try:
                                # Сначала пытаемся получить через user_channel (приоритет 1)
                                result = await self.db_session.execute(
                                    text("""
                                        SELECT u.id::text
                                        FROM users u
                                        JOIN user_channel uc ON uc.user_id = u.id
                                        WHERE uc.channel_id = :channel_id
                                        LIMIT 1
                                    """),
                                    {"channel_id": channel_id}
                                )
                                row = result.fetchone()
                                if row:
                                    user_uuid = str(row[0])
                                    logger.debug("Found user_uuid via user_channel",
                                               channel_id=channel_id,
                                               user_uuid=user_uuid)
                                else:
                                    logger.debug("No user_channel found, trying direct telegram_id lookup",
                                               channel_id=channel_id,
                                               telegram_id=user_id)
                                    # Fallback: пытаемся получить по telegram_id (приоритет 2)
                                    result2 = await self.db_session.execute(
                                        text("SELECT id::text FROM users WHERE telegram_id = :telegram_id LIMIT 1"),
                                        {"telegram_id": int(user_id)}
                                    )
                                    row2 = result2.fetchone()
                                    if row2:
                                        user_uuid = str(row2[0])
                                        logger.debug("Found user_uuid via direct telegram_id lookup",
                                                   channel_id=channel_id,
                                                   user_uuid=user_uuid,
                                                   telegram_id=user_id)
                            except Exception as e:
                                logger.warning(
                                    "Failed to get user UUID for albums",
                                    channel_id=channel_id,
                                    telegram_id=user_id,
                                    error=str(e),
                                    error_type=type(e).__name__,
                                    exc_info=True
                                )
                            
                            if not user_uuid:
                                logger.error(
                                    "User UUID not found for channel/telegram_id, skipping album processing",
                                    channel_id=channel_id,
                                    telegram_id=user_id,
                                    grouped_ids=list(grouped_ids_in_batch)[:3]
                                )
                                # Context7: Пробуем найти user_id по telegram_id напрямую
                                try:
                                    result3 = await self.db_session.execute(
                                        text("SELECT id::text FROM users WHERE telegram_id = :telegram_id LIMIT 1"),
                                        {"telegram_id": int(user_id) if user_id else None}
                                    )
                                    row3 = result3.fetchone()
                                    if row3:
                                        user_uuid = str(row3[0])
                                        logger.info("Found user UUID via direct telegram_id lookup",
                                                  user_uuid=user_uuid, telegram_id=user_id)
                                except Exception as e:
                                    logger.warning("Failed direct telegram_id lookup", error=str(e))
                            
                            if not user_uuid:
                                # Context7: КРИТИЧНО - пытаемся создать user_channel прямо сейчас
                                logger.warning(
                                    "User UUID not found - attempting to create user_channel",
                                    channel_id=channel_id,
                                    telegram_id=user_id,
                                    grouped_ids_count=len(grouped_ids_in_batch)
                                )
                                
                                # Context7: Создаём user_channel через atomic_saver
                                try:
                                    # Context7 P2: Получение first_name из Telegram API (если доступен telegram_client)
                                    user_first_name = ''
                                    user_last_name = ''
                                    user_username = ''
                                    
                                    # Context7 P2: Получаем first_name из Telegram API через telegram_client (передается как параметр)
                                    if telegram_client and user_id:
                                        try:
                                            user_entity = await telegram_client.get_entity(int(user_id))
                                            if user_entity:
                                                user_first_name = getattr(user_entity, 'first_name', '') or ''
                                                user_last_name = getattr(user_entity, 'last_name', '') or ''
                                                user_username = getattr(user_entity, 'username', '') or ''
                                                
                                                logger.debug(
                                                    "User info retrieved from Telegram API (for albums)",
                                                    user_id=user_id,
                                                    first_name=user_first_name[:50] if user_first_name else None,
                                                    username=user_username
                                                )
                                        except Exception as e:
                                            logger.debug(
                                                "Failed to get user entity from Telegram API (for albums)",
                                                user_id=user_id,
                                                error=str(e)
                                            )
                                            # Продолжаем с пустыми значениями - не критично
                                    
                                    user_data = {
                                        'telegram_id': user_id,
                                        'tenant_id': tenant_id,
                                        'first_name': user_first_name,
                                        'last_name': user_last_name,
                                        'username': user_username
                                    }
                                    
                                    channel_title = channel_entity.title if channel_entity and hasattr(channel_entity, 'title') else ''
                                    channel_username = channel_entity.username if channel_entity and hasattr(channel_entity, 'username') else ''
                                    
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
                                        'id': channel_id,
                                        'telegram_id': tg_channel_id,
                                        'title': channel_title,
                                        'username': channel_username
                                    }
                                    
                                    # Context7: НЕ создаем user_channel автоматически при парсинге!
                                    # Подписки должны создаваться только при явном запросе пользователя через API
                                    # Проверяем, подписан ли пользователь на канал
                                    check_subscription = await self.db_session.execute(
                                        text("""
                                            SELECT user_id FROM user_channel 
                                            WHERE user_id = (SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1)
                                              AND channel_id = :channel_id
                                              AND is_active = true
                                            LIMIT 1
                                        """),
                                        {"telegram_id": int(user_id) if isinstance(user_id, str) else user_id, "channel_id": channel_id}
                                    )
                                    
                                    if not check_subscription.fetchone():
                                        # Пользователь не подписан или подписка неактивна - НЕ создаем подписку автоматически
                                        logger.warning("User not subscribed to channel or subscription inactive, cannot process albums",
                                                     channel_id=channel_id,
                                                     telegram_id=user_id)
                                        user_uuid = None
                                    else:
                                        # Пользователь подписан - создаем user если нужно
                                        await self.atomic_saver._upsert_user(self.db_session, user_data)
                                        channel_id_uuid = await self.atomic_saver._upsert_channel(self.db_session, channel_data)
                                        
                                        # Повторно пытаемся получить user_uuid
                                    result3 = await self.db_session.execute(
                                        text("SELECT id::text FROM users WHERE telegram_id = :telegram_id LIMIT 1"),
                                        {"telegram_id": int(user_id) if user_id else None}
                                    )
                                    row3 = result3.fetchone()
                                    if row3:
                                        user_uuid = str(row3[0])
                                        logger.info("Created user_channel and found user UUID",
                                                  user_uuid=user_uuid,
                                                  telegram_id=user_id)
                                except Exception as e:
                                    logger.error("Failed to create user_channel for albums",
                                               channel_id=channel_id,
                                               error=str(e),
                                               exc_info=True)
                            
                            if not user_uuid:
                                logger.error(
                                    "CRITICAL: Cannot save albums - user UUID not found after creation attempt",
                                    channel_id=channel_id,
                                    telegram_id=user_id,
                                    grouped_ids_count=len(grouped_ids_in_batch)
                                )
                            else:
                                # Context7: Для каждого grouped_id из текущего batch собираем альбом из БД
                                logger.info("Processing albums for user",
                                           user_uuid=user_uuid,
                                           grouped_ids_count=len(grouped_ids_in_batch),
                                           channel_id=channel_id)
                                for grouped_id in grouped_ids_in_batch:
                                    try:
                                        # Context7: Проверяем, есть ли уже альбом в media_groups (идемпотентность)
                                        existing_album = await self.db_session.execute(
                                            text("""
                                                SELECT id FROM media_groups 
                                                WHERE user_id = :user_id 
                                                  AND channel_id = :channel_id 
                                                  AND grouped_id = :grouped_id
                                                LIMIT 1
                                            """),
                                            {"user_id": user_uuid, "channel_id": channel_id, "grouped_id": grouped_id}
                                        )
                                        if existing_album.fetchone():
                                            # Альбом уже существует, пропускаем
                                            logger.debug(
                                                "Album already exists in media_groups, skipping",
                                                grouped_id=grouped_id,
                                                channel_id=channel_id
                                            )
                                            continue
                                        
                                        # Context7: Получаем ВСЕ посты с этим grouped_id из БД (не только из текущего batch)
                                        posts_result = await self.db_session.execute(
                                            text("""
                                                SELECT 
                                                    p.id,
                                                    p.content,
                                                    p.posted_at,
                                                    p.telegram_message_id,
                                                    COUNT(DISTINCT pm.file_sha256) as media_count
                                                FROM posts p
                                                LEFT JOIN post_media_map pm ON pm.post_id = p.id
                                                WHERE p.channel_id = :channel_id
                                                  AND p.grouped_id = :grouped_id
                                                GROUP BY p.id, p.content, p.posted_at, p.telegram_message_id
                                                ORDER BY p.telegram_message_id ASC
                                            """),
                                            {"channel_id": channel_id, "grouped_id": grouped_id}
                                        )
                                        
                                        album_posts = posts_result.fetchall()
                                        
                                        if len(album_posts) <= 1:
                                            # Одиночное медиа, не альбом
                                            logger.debug(
                                                "Skipping single post (not an album)",
                                                grouped_id=grouped_id,
                                                posts_count=len(album_posts)
                                            )
                                            continue
                                        
                                        # Context7: Собираем данные альбома
                                        actual_post_ids = [str(row.id) for row in album_posts]
                                        
                                        # Получаем медиа информацию из post_media_map и media_objects
                                        # Context7: Сортируем по telegram_message_id для сохранения порядка альбома
                                        # Context7 best practice: asyncpg требует правильный синтаксис для массивов
                                        # Используем CAST как в atomic_db_saver.py для text[], но для uuid[]
                                        media_result = await self.db_session.execute(
                                            text("""
                                                SELECT 
                                                    pm.post_id,
                                                    mo.mime as mime_type,
                                                    mo.file_sha256,
                                                    mo.size_bytes,
                                                    p.telegram_message_id
                                                FROM post_media_map pm
                                                JOIN media_objects mo ON mo.file_sha256 = pm.file_sha256
                                                JOIN posts p ON p.id = pm.post_id
                                                WHERE pm.post_id = ANY(CAST(:post_ids AS uuid[]))
                                                ORDER BY p.telegram_message_id ASC, pm.post_id
                                            """),
                                            {"post_ids": actual_post_ids}
                                        )
                                        
                                        media_rows = media_result.fetchall()
                                        
                                        # Группируем медиа по post_id и сортируем по порядку постов
                                        media_by_post = {}
                                        for row in media_rows:
                                            post_id_str = str(row.post_id)
                                            if post_id_str not in media_by_post:
                                                media_by_post[post_id_str] = []
                                            media_by_post[post_id_str].append({
                                                'mime_type': row.mime_type,
                                                'sha256': row.file_sha256,
                                                'size_bytes': row.size_bytes
                                            })
                                        
                                        # Context7: Собираем media_types, media_sha256s, media_bytes в порядке постов
                                        # КРИТИЧНО: Каждый пост должен иметь ОДИН элемент в каждом массиве
                                        # Если у поста несколько медиа, берем первое (основное)
                                        media_types = []
                                        media_sha256s = []
                                        media_bytes = []
                                        media_kinds = []
                                        
                                        for post_row in album_posts:
                                            post_id_str = str(post_row.id)
                                            if post_id_str in media_by_post and media_by_post[post_id_str]:
                                                # Берем первое медиа из поста (основное)
                                                media = media_by_post[post_id_str][0]
                                                mime = media['mime_type'] or ''
                                                media_type = (
                                                    'photo' if 'image' in mime else
                                                    'video' if 'video' in mime else
                                                    'document'
                                                )
                                                media_types.append(media_type)
                                                media_sha256s.append(media['sha256'])
                                                media_bytes.append(media['size_bytes'])
                                                # Определяем media_kind (photo/video/document/audio)
                                                media_kind = media_type if media_type in ['photo', 'video', 'document', 'audio'] else None
                                                media_kinds.append(media_kind)
                                            else:
                                                # Пост без медиа в post_media_map - проверяем текущий batch
                                                # Context7: Медиа может быть в текущем batch, но еще не сохранено в post_media_map
                                                found_in_batch = False
                                                for pd in posts_data:
                                                    if str(pd.get('id')) == post_id_str and pd.get('media_files'):
                                                        # Нашли медиа в текущем batch - используем его
                                                        mf = pd['media_files'][0]  # Берем первое медиа
                                                        mime = mf.mime_type or ''
                                                        media_type = (
                                                            'photo' if 'image' in mime else
                                                            'video' if 'video' in mime else
                                                            'document'
                                                        )
                                                        media_types.append(media_type)
                                                        media_sha256s.append(mf.sha256)
                                                        media_bytes.append(mf.size_bytes)
                                                        media_kinds.append(media_type if media_type in ['photo', 'video', 'document', 'audio'] else None)
                                                        found_in_batch = True
                                                        logger.info(
                                                            "Found media in batch for album post",
                                                            post_id=post_id_str,
                                                            grouped_id=grouped_id,
                                                            sha256=mf.sha256[:16] if mf.sha256 else None,
                                                            mime_type=mime
                                                        )
                                                        break
                                                
                                                if not found_in_batch:
                                                    # Медиа не найдено ни в БД, ни в batch - используем значения по умолчанию
                                                    logger.warning(
                                                        "Post in album has no media anywhere, using defaults",
                                                        post_id=post_id_str,
                                                        grouped_id=grouped_id,
                                                        channel_id=channel_id,
                                                        telegram_message_id=post_row.telegram_message_id
                                                    )
                                                    media_types.append('photo')  # Fallback
                                                    media_sha256s.append(None)
                                                    media_bytes.append(None)
                                                    media_kinds.append('photo')
                                        
                                        # Context7: Проверяем соответствие длин массивов
                                        if len(media_types) != len(actual_post_ids):
                                            logger.error(
                                                "Mismatch between post_ids and media_types lengths",
                                                grouped_id=grouped_id,
                                                post_ids_count=len(actual_post_ids),
                                                media_types_count=len(media_types),
                                                channel_id=channel_id
                                            )
                                            # Пропускаем этот альбом - не можем сохранить с несоответствием
                                            continue
                                        
                                        # Получаем caption_text и posted_at из первого поста
                                        first_post = album_posts[0]
                                        caption_text = first_post.content if first_post.content else None
                                        posted_at = first_post.posted_at
                                        
                                        # Context7: Сохраняем альбом с проверенными массивами
                                        group_id = await save_media_group(
                                            db_session=self.db_session,
                                            user_id=user_uuid,
                                            channel_id=channel_id,
                                            grouped_id=grouped_id,
                                            post_ids=actual_post_ids,
                                            media_types=media_types,  # Уже правильной длины
                                            media_sha256s=media_sha256s if media_sha256s and any(m for m in media_sha256s if m) else None,
                                            media_bytes=media_bytes if media_bytes and any(m for m in media_bytes if m) else None,
                                            caption_text=caption_text,
                                            posted_at=posted_at,
                                            media_kinds=media_kinds,  # Уже правильной длины
                                            trace_id=f"{tenant_id}:{channel_id}:{grouped_id}",
                                            tenant_id=tenant_id,
                                            event_publisher=self.event_publisher,
                                            redis_client=self.redis_client
                                        )
                                        
                                        if group_id:
                                            # Context7: Детальное логирование для мониторинга альбомов
                                            posts_from_current_batch = len([p for p in posts_data if p.get('grouped_id') == grouped_id])
                                            media_with_sha256 = len([s for s in media_sha256s if s]) if media_sha256s else 0
                                            # Определяем album_kind из media_types
                                            unique_types = set(media_types) if media_types else set()
                                            album_kind_value = media_types[0] if len(unique_types) == 1 and media_types else "mixed" if len(unique_types) > 1 else None
                                            logger.info(
                                                "Media group saved to DB",
                                                group_id=group_id,
                                                grouped_id=grouped_id,
                                                items_count=len(actual_post_ids),
                                                posts_from_current_batch=posts_from_current_batch,
                                                media_with_sha256=media_with_sha256,
                                                media_without_sha256=len(actual_post_ids) - media_with_sha256,
                                                album_kind=album_kind_value,
                                                channel_id=channel_id
                                            )
                                            
                                            # Context7: Эмиссия Vision события для альбома целиком
                                            if self.media_processor and media_sha256s and any(m for m in media_sha256s if m):
                                                try:
                                                    # Собираем все MediaFile из альбома из posts_data
                                                    album_media_files = []
                                                    for post_id_str in actual_post_ids:
                                                        for pd in posts_data:
                                                            if str(pd.get('id')) == post_id_str and pd.get('media_files'):
                                                                # Добавляем все медиа файлы из поста
                                                                for mf in pd['media_files']:
                                                                    if mf.sha256 in media_sha256s:
                                                                        album_media_files.append(mf)
                                                                    elif not media_sha256s:  # Если media_sha256s None, добавляем все
                                                                        album_media_files.append(mf)
                                                    
                                                    # Если не нашли в posts_data, пытаемся получить из БД через media_objects
                                                    if not album_media_files and media_sha256s:
                                                        try:
                                                            from worker.events.schemas.posts_vision_v1 import MediaFile
                                                            media_objects_result = await self.db_session.execute(
                                                                text("""
                                                                    SELECT mo.file_sha256, mo.s3_key, mo.mime, mo.size_bytes
                                                                    FROM media_objects mo
                                                                    WHERE mo.file_sha256 = ANY(CAST(:sha256_list AS text[]))
                                                                    ORDER BY array_position(CAST(:sha256_list AS text[]), mo.file_sha256)
                                                                """),
                                                                {"sha256_list": [s for s in media_sha256s if s]}
                                                            )
                                                            media_rows = media_objects_result.fetchall()
                                                            for row in media_rows:
                                                                album_media_files.append(MediaFile(
                                                                    sha256=row.file_sha256,
                                                                    s3_key=row.s3_key,
                                                                    mime_type=row.mime,
                                                                    size_bytes=row.size_bytes
                                                                ))
                                                        except Exception as db_error:
                                                            logger.warning(
                                                                "Failed to fetch media files from DB for album Vision event",
                                                                grouped_id=grouped_id,
                                                                error=str(db_error)
                                                            )
                                                    
                                                    # Эмитим Vision событие для альбома, если есть подходящие медиа
                                                    if album_media_files:
                                                        await self.media_processor.emit_vision_uploaded_event(
                                                            post_id=f"album:{grouped_id}",  # Используем grouped_id для идентификации альбома
                                                            tenant_id=tenant_id,
                                                            media_files=album_media_files,
                                                            trace_id=f"{tenant_id}:{channel_id}:{grouped_id}:album_vision"
                                                        )
                                                        logger.info(
                                                            "Vision uploaded event emitted for album",
                                                            grouped_id=grouped_id,
                                                            group_id=group_id,
                                                            media_count=len(album_media_files),
                                                            channel_id=channel_id
                                                        )
                                                except Exception as vision_error:
                                                    logger.warning(
                                                        "Failed to emit Vision event for album",
                                                        grouped_id=grouped_id,
                                                        error=str(vision_error),
                                                        exc_info=True
                                                    )
                                        else:
                                            logger.warning(
                                                "Failed to save media group (save_media_group returned None)",
                                                grouped_id=grouped_id,
                                                channel_id=channel_id,
                                                post_ids_count=len(actual_post_ids),
                                                media_types_count=len(media_types) if 'media_types' in locals() else 0
                                            )
                                    except Exception as e:
                                        # Context7: Детальное логирование ошибок сохранения альбомов
                                        logger.error(
                                            "Failed to save media group to DB",
                                            grouped_id=grouped_id,
                                            channel_id=channel_id,
                                            user_uuid=user_uuid if 'user_uuid' in locals() else None,
                                            post_ids_count=len(actual_post_ids) if 'actual_post_ids' in locals() else 0,
                                            media_types_count=len(media_types) if 'media_types' in locals() else 0,
                                            error=str(e),
                                            error_type=type(e).__name__,
                                            exc_info=True
                                        )
                                        # Context7: Метрика для отслеживания ошибок сохранения альбомов
                                        album_save_failures_total.labels(error_type=type(e).__name__).inc()
                                        # Context7: Не прерываем транзакцию для других альбомов
                                        # Каждый альбом обрабатывается независимо
                                        # Транзакция будет откачена автоматически при ошибке через context manager
                                        continue
                            # Транзакция для альбомов автоматически коммитится через context manager
                except ImportError as e:
                    logger.debug("media_group_saver not available", error=str(e))
                except Exception as e:
                    logger.warning("Failed to save albums to DB", error=str(e))
                    # Context7: Если была активна транзакция, откатываем
                    if self.db_session.in_transaction():
                        try:
                            await self.db_session.rollback()
                        except Exception as rollback_error:
                            logger.error("Failed to rollback after album save error", 
                                       error=str(rollback_error))
                
                # Context7: Сохранение деталей forwards/reactions/replies и медиа в CAS для каждого поста
                try:
                    from services.message_enricher import (
                        extract_forwards_details,
                        extract_reactions_details,
                        extract_replies_details
                    )
                    
                    # Context7: Используем сохранённый mapping для извлечения деталей
                    for message, post_data in message_to_post_mapping:
                        # Context7: Получаем реальный post_id из БД после вставки
                        # Используем channel_id + telegram_message_id для поиска
                        channel_id = post_data.get('channel_id')
                        telegram_message_id = post_data.get('telegram_message_id')
                        
                        if not channel_id or not telegram_message_id:
                            logger.warning("Missing channel_id or telegram_message_id for post",
                                         post_data=post_data)
                            continue
                        
                        # Получаем post_id из БД
                        try:
                            result = await self.db_session.execute(
                                text("SELECT id FROM posts WHERE channel_id = :channel_id AND telegram_message_id = :telegram_message_id"),
                                {"channel_id": channel_id, "telegram_message_id": telegram_message_id}
                            )
                            row = result.fetchone()
                            if not row:
                                logger.warning("Post not found in DB after insert",
                                             channel_id=channel_id,
                                             telegram_message_id=telegram_message_id)
                                continue
                            post_id = str(row.id)
                        except Exception as e:
                            logger.error("Failed to get post_id from DB",
                                       channel_id=channel_id,
                                       telegram_message_id=telegram_message_id,
                                       error=str(e))
                            continue
                        
                        try:
                            # Извлекаем детали из оригинального сообщения
                            forwards = extract_forwards_details(message)
                            reactions = extract_reactions_details(message)
                            replies = extract_replies_details(message, post_id)
                            
                            # Context7 P1: Извлечение reply-цепочек для каналов с комментариями
                            # Проверяем, есть ли у канала включённые комментарии (с кэшированием в Redis)
                            try:
                                if telegram_client and channel_entity:
                                    has_comments = await check_channel_has_comments(
                                        telegram_client,
                                        channel_entity,
                                        redis_client=self.redis_client  # Context7 P1: Передаём Redis для кэширования
                                    )
                                    if has_comments:
                                        # Извлекаем reply-цепочку через GetDiscussionMessage
                                        discussion_replies = await extract_reply_chain(
                                            telegram_client,
                                            channel_entity,
                                            message.id,
                                            max_depth=10,
                                            max_replies=100
                                        )
                                        
                                        # Объединяем replies из message.reply_to и discussion replies
                                        if discussion_replies:
                                            # Преобразуем discussion replies в формат для сохранения
                                            for disc_reply in discussion_replies:
                                                reply_data = {
                                                    'post_id': post_id,
                                                    'reply_to_post_id': None,
                                                    'reply_message_id': disc_reply.get('reply_message_id'),
                                                    'reply_chat_id': disc_reply.get('reply_chat_id'),
                                                    'reply_author_tg_id': disc_reply.get('reply_author_tg_id'),
                                                    'reply_author_username': disc_reply.get('reply_author_username'),
                                                    'reply_content': disc_reply.get('reply_content'),
                                                    'reply_posted_at': disc_reply.get('reply_posted_at'),
                                                    'thread_id': disc_reply.get('thread_id')
                                                }
                                                replies.append(reply_data)
                                            
                                            logger.debug("Extracted discussion replies",
                                                         post_id=post_id,
                                                         discussion_replies_count=len(discussion_replies))
                            except Exception as e:
                                logger.warning("Failed to extract discussion replies",
                                             post_id=post_id,
                                             error=str(e),
                                             exc_info=True)
                                # Не прерываем обработку - продолжаем с базовыми replies
                            
                            # Сохраняем forwards/reactions/replies в БД, если есть данные
                            if forwards or reactions or replies:
                                await self.atomic_saver.save_forwards_reactions_replies(
                                    db_session=self.db_session,
                                    post_id=post_id,
                                    forwards_data=forwards,
                                    reactions_data=reactions,
                                    replies_data=replies
                                )
                                logger.debug("Saved forwards/reactions/replies",
                                           post_id=post_id,
                                           forwards=len(forwards),
                                           reactions=len(reactions),
                                           replies=len(replies))
                            
                            # Context7: Сохранение медиа в CAS таблицы (media_objects + post_media_map)
                            # Выполняется для всех постов с медиа, независимо от наличия forwards/reactions/replies
                            media_files = post_data.get('media_files', [])
                            if media_files and self.media_processor:
                                try:
                                    s3_bucket = self.media_processor.s3_service.bucket_name
                                    await self.atomic_saver.save_media_to_cas(
                                        db_session=self.db_session,
                                        post_id=post_id,
                                        media_files=media_files,
                                        s3_bucket=s3_bucket,
                                        trace_id=trace_id
                                    )
                                    logger.info("Saved media to CAS",
                                               post_id=post_id,
                                               media_count=len(media_files),
                                               trace_id=trace_id)
                                except Exception as e:
                                    logger.warning(
                                        "Failed to save media to CAS",
                                        post_id=post_id,
                                        error=str(e),
                                        trace_id=trace_id,
                                        exc_info=True
                                    )
                                    # Не прерываем транзакцию
                            elif post_data.get('media_urls'):
                                # Context7: Логируем, почему медиа не сохраняется в CAS
                                logger.warning(
                                    "Media not saved to CAS - missing media_files or media_processor",
                                    post_id=post_id,
                                    has_media_files=bool(media_files),
                                    media_files_count=len(media_files) if media_files else 0,
                                    has_media_processor=bool(self.media_processor),
                                    has_media_urls=bool(post_data.get('media_urls')),
                                    trace_id=trace_id
                                )
                            
                            # Commit после сохранения всех данных для поста
                            await self.db_session.commit()
                            
                        except Exception as e:
                            await self.db_session.rollback()
                            logger.warning("Failed to save post details",
                                         post_id=post_id, error=str(e), exc_info=True)
                            # Не прерываем основной поток - продолжаем с следующим постом
                
                except ImportError as e:
                    logger.warning("message_enricher not available", error=str(e))
                except Exception as e:
                    logger.warning("Failed to save post details",
                                 error=str(e), exc_info=True)
                    # Не прерываем основной поток
                
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
                
                # Context7: Эмиссия VisionUploadedEventV1 для медиа файлов
                if self.media_processor:
                    for post_data in posts_data:
                        if post_data.get('media_files'):
                            try:
                                post_id = post_data.get('id')
                                media_files = post_data.get('media_files', [])
                                trace_id = post_data.get('idempotency_key', str(uuid.uuid4()))
                                
                                await self.media_processor.emit_vision_uploaded_event(
                                    post_id=post_id,
                                    tenant_id=tenant_id,
                                    media_files=media_files,
                                    trace_id=trace_id
                                )
                                
                                # Context7: Логируем на уровне INFO для мониторинга Vision пайплайна
                                vision_suitable_count = len([mf for mf in media_files if self.media_processor._is_vision_suitable(mf.mime_type)])
                                logger.info(
                                    "Vision uploaded event emitted",
                                    post_id=post_id,
                                    media_count=len(media_files),
                                    vision_suitable_count=vision_suitable_count,
                                    channel_id=channel_id,
                                    trace_id=trace_id
                                )
                            except Exception as e:
                                logger.warning(
                                    "Failed to emit vision uploaded event",
                                    post_id=post_data.get('id'),
                                    error=str(e),
                                    channel_id=channel_id,
                                    exc_info=True
                                )
                                # Продолжаем обработку даже при ошибке события
            else:
                # Context7: При ошибке сохранения processed остается 0
                processed = 0
                logger.error("Atomic batch save failed", 
                           channel_id=channel_id,
                           error=error,
                           posts_data_count=len(posts_data))
                self.stats['errors'] += 1
        
        # Context7: Возвращаем processed только после успешного сохранения
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
        
        # Context7: Генерация content_hash для идемпотентности
        content_hash = self._create_content_hash(text)
        
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
        
        # Context7: Извлечение grouped_id для поддержки альбомов
        grouped_id = getattr(message, 'grouped_id', None)
        
        # Context7 P1.1: Быстрые поля для forwards и replies в Post
        forward_from_peer_id = None
        forward_from_chat_id = None
        forward_from_message_id = None
        forward_date = None
        forward_from_name = None
        thread_id = None
        forum_topic_id = None
        
        # Context7 P2: Извлечение author_peer_id и author_type из message.from_id
        author_peer_id = None
        author_type = None
        post_author = None
        
        if hasattr(message, 'from_id') and message.from_id:
            from_id = message.from_id
            if hasattr(from_id, 'user_id'):
                author_peer_id = {'user_id': from_id.user_id}
                author_type = 'user'
                post_author = str(from_id.user_id)
            elif hasattr(from_id, 'channel_id'):
                author_peer_id = {'channel_id': from_id.channel_id}
                author_type = 'channel'
                post_author = f"channel_{from_id.channel_id}"
            elif hasattr(from_id, 'chat_id'):
                author_peer_id = {'chat_id': from_id.chat_id}
                author_type = 'chat'
                post_author = f"chat_{from_id.chat_id}"
        
        # Context7 P2: Попытка получить имя автора из message.sender (если доступно)
        # Используем message.sender как fallback для получения username/first_name
        if hasattr(message, 'sender') and message.sender:
            sender = message.sender
            # Определяем тип на основе sender
            if hasattr(sender, 'bot') and sender.bot:
                author_type = 'bot'
            elif author_type is None:
                # Если author_type ещё не определён, пытаемся определить по типу sender
                from telethon.tl.types import User, Channel, Chat
                if isinstance(sender, User):
                    author_type = 'bot' if getattr(sender, 'bot', False) else 'user'
                elif isinstance(sender, Channel):
                    author_type = 'channel'
                elif isinstance(sender, Chat):
                    author_type = 'chat'
            
            # Получаем имя автора
            if hasattr(sender, 'first_name'):
                if not post_author or post_author.startswith(('channel_', 'chat_', 'bot_')):
                    # Используем first_name только если post_author - это ID
                    post_author = sender.first_name
            elif hasattr(sender, 'username') and sender.username:
                if not post_author or post_author.startswith(('channel_', 'chat_', 'bot_')):
                    post_author = sender.username
            elif hasattr(sender, 'title'):
                # Для каналов/чатов используем title
                post_author = sender.title
        
        # Извлечение forwards (быстрые поля в Post)
        if hasattr(message, 'fwd_from') and message.fwd_from:
            fwd_from = message.fwd_from
            if hasattr(fwd_from, 'from_id') and fwd_from.from_id:
                from_id = fwd_from.from_id
                if hasattr(from_id, 'user_id'):
                    forward_from_peer_id = {'user_id': from_id.user_id}
                elif hasattr(from_id, 'channel_id'):
                    forward_from_peer_id = {'channel_id': from_id.channel_id}
                    forward_from_chat_id = from_id.channel_id
                elif hasattr(from_id, 'chat_id'):
                    forward_from_peer_id = {'chat_id': from_id.chat_id}
                    forward_from_chat_id = from_id.chat_id
            
            if hasattr(fwd_from, 'channel_post'):
                forward_from_message_id = fwd_from.channel_post
            
            if hasattr(fwd_from, 'date') and fwd_from.date:
                forward_date = fwd_from.date
            
            if hasattr(fwd_from, 'from_name'):
                forward_from_name = fwd_from.from_name
        
        # Извлечение thread_id для replies
        if hasattr(message, 'reply_to') and message.reply_to:
            reply_to = message.reply_to
            if hasattr(reply_to, 'reply_to_top_id'):
                thread_id = reply_to.reply_to_top_id
            elif hasattr(reply_to, 'reply_to_forum_top_id'):
                forum_topic_id = reply_to.reply_to_forum_top_id
        
        return {
            'id': post_id,
            'channel_id': channel_id,  # Context7: глобальные каналы без tenant_id
            'telegram_message_id': message.id if hasattr(message, 'id') else int(time.time() * 1000),
            'content': text,
            'media_urls': json.dumps(urls) if urls else '[]',  # [C7-ID: dev-mode-014] Context7: JSONB формат (строка JSON, не список)
            'urls': urls,  # Context7: Raw URLs список для events
            'content_hash': content_hash,  # Context7: Hash для идемпотентности
            'posted_at': posted_at,
            'created_at': datetime.now(timezone.utc),
            'is_processed': False,
            'has_media': bool(message.media if hasattr(message, 'media') else False),
            'yyyymm': yyyymm,
            'views_count': views_count,
            'forwards_count': forwards_count,
            'reactions_count': reactions_count,
            'replies_count': replies_count,
            'telegram_post_url': telegram_post_url,
            'grouped_id': grouped_id,  # Context7: ID альбома для дедупликации
            # Context7 P1.1: Быстрые поля для forwards и replies
            'forward_from_peer_id': forward_from_peer_id,
            'forward_from_chat_id': forward_from_chat_id,
            'forward_from_message_id': forward_from_message_id,
            'forward_date': forward_date,
            'forward_from_name': forward_from_name,
            'thread_id': thread_id,
            'forum_topic_id': forum_topic_id,
            # Context7 P2: Данные об авторе для Graph-RAG
            'author_peer_id': author_peer_id,
            'author_type': author_type,
            'post_author': post_author
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
        # Context7 P2: Расширяем событие данными о forwards/replies/author для Graph-RAG
        return dict(
            idempotency_key=idempotency_key,
            user_id=user_id,
            channel_id=channel_id,
            post_id=post_data['id'],
            tenant_id=tenant_id,
            text=post_data['content'] or "",
            urls=post_data.get('urls', []),
            posted_at=posted_at_iso,
            # Context7: Добавляем media_sha256_list для связи с обработанными медиа
            media_sha256_list=post_data.get('media_sha256_list', []),
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
            reactions_count=post_data.get('reactions_count', 0),
            # Context7 P2: Данные о forwards для Graph-RAG
            forward_from_peer_id=post_data.get('forward_from_peer_id'),
            forward_from_chat_id=post_data.get('forward_from_chat_id'),
            forward_from_message_id=post_data.get('forward_from_message_id'),
            forward_date=post_data.get('forward_date').isoformat() if post_data.get('forward_date') and hasattr(post_data.get('forward_date'), 'isoformat') else post_data.get('forward_date'),
            forward_from_name=post_data.get('forward_from_name'),
            # Context7 P2: Данные о replies для Graph-RAG
            reply_to_message_id=post_data.get('reply_to_message_id'),
            reply_to_chat_id=post_data.get('reply_to_chat_id'),
            thread_id=post_data.get('thread_id'),
            # Context7 P2: Данные об авторе для Graph-RAG (если доступны)
            author_peer_id=post_data.get('author_peer_id'),  # TODO: извлечь из message если доступно
            author_name=post_data.get('post_author'),  # Используем post_author как author_name
            author_type=post_data.get('author_type')  # TODO: определить тип автора
        )
    
    # Context7: Методы _bulk_insert_posts и _legacy_bulk_insert_posts удалены
    # Используется atomic_saver.save_batch_atomic напрямую в _process_message_batch
    
    async def _publish_parsed_events(self, events_data: List[Dict[str, Any]]):  # PostParsedEventV1 - temporarily disabled
        """Публикация событий post.parsed."""
        try:
            if not events_data:
                return
                
            # Context7: Если event_publisher=None, публикуем напрямую в Redis Streams
            if self.event_publisher is None:
                stream_key = "stream:posts:parsed"  # Context7: Unified stream naming
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
            # Context7: Проверяем состояние транзакции перед обновлением
            # Если транзакция прервана, делаем rollback и начинаем новую
            if self.db_session.in_transaction():
                try:
                    # Проверяем, не прервана ли транзакция
                    await self.db_session.execute(text("SELECT 1"))
                except Exception as check_error:
                    if "aborted" in str(check_error).lower() or "failed" in str(check_error).lower():
                        logger.warning("Transaction aborted, rolling back before last_parsed_at update",
                                     channel_id=channel_id, error=str(check_error))
                        await self.db_session.rollback()
            elif "aborted" in str(getattr(self.db_session, '_transaction', None) or "").lower():
                # Если транзакция была прервана, делаем rollback
                try:
                    await self.db_session.rollback()
                except Exception:
                    pass
            
            now = datetime.now(timezone.utc)
            
            # Context7: Используем отдельную транзакцию для обновления last_parsed_at
            async with self.db_session.begin():
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
                    # Коммит выполняется автоматически через context manager
            
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
    
    async def _monitor_missing_posts(self, channel_id: str):
        """
        Context7: [C7-ID: monitoring-missing-posts-002] Мониторинг пропусков постов с адаптивными порогами.
        
        Сравнивает текущее время с реальным временем последнего поста (MAX(posted_at))
        и использует адаптивный порог на основе статистики канала.
        Обновляет метрики для наблюдаемости.
        
        Args:
            channel_id: ID канала в БД
        """
        try:
            # Импортируем метрики из scheduler task
            # Используем try/except, так как метрики могут быть недоступны
            try:
                from tasks.parse_all_channels_task import (
                    channel_gap_seconds,
                    adaptive_threshold_seconds,
                    channel_last_post_timestamp_seconds,
                    parser_last_success_seconds
                )
            except ImportError:
                # Метрики недоступны в этом контексте, пропускаем
                return
            
            now = datetime.now(timezone.utc)
            
            # Получаем High Watermark (реальное время последнего поста)
            last_post_date = await self._get_high_watermark(channel_id)
            
            if not last_post_date:
                # Нет постов в БД, это нормально для новых каналов
                return
            
            # Вычисляем gap: время с момента последнего поста (всегда >= 0)
            gap_seconds = max(0, int((now - last_post_date).total_seconds()))
            
            # Получаем адаптивный порог
            threshold_seconds = await self._compute_adaptive_threshold(channel_id)
            
            # Обновляем метрики
            channel_gap_seconds.labels(channel_id=channel_id).set(gap_seconds)
            adaptive_threshold_seconds.labels(channel_id=channel_id).set(threshold_seconds)
            channel_last_post_timestamp_seconds.labels(channel_id=channel_id).set(
                int(last_post_date.timestamp())
            )
            
            # Получаем last_parsed_at для метрики parser_last_success_seconds
            result = await self.db_session.execute(
                text("SELECT last_parsed_at FROM channels WHERE id = :channel_id"),
                {"channel_id": channel_id}
            )
            row = result.fetchone()
            if row and row.last_parsed_at:
                last_parsed_utc = ensure_dt_utc(row.last_parsed_at)
                if last_parsed_utc:
                    parser_last_success_seconds.labels(channel_id=channel_id).set(
                        int(last_parsed_utc.timestamp())
                    )
            
            # Проверяем, превышен ли адаптивный порог
            if gap_seconds > threshold_seconds:
                # Определяем контекст времени
                is_quiet, quiet_reason = self._is_quiet_hours(now)
                gap_hours = gap_seconds / 3600
                threshold_hours = threshold_seconds / 3600
                
                logger.warning(
                    "Potential missing posts detected",
                    channel_id=channel_id,
                    last_post_date=last_post_date.isoformat(),
                    gap_seconds=gap_seconds,
                    gap_hours=gap_hours,
                    threshold_seconds=threshold_seconds,
                    threshold_hours=threshold_hours,
                    context=quiet_reason,
                    is_quiet=is_quiet
                )
            elif gap_seconds > 3600:
                # Gap больше часа, но не превышает адаптивный порог - это нормально для quiet hours
                is_quiet, quiet_reason = self._is_quiet_hours(now)
                logger.debug(
                    "Gap within acceptable threshold",
                    channel_id=channel_id,
                    gap_seconds=gap_seconds,
                    threshold_seconds=threshold_seconds,
                    context=quiet_reason
                )
            
        except Exception as e:
            # Не критичная ошибка, логируем и продолжаем
            logger.warning("Failed to monitor missing posts",
                         channel_id=channel_id,
                         error=str(e))
    
    async def _update_channel_stats(self, channel_id: str, messages_count: int):
        """
        Обновление статистики канала.
        Context7 best practice: Используем отдельную транзакцию для обновления статистики.
        """
        try:
            # Context7: Проверяем состояние транзакции
            if self.db_session.in_transaction():
                try:
                    await self.db_session.execute(text("SELECT 1"))
                except Exception as check_error:
                    if "aborted" in str(check_error).lower() or "failed" in str(check_error).lower():
                        logger.warning("Transaction aborted, rolling back before channel stats update",
                                     channel_id=channel_id, error=str(check_error))
                        await self.db_session.rollback()
            
            # Context7: Используем отдельную транзакцию для обновления статистики
            # Context7: Проверяем состояние транзакции перед началом новой
            if self.db_session.in_transaction():
                await self.db_session.rollback()
            async with self.db_session.begin():
                await self.db_session.execute(
                    text("""
                        UPDATE channels 
                        SET last_message_at = NOW()
                        WHERE id = :channel_id
                    """),
                    {"channel_id": channel_id}
                )
                # Коммит выполняется автоматически через context manager
            
        except Exception as e:
            logger.error("Failed to update channel stats", 
                        channel_id=channel_id,
                        error=str(e),
                        exc_info=True)
            # Context7: Если была активна транзакция, откатываем
            if self.db_session.in_transaction():
                try:
                    await self.db_session.rollback()
                except Exception as rollback_error:
                    logger.error("Failed to rollback after channel stats update error",
                               channel_id=channel_id,
                               error=str(rollback_error))
    
    async def handle_flood_wait(self, error: errors.FloodWaitError, channel_id: str):
        """Обработка FloodWait ошибки с установкой blocked_until в БД.
        
        Context7: Вместо sleep обновляем blocked_until в БД, чтобы канал был пропущен
        в следующих тиках до истечения cooldown периода.
        
        Args:
            error: FloodWaitError из Telethon
            channel_id: ID канала в БД
        """
        wait_time = min(error.seconds, self.config.max_flood_wait)
        blocked_until = datetime.now(timezone.utc) + timedelta(seconds=wait_time)
        
        # Context7: Обновляем blocked_until в БД вместо sleep
        try:
            if self.db_session.in_transaction():
                await self.db_session.rollback()
            
            async with self.db_session.begin():
                await self.db_session.execute(
                    text("""
                        UPDATE channels 
                        SET blocked_until = :blocked_until 
                        WHERE id = :channel_id
                    """),
                    {"blocked_until": blocked_until, "channel_id": channel_id}
                )
            
            logger.warning(
                "Channel blocked due to FloodWait",
                channel_id=channel_id,
                wait_seconds=wait_time,
                blocked_until=blocked_until.isoformat()
            )
        except Exception as e:
            logger.error("Failed to set blocked_until for channel",
                        channel_id=channel_id,
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True)
            # Fallback: используем sleep если не удалось обновить БД
            await asyncio.sleep(wait_time)
        
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
    # Используем DATABASE_URL из окружения (пример)
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL must be set")
    engine = create_async_engine(db_url)
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
