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
from telethon.tl.types import Channel
from telethon.tl.types import Message, Channel, Chat
import structlog

# Context7: Импорт новых компонентов
from .telethon_retry import fetch_messages_with_retry, is_channel_in_cooldown
from .atomic_db_saver import AtomicDBSaver
from .rate_limiter import RateLimiter, check_parsing_rate_limit
from .session_rate_limiter import SessionRateLimiter
from .discussion_extractor import (
    get_discussion_message,
    extract_reply_chain,
    check_channel_has_comments
)
from utils.time_utils import ensure_dt_utc
from prometheus_client import Counter, Gauge

# WORKER IMPORT DISABLED - will be restored when worker module is available
# from worker.event_bus import EventPublisher, PostParsedEvent
# from worker.events.schemas.posts_parsed_v1 import PostParsedEventV1

logger = structlog.get_logger()

# Context7: Метрики для отслеживания проблем с парсингом
# Context7: Используем проверку на существование метрики для предотвращения дублирования
from prometheus_client import REGISTRY

def _get_or_create_counter(name, description, labels, namespace=None):
    """Получить существующую метрику или создать новую."""
    try:
        # Пытаемся получить существующую метрику
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError):
        pass
    
    # Создаём новую метрику
    if namespace:
        return Counter(name, description, labels, namespace=namespace)
    return Counter(name, description, labels)

def _get_or_create_gauge(name, description, labels):
    """Получить существующую метрику Gauge или создать новую."""
    try:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError):
        pass
    
    return Gauge(name, description, labels)

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

# Context7: Метрики для отслеживания потерь постов и покрытия каналов
posts_lost_total = _get_or_create_counter(
    'posts_lost_total',
    'Total posts lost (not saved)',
    ['reason']  # duplicate, subscription, filter, error
)

posts_skipped_duplicate_total = _get_or_create_counter(
    'posts_skipped_duplicate_total',
    'Total posts skipped as duplicates',
    []  # Без labels для контроля кардинальности
)

posts_skipped_subscription_total = _get_or_create_counter(
    'posts_skipped_subscription_total',
    'Total posts skipped due to subscription issues',
    []  # Без labels для контроля кардинальности
)

# Context7: Метрика покрытия каналов (процент сохраненных постов)
# Используем Gauge для текущего значения покрытия
# Кардинальность контролируется: метрика обновляется периодически, не на каждое событие
channel_coverage_percent = _get_or_create_gauge(
    'channel_coverage_percent',
    'Channel posts coverage percentage (actual/expected * 100)',
    ['channel_username']  # Используем username вместо channel_id для контроля кардинальности
)

# Context7: Метрики для резолва каналов
channel_resolve_attempts_total = _get_or_create_counter(
    'channel_resolve_attempts_total',
    'Channel resolution attempts',
    ['method', 'result']  # method: 'id'|'username', result: 'ok'|'not_found'|'floodwait'|'no_access'|'invalid'
)

channels_skipped_due_to_global_floodwait_total = _get_or_create_counter(
    'channels_skipped_due_to_global_floodwait_total',
    'Channels skipped due to global FloodWait',
    []  # Без labels для контроля кардинальности
)

# Context7: Метрики для переключений сессий и подписок collector
session_switch_total = _get_or_create_counter(
    'session_switch_total',
    'Session switches during channel resolution',
    ['from_account', 'to_account', 'reason'],  # reason: 'floodwait', 'no_access'
    namespace='telethon'
)

resolution_aborted_total = _get_or_create_counter(
    'resolution_aborted_total',
    'Resolution passes aborted due to policy',
    ['context', 'reason'],  # context: 'operational', 'repair', reason: 'large_floodwait', 'max_attempts'
    namespace='telethon'
)

collector_subscriptions_total = _get_or_create_counter(
    'collector_subscriptions_total',
    'Collector subscription attempts',
    ['status'],  # status: 'subscribed', 'failed', 'private'
    namespace='telethon'
)

collector_subscription_attempts_total = _get_or_create_counter(
    'collector_subscription_attempts_total',
    'Total collector subscription attempts',
    [],
    namespace='telethon'
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
    max_messages_per_batch: int = int(os.getenv("PARSER_MAX_MESSAGES_PER_BATCH", "50"))
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
        media_processor: Optional[Any] = None,  # MediaProcessor для обработки медиа
        floodwait_manager: Optional[Any] = None,  # Context7: FloodWaitManager для глобального circuit breaker
        session_rate_limiter: Optional[SessionRateLimiter] = None,  # Context7: Per-session rate limiter
        ingest_account_pool: Optional[Any] = None  # Context7: IngestAccountPool для управления пулом аккаунтов
    ):
        self.config = config
        self.db_session = db_session
        self.event_publisher = event_publisher
        
        # Context7: InputPeerChannel для прямого использования в чтении сообщений (снижает лишние запросы)
        self._current_input_peer = None
        
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
        self.floodwait_manager = floodwait_manager  # Context7: FloodWaitManager для глобального circuit breaker
        self.session_rate_limiter = session_rate_limiter  # Context7: Per-session rate limiter
        self.ingest_account_pool = ingest_account_pool  # Context7: IngestAccountPool для управления пулом аккаунтов
        
        # Context7: Инициализация SessionRateLimiter если не передан
        if not self.session_rate_limiter and self.redis_client:
            self.session_rate_limiter = SessionRateLimiter(self.redis_client)
        
        # Context7: Инициализация IngestAccountPool если не передан
        if not self.ingest_account_pool and self.db_session and self.redis_client:
            from services.ingest_account_pool import IngestAccountPool
            self.ingest_account_pool = IngestAccountPool(self.db_session, self.redis_client)
        
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
                        # КРИТИЧНО: Проверяем и откатываем транзакцию перед обновлением
                        try:
                            if self.db_session.in_transaction():
                                await self.db_session.rollback()
                                logger.debug("Rolled back transaction before updating last_parsed_at after cooldown",
                                           channel_id=channel_id)
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
            
            # Context7: Детальное логирование для диагностики messages_processed: 0
            last_parsed_at = channel_data.get('last_parsed_at')
            logger.info(
                "Starting channel parsing",
                channel_id=channel_id,
                channel_title=channel_entity.title,
                mode=mode,
                since_date=since_date.isoformat() if since_date else None,
                last_parsed_at=last_parsed_at.isoformat() if last_parsed_at else None,
                is_new_channel=last_parsed_at is None,
                tg_channel_id=tg_channel_id
            )
            
            # Парсинг сообщений батчами
            messages_processed = 0
            batch_count = 0
            has_successful_save = False  # Context7: Отслеживаем успешное сохранение хотя бы одного батча
            
            # Context7: Логирование начала парсинга батчей
            logger.debug("Starting message batch processing",
                        channel_id=channel_id,
                        mode=mode,
                        since_date=since_date.isoformat() if since_date else None)
            
            # Context7: КРИТИЧНО - восстанавливаем _current_input_peer перед использованием
            # Он был сохранен после _resolve_channel_entity, но очищен в finally блоке
            # Context7 best practice: используем get_input_entity если InputPeerChannel недоступен
            if 'saved_input_peer' in locals() and saved_input_peer:
                self._current_input_peer = saved_input_peer
                logger.debug("Restored InputPeerChannel for message fetch",
                           channel_id=channel_id,
                           has_input_peer=bool(saved_input_peer))
            elif not self._current_input_peer:
                # Context7: Fallback - используем get_input_entity для получения InputPeerChannel
                # Это гарантирует, что мы используем актуальный access_hash
                try:
                    from telethon.tl.types import InputPeerChannel
                    input_entity = await telegram_client.get_input_entity(channel_entity)
                    if isinstance(input_entity, InputPeerChannel):
                        self._current_input_peer = input_entity
                        logger.debug("Created InputPeerChannel via get_input_entity fallback",
                                   channel_id=channel_id,
                                   input_channel_id=input_entity.channel_id,
                                   has_access_hash=bool(input_entity.access_hash))
                except Exception as e:
                    logger.warning("Failed to get input_entity for channel",
                                 channel_id=channel_id,
                                 error=str(e))
            
            async for message_batch in self._get_message_batches(
                telegram_client, channel_entity, since_date, mode, channel_id
            ):
                batch_count += 1
                
                # Context7: Логирование размера батча для диагностики
                logger.debug("Processing message batch",
                           channel_id=channel_id,
                           batch_number=batch_count,
                           batch_size=len(message_batch),
                           mode=mode)
                
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
                
                # Context7: Логирование результата батча для диагностики
                logger.debug("Batch processing result",
                           channel_id=channel_id,
                           batch_number=batch_count,
                           batch_processed=batch_processed,
                           batch_skipped=batch_skipped,
                           total_processed=messages_processed)
                
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
            
            # Context7: Логирование завершения парсинга батчей
            logger.info("Finished message batch processing",
                       channel_id=channel_id,
                       total_batches=batch_count,
                       total_processed=messages_processed,
                       has_successful_save=has_successful_save)
            
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
            
            # Context7: Специальное логирование для проблемных каналов
            await self._log_problematic_channel_stats(
                channel_id=channel_id,
                channel_entity=channel_entity,
                messages_processed=messages_processed,
                messages_skipped=self.stats.get('messages_skipped', 0),
                batch_count=batch_count,
                processing_time=processing_time,
                mode=mode
            )
            
            logger.info("Channel parsing completed", **result)
            return result
            
        except Exception as e:
            self.stats['errors'] += 1
            logger.error("Channel parsing failed", 
                        channel_id=channel_id, mode=mode, error=str(e))
            raise
    
    async def _select_session_for_channel(
        self,
        channel_id: str,
        preferred_account_id: Optional[int],
        context: str = 'operational',
        max_fallback_sessions: int = 2,
        task_type: str = 'read'
    ) -> Optional[tuple[TelegramClient, int, Optional[Any]]]:
        """
        Выбор сессии для резолва канала с использованием пула сервисных аккаунтов.
        
        Context7: Использует IngestAccountPool для выбора аккаунтов из пула сервисных аккаунтов.
        Фильтрует по blocked_until и учитывает inflight из Redis.
        
        Args:
            channel_id: UUID канала в БД
            preferred_account_id: telegram_id предпочтительной сессии (legacy, для обратной совместимости)
            context: 'operational' (парсинг) или 'repair' (скрипт валидации)
            max_fallback_sessions: Максимальное количество альтернативных сессий
            task_type: Тип задачи ('read' или 'resolver')
        
        Returns:
            Tuple (TelegramClient, telegram_id, ingest_account_id) или None если нет доступных сессий
        """
        if not self.telegram_client_manager or not self.floodwait_manager:
            logger.warning("TelegramClientManager or FloodWaitManager not available")
            return None
        
        # Context7: Используем IngestAccountPool если доступен
        if self.ingest_account_pool:
            try:
                # Получаем preferred_ingest_account_id из БД (приоритет) или по telegram_id (fallback)
                preferred_ingest_account_id = None
                try:
                    result = await self.db_session.execute(
                        text("SELECT preferred_ingest_account_id FROM channels WHERE id = :channel_id"),
                        {"channel_id": channel_id}
                    )
                    row = result.fetchone()
                    if row and row[0]:
                        preferred_ingest_account_id = row[0]
                except Exception as e:
                    logger.debug("Failed to get preferred_ingest_account_id from DB", 
                               channel_id=channel_id, error=str(e))
                
                # Fallback: если preferred_ingest_account_id не найден, ищем по telegram_id
                if not preferred_ingest_account_id and preferred_account_id:
                    account = await self.ingest_account_pool.get_account_by_telegram_id(preferred_account_id)
                    if account:
                        preferred_ingest_account_id = account['id']
                
                # Выбираем аккаунт из пула
                result = await self.ingest_account_pool.select_account_for_channel(
                    channel_id=channel_id,
                    task_type=task_type,
                    preferred_account_id=preferred_ingest_account_id
                )
                
                if not result:
                    logger.warning("No available accounts from pool",
                                 channel_id=channel_id,
                                 task_type=task_type)
                    return None
                
                telegram_id, ingest_account_id = result
                
                # Проверяем healthy сессии через FloodWaitManager
                healthy_sessions = await self.floodwait_manager.get_healthy_sessions(
                    [telegram_id],
                    max_floodwait_seconds=60
                )
                
                if not healthy_sessions:
                    # Аккаунт в FloodWait - обновляем blocked_until
                    logger.warning("Selected account is in FloodWait",
                                 channel_id=channel_id,
                                 telegram_id=telegram_id,
                                 ingest_account_id=str(ingest_account_id))
                    # Освобождаем inflight
                    await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                    return None
                
                # Получаем клиент
                client = await self.telegram_client_manager.get_client(telegram_id)
                if not client:
                    logger.warning("Failed to get client for account",
                                 channel_id=channel_id,
                                 telegram_id=telegram_id)
                    # Освобождаем inflight
                    await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                    return None
                
                # Проверка per-session rate limit
                if self.session_rate_limiter:
                    if not await self.session_rate_limiter.acquire_session(telegram_id, timeout=10.0):
                        logger.debug("Session rate limit reached",
                                    account_id=telegram_id,
                                    channel_id=channel_id)
                        await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                        return None
                    
                    try:
                        # Джиттер перед запросом
                        await self.session_rate_limiter.wait_for_token(telegram_id, min_delay=2.0, max_delay=5.0)
                        # Записываем запрос для метрики
                        await self.session_rate_limiter.record_request(telegram_id)
                    except Exception as e:
                        logger.warning("Error in rate limiter, releasing",
                                     account_id=telegram_id,
                                     channel_id=channel_id,
                                     error=str(e))
                        await self.session_rate_limiter.release_session(telegram_id)
                        await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                        return None
                
                logger.debug("Selected account from pool",
                            channel_id=channel_id,
                            telegram_id=telegram_id,
                            ingest_account_id=str(ingest_account_id),
                            task_type=task_type)
                
                return (client, telegram_id, ingest_account_id)
                
            except Exception as e:
                logger.error("Failed to select account from pool",
                           channel_id=channel_id,
                           task_type=task_type,
                           error=str(e))
                return None
        
        # Fallback: старая логика (если пул недоступен)
        logger.warning("IngestAccountPool not available, using fallback logic")
        collector_id = int(os.getenv("COLLECTOR_TELEGRAM_ID", "8124731874"))
        
        # Формируем пул сессий: [preferred, collector]
        session_pool = []
        if preferred_account_id:
            session_pool.append(preferred_account_id)
        if collector_id not in session_pool:
            session_pool.append(collector_id)
        
        # Context7: Для repair - только preferred или collector, без других сессий
        if context == 'repair':
            max_fallback_sessions = 1
        
        # Фильтруем healthy сессии
        healthy_sessions = await self.floodwait_manager.get_healthy_sessions(
            session_pool,
            max_floodwait_seconds=60
        )
        
        if not healthy_sessions:
            logger.warning("No healthy sessions available for channel",
                          channel_id=channel_id,
                          context=context)
            return None
        
        # Пробуем первую доступную сессию
        account_id = healthy_sessions[0]
        
        # Проверка per-session rate limit
        if self.session_rate_limiter:
            if not await self.session_rate_limiter.acquire_session(account_id, timeout=10.0):
                logger.debug("Session rate limit reached",
                            account_id=account_id,
                            channel_id=channel_id)
                return None
            
            try:
                await self.session_rate_limiter.wait_for_token(account_id, min_delay=2.0, max_delay=5.0)
                client = await self.telegram_client_manager.get_client(account_id)
                if not client:
                    await self.session_rate_limiter.release_session(account_id)
                    return None
                await self.session_rate_limiter.record_request(account_id)
                return (client, account_id, None)
            except Exception as e:
                logger.warning("Error getting client, releasing semaphore",
                             account_id=account_id,
                             channel_id=channel_id,
                             error=str(e))
                await self.session_rate_limiter.release_session(account_id)
                return None
        else:
            client = await self.telegram_client_manager.get_client(account_id)
            if client:
                return (client, account_id, None)
        
        return None
        
        # Context7: Детальное логирование причины недоступности сессий
        healthy_count = len(healthy_sessions) if 'healthy_sessions' in locals() else 0
        logger.warning("no_available_sessions",
                      channel_id=channel_id,
                      context=context,
                      attempts=attempts,
                      healthy_sessions_count=healthy_count,
                      max_fallback_sessions=max_fallback_sessions,
                      session_pool_size=len(session_pool) if 'session_pool' in locals() else 0)
        return None
    
    async def _get_session_id(self, client: TelegramClient) -> str:
        """
        Получение идентификатора сессии Telegram для глобального circuit breaker.
        
        Context7: Использует telegram_id из авторизованной сессии или
        дефолтный идентификатор если telegram_id недоступен.
        """
        try:
            if client.is_connected() and await client.is_user_authorized():
                me = await client.get_me()
                if me and hasattr(me, 'id'):
                    return str(me.id)
        except Exception as e:
            logger.debug("Failed to get session_id from client", error=str(e))
        
        # Fallback: используем дефолтный идентификатор
        return "default"
    
    def _calculate_backoff(self, attempts: int) -> timedelta:
        """
        Вычисление экспоненциального backoff для повторных попыток резолва.
        
        Context7: Экспоненциальный backoff: 15min, 1h, 6h, 24h
        
        Args:
            attempts: Количество неудачных попыток
        
        Returns:
            timedelta для blocked_until
        """
        if attempts <= 1:
            return timedelta(minutes=15)
        elif attempts == 2:
            return timedelta(hours=1)
        elif attempts == 3:
            return timedelta(hours=6)
        else:
            return timedelta(hours=24)
    
    async def _resolve_channel_entity(
        self,
        client: TelegramClient,
        channel_id: str,
        tg_channel_id_db: Optional[int],
        username: Optional[str],
        title: str,
        context: str = 'operational'
    ) -> Optional[tuple[Channel, int]]:
        """
        Единая стратегия резолва канала с детерминированным порядком и поддержкой пула сессий.
        
        Context7: Детерминированный порядок резолва:
        1. tg_channel_id (если есть)
        2. username (если есть и tg_channel_id не сработал/отсутствует)
        3. hard-fail если нет идентификаторов
        
        После успешного резолва по username сохраняет tg_channel_id в БД.
        При ошибке entity_not_found использует экспоненциальный backoff.
        Поддерживает пул сессий с ограниченным fallback и защитой от каскадного FloodWait.
        
        Args:
            client: TelegramClient для резолва (может быть заменен через пул сессий)
            channel_id: UUID канала в БД
            tg_channel_id_db: tg_channel_id из БД (может быть None)
            username: username канала (может быть None)
            title: название канала (для логирования)
            context: 'operational' (парсинг) или 'repair' (скрипт валидации)
        
        Returns:
            Tuple (entity, tg_channel_id) или None если резолв не удался
        """
        # Context7: Переменная для отслеживания account_id и необходимости освобождения семафора
        account_id = None
        semaphore_acquired = False
        
        try:
            # Context7: Получаем preferred_ingest_account_id и preferred_account_id из БД
            preferred_ingest_account_id_db = None
            preferred_account_id_db = None
            try:
                result = await self.db_session.execute(
                    text("""
                        SELECT preferred_ingest_account_id, preferred_account_id 
                        FROM channels 
                        WHERE id = :channel_id
                    """),
                    {"channel_id": channel_id}
                )
                row = result.fetchone()
                if row:
                    preferred_ingest_account_id_db = row[0]  # UUID из ingest_accounts
                    preferred_account_id_db = row[1]  # telegram_id (legacy)
            except Exception as e:
                logger.debug("Failed to get preferred account from DB", channel_id=channel_id, error=str(e))
            
            # Context7: Выбираем сессию из пула с ограниченным fallback
            session_result = await self._select_session_for_channel(
                channel_id=channel_id,
                preferred_account_id=preferred_account_id_db,  # legacy для fallback
                context=context,
                max_fallback_sessions=2 if context == 'operational' else 1,
                task_type='resolver'  # Резолв канала - это resolver задача
            )
            
            # Context7: Если есть preferred_ingest_account_id, используем его для выбора из пула
            if not session_result and preferred_ingest_account_id_db and self.ingest_account_pool:
                # Пробуем использовать preferred_ingest_account_id напрямую
                result = await self.ingest_account_pool.select_account_for_channel(
                    channel_id=channel_id,
                    task_type='resolver',
                    preferred_account_id=preferred_ingest_account_id_db
                )
                if result:
                    telegram_id, ingest_account_id = result
                    client = await self.telegram_client_manager.get_client(telegram_id)
                    if client:
                        session_result = (client, telegram_id, ingest_account_id)
            
            if not session_result:
                logger.warning("No available session for channel resolution",
                             channel_id=channel_id,
                             context=context)
                return None
            
            # Используем выбранную сессию
            # Новый формат: (client, telegram_id, ingest_account_id)
            if len(session_result) == 3:
                client, account_id, ingest_account_id = session_result
            else:
                # Fallback для старого формата
                client, account_id = session_result
                ingest_account_id = None
            
            semaphore_acquired = True  # Семафор уже получен в _select_session_for_channel
            
            # Context7: Сохраняем ingest_account_id для последующего освобождения inflight
            
            # Проверка глобального FloodWait для выбранной сессии
            if self.floodwait_manager:
                session_id = str(account_id)
                global_wait = await self.floodwait_manager.check_global_floodwait(session_id)
                if global_wait and global_wait > 0:
                    logger.warning("Skipping resolution due to global FloodWait",
                                 channel_id=channel_id,
                                 account_id=account_id,
                                 wait_seconds=global_wait)
                    channels_skipped_due_to_global_floodwait_total.inc()
                    return None
            
            entity = None
            tg_channel_id = None
            
            # Context7: Получаем access_hash и preferred_account_id из БД
            access_hash_db = None
            try:
                result = await self.db_session.execute(
                    text("SELECT access_hash FROM channels WHERE id = :channel_id"),
                    {"channel_id": channel_id}
                )
                row = result.fetchone()
                if row:
                    access_hash_db = row[0]
            except Exception as e:
                logger.debug("Failed to get access_hash from DB", channel_id=channel_id, error=str(e))
            
            # Стратегия 1: tg_channel_id с access_hash (приоритет)
            if tg_channel_id_db is not None:
                try:
                    # Context7: Конвертируем peer_id из БД обратно в channel_id для использования
                    # Формат в БД: -100{channel_id} (constraint требует < 0)
                    # Формат для API: положительный channel_id
                    tg_channel_id_db_int = int(tg_channel_id_db)
                    input_peer = None
                    
                    # Context7: КРИТИЧНО - конвертируем peer_id в channel_id правильно
                    # Формат в БД: -100{channel_id} (peer_id для каналов)
                    # Формат для InputPeerChannel: положительный channel_id (entity.id)
                    if tg_channel_id_db_int < 0:
                        # Извлекаем channel_id из peer_id: -100{channel_id} → channel_id
                        # Context7: Используем % 1000000000000 для извлечения "короткого" channel_id
                        channel_id_for_input = abs(tg_channel_id_db_int) % 1000000000000
                    else:
                        # Старый формат (положительный) - используем как есть
                        channel_id_for_input = tg_channel_id_db_int
                    
                    # Context7: ДИАГНОСТИКА - логируем параметры перед созданием InputPeerChannel
                    logger.debug("Creating InputPeerChannel from DB",
                               channel_id=channel_id,
                               tg_channel_id_db=tg_channel_id_db_int,
                               channel_id_for_input=channel_id_for_input,
                               access_hash_db=access_hash_db,
                               access_hash_is_negative=access_hash_db < 0 if access_hash_db is not None else None,
                               access_hash_length=len(str(access_hash_db)) if access_hash_db else 0)
                    
                    # Context7: ВАЛИДАЦИЯ - проверяем, что channel_id_for_input не начинается с 100...
                    # Если channel_id_for_input >= 1000000000000, это ошибка конвертации
                    if channel_id_for_input >= 1000000000000:
                        logger.error("Invalid channel_id_for_input (too large, likely conversion error)",
                                   channel_id=channel_id,
                                   tg_channel_id_db=tg_channel_id_db_int,
                                   channel_id_for_input=channel_id_for_input)
                        # Используем get_entity напрямую без InputPeerChannel
                        entity = await client.get_entity(channel_id_for_input)
                        self._current_input_peer = None
                    elif access_hash_db is not None:
                        # Context7: КРИТИЧНО - используем access_hash как signed int64 БЕЗ конвертации
                        # Context7: Telethon ожидает signed int64, отрицательные значения - нормально
                        # Context7: В PostgreSQL BIGINT signed, что идеально соответствует требованиям Telethon
                        # НЕ конвертируем в unsigned - это вызывает struct.error: argument out of range
                        
                        # Context7: Валидация диапазона signed int64
                        MIN_I64 = -(2**63)
                        MAX_I64 = 2**63 - 1
                        if access_hash_db < MIN_I64 or access_hash_db > MAX_I64:
                            logger.error("access_hash out of signed int64 range, using get_entity fallback",
                                       channel_id=channel_id,
                                       access_hash=access_hash_db,
                                       min_i64=MIN_I64,
                                       max_i64=MAX_I64)
                            # Используем get_entity напрямую без InputPeerChannel
                            entity = await client.get_entity(channel_id_for_input)
                            self._current_input_peer = None
                        else:
                            # Context7: Используем InputPeerChannel с access_hash как есть (signed int64)
                            # Context7 best practice: валидируем InputPeerChannel через get_entity перед использованием
                            from telethon.tl.types import InputPeerChannel
                            # Context7: Создаем InputPeerChannel с положительным channel_id и signed access_hash
                            input_peer = InputPeerChannel(channel_id_for_input, access_hash_db)
                        
                        # Context7: ВАЛИДАЦИЯ ПАРЫ - проверяем совместимость (channel_id, access_hash)
                        try:
                            # Context7: Валидируем InputPeerChannel через get_entity
                            # Это гарантирует, что access_hash актуален и соответствует channel_id
                            entity = await client.get_entity(input_peer)
                            
                            # Context7: КРИТИЧНО - проверяем, что entity.id совпадает с channel_id_for_input
                            if hasattr(entity, 'id') and entity.id != channel_id_for_input:
                                logger.error("InputPeerChannel validation failed: entity.id mismatch",
                                           channel_id=channel_id,
                                           channel_id_for_input=channel_id_for_input,
                                           entity_id=entity.id,
                                           error="entity.id != channel_id_for_input")
                                # Пара несовместима - используем get_entity напрямую
                                entity = await client.get_entity(channel_id_for_input)
                                self._current_input_peer = None
                            else:
                                # Сохраняем input_peer только после успешной валидации
                                self._current_input_peer = input_peer
                                logger.debug("Validated InputPeerChannel from DB",
                                           channel_id=channel_id,
                                           input_channel_id=input_peer.channel_id,
                                           entity_id=entity.id if hasattr(entity, 'id') else None,
                                           has_access_hash=bool(input_peer.access_hash))
                        except Exception as e:
                            # Context7: КРИТИЧНО - если InputPeerChannel невалиден, пара (channel_id, access_hash) битая
                            # Context7: НЕ используем get_entity(int) - это может вернуть не то из кэша
                            # Context7: Вместо этого чистим access_hash и форсируем username-resolve
                            logger.warning("InputPeerChannel from DB is invalid, clearing access_hash and forcing username-resolve",
                                         channel_id=channel_id,
                                         input_channel_id=channel_id_for_input,
                                         error=str(e),
                                         error_type=type(e).__name__)
                            
                            # Чистим access_hash в БД
                            try:
                                await self.db_session.execute(
                                    text("""
                                        UPDATE channels 
                                        SET access_hash = NULL,
                                            resolve_status = 'pending',
                                            resolve_attempts = 0,
                                            last_resolve_at = NOW(),
                                            last_resolve_error = 'peer_hash_mismatch: ' || :error_type
                                        WHERE id = :channel_id
                                    """),
                                    {"channel_id": channel_id, "error_type": type(e).__name__}
                                )
                                await self.db_session.commit()
                            except Exception as db_error:
                                logger.warning("Failed to clear access_hash", channel_id=channel_id, error=str(db_error))
                            
                            # Переходим к username-resolve (если username есть)
                            # Если username нет - вернем None
                            if username:
                                logger.info("Forcing username-resolve after InputPeerChannel validation failed",
                                          channel_id=channel_id,
                                          username=username)
                                # Продолжаем выполнение - username-resolve будет выполнен ниже
                                entity = None  # Сбрасываем entity, чтобы username-resolve выполнился
                            else:
                                # Нет username - возвращаем None
                                return None
                    else:
                        entity = await client.get_entity(channel_id_for_input)
                        self._current_input_peer = None
                    # Сохраняем оригинальный peer_id из БД для логирования
                    tg_channel_id = tg_channel_id_db_int
                    
                    # Context7: Structured logging для успешного резолва
                    logger.info("channel_resolve",
                              event="channel_resolve",
                              channel_id=channel_id,
                              username=username,
                              tg_channel_id=tg_channel_id,
                              method="id",
                              result="ok",
                              error=None,
                              blocked_until=None)
                    
                    # Метрика
                    channel_resolve_attempts_total.labels(method="id", result="ok").inc()
                    
                    logger.debug("Resolved by tg_channel_id", channel_id=channel_id, tg_channel_id=tg_channel_id)
                    
                    # Context7: Успешный резолв - сбрасываем resolve_attempts и обновляем статус
                    try:
                        await self.db_session.execute(
                            text("""
                                UPDATE channels 
                                SET resolve_attempts = 0,
                                    resolve_status = 'ok',
                                    last_resolve_at = NOW(),
                                    last_resolve_error = NULL
                                WHERE id = :channel_id
                            """),
                            {"channel_id": channel_id}
                        )
                        await self.db_session.commit()
                    except Exception as reset_error:
                        logger.warning("Failed to reset resolve_attempts", channel_id=channel_id, error=str(reset_error))
                    
                    # Context7: Сохраняем access_hash и preferred_account_id/preferred_ingest_account_id при успешном резолве
                    if hasattr(entity, 'access_hash') and entity.access_hash:
                        await self._save_access_hash_and_preferred_account(
                            channel_id, entity.access_hash, account_id, ingest_account_id
                        )
                    
                    # Context7: КРИТИЧНО - подписка опциональна, только для recovery
                    # Используем аккаунты с role='resolver' или 'both' для подписки
                    if ingest_account_id and self.ingest_account_pool:
                        # Проверяем роль аккаунта
                        account_info = await self.ingest_account_pool._get_account_by_id(ingest_account_id)
                        if account_info and account_info.get('role') in ('resolver', 'both'):
                            await self._ensure_collector_subscription(
                                client, entity, channel_id, account_id
                            )
                    else:
                        # Fallback: старая логика для обратной совместимости
                        collector_id = int(os.getenv("COLLECTOR_TELEGRAM_ID", "8124731874"))
                        if account_id == collector_id:
                            await self._ensure_collector_subscription(
                                client, entity, channel_id, account_id
                            )
                    
                    # Освобождаем ресурсы после успешного резолва
                    semaphore_acquired = False  # Освобождаем семафор
                    if self.session_rate_limiter:
                        await self.session_rate_limiter.release_session(account_id)
                    # Освобождаем inflight в пуле
                    if ingest_account_id and self.ingest_account_pool:
                        await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                    
                    return (entity, tg_channel_id)
                except Exception as e:
                    error_str = str(e).lower()
                    is_entity_not_found = "could not find the input entity" in error_str
                    
                    if is_entity_not_found:
                        # Context7: Записываем ошибку доступа в channel_access
                        await self._mark_channel_access(
                            channel_id=channel_id,
                            account_id=account_id,
                            access_level='not_found',
                            error=str(e)[:500]
                        )
                        
                        # Context7: tg_channel_id неверный - увеличиваем resolve_attempts и устанавливаем экспоненциальный backoff
                        new_attempts = 0
                        try:
                            # Получаем текущее значение resolve_attempts
                            result = await self.db_session.execute(
                                text("SELECT resolve_attempts FROM channels WHERE id = :channel_id"),
                                {"channel_id": channel_id}
                            )
                            row = result.fetchone()
                            current_attempts = row.resolve_attempts if row else 0
                            new_attempts = current_attempts + 1
                            
                            # Вычисляем backoff
                            backoff = self._calculate_backoff(new_attempts)
                            blocked_until = datetime.now(timezone.utc) + backoff
                            
                            # Обновляем resolve_attempts, resolve_status, last_resolve_at, last_resolve_error и blocked_until
                            await self.db_session.execute(
                                text("""
                                    UPDATE channels 
                                    SET resolve_attempts = :attempts,
                                        resolve_status = 'not_found',
                                        last_resolve_at = NOW(),
                                        last_resolve_error = :error,
                                        blocked_until = :blocked_until
                                    WHERE id = :channel_id
                                """),
                                {
                                    "attempts": new_attempts,
                                    "error": str(e)[:500],  # Ограничиваем длину ошибки
                                    "blocked_until": blocked_until,
                                    "channel_id": channel_id
                                }
                            )
                            await self.db_session.commit()
                            
                            # Context7: Structured logging
                            logger.warning("channel_resolve",
                                         event="channel_resolve",
                                         channel_id=channel_id,
                                         username=username,
                                         tg_channel_id=tg_channel_id_db,
                                         method="id",
                                         result="not_found",
                                         error=str(e)[:200],
                                         blocked_until=blocked_until.isoformat(),
                                         attempts=new_attempts,
                                         backoff_hours=backoff.total_seconds() / 3600)
                            
                            # Метрика
                            channel_resolve_attempts_total.labels(method="id", result="not_found").inc()
                            
                            logger.warning("tg_channel_id invalid, exponential backoff applied",
                                         channel_id=channel_id,
                                         tg_channel_id=tg_channel_id_db,
                                         attempts=new_attempts,
                                         backoff_hours=backoff.total_seconds() / 3600,
                                         blocked_until=blocked_until.isoformat())
                        except Exception as db_error:
                            logger.error("Failed to update resolve_attempts", channel_id=channel_id, error=str(db_error))
                            # При ошибке БД используем дефолтное значение
                            new_attempts = 1
                        
                        # Пробуем username только если attempts < 3 (не слишком много попыток)
                        if new_attempts < 3 and username:
                            logger.info("Trying username fallback after tg_channel_id failed",
                                      channel_id=channel_id, attempts=new_attempts)
                            # Продолжаем к стратегии 2 (username)
                        else:
                            # Слишком много попыток или нет username - возвращаем None
                            return None
                    else:
                        # Другие ошибки (Timeout, Connection) - можно попробовать username
                        logger.warning("tg_channel_id failed with non-entity error, trying username",
                                     channel_id=channel_id, error=str(e))
                        # Продолжаем к стратегии 2 (username) - семафор НЕ освобождаем, он будет освобожден в finally
            
            # Стратегия 2: username (fallback)
            if username:
                try:
                    clean_username = username.lstrip('@')
                    entity = await client.get_entity(clean_username)
                    
                    # Context7: КРИТИЧНО - диагностика типа entity
                    # Context7: Логируем тип entity для выявления случаев, когда username резолвится в User/Bot вместо Channel
                    entity_type = type(entity).__name__
                    entity_broadcast = getattr(entity, "broadcast", None)
                    entity_megagroup = getattr(entity, "megagroup", None)
                    entity_title = getattr(entity, "title", None) if hasattr(entity, "title") else None
                    entity_id = getattr(entity, "id", None)
                    entity_access_hash = getattr(entity, "access_hash", None)
                    
                    logger.debug("Entity resolved from username",
                               channel_id=channel_id,
                               username=clean_username,
                               entity_type=entity_type,
                               entity_id=entity_id,
                               entity_broadcast=entity_broadcast,
                               entity_megagroup=entity_megagroup,
                               entity_title=entity_title,
                               has_access_hash=entity_access_hash is not None)
                    
                    # Context7: КРИТИЧНО - жесткая валидация "это канал"
                    # Context7: Если entity не Channel/ChannelForbidden, это не канал - не сохраняем и возвращаем None
                    from telethon.tl.types import Channel, ChannelForbidden
                    if not isinstance(entity, (Channel, ChannelForbidden)):
                        logger.error("Entity resolved from username is not a Channel",
                                   channel_id=channel_id,
                                   username=clean_username,
                                   entity_type=entity_type,
                                   entity_id=entity_id)
                        
                        # Обновляем статус резолва
                        try:
                            await self.db_session.execute(
                                text("""
                                    UPDATE channels 
                                    SET resolve_status = 'not_channel',
                                        resolve_attempts = resolve_attempts + 1,
                                        last_resolve_at = NOW(),
                                        last_resolve_error = 'username_not_channel: entity is ' || :entity_type
                                    WHERE id = :channel_id
                                """),
                                {"channel_id": channel_id, "entity_type": entity_type}
                            )
                            await self.db_session.commit()
                        except Exception as db_error:
                            logger.warning("Failed to update resolve_status", channel_id=channel_id, error=str(db_error))
                        
                        return None
                    
                    # Получаем tg_channel_id из entity
                    # Context7: Конвертируем положительный channel_id в отрицательный peer_id для БД
                    # Constraint требует tg_channel_id < 0, поэтому используем формат -100{channel_id}
                    if hasattr(entity, 'id') and entity.id is not None:
                        # Получаем положительный channel_id из entity
                        channel_id_positive = entity.id
                        # Конвертируем в peer_id для хранения в БД (constraint требует < 0)
                        # Формат: -100{channel_id} для каналов
                        if channel_id_positive > 0:
                            tg_channel_id = -1000000000000 - channel_id_positive
                        else:
                            tg_channel_id = channel_id_positive
                        
                        # Сохраняем положительный channel_id для использования в InputPeerChannel
                        channel_id_for_api = channel_id_positive
                        
                        # Context7: Сохраняем tg_channel_id в БД и обновляем статус
                        try:
                            await self.db_session.execute(
                                text("""
                                    UPDATE channels 
                                    SET tg_channel_id = :tg_channel_id,
                                        resolve_attempts = 0,
                                        resolve_status = 'ok',
                                        last_resolve_at = NOW(),
                                        last_resolve_error = NULL
                                    WHERE id = :channel_id
                                """),
                                {"tg_channel_id": tg_channel_id, "channel_id": channel_id}
                            )
                            await self.db_session.commit()
                            
                            # Context7: Если есть access_hash, создаем input_peer для будущего использования
                            # Context7 best practice: используем entity.id напрямую (положительный channel_id)
                            # InputPeerChannel требует положительный channel_id и валидный access_hash
                            if hasattr(entity, 'access_hash') and entity.access_hash:
                                from telethon.tl.types import InputPeerChannel
                                # Context7: Используем entity.id напрямую (положительный channel_id)
                                # Это гарантирует соответствие между entity и InputPeerChannel
                                input_peer = InputPeerChannel(entity.id, entity.access_hash)
                                self._current_input_peer = input_peer
                                logger.debug("Created InputPeerChannel from entity",
                                           channel_id=channel_id,
                                           entity_id=entity.id,
                                           input_channel_id=input_peer.channel_id,
                                           has_access_hash=bool(input_peer.access_hash))
                            else:
                                self._current_input_peer = None
                            
                            # Context7: Structured logging
                            logger.info("channel_resolve",
                                      event="channel_resolve",
                                      channel_id=channel_id,
                                      username=username,
                                      tg_channel_id=tg_channel_id,
                                      method="username",
                                      result="ok",
                                      error=None,
                                      blocked_until=None)
                            
                            # Метрика
                            channel_resolve_attempts_total.labels(method="username", result="ok").inc()
                            
                            logger.info("Auto-saved tg_channel_id from username",
                                       channel_id=channel_id, username=username, tg_channel_id=tg_channel_id)
                        except Exception as save_error:
                            # Context7: Обрабатываем constraint violation и другие ошибки
                            error_str = str(save_error)
                            if "CheckViolationError" in error_str or "check constraint" in error_str.lower():
                                logger.warning("Constraint violation saving tg_channel_id, trying rollback and retry",
                                             channel_id=channel_id,
                                             tg_channel_id=tg_channel_id,
                                             error=error_str)
                                try:
                                    await self.db_session.rollback()
                                except Exception:
                                    pass
                            else:
                                logger.warning("Failed to save tg_channel_id", channel_id=channel_id, error=error_str)
                        
                        # Context7: КРИТИЧНО - валидация entity перед сохранением
                        # Context7: Проверяем, что это Channel и имеет access_hash
                        from telethon.tl.types import Channel
                        if not isinstance(entity, Channel):
                            logger.error("Entity is not a Channel, cannot save access_hash",
                                       channel_id=channel_id,
                                       entity_type=type(entity).__name__)
                        elif not hasattr(entity, 'access_hash') or entity.access_hash is None:
                            logger.error("Channel entity has no access_hash",
                                       channel_id=channel_id,
                                       entity_id=entity.id if hasattr(entity, 'id') else None)
                        else:
                            # Context7: КРИТИЧНО - тест истины: проверяем доступ через iter_messages
                            # Это гарантирует, что entity валиден и доступен для чтения
                            try:
                                # Тест: пытаемся получить хотя бы одно сообщение
                                test_messages = await client.get_messages(entity, limit=1)
                                logger.debug("Entity validation passed: can fetch messages",
                                           channel_id=channel_id,
                                           entity_id=entity.id,
                                           messages_count=len(test_messages) if test_messages else 0)
                            except Exception as test_error:
                                logger.warning("Entity validation failed: cannot fetch messages",
                                            channel_id=channel_id,
                                            entity_id=entity.id,
                                            error=str(test_error),
                                            error_type=type(test_error).__name__)
                                # Не сохраняем access_hash, если не можем получить сообщения
                                # Это предотвращает сохранение невалидной пары (channel_id, access_hash)
                                return None
                            
                            # Context7: Сохраняем access_hash и preferred_account_id/preferred_ingest_account_id при успешном резолве
                            # Важно: делаем это в отдельной транзакции, чтобы не зависеть от ошибок сохранения tg_channel_id
                            try:
                                # Проверяем, что транзакция не в failed состоянии
                                if self.db_session.in_transaction():
                                    try:
                                        await self.db_session.rollback()
                                    except Exception:
                                        pass
                                
                                await self._save_access_hash_and_preferred_account(
                                    channel_id, entity.access_hash, account_id, ingest_account_id
                                )
                            except Exception as hash_error:
                                logger.error("Failed to save access_hash after tg_channel_id save",
                                           channel_id=channel_id,
                                           error=str(hash_error))
                        
                        # Context7: КРИТИЧНО - подписка опциональна, только для recovery
                        # Используем аккаунты с role='resolver' или 'both' для подписки
                        if ingest_account_id and self.ingest_account_pool:
                            # Проверяем роль аккаунта
                            account_info = await self.ingest_account_pool._get_account_by_id(ingest_account_id)
                            if account_info and account_info.get('role') in ('resolver', 'both'):
                                await self._ensure_collector_subscription(
                                    client, entity, channel_id, account_id
                                )
                        else:
                            # Fallback: старая логика для обратной совместимости
                            collector_id = int(os.getenv("COLLECTOR_TELEGRAM_ID", "8124731874"))
                            if account_id == collector_id:
                                await self._ensure_collector_subscription(
                                    client, entity, channel_id, account_id
                                )
                        
                        # Освобождаем ресурсы после успешного резолва
                        semaphore_acquired = False  # Освобождаем семафор
                        if self.session_rate_limiter:
                            await self.session_rate_limiter.release_session(account_id)
                        # Освобождаем inflight в пуле
                        if ingest_account_id and self.ingest_account_pool:
                            await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                        
                        return (entity, tg_channel_id)
                    else:
                        raise ValueError("Entity has no valid ID")
                        
                except errors.FloodWaitError as e:
                    # Context7: Политика переключения сессий с защитой от каскадного FloodWait
                    # Обновляем blocked_until в пуле аккаунтов
                    if 'ingest_account_id' in locals() and ingest_account_id and self.ingest_account_pool:
                        from datetime import timedelta
                        blocked_until = datetime.now(timezone.utc) + timedelta(seconds=e.seconds)
                        error_code = f"FLOOD_WAIT_{e.seconds}"
                        await self.ingest_account_pool.update_blocked_until(
                            ingest_account_id, blocked_until, error_code
                        )
                    
                    if self.floodwait_manager:
                        should_abort = await self.floodwait_manager.should_abort_resolution(
                            e.seconds, context
                        )
                        
                        if should_abort:
                            # Большой FloodWait - останавливаем весь проход резолва
                            session_id = str(account_id)
                            await self.floodwait_manager.set_global_floodwait(session_id, e.seconds)
                            logger.error("Aborting resolution due to large FloodWait",
                                       channel_id=channel_id,
                                       account_id=account_id,
                                       seconds=e.seconds,
                                       context=context)
                            resolution_aborted_total.labels(context=context, reason='large_floodwait').inc()
                            semaphore_acquired = False  # Освобождаем семафор
                            if self.session_rate_limiter:
                                await self.session_rate_limiter.release_session(account_id)
                            # Освобождаем inflight
                            if 'ingest_account_id' in locals() and ingest_account_id and self.ingest_account_pool:
                                await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                            raise  # Прерываем весь проход резолва
                        
                        # Малый FloodWait - устанавливаем для сессии
                        session_id = str(account_id)
                        await self.floodwait_manager.set_global_floodwait(session_id, e.seconds)
                        logger.warning("FloodWait on session",
                                     channel_id=channel_id,
                                     account_id=account_id,
                                     seconds=e.seconds,
                                     context=context)
                        # Записываем ошибку доступа в channel_access
                        await self._mark_channel_access(
                            channel_id=channel_id,
                            account_id=account_id,
                            access_level='no_access',
                            error=f"FloodWait {e.seconds}s"
                        )
                        # Возвращаем None - не пробуем другую сессию (защита от каскадного FloodWait)
                        return None
                    
                    # Малый FloodWait - обрабатываем локально
                    blocked_until = datetime.now(timezone.utc) + timedelta(seconds=min(e.seconds, 3600))
                    
                    # Context7: Structured logging
                    logger.warning("channel_resolve",
                                 event="channel_resolve",
                                 channel_id=channel_id,
                                 username=username,
                                 tg_channel_id=None,
                                 method="username",
                                 result="floodwait",
                                 error=f"FloodWait {e.seconds}s",
                                 blocked_until=blocked_until.isoformat())
                    
                    # Метрика
                    channel_resolve_attempts_total.labels(method="username", result="floodwait").inc()
                    
                    logger.warning("FloodWait on username resolution",
                                 channel_id=channel_id, username=username, seconds=e.seconds)
                    # Устанавливаем blocked_until и обновляем статус
                    try:
                        await self.db_session.execute(
                            text("""
                                UPDATE channels 
                                SET blocked_until = :blocked_until,
                                    resolve_status = 'floodwait',
                                    last_resolve_at = NOW(),
                                    last_resolve_error = :error
                                WHERE id = :channel_id
                            """),
                            {
                                "blocked_until": blocked_until,
                                "error": f"FloodWait {e.seconds}s",
                                "channel_id": channel_id
                            }
                        )
                        await self.db_session.commit()
                    except Exception as db_error:
                        logger.error("Failed to set blocked_until after FloodWait",
                                   channel_id=channel_id, error=str(db_error))
                    return None
                    
                except Exception as e:
                    error_msg = str(e)[:200]
                    
                    # Context7: Structured logging
                    logger.warning("channel_resolve",
                                 event="channel_resolve",
                                 channel_id=channel_id,
                                 username=username,
                                 tg_channel_id=None,
                                 method="username",
                                 result="invalid",
                                 error=error_msg,
                                 blocked_until=None)
                    
                    # Метрика
                    channel_resolve_attempts_total.labels(method="username", result="invalid").inc()
                    
                    logger.warning("Failed to resolve by username",
                                 channel_id=channel_id, username=username, error=error_msg)
                    
                    # Context7: Записываем ошибку доступа в channel_access
                    await self._mark_channel_access(
                        channel_id=channel_id,
                        account_id=account_id,
                        access_level='no_access',
                        error=error_msg
                    )
                    
                    # Обновляем статус
                    try:
                        await self.db_session.execute(
                            text("""
                                UPDATE channels 
                                SET resolve_status = 'invalid_username',
                                    last_resolve_at = NOW(),
                                    last_resolve_error = :error
                                WHERE id = :channel_id
                            """),
                            {
                                "error": str(e)[:500],
                                "channel_id": channel_id
                            }
                        )
                        await self.db_session.commit()
                    except Exception as db_error:
                        logger.warning("Failed to update resolve_status", channel_id=channel_id, error=str(db_error))
                    return None
            
            # Стратегия 3: hard-fail (нет идентификаторов)
            # Context7: Structured logging
            logger.warning("channel_resolve",
                     event="channel_resolve",
                     channel_id=channel_id,
                     username=None,
                     tg_channel_id=None,
                     method="none",
                     result="needs_id",
                     error="No identifiers available",
                     blocked_until=None)
        
            # Метрика
            channel_resolve_attempts_total.labels(method="none", result="needs_id").inc()
            
            logger.warning("Channel has no identifiers for resolution",
                         channel_id=channel_id, title=title)
            
            # Context7: Записываем ошибку доступа в channel_access
            if account_id:
                await self._mark_channel_access(
                    channel_id=channel_id,
                    account_id=account_id,
                    access_level='no_access',
                    error='No identifiers available'
                )
            
            # Обновляем статус
            try:
                await self.db_session.execute(
                    text("""
                        UPDATE channels 
                        SET resolve_status = 'needs_id',
                            last_resolve_at = NOW(),
                            last_resolve_error = 'No identifiers available'
                        WHERE id = :channel_id
                    """),
                    {"channel_id": channel_id}
                )
                await self.db_session.commit()
            except Exception as db_error:
                logger.warning("Failed to update resolve_status", channel_id=channel_id, error=str(db_error))
            return None
        finally:
            # Context7: Гарантируем освобождение семафора во всех случаях
            if semaphore_acquired and self.session_rate_limiter and account_id:
                try:
                    await self.session_rate_limiter.release_session(account_id)
                    logger.debug("Released semaphore in finally block",
                               channel_id=channel_id,
                               account_id=account_id)
                except Exception as release_error:
                    logger.warning("Failed to release semaphore in finally block",
                                 channel_id=channel_id,
                                 account_id=account_id,
                                 error=str(release_error))
            
            # Context7: Гарантируем освобождение inflight в пуле
            if 'ingest_account_id' in locals() and ingest_account_id and self.ingest_account_pool:
                try:
                    await self.ingest_account_pool.mark_account_complete(ingest_account_id)
                    logger.debug("Released inflight in finally block",
                               channel_id=channel_id,
                               ingest_account_id=str(ingest_account_id))
                except Exception as release_error:
                    logger.warning("Failed to release inflight in finally block",
                                 channel_id=channel_id,
                                 ingest_account_id=str(ingest_account_id) if 'ingest_account_id' in locals() else None,
                                 error=str(release_error))
            
            # Context7: Очищаем input_peer в finally блоке
            # Это гарантирует, что input_peer не будет использоваться для другого канала
            self._current_input_peer = None
    
    async def _mark_channel_access(
        self,
        channel_id: str,
        account_id: int,
        access_level: str,
        error: Optional[str] = None
    ):
        """
        Запись доступа сессии к каналу в channel_access для аналитики.
        
        Context7: Отслеживание истории успешных резолвов по сессиям для
        автоматического выбора лучшей сессии и аналитики доступности каналов.
        
        Args:
            channel_id: UUID канала в БД
            account_id: telegram_id сессии
            access_level: 'public_ok', 'member_ok', 'no_access', 'not_found'
            error: Текст ошибки (если есть)
        """
        try:
            if access_level in ('public_ok', 'member_ok'):
                # Успешный доступ - обновляем last_ok_at и сбрасываем fail_count
                await self.db_session.execute(
                    text("""
                        INSERT INTO channel_access (channel_id, account_id, access_level, last_ok_at, fail_count, last_error, updated_at)
                        VALUES (:channel_id, :account_id, :access_level, NOW(), 0, NULL, NOW())
                        ON CONFLICT (channel_id, account_id)
                        DO UPDATE SET
                            access_level = :access_level,
                            last_ok_at = NOW(),
                            fail_count = 0,
                            last_error = NULL,
                            updated_at = NOW()
                    """),
                    {
                        "channel_id": channel_id,
                        "account_id": account_id,
                        "access_level": access_level
                    }
                )
            else:
                # Ошибка доступа - увеличиваем fail_count
                await self.db_session.execute(
                    text("""
                        INSERT INTO channel_access (channel_id, account_id, access_level, fail_count, last_error, updated_at)
                        VALUES (:channel_id, :account_id, :access_level, 1, :error, NOW())
                        ON CONFLICT (channel_id, account_id)
                        DO UPDATE SET
                            access_level = :access_level,
                            fail_count = channel_access.fail_count + 1,
                            last_error = :error,
                            updated_at = NOW()
                    """),
                    {
                        "channel_id": channel_id,
                        "account_id": account_id,
                        "access_level": access_level,
                        "error": error[:500] if error else None  # Ограничиваем длину ошибки
                    }
                )
            
            await self.db_session.commit()
            
            logger.debug("Marked channel access",
                        channel_id=channel_id,
                        account_id=account_id,
                        access_level=access_level)
        except Exception as e:
            logger.warning("Failed to mark channel access",
                          channel_id=channel_id,
                          account_id=account_id,
                          access_level=access_level,
                          error=str(e))
            await self.db_session.rollback()
    
    async def _save_access_hash_and_preferred_account(
        self,
        channel_id: str,
        access_hash: int,
        account_id: int,
        ingest_account_id: Optional[Any] = None
    ):
        """
        Сохранение access_hash и preferred_account_id/preferred_ingest_account_id в channels и TelegramEntity.
        
        Context7: Синхронизация access_hash в оба места для быстрого доступа
        и нормализованного хранения метаданных. Сохраняет preferred_ingest_account_id (FK) и
        preferred_account_id (legacy) для обратной совместимости.
        
        Args:
            channel_id: UUID канала в БД
            access_hash: access_hash из entity
            account_id: telegram_id сессии, которая успешно резолвит канал
            ingest_account_id: UUID аккаунта из ingest_accounts (если используется пул)
        """
        try:
            # Сохраняем в channels
            # Context7: Dual-write для обратной совместимости
            update_data = {
                "access_hash": access_hash,
                "account_id": account_id,
                "channel_id": channel_id
            }
            
            if ingest_account_id:
                # Сохраняем preferred_ingest_account_id (FK)
                update_query = text("""
                    UPDATE channels 
                    SET access_hash = :access_hash,
                        preferred_account_id = :account_id,
                        preferred_ingest_account_id = :ingest_account_id
                    WHERE id = :channel_id
                """)
                update_data["ingest_account_id"] = ingest_account_id
            else:
                # Только legacy preferred_account_id
                update_query = text("""
                    UPDATE channels 
                    SET access_hash = :access_hash,
                        preferred_account_id = :account_id
                    WHERE id = :channel_id
                """)
            
            await self.db_session.execute(update_query, update_data)
            
            # Context7: Синхронизация с TelegramEntity (если есть tg_channel_id)
            result = await self.db_session.execute(
                text("SELECT tg_channel_id FROM channels WHERE id = :channel_id"),
                {"channel_id": channel_id}
            )
            row = result.fetchone()
            if row and row[0]:
                tg_channel_id = row[0]
                # UPSERT в TelegramEntity
                # Context7: tg_entities использует peer_id и peer_type, не telegram_id и entity_type
                # Конвертируем peer_id из БД в положительный channel_id для tg_entities
                peer_id_for_entity = abs(int(tg_channel_id)) % 1000000000000 if tg_channel_id < 0 else abs(int(tg_channel_id))
                await self.db_session.execute(
                    text("""
                        INSERT INTO tg_entities (id, peer_id, peer_type, access_hash, updated_at, is_channel)
                        VALUES (gen_random_uuid(), :peer_id, 'channel', :access_hash, NOW(), true)
                        ON CONFLICT (peer_id, peer_type) 
                        DO UPDATE SET access_hash = :access_hash, updated_at = NOW()
                    """),
                    {
                        "peer_id": peer_id_for_entity,  # TelegramEntity хранит положительный channel_id
                        "access_hash": access_hash
                    }
                )
            
            # Context7: Записываем успешный доступ в channel_access
            await self._mark_channel_access(
                channel_id=channel_id,
                account_id=account_id,
                access_level='public_ok'  # Успешный резолв означает доступ
            )
            
            await self.db_session.commit()
            
            logger.info("Saved access_hash and preferred_account_id",
                       channel_id=channel_id,
                       account_id=account_id,
                       access_hash=access_hash)
        except Exception as e:
            logger.error("Failed to save access_hash and preferred_account_id",
                        channel_id=channel_id,
                        account_id=account_id,
                        error=str(e))
            await self.db_session.rollback()
    
    async def _ensure_collector_subscription(
        self,
        client: TelegramClient,
        entity: Channel,
        channel_id: str,
        account_id: int
    ) -> bool:
        """
        Обеспечивает опциональную подписку сервисного аккаунта на канал (только для recovery).
        
        Context7: Подписка опциональна, не обязательна для чтения публичных каналов.
        Используется только как recovery механизм для конкретных кейсов.
        Использует аккаунты с role='resolver' или 'both' из пула.
        
        Args:
            client: TelegramClient для сессии
            entity: Резолвленный entity канала
            channel_id: UUID канала в БД
            account_id: telegram_id сессии
        
        Returns:
            True если подписка успешна или не требуется, False если ошибка
        """
        # Context7: Проверяем роль аккаунта через пул
        if self.ingest_account_pool:
            account_info = await self.ingest_account_pool.get_account_by_telegram_id(account_id)
            if not account_info or account_info.get('role') not in ('resolver', 'both'):
                # Аккаунт не предназначен для подписки
                return True
        else:
            # Fallback: старая логика для обратной совместимости
            collector_id = int(os.getenv("COLLECTOR_TELEGRAM_ID", "8124731874"))
            if account_id != collector_id:
                return True  # Не collector - подписка не требуется
        
        # Проверяем статус подписки в БД
        try:
            result = await self.db_session.execute(
                text("""
                    SELECT collector_subscription_status, collector_subscribed_at
                    FROM channels
                    WHERE id = :channel_id
                """),
                {"channel_id": channel_id}
            )
            row = result.fetchone()
            
            if row and row[0] == 'subscribed':
                return True  # Уже подписан
            
            if row and row[0] == 'private':
                return False  # Частный канал, подписка невозможна
        except Exception as e:
            logger.warning("Failed to check subscription status",
                          channel_id=channel_id,
                          error=str(e))
        
        # Пытаемся подписаться
        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            
            # Context7: Rate limiting для подписок (больше джиттер, чем для резолва)
            if self.session_rate_limiter:
                await self.session_rate_limiter.wait_for_token(account_id, min_delay=5.0, max_delay=10.0)
            
            await client(JoinChannelRequest(entity))
            
            # Метрика успешной подписки
            collector_subscriptions_total.labels(status='subscribed').inc()
            collector_subscription_attempts_total.inc()
            
            # Обновляем статус в БД
            await self.db_session.execute(
                text("""
                    UPDATE channels
                    SET collector_subscription_status = 'subscribed',
                        collector_subscribed_at = NOW()
                    WHERE id = :channel_id
                """),
                {"channel_id": channel_id}
            )
            await self.db_session.commit()
            
            logger.info("Collector subscribed to channel",
                       channel_id=channel_id,
                       account_id=account_id)
            return True
            
        except errors.UserAlreadyParticipantError:
            # Уже подписан (возможно, подписался вручную)
            await self.db_session.execute(
                text("""
                    UPDATE channels
                    SET collector_subscription_status = 'subscribed',
                        collector_subscribed_at = NOW()
                    WHERE id = :channel_id
                """),
                {"channel_id": channel_id}
            )
            await self.db_session.commit()
            return True
            
        except errors.ChannelPrivateError:
            # Частный канал - нужен инвайт
            collector_subscriptions_total.labels(status='private').inc()
            collector_subscription_attempts_total.inc()
            
            await self.db_session.execute(
                text("""
                    UPDATE channels
                    SET collector_subscription_status = 'private'
                    WHERE id = :channel_id
                """),
                {"channel_id": channel_id}
            )
            await self.db_session.commit()
            logger.warning("Channel is private, cannot subscribe",
                          channel_id=channel_id)
            return False
            
        except errors.FloodWaitError as e:
            # FloodWait на подписку - отложить
            # Context7: Обновляем blocked_until в пуле аккаунтов
            if self.ingest_account_pool:
                # Получаем ingest_account_id по telegram_id
                account_info = await self.ingest_account_pool.get_account_by_telegram_id(account_id)
                if account_info:
                    from datetime import timedelta
                    blocked_until = datetime.now(timezone.utc) + timedelta(seconds=e.seconds)
                    error_code = f"FLOOD_WAIT_JOIN_{e.seconds}"
                    await self.ingest_account_pool.update_blocked_until(
                        account_info['id'], blocked_until, error_code
                    )
            
            if self.floodwait_manager:
                await self.floodwait_manager.handle_floodwait(
                    e, str(account_id), "JoinChannel"
                )
            logger.warning("FloodWait on subscription, will retry later",
                         channel_id=channel_id,
                         seconds=e.seconds)
            return False
            
        except Exception as e:
            # Другая ошибка
            collector_subscriptions_total.labels(status='failed').inc()
            collector_subscription_attempts_total.inc()
            
            await self.db_session.execute(
                text("""
                    UPDATE channels
                    SET collector_subscription_status = 'failed'
                    WHERE id = :channel_id
                """),
                {"channel_id": channel_id}
            )
            await self.db_session.commit()
            logger.error("Failed to subscribe collector to channel",
                        channel_id=channel_id,
                        error=str(e))
            return False
    
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
            
            # Context7: Используем единый метод резолва с детерминированным порядком
            result = await self._resolve_channel_entity(
                client=client,
                channel_id=channel_id,
                tg_channel_id_db=tg_channel_id_db,
                username=username,
                title=title,
                context='operational'  # Context7: operational парсинг
            )
            
            if result is None:
                return None
            
            entity, tg_channel_id = result
            
            # Context7: КРИТИЧНО - сохраняем _current_input_peer перед использованием
            # _resolve_channel_entity устанавливает _current_input_peer, но он очищается в finally блоке
            # Context7 best practice: используем get_input_entity для получения InputPeerChannel если access_hash есть
            # Сохраняем input_peer для использования в _get_message_batches
            saved_input_peer = self._current_input_peer
            
            # Context7: Fallback - если _current_input_peer не установлен, но есть access_hash в БД,
            # создаем InputPeerChannel напрямую из данных БД
            if not saved_input_peer:
                try:
                    # Получаем access_hash из БД
                    result = await self.db_session.execute(
                        text("SELECT access_hash, tg_channel_id FROM channels WHERE id = :channel_id"),
                        {"channel_id": channel_id}
                    )
                    row = result.fetchone()
                    if row and row[0]:  # access_hash есть
                        access_hash_db = row[0]
                        tg_channel_id_db = row[1]
                        
                        # Конвертируем peer_id в channel_id
                        if tg_channel_id_db and tg_channel_id_db < 0:
                            channel_id_for_input = abs(tg_channel_id_db) % 1000000000000
                        else:
                            channel_id_for_input = tg_channel_id_db if tg_channel_id_db else entity.id
                        
                        # Context7 best practice: используем get_input_entity для валидации вместо создания вручную
                        # Это гарантирует актуальность access_hash и правильность channel_id
                        # Context7: НЕ используем telegram_client здесь, так как он недоступен в этом контексте
                        # Вместо этого создаем InputPeerChannel напрямую - он будет валидирован позже через get_input_entity
                        try:
                            from telethon.tl.types import InputPeerChannel
                            # Создаем InputPeerChannel из данных БД
                            # Валидация произойдет позже при использовании через get_input_entity в _get_message_batches
                            saved_input_peer = InputPeerChannel(channel_id_for_input, access_hash_db)
                            logger.debug("Created InputPeerChannel from DB (will be validated later)",
                                       channel_id=channel_id,
                                       input_channel_id=saved_input_peer.channel_id,
                                       has_access_hash=bool(saved_input_peer.access_hash))
                        except Exception as e:
                            # Context7: Если создание не удалось, не используем InputPeerChannel
                            saved_input_peer = None
                            logger.warning("Failed to create InputPeerChannel from DB",
                                         channel_id=channel_id,
                                         error=str(e))
                except Exception as e:
                    logger.warning("Failed to create InputPeerChannel from DB",
                                 channel_id=channel_id,
                                 error=str(e))
            
            # Context7: Успешно получили entity - продолжаем обработку
            # Примечание: _resolve_channel_entity уже сохраняет tg_channel_id в БД при резолве по username,
            # поэтому дополнительное сохранение здесь не требуется
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
            # Context7: [C7-ID: incremental-since-date-fix-004] КРИТИЧНО - приоритетная логика для since_date
            # Приоритет 1: last_parsed_at (точка последнего парсинга) - самый актуальный источник истины
            # Приоритет 2: last_post_date (последний пост в БД) - fallback для проверки gap
            # Приоритет 3: Redis HWM - временное хранилище
            # 
            # Проблема предыдущей логики: использование last_post_date приводило к пропуску постов,
            # так как last_parsed_at мог быть обновлен даже без новых постов, а since_date
            # вычислялся от старого last_post_date.
            
            last_parsed_at_raw = channel.get('last_parsed_at')
            last_parsed_utc = ensure_dt_utc(last_parsed_at_raw) if last_parsed_at_raw else None
            last_post_date = await self._get_last_post_date(channel_id)
            
            # Приоритет 1: Используем last_parsed_at если он есть и не слишком старый (< 48 часов)
            if last_parsed_utc:
                age_hours = (now - last_parsed_utc).total_seconds() / 3600
                if age_hours < self.config.lpa_max_age_hours:
                    base_utc = last_parsed_utc
                    logger.debug("Using last_parsed_at as base for incremental mode",
                               channel_id=channel_id,
                               last_parsed_at=last_parsed_utc.isoformat(),
                               age_hours=age_hours)
                else:
                    # last_parsed_at слишком старый - используем last_post_date или min(last_parsed_at, last_post_date)
                    if last_post_date:
                        # Используем максимум из last_parsed_at и last_post_date для безопасности
                        base_utc = max(last_parsed_utc, last_post_date)
                        logger.debug("last_parsed_at too old, using max(last_parsed_at, last_post_date)",
                                   channel_id=channel_id,
                                   last_parsed_at=last_parsed_utc.isoformat(),
                                   last_post_date=last_post_date.isoformat(),
                                   base_date=base_utc.isoformat(),
                                   age_hours=age_hours)
                    else:
                        base_utc = last_parsed_utc
                        logger.debug("Using last_parsed_at as base (no posts in DB)",
                                   channel_id=channel_id,
                                   last_parsed_at=last_parsed_utc.isoformat(),
                                   age_hours=age_hours)
            elif last_post_date:
                # Приоритет 2: Используем last_post_date если нет last_parsed_at
                base_utc = last_post_date
                logger.debug("Using last_post_date as base for incremental mode (no last_parsed_at)",
                           channel_id=channel_id,
                           last_post_date=last_post_date.isoformat())
            elif redis_hwm:
                # Приоритет 3: Используем Redis HWM как fallback
                base_utc = redis_hwm
                logger.debug("Using Redis HWM as base for incremental mode",
                           channel_id=channel_id,
                           hwm=redis_hwm.isoformat())
            else:
                # Fallback: если нет данных, используем incremental окно
                logger.debug("No base date available, using incremental window fallback",
                           channel_id=channel_id)
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
        mode: str = "historical",
        channel_id: Optional[str] = None  # Context7: UUID канала в БД для получения last_message_id
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
            # Context7: Оптимизированный лимит для предотвращения таймаутов
            # Используем разумный лимит, но проверяем больше сообщений через max_check_before_stop
            # Telegram API может медленно работать с большими лимитами (>10000)
            limit = batch_size * 100 if mode == "incremental" else batch_size * 200
            
            # Context7: КРИТИЧНО - offset_date в Telethon возвращает сообщения ПРЕДШЕСТВУЮЩИЕ дате, а не ПОСЛЕ!
            # Источник: Telethon docs: "offset_date: Offset date (messages *previous* to this date will be retrieved). Exclusive."
            # 
            # Для ОБОИХ режимов (incremental и historical) НЕ используем offset_date, потому что:
            # - incremental: нужны сообщения НОВЕЕ since_date (последние сообщения)
            # - historical: нужны сообщения НОВЕЕ since_date (последние 24 часа для новых каналов)
            # 
            # Context7: КРИТИЧНО - offset_id в Telethon возвращает сообщения СТАРШЕ указанного ID, а не новее!
            # Источник: "offset_id: Start getting messages with an identifier lower than this one. Only messages older than the message with id = offset_id will be fetched."
            # Поэтому НЕ используем offset_id для получения новых сообщений!
            # Вместо этого получаем последние N сообщений и фильтруем по message.id > last_message_id
            offset_date_param = None
            offset_id_param = None
            last_message_id = None
            
            if mode == "incremental" and channel_id:
                # Получаем последний telegram_message_id из БД для фильтрации сообщений
                # Context7: channel_id - это UUID канала в БД, переданный из parse_channel
                try:
                    result = await self.db_session.execute(
                        text("SELECT MAX(telegram_message_id) FROM posts WHERE channel_id = :channel_id"),
                        {"channel_id": channel_id}
                    )
                    last_message_id = result.scalar()
                    if last_message_id:
                        logger.debug("Got last_message_id for filtering",
                                   channel_id=channel_id,
                                   last_message_id=last_message_id,
                                   since_date=since_date.isoformat())
                except Exception as e:
                    logger.warning("Failed to get last_message_id for filtering",
                                 channel_id=channel_id,
                                 error=str(e))
            
            # Context7: Для historical режима увеличиваем лимит, чтобы гарантированно захватить все сообщения за последние 24 часа
            if mode == "historical":
                limit = batch_size * 200  # Увеличиваем лимит для historical режима (новые каналы)
            
            logger.debug("Fetching messages batch",
                        channel_id=channel_entity.id,
                        mode=mode,
                        limit=limit,
                        since_date=since_date.isoformat(),
                        offset_date=offset_date_param.isoformat() if offset_date_param else None)
            
            # Context7: Используем InputPeerChannel напрямую для чтения сообщений, если он есть
            # Context7 best practice: предпочитаем InputPeerChannel для снижения количества запросов
            # Если InputPeerChannel недоступен, используем get_input_entity как fallback
            channel_for_fetch = None
            
            if self._current_input_peer:
                from telethon.tl.types import InputPeerChannel
                if isinstance(self._current_input_peer, InputPeerChannel):
                    # Context7: ДИАГНОСТИКА - логируем параметры InputPeerChannel перед использованием
                    logger.debug("Validating InputPeerChannel before iter_messages",
                               channel_id=channel_entity.id,
                               input_channel_id=self._current_input_peer.channel_id,
                               has_access_hash=bool(self._current_input_peer.access_hash),
                               access_hash_value=self._current_input_peer.access_hash)
                    
                    # Context7: ВАЛИДАЦИЯ - проверяем, что channel_id не начинается с 100...
                    if self._current_input_peer.channel_id >= 1000000000000:
                        logger.error("Invalid InputPeerChannel.channel_id (too large, likely conversion error)",
                                   channel_id=channel_entity.id,
                                   input_channel_id=self._current_input_peer.channel_id)
                        channel_for_fetch = None
                    else:
                        # Context7: ВАЛИДАЦИЯ ПАРЫ - проверяем совместимость через get_entity
                        try:
                            validated_entity = await client.get_entity(self._current_input_peer)
                            # Context7: КРИТИЧНО - проверяем, что entity.id совпадает с input_channel_id
                            if hasattr(validated_entity, 'id') and validated_entity.id != self._current_input_peer.channel_id:
                                logger.error("InputPeerChannel validation failed: entity.id mismatch",
                                           channel_id=channel_entity.id,
                                           input_channel_id=self._current_input_peer.channel_id,
                                           entity_id=validated_entity.id)
                                channel_for_fetch = None
                            else:
                                channel_for_fetch = self._current_input_peer
                                logger.debug("InputPeerChannel validated successfully",
                                           channel_id=channel_entity.id,
                                           input_channel_id=self._current_input_peer.channel_id,
                                           entity_id=validated_entity.id if hasattr(validated_entity, 'id') else None)
                        except Exception as e:
                            logger.warning("InputPeerChannel validation failed, will use get_input_entity",
                                         channel_id=channel_entity.id,
                                         input_channel_id=self._current_input_peer.channel_id,
                                         error=str(e),
                                         error_type=type(e).__name__)
                            channel_for_fetch = None
                else:
                    logger.warning("_current_input_peer is not InputPeerChannel, using get_input_entity",
                                 channel_id=channel_entity.id,
                                 input_type=type(self._current_input_peer).__name__)
                    channel_for_fetch = None
            
            # Context7: Fallback - используем get_input_entity для получения актуального InputPeerChannel
            # Context7 best practice: get_input_entity использует кеш и возвращает InputPeerChannel
            if not channel_for_fetch:
                try:
                    input_entity = await client.get_input_entity(channel_entity)
                    if isinstance(input_entity, InputPeerChannel):
                        channel_for_fetch = input_entity
                        # Сохраняем для будущего использования
                        self._current_input_peer = input_entity
                        logger.debug("Using InputPeerChannel from get_input_entity",
                                   channel_id=channel_entity.id,
                                   input_channel_id=input_entity.channel_id,
                                   has_access_hash=bool(input_entity.access_hash))
                    else:
                        # Если get_input_entity не вернул InputPeerChannel, используем entity напрямую
                        channel_for_fetch = channel_entity
                        logger.debug("Using Channel entity (get_input_entity returned non-InputPeerChannel)",
                                   channel_id=channel_entity.id,
                                   input_type=type(input_entity).__name__)
                except Exception as e:
                    # В случае ошибки используем entity напрямую
                    channel_for_fetch = channel_entity
                    logger.warning("Failed to get input_entity, using Channel entity",
                                 channel_id=channel_entity.id,
                                 error=str(e))
            
            # Финальный fallback - если channel_for_fetch все еще None
            if not channel_for_fetch:
                channel_for_fetch = channel_entity
                logger.debug("Using Channel entity as final fallback",
                           channel_id=channel_entity.id,
                           entity_id=channel_entity.id if hasattr(channel_entity, 'id') else None)
            
            messages = await fetch_messages_with_retry(
                client,
                channel_for_fetch,
                limit=limit,
                redis_client=self.redis_client,
                offset_date=offset_date_param,  # Всегда None для обоих режимов (получаем последние сообщения)
                offset_id=None,  # Context7: НЕ используем offset_id - он возвращает старые сообщения!
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
            # Context7: УВЕЛИЧЕНО с 20 до 200 для предотвращения пропуска новых постов
            # Telegram API может вернуть новые сообщения не сразу или не в начале списка
            max_check_before_stop = 200  # Context7: Максимальное количество сообщений для проверки перед остановкой в incremental режиме
            
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
                        logger.info("Stopping message batch - reached since_date in historical mode",
                                   channel_id=channel_entity.id,
                                   mode=mode,
                                   reason="reached_since_date",
                                   message_date_utc=message_date_utc.isoformat(),
                                   since_date=since_date.isoformat(),
                                   messages_checked=messages_checked,
                                   messages_yielded=messages_yielded,
                                   messages_filtered=messages_filtered)
                        break
                else:  # incremental
                    # Context7: КРИТИЧНО - для incremental режима проверяем, есть ли сообщения новее since_date
                    # Улучшенная логика: проверяем несколько сообщений перед остановкой, чтобы не пропустить новые
                    # если они не в начале списка (например, из-за задержек в Telegram API)
                    # Дополнительно фильтруем по message.id > last_message_id для гарантии получения новых постов
                    is_newer_by_date = message_date_utc > since_date
                    is_newer_by_id = last_message_id is None or (hasattr(message, 'id') and message.id > last_message_id)
                    
                    if is_newer_by_date or is_newer_by_id:
                        if is_newer_by_date:
                            found_newer_messages = True
                        # Включаем сообщение в batch
                        batch.append(message)
                        messages_yielded += 1
                        messages_filtered += 1
                    elif found_newer_messages:
                        # Мы уже нашли новые сообщения, но теперь встретили старое - останавливаемся
                        logger.info("Stopping message batch - reached since_date after processing newer messages in incremental mode",
                                   channel_id=channel_entity.id,
                                   mode=mode,
                                   reason="reached_since_date_after_newer",
                                   message_date_utc=message_date_utc.isoformat(),
                                   since_date=since_date.isoformat(),
                                   messages_checked=messages_checked,
                                   messages_yielded=messages_yielded,
                                   messages_filtered=messages_filtered)
                        break
                    else:
                        # Сообщение старше since_date, но проверяем по ID
                        # Context7: Проверяем несколько сообщений перед остановкой, чтобы не пропустить новые
                        # которые могут быть не в начале списка из-за задержек или нехронологического порядка
                        is_newer_by_id = last_message_id is None or (hasattr(message, 'id') and message.id > last_message_id)
                        if is_newer_by_id:
                            # Сообщение новее по ID, даже если дата старше - включаем его
                            found_newer_messages = True
                            batch.append(message)
                            messages_yielded += 1
                            messages_filtered += 1
                            logger.debug("Message is newer by ID, including it",
                                       channel_id=channel_entity.id,
                                       mode=mode,
                                       message_id=message.id if hasattr(message, 'id') else None,
                                       message_date_utc=message_date_utc.isoformat(),
                                       last_message_id=last_message_id,
                                       since_date=since_date.isoformat())
                            continue
                        elif messages_checked < max_check_before_stop:
                            # Продолжаем проверку - возможно, новые сообщения дальше в списке
                            logger.debug("Message is older than since_date, checking more messages",
                                       channel_id=channel_entity.id,
                                       mode=mode,
                                       message_index=messages_checked,
                                       message_date_utc=message_date_utc.isoformat(),
                                       since_date=since_date.isoformat(),
                                       messages_checked=messages_checked,
                                       max_check_before_stop=max_check_before_stop)
                            continue
                        else:
                            # Проверили достаточно сообщений - новых нет
                            logger.info("Stopping message batch - no newer messages found in incremental mode",
                                       channel_id=channel_entity.id,
                                       mode=mode,
                                       reason="no_newer_messages",
                                       messages_checked=messages_checked,
                                       last_checked_message_date=message_date_utc.isoformat(),
                                       since_date=since_date.isoformat(),
                                       messages_yielded=messages_yielded,
                                       messages_filtered=messages_filtered)
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
            
            # Context7: Логирование завершения генерации батчей для диагностики
            logger.info("Finished generating message batches",
                       channel_id=channel_entity.id,
                       mode=mode,
                       total_messages_checked=messages_checked,
                       total_messages_yielded=messages_yielded,
                       total_messages_filtered=messages_filtered,
                       found_newer_messages=found_newer_messages if mode == "incremental" else None,
                       since_date=since_date.isoformat())
                
        except Exception as e:
            logger.error("Failed to fetch messages with retry", 
                        channel_id=channel_entity.id,
                        error=str(e))
            # Fallback к старому методу при ошибке
            # [C7-ID: dev-mode-017] Context7 best practice: для ОБОИХ режимов НЕ используем offset_date
            # Получаем последние сообщения и фильтруем локально по date >= since_date (historical) или date > since_date (incremental)
            # Это гарантирует, что мы получим ВСЕ новые сообщения независимо от режима
            # Context7: УВЕЛИЧЕНО для incremental режима с 100 до 500 для предотвращения пропуска новых постов
            limit_fallback = batch_size * 200 if mode == "historical" else batch_size * 500
            iter_params = {"limit": limit_fallback}
            
            # Context7: Используем InputPeerChannel напрямую для чтения сообщений, если он есть
            channel_for_iter = self._current_input_peer if self._current_input_peer else channel_entity
            
            async for message in client.iter_messages(channel_for_iter, **iter_params):
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
                    # Context7: Метрика для потерь постов (дубликаты)
                    posts_lost_total.labels(reason='duplicate').inc()
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
                    # Context7: Логируем, почему медиа не обрабатывается (на уровне WARNING для диагностики)
                    logger.warning(
                        "Media not processed - MediaProcessor or message.media check failed",
                        post_id=post_id,
                        has_media_processor=bool(self.media_processor),
                        has_message_media=bool(message.media),
                        has_telegram_client_manager=bool(self.telegram_client_manager),
                        channel_id=channel_id,
                        message_id=message.id
                    )
                    # Context7: Устанавливаем has_media в False, если медиа не обработано
                    post_data['has_media'] = False
                
                # Сохраняем информацию о медиа в post_data для последующего использования
                if media_files:
                    post_data['media_files'] = media_files
                    post_data['media_count'] = len(media_files)
                    # Context7: Извлекаем SHA256 для передачи в событие
                    post_data['media_sha256_list'] = [mf.sha256 for mf in media_files]
                    # Context7: Обновляем has_media на основе реально обработанных медиа
                    post_data['has_media'] = True
                elif message.media:
                    # Context7: Если медиа есть, но не обработано - логируем предупреждение
                    logger.warning(
                        "Message has media but media_files is empty",
                        post_id=post_id,
                        channel_id=channel_id,
                        has_media_processor=bool(self.media_processor),
                        has_telegram_client_manager=bool(self.telegram_client_manager),
                        message_id=message.id
                    )
                    # Context7: Устанавливаем has_media в False, если медиа не обработано
                    post_data['has_media'] = False
                
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
                
                # Context7: Парсинг каналов - глобальный процесс, не привязан к конкретному пользователю
                # Посты сохраняются глобально, изоляция происходит через user_channel при запросах пользователя
                # Проверяем только активность канала
                channel_active_check = await self.db_session.execute(
                    text("SELECT is_active FROM channels WHERE id = :channel_id LIMIT 1"),
                    {"channel_id": channel_id}
                )
                channel_active_row = channel_active_check.fetchone()
                is_channel_active = channel_active_row and channel_active_row.is_active
                
                if not is_channel_active:
                    # Канал неактивен - пропускаем парсинг
                    logger.warning("Channel is inactive, skipping parsing",
                                 channel_id=channel_id)
                    return {
                        "status": "skipped",
                        "reason": "channel_inactive",
                        "processed": 0,
                        "skipped": 0,
                        "max_message_date": None
                    }
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
                                # Context7: КРИТИЧНО - получаем через user_channel с проверкой tenant_id (приоритет 1)
                                result = await self.db_session.execute(
                                    text("""
                                        SELECT u.id::text
                                        FROM users u
                                        JOIN user_channel uc ON uc.user_id = u.id
                                        WHERE uc.channel_id = :channel_id
                                          AND u.telegram_id = :telegram_id
                                          AND u.tenant_id = :tenant_id
                                        LIMIT 1
                                    """),
                                    {"channel_id": channel_id, "telegram_id": int(user_id), "tenant_id": tenant_id}
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
                                    # Context7: КРИТИЧНО - Fallback с проверкой tenant_id для предотвращения утечки данных
                                    result2 = await self.db_session.execute(
                                        text("""
                                            SELECT id::text FROM users 
                                            WHERE telegram_id = :telegram_id 
                                              AND tenant_id = :tenant_id
                                            LIMIT 1
                                        """),
                                        {"telegram_id": int(user_id), "tenant_id": tenant_id}
                                    )
                                    row2 = result2.fetchone()
                                    if row2:
                                        user_uuid = str(row2[0])
                                        logger.debug("Found user_uuid via direct telegram_id lookup",
                                                   channel_id=channel_id,
                                                   user_uuid=user_uuid,
                                                   telegram_id=user_id,
                                                   tenant_id=tenant_id)
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
                                # Context7: КРИТИЧНО - пробуем найти user_id по telegram_id с проверкой tenant_id
                                try:
                                    result3 = await self.db_session.execute(
                                        text("""
                                            SELECT id::text FROM users 
                                            WHERE telegram_id = :telegram_id 
                                              AND tenant_id = :tenant_id
                                            LIMIT 1
                                        """),
                                        {"telegram_id": int(user_id) if user_id else None, "tenant_id": tenant_id}
                                    )
                                    row3 = result3.fetchone()
                                    if row3:
                                        user_uuid = str(row3[0])
                                        logger.info("Found user UUID via direct telegram_id lookup",
                                                  user_uuid=user_uuid, 
                                                  telegram_id=user_id,
                                                  tenant_id=tenant_id)
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
                                    
                                    # Context7: КРИТИЧНО - НЕ создаем user_channel автоматически при парсинге!
                                    # Подписки должны создаваться только при явном запросе пользователя через API
                                    # Проверяем, подписан ли пользователь на канал с фильтрацией по tenant_id
                                    check_subscription = await self.db_session.execute(
                                        text("""
                                            SELECT uc.user_id FROM user_channel uc
                                            JOIN users u ON uc.user_id = u.id
                                            WHERE u.telegram_id = :telegram_id
                                              AND u.tenant_id = :tenant_id
                                              AND uc.channel_id = :channel_id
                                              AND uc.is_active = true
                                            LIMIT 1
                                        """),
                                        {"telegram_id": int(user_id) if isinstance(user_id, str) else user_id, 
                                         "tenant_id": tenant_id,
                                         "channel_id": channel_id}
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
                                        
                                        # Context7: КРИТИЧНО - повторно пытаемся получить user_uuid с проверкой tenant_id
                                    result3 = await self.db_session.execute(
                                        text("""
                                            SELECT id::text FROM users 
                                            WHERE telegram_id = :telegram_id 
                                              AND tenant_id = :tenant_id
                                            LIMIT 1
                                        """),
                                        {"telegram_id": int(user_id) if user_id else None, "tenant_id": tenant_id}
                                    )
                                    row3 = result3.fetchone()
                                    if row3:
                                        user_uuid = str(row3[0])
                                        logger.info("Created user_channel and found user UUID",
                                                  user_uuid=user_uuid,
                                                  telegram_id=user_id,
                                                  tenant_id=tenant_id)
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
            # Context7: Метрика для пропусков дубликатов
            posts_skipped_duplicate_total.inc()
            return True
        
        # Проверка в БД (Context7: используем существующий уникальный индекс)
        # Context7: Проверяем БД в первую очередь для надежности
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
            # Context7: Уменьшен TTL до 15 минут для лучшей синхронизации с БД
            await self.redis_client.setex(cache_key, 900, "1")  # TTL 15 минут (было 3600 = 1 час)
            
            # Context7: Метрика для пропусков дубликатов
            posts_skipped_duplicate_total.inc()
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
                    redis_stream_maxlen = int(os.getenv("REDIS_STREAM_MAXLEN", "10000"))
                    await self.redis_client.xadd(stream_key, event_payload, maxlen=redis_stream_maxlen)  # Context7: ограничиваем размер stream
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
    
    async def _log_problematic_channel_stats(
        self,
        channel_id: str,
        channel_entity: Any,
        messages_processed: int,
        messages_skipped: int,
        batch_count: int,
        processing_time: float,
        mode: str
    ):
        """
        Context7: Специальное логирование для проблемных каналов.
        
        Логирует детальную статистику для каналов с:
        - Низким покрытием (< 10%)
        - Высоким процентом пропусков
        - Большими диапазонами message_id
        - Критическими потерями постов
        """
        try:
            # Получаем информацию о канале из БД
            result = await self.db_session.execute(
                text("""
                    SELECT 
                        c.username,
                        c.title,
                        c.tg_channel_id,
                        COUNT(p.id) as posts_count,
                        MIN(p.telegram_message_id) as min_message_id,
                        MAX(p.telegram_message_id) as max_message_id,
                        COUNT(DISTINCT DATE_TRUNC('day', p.posted_at)) as days_with_posts
                    FROM channels c
                    LEFT JOIN posts p ON p.channel_id = c.id
                    WHERE c.id = :channel_id
                    GROUP BY c.id, c.username, c.title, c.tg_channel_id
                """),
                {"channel_id": channel_id}
            )
            channel_row = result.fetchone()
            
            if not channel_row:
                return
            
            username = channel_row.username or "unknown"
            title = channel_row.title or "Unknown"
            posts_count = channel_row.posts_count or 0
            min_message_id = channel_row.min_message_id
            max_message_id = channel_row.max_message_id
            days_with_posts = channel_row.days_with_posts or 0
            
            # Вычисляем статистику
            message_id_range = None
            expected_posts = None
            coverage_percent = None
            gap_size = None
            
            if min_message_id and max_message_id:
                message_id_range = max_message_id - min_message_id + 1
                gap_size = message_id_range - posts_count if message_id_range > posts_count else 0
                
                # Ожидаемое количество постов = диапазон message_id
                expected_posts = message_id_range
                
                # Вычисляем покрытие (процент сохраненных постов от ожидаемого диапазона)
                if expected_posts > 0:
                    coverage_percent = (posts_count / expected_posts) * 100
            
            # Определяем, является ли канал проблемным
            is_problematic = False
            problem_reasons = []
            
            # Критерии проблемного канала
            if coverage_percent is not None and coverage_percent < 10:
                is_problematic = True
                problem_reasons.append(f"low_coverage_{coverage_percent:.2f}%")
            
            if gap_size and gap_size > 1000:
                is_problematic = True
                problem_reasons.append(f"large_gap_{gap_size}")
            
            if messages_processed == 0 and batch_count > 0:
                is_problematic = True
                problem_reasons.append("no_messages_processed")
            
            if messages_skipped > messages_processed * 2:
                is_problematic = True
                problem_reasons.append(f"high_skip_ratio_{messages_skipped}/{messages_processed}")
            
            # Специальное логирование для проблемных каналов
            if is_problematic:
                logger.warning(
                    "PROBLEMATIC_CHANNEL_DETECTED",
                    channel_id=channel_id,
                    channel_username=username,
                    channel_title=title,
                    tg_channel_id=channel_row.tg_channel_id,
                    messages_processed=messages_processed,
                    messages_skipped=messages_skipped,
                    batch_count=batch_count,
                    processing_time_seconds=processing_time,
                    mode=mode,
                    posts_count_in_db=posts_count,
                    min_message_id=min_message_id,
                    max_message_id=max_message_id,
                    message_id_range=message_id_range,
                    expected_posts=expected_posts,
                    gap_size=gap_size,
                    coverage_percent=coverage_percent,
                    days_with_posts=days_with_posts,
                    problem_reasons=problem_reasons,
                    stats=self.stats.copy()
                )
                
                # Обновляем метрику покрытия для проблемных каналов
                if coverage_percent is not None:
                    try:
                        channel_coverage_percent.labels(channel_username=username).set(coverage_percent)
                    except Exception as e:
                        logger.debug("Failed to update channel_coverage_percent metric",
                                   channel_id=channel_id,
                                   error=str(e))
            else:
                # Обычное логирование для нормальных каналов (менее детальное)
                logger.debug(
                    "Channel parsing stats",
                    channel_id=channel_id,
                    channel_username=username,
                    messages_processed=messages_processed,
                    messages_skipped=messages_skipped,
                    posts_count_in_db=posts_count,
                    coverage_percent=coverage_percent
                )
                
                # Обновляем метрику покрытия для всех каналов
                if coverage_percent is not None:
                    try:
                        channel_coverage_percent.labels(channel_username=username).set(coverage_percent)
                    except Exception as e:
                        logger.debug("Failed to update channel_coverage_percent metric",
                                   channel_id=channel_id,
                                   error=str(e))
        
        except Exception as e:
            # Не критичная ошибка, логируем и продолжаем
            logger.warning("Failed to log problematic channel stats",
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
