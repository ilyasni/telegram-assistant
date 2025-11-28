"""
Scheduler для периодического парсинга каналов.
Context7 best practice: автоопределение режима, устойчивость к сбоям через Redis HWM.

УПРОЩЁННАЯ ВЕРСИЯ для тестирования: использует psycopg2 напрямую, без полной интеграции ChannelParser.
"""

import asyncio
import os
import logging
import random
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import structlog
import redis.asyncio as redis
import psycopg2
from psycopg2.extras import RealDictCursor
from prometheus_client import Counter, Histogram, Gauge, Summary

from config import settings
from utils.time_utils import ensure_dt_utc

logger = structlog.get_logger()

# Prometheus метрики
parser_runs_total = Counter(
    'parser_runs_total',
    'Total parser runs',
    ['mode', 'status']
)

parsing_duration_seconds = Histogram(
    'parsing_duration_seconds',
    'Channel parsing duration',
    ['mode']
)

posts_parsed_total = Counter(
    'posts_parsed_total',
    'Total posts parsed',
    ['mode', 'status']
)

incremental_watermark_age_seconds = Gauge(
    'incremental_watermark_age_seconds',
    'Age of last_parsed_at watermark',
    ['channel_id']
)

scheduler_lock_acquired_total = Counter(
    'scheduler_lock_acquired_total',
    'Scheduler lock acquisition attempts',
    ['status']
)

parser_hwm_age_seconds = Gauge(
    'parser_hwm_age_seconds',
    'Age of Redis HWM watermark',
    ['channel_id']
)

parser_mode_forced_total = Counter(
    'parser_mode_forced_total',
    'Count of forced mode changes',
    ['reason']
)

scheduler_last_tick_ts_seconds = Gauge(
    'scheduler_last_tick_ts_seconds',
    'Unix timestamp of last scheduler tick'
)

parser_retries_total = Counter(
    'parser_retries_total',
    'Total parser retry attempts',
    ['reason']
)

# Context7: Summary для времени обработки каналов с перцентилями
# Используем Histogram вместо Summary для совместимости с prometheus_client
parser_channel_processing_seconds = Histogram(
    'parser_channel_processing_seconds',
    'Time spent processing a single channel',
    ['mode', 'status'],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
)

parser_floodwait_seconds_total = Counter(
    'parser_floodwait_seconds_total',
    'Total time spent waiting for FloodWait',
    ['channel_id']
)

# Context7: Метрики для мониторинга пропусков постов
posts_missing_duration_seconds = Gauge(
    'posts_missing_duration_seconds',
    'Duration of missing posts gap (difference between last_parsed_at and MAX(posted_at))',
    ['channel_id']
)

posts_backfill_triggered_total = Counter(
    'posts_backfill_triggered_total',
    'Total backfill operations triggered for missing posts',
    ['channel_id', 'reason']
)

# Расширенные метрики для адаптивных порогов
channel_last_post_timestamp_seconds = Gauge(
    'channel_last_post_timestamp_seconds',
    'Timestamp of last post (MAX(posted_at)) in epoch seconds',
    ['channel_id']
)

parser_last_success_seconds = Gauge(
    'parser_last_success_seconds',
    'Timestamp of last successful parsing in epoch seconds',
    ['channel_id']
)

adaptive_threshold_seconds = Gauge(
    'adaptive_threshold_seconds',
    'Current adaptive threshold for missing posts detection in seconds',
    ['channel_id']
)

channel_gap_seconds = Gauge(
    'channel_gap_seconds',
    'Current gap between now and last post (now - MAX(posted_at)) in seconds',
    ['channel_id']
)

backfill_jobs_total = Counter(
    'backfill_jobs_total',
    'Total backfill jobs (enqueued, completed, failed)',
    ['channel_id', 'status']
)

interarrival_seconds = Histogram(
    'interarrival_seconds',
    'Interarrival time between posts in seconds',
    ['channel_id'],
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400]  # 1m to 24h
)


class ParseAllChannelsTask:
    """Scheduler для периодического парсинга всех активных каналов."""
    
    def __init__(self, config, db_url: str, redis_client: Optional[Any], parser=None, app_state: Optional[Dict] = None, telegram_client_manager: Optional[Any] = None, media_processor: Optional[Any] = None):
        self.config = config
        self.db_url = db_url
        self.redis: Optional[redis.Redis] = redis_client  # Context7: Используем переданный async Redis клиент
        self.parser = parser  # Будет инициализирован при необходимости (legacy, для обратной совместимости)
        self.app_state = app_state
        self.telegram_client_manager = telegram_client_manager  # TelegramClientManager для парсинга
        self.media_processor = media_processor  # MediaProcessor для обработки медиа
        self.interval_sec = int(os.getenv("PARSER_SCHEDULER_INTERVAL_SEC", "300"))
        self.enabled = os.getenv("FEATURE_INCREMENTAL_PARSING_ENABLED", "true").lower() == "true"
        
        # Semaphore for concurrency control
        self.semaphore = asyncio.Semaphore(self.config.max_concurrency)
        
        # Context7: Создаем engine и session factory для создания отдельных sessions на канал
        # Это предотвращает дедлоки при параллельной обработке каналов
        import re
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        
        db_url_async = self.db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        parsed = urlparse(db_url_async)
        qs = parse_qs(parsed.query)
        # Remove asyncpg-unsupported parameters
        for key in ['connect_timeout', 'application_name', 'keepalives', 'keepalives_idle', 'keepalives_interval', 'keepalives_count']:
            qs.pop(key, None)
        new_query = urlencode(qs, doseq=True)
        db_url_async = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        
        # Context7: Добавляем таймауты для предотвращения зависаний
        self.engine = create_async_engine(
            db_url_async, 
            pool_pre_ping=True, 
            pool_size=10,  # Увеличено для параллельной обработки
            max_overflow=20,
            pool_timeout=30,
            connect_args={
                "command_timeout": 60,
                "server_settings": {
                    "application_name": "telethon_parser"
                }
            }
        )
        self.async_session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        
        logger.info(
            "ParseAllChannelsTask initialized (simplified version for testing)",
            interval_sec=self.interval_sec,
            enabled=self.enabled,
            max_concurrency=self.config.max_concurrency,
            has_parser=parser is not None,
            has_media_processor=media_processor is not None
        )
    
    async def run_forever(self):
        """Бесконечный цикл парсинга."""
        if not self.enabled:
            logger.info("Incremental parsing disabled, scheduler not started")
            return
        
        # Инициализация Redis, если не передан
        if self.redis is None:
            try:
                # Context7: Создаём async Redis клиент с decode_responses для совместимости с parser и таймаутами
                self.redis = redis.from_url(
                    settings.redis_url, 
                    decode_responses=True,
                    socket_connect_timeout=10,
                    socket_timeout=30,
                    retry_on_timeout=True
                )
                logger.info("Redis initialized for scheduler")
            except Exception as e:
                logger.error(f"Failed to initialize Redis: {str(e)}")
        
        logger.info("Starting parse_all_channels scheduler loop (active parsing mode)")
        
        # Context7: Реальный парсинг с мониторингом
        while True:
            try:
                await self._run_tick()
            except Exception as e:
                logger.exception("scheduler tick failed", error=str(e))
            
            await asyncio.sleep(self.interval_sec)
    
    async def _acquire_lock(self) -> bool:
        """Try to acquire scheduler lock"""
        instance_id = os.getenv("HOSTNAME", "default")
        lock_key = "parse_all_channels:lock"
        ttl = self.interval_sec * 2
        
        try:
            # Initialize Redis if not available
            if self.redis is None:
                # Context7: Создаём async Redis клиент (redis.asyncio)
                self.redis = redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=10,
                    socket_timeout=30,
                    retry_on_timeout=True
                )
                logger.info("Redis initialized for lock acquisition")
            
            # Context7: Проверяем текущее состояние lock перед попыткой установки
            existing_lock = await self.redis.get(lock_key)
            if existing_lock:
                logger.debug("Lock exists in Redis",
                           lock_key=lock_key,
                           existing_value=existing_lock,
                           instance_id=instance_id)
            
            # Context7: async Redis - используем await для set()
            acquired = await self.redis.set(
                lock_key,
                instance_id,
                nx=True,
                ex=ttl
            )
            
            logger.debug("Lock acquisition attempt",
                        lock_key=lock_key,
                        instance_id=instance_id,
                        acquired=acquired,
                        existing_lock=existing_lock)
            
            if acquired:
                scheduler_lock_acquired_total.labels(status="acquired").inc()
                logger.info("Lock acquired successfully",
                           lock_key=lock_key,
                           instance_id=instance_id,
                           ttl=ttl)
                if self.app_state:
                    self.app_state["scheduler"]["lock_owner"] = instance_id
                return True
            else:
                scheduler_lock_acquired_total.labels(status="missed").inc()
                logger.debug("Lock acquisition failed - lock already held",
                           lock_key=lock_key,
                           instance_id=instance_id,
                           existing_lock=existing_lock)
                return False
        except Exception as e:
            logger.error("Failed to acquire lock",
                        lock_key=lock_key,
                        instance_id=instance_id,
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True)
            return False
    
    async def _release_lock(self):
        """Release scheduler lock"""
        try:
            # Context7: async Redis - используем await для delete()
            # Context7: Проверяем, что lock существует перед удалением
            lock_key = "parse_all_channels:lock"
            deleted = await self.redis.delete(lock_key)
            if deleted > 0:
                logger.debug("Lock released successfully", lock_key=lock_key)
            else:
                logger.warning("Lock was not found when trying to release", lock_key=lock_key)
            
            if self.app_state:
                self.app_state["scheduler"]["lock_owner"] = None
        except Exception as e:
            logger.error("Failed to release lock", 
                        error=str(e), 
                        error_type=type(e).__name__,
                        exc_info=True)
    
    async def _update_hwm(self, channel_id: str, max_message_date: datetime):
        """Update Redis HWM watermark"""
        try:
            hwm_key = f"parse_hwm:{channel_id}"
            # Context7: async Redis - используем await для set()
            await self.redis.set(
                hwm_key,
                max_message_date.isoformat(),
                ex=86400  # TTL 24 hours
            )
            logger.debug("Updated HWM", extra={"channel_id": channel_id, "hwm": max_message_date.isoformat()})
        except Exception as e:
            logger.error(f"Failed to update HWM for channel {channel_id}: {str(e)}")
    
    async def _clear_hwm(self, channel_id: str):
        """Clear Redis HWM after successful parsing"""
        try:
            hwm_key = f"parse_hwm:{channel_id}"
            # Context7: async Redis - используем await для delete()
            await self.redis.delete(hwm_key)
            logger.debug("Cleared HWM", extra={"channel_id": channel_id})
        except Exception as e:
            logger.error(f"Failed to clear HWM for channel {channel_id}: {str(e)}")
    
    async def _update_last_parsed_at_async(self, channel_id: str, db_session):
        """
        Context7: Асинхронное обновление last_parsed_at через SQLAlchemy.
        Используется для каналов с ошибками/пропусками, чтобы они не оставались с NULL.
        
        ВАЖНО: channel_parser.py уже обновляет last_parsed_at после успешного парсинга,
        поэтому этот метод используется только для ошибок/пропусков.
        """
        try:
            from sqlalchemy import text
            now = datetime.now(timezone.utc)
            
            # Context7: Используем async SQLAlchemy для неблокирующего обновления
            result = await db_session.execute(
                text("UPDATE channels SET last_parsed_at = :now WHERE id = :channel_id"),
                {"now": now, "channel_id": channel_id}
            )
            
            rows_affected = result.rowcount
            if rows_affected == 0:
                logger.warning("No rows updated for last_parsed_at", 
                             channel_id=channel_id)
            
            logger.debug("Updated last_parsed_at for channel",
                        channel_id=channel_id,
                        rows_affected=rows_affected)
        except Exception as e:
            logger.error("Failed to update last_parsed_at for channel",
                        channel_id=channel_id,
                        error=str(e),
                        error_type=type(e).__name__)
    
    async def _update_last_parsed_at(self, channel_id: str):
        """
        DEPRECATED: Используйте _update_last_parsed_at_async вместо этого метода.
        
        Context7: Этот метод использует синхронный psycopg2 и блокирует event loop.
        Оставлен для обратной совместимости, но должен быть удален после миграции.
        """
        try:
            import psycopg2
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor()
            
            # Context7: Обновляем last_parsed_at на текущее время для отслеживания попытки парсинга
            cursor.execute("""
                UPDATE channels 
                SET last_parsed_at = NOW() 
                WHERE id = %s
            """, (channel_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.debug("Updated last_parsed_at for channel with error",
                        channel_id=channel_id)
        except Exception as e:
            logger.error("Failed to update last_parsed_at for channel with error",
                        channel_id=channel_id,
                        error=str(e),
                        error_type=type(e).__name__)
    
    def _get_system_user_and_tenant_sync(self) -> Tuple[int, str]:
        """Синхронная версия для использования в run_in_executor.
        
        Context7: Синхронный psycopg2 вызов блокирует event loop при параллельной обработке.
        Используем run_in_executor для неблокирующего выполнения.
        """
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT u.telegram_id as telegram_id, u.tenant_id as tenant_id
                FROM users u
                WHERE u.telegram_auth_status = 'authorized' AND u.telegram_id IS NOT NULL
                ORDER BY u.telegram_auth_created_at DESC
                LIMIT 1
            """)
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result and result['telegram_id']:
                # Context7: telegram_id должен быть int для get_client()
                return int(result['telegram_id']), str(result['tenant_id'])
            else:
                # Fallback: попробуем найти любой telegram_id
                conn = psycopg2.connect(self.db_url)
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL LIMIT 1")
                fallback_result = cursor.fetchone()
                cursor.close()
                conn.close()
                if fallback_result and fallback_result['telegram_id']:
                    return int(fallback_result['telegram_id']), "00000000-0000-0000-0000-000000000000"
                return 0, "00000000-0000-0000-0000-000000000000"
                
        except Exception as e:
            logger.error(f"Failed to get system user/tenant: {str(e)}")
            return 0, "00000000-0000-0000-0000-000000000000"
    
    async def _get_system_user_and_tenant(self) -> Tuple[int, str]:
        """Get system telegram_id (int) and tenant_id (str) from the first authorized session.
        
        Context7: Обертываем синхронный psycopg2 вызов в run_in_executor для неблокирующего выполнения.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            # Context7: Используем run_in_executor для неблокирующего выполнения синхронного DB запроса
            telegram_id, tenant_id = await asyncio.wait_for(
                loop.run_in_executor(None, self._get_system_user_and_tenant_sync),
                timeout=5.0
            )
            return telegram_id, tenant_id
        except asyncio.TimeoutError:
            logger.error("Timeout getting system user/tenant")
            return 0, "00000000-0000-0000-0000-000000000000"
        except Exception as e:
            logger.error(f"Failed to get system user/tenant: {str(e)}")
            return 0, "00000000-0000-0000-0000-000000000000"
    
    async def _parse_channel_with_retry_internal(self, channel: Dict[str, Any], mode: str, parser, telegram_id: int, tenant_id: str):
        """
        Internal method for parsing channel with exponential backoff retry and FloodWait handling.
        
        Context7: Этот метод используется внутри parse_single_channel, где parser уже создан
        с отдельным DB session. Все блокирующие операции уже выполнены до вызова этого метода.
        
        Args:
            channel: Channel data dictionary
            mode: Parsing mode (historical/incremental)
            parser: ChannelParser instance with separate DB session
            telegram_id: Telegram user ID (int)
            tenant_id: Tenant ID (str)
            
        Returns:
            Parsing result or None if all retries exhausted
        """
        max_retries = self.config.retry_max
        base_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                async with self.semaphore:
                    logger.debug(f"Parsing channel {channel['id']} with retry - mode={mode}, attempt={attempt + 1}")
                    
                    # Call actual parser
                    result = await parser.parse_channel_messages(
                        channel_id=channel['id'],
                        user_id=str(telegram_id),  # user_id для парсера — строка
                        tenant_id=tenant_id,
                        mode=mode
                    )
                    
                    # Update HWM if we have max_message_date
                    if result.get("max_message_date"):
                        # Context7 best practice: безопасная обработка через ensure_dt_utc
                        max_date = ensure_dt_utc(result["max_message_date"])
                        if max_date:
                            await self._update_hwm(channel['id'], max_date)
                    
                    # Clear HWM after successful parsing
                    await self._clear_hwm(channel['id'])
                    
                    return result
                    
            except Exception as e:
                error_type = type(e).__name__
                
                # FloodWait handling
                if "FloodWait" in error_type or "FLOOD_WAIT" in str(e):
                    # Extract wait time from error message if available
                    wait_match = re.search(r'(\d+)', str(e))
                    wait_seconds = int(wait_match.group(1)) if wait_match else 10
                    wait_seconds = min(wait_seconds, 300)  # Cap at 5 minutes
                    
                    logger.warning(f"FloodWait {wait_seconds:.1f}s for channel {channel['id']} - attempt={attempt + 1}/{max_retries}")
                    
                    parser_retries_total.labels(reason="floodwait").inc()
                    parser_floodwait_seconds_total.labels(channel_id=channel['id']).inc(wait_seconds)
                    
                    # Context7: Устанавливаем blocked_until в БД вместо sleep
                    blocked_until = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
                    try:
                        import psycopg2
                        conn = psycopg2.connect(self.db_url)
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE channels 
                            SET blocked_until = %s 
                            WHERE id = %s
                        """, (blocked_until, channel['id']))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        logger.info("Channel blocked_until set due to FloodWait",
                                   channel_id=channel['id'],
                                   wait_seconds=wait_seconds,
                                   blocked_until=blocked_until.isoformat())
                    except Exception as db_error:
                        logger.error("Failed to set blocked_until for channel",
                                    channel_id=channel['id'],
                                    error=str(db_error))
                        # Fallback: используем sleep если не удалось обновить БД
                        await asyncio.sleep(wait_seconds)
                    
                    # Context7: Прерываем попытки парсинга для этого канала
                    # Канал будет пропущен в следующих тиках до истечения blocked_until
                    return {"status": "error", "error": "flood_wait", "blocked_until": blocked_until.isoformat()}
                
                # Transient errors (timeout, connection)
                elif any(err_type in error_type for err_type in ["Timeout", "Connection", "Network"]):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Transient error for channel {channel['id']}: {e}, retrying in {delay:.1f}s - attempt={attempt + 1}")
                    
                    parser_retries_total.labels(reason="transient_error").inc()
                    await asyncio.sleep(delay)
                    continue
                
                # Permanent error or last attempt
                else:
                    if attempt == max_retries - 1:
                        logger.error(f"Parse channel {channel['id']} failed after {max_retries} retries: {str(e)}")
                        parser_retries_total.labels(reason="failed").inc()
                        parser_runs_total.labels(mode=mode, status="failed").inc()
                        raise
                    else:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"Parse error for channel {channel['id']}: {str(e)}, retrying in {delay:.1f}s - attempt={attempt + 1}")
                        parser_retries_total.labels(reason="parse_error").inc()
                        await asyncio.sleep(delay)
        
        logger.error(f"Parse channel exhausted all retries for channel {channel['id']}")
        return None
    
    async def parse_single_channel(self, channel: Dict[str, Any], tick_start_time: datetime):
        """Обработка одного канала с отдельным DB session и полным таймаутом.
        
        Context7: Все блокирующие операции (get_client, создание parser'а) внутри этого метода,
        который обернут в asyncio.wait_for на уровне задачи. Отдельный DB session для каждого канала
        предотвращает дедлоки при параллельной обработке.
        
        Args:
            channel: Словарь с данными канала
            tick_start_time: Время начала tick'а для проверки времени
            
        Returns:
            Результат обработки канала или None
        """
        process_start_time = datetime.now(timezone.utc)
        mode = None
        status = "unknown"  # Будет обновлен в процессе обработки
        
        # Context7: Логирование начала обработки канала
        is_new_channel = channel.get('last_parsed_at') is None
        logger.info(
            "CHANNEL_PARSE_START",
            channel_id=channel['id'],
            channel_title=channel.get('title'),
            channel_username=channel.get('username'),
            is_new_channel=is_new_channel,
            last_parsed_at=channel.get('last_parsed_at')
        )
        
        try:
            # Get HWM from Redis
            hwm_key = f"parse_hwm:{channel['id']}"
            # Context7: async Redis - используем await для get()
            hwm_raw = await self.redis.get(hwm_key)
            
            # Context7 best practice: безопасная обработка типов через ensure_dt_utc
            hwm_ts = ensure_dt_utc(hwm_raw)
            if hwm_ts:
                age_seconds = (datetime.now(timezone.utc) - hwm_ts).total_seconds()
                parser_hwm_age_seconds.labels(channel_id=channel['id']).set(age_seconds)
            
            # Определение режима
            mode = self._decide_mode(channel)
            
            # Context7: Все блокирующие операции внутри этого метода (внутри wait_for)
            # Получение telegram_id и tenant_id
            # Context7: Используем async версию, которая обертывает синхронный вызов в run_in_executor
            logger.debug("Getting system user/tenant",
                        channel_id=channel['id'])
            telegram_id, tenant_id = await self._get_system_user_and_tenant()
            logger.debug("Got system user/tenant",
                        channel_id=channel['id'],
                        telegram_id=telegram_id,
                        has_tenant_id=bool(tenant_id))
            
            if not telegram_id or telegram_id == 0:
                logger.warning("No telegram_id found in database, skipping parsing",
                             channel_id=channel['id'])
                status = "skipped"
                return {"status": "skipped", "reason": "no_telegram_id", "parsed": 0, "max_message_date": None}
            
            # Context7: Получение Telegram клиента - внутри wait_for
            if not self.telegram_client_manager:
                logger.warning("TelegramClientManager not available, skipping parsing",
                             channel_id=channel['id'])
                status = "skipped"
                return {"status": "skipped", "reason": "no_client_manager", "parsed": 0, "max_message_date": None}
            
            logger.debug("Getting telegram client",
                        channel_id=channel['id'],
                        telegram_id=telegram_id)
            telegram_client = await self.telegram_client_manager.get_client(telegram_id)
            if not telegram_client:
                logger.warning("No telegram client available, skipping parsing",
                             channel_id=channel['id'],
                             telegram_id=telegram_id)
                status = "skipped"
                return {"status": "skipped", "reason": "no_client", "parsed": 0, "max_message_date": None}
            logger.debug("Got telegram client",
                        channel_id=channel['id'])
            
            # Context7: Создаем отдельный DB session для каждого канала
            # Это предотвращает дедлоки при параллельной обработке
            async with self.async_session_factory() as db_session:
                # Context7: Создаем parser с новым session для каждого канала
                from services.channel_parser import ChannelParser, ParserConfig
                
                config = ParserConfig()
                config.db_url = self.db_url
                config.redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
                
                parser = ChannelParser(
                    config=config,
                    db_session=db_session,
                    event_publisher=None,
                    redis_client=self.redis,
                    telegram_client_manager=self.telegram_client_manager,
                    media_processor=self.media_processor
                )
                
                # Context7: Парсинг канала с retry (все внутри wait_for)
                result = await self._parse_channel_with_retry_internal(channel, mode, parser, telegram_id, tenant_id)
                
                # Context7: Обработка результатов парсинга
                # ВАЖНО: channel_parser.py уже обновляет last_parsed_at после успешного парсинга,
                # поэтому НЕ вызываем _update_last_parsed_at здесь, чтобы избежать дублирования
                if result and "messages_processed" in result:
                    parsed_count = result.get("messages_processed", 0)
                    posts_parsed_total.labels(mode=mode, status="success").inc(parsed_count)
                    parser_runs_total.labels(mode=mode, status="ok").inc()
                    status = "ok"
                    logger.info("CHANNEL_PARSE_END",
                               channel_id=channel['id'],
                               status="ok",
                               messages_processed=parsed_count,
                               mode=mode)
                    return result
                elif result and result.get("status") == "skipped":
                    parser_runs_total.labels(mode=mode, status="skipped").inc()
                    status = "skipped"
                    # Context7: Для пропущенных каналов обновляем last_parsed_at через async SQLAlchemy
                    await self._update_last_parsed_at_async(channel['id'], db_session)
                    logger.info("CHANNEL_PARSE_END",
                               channel_id=channel['id'],
                               status="skipped",
                               reason=result.get("reason"),
                               mode=mode)
                    return result
                elif result and result.get("status") == "error":
                    error_type = result.get("error", "unknown")
                    logger.warning("CHANNEL_PARSE_END",
                                 channel_id=channel['id'],
                                 status="error",
                                 error_type=error_type,
                                 mode=mode)
                    parser_runs_total.labels(mode=mode, status="error").inc()
                    status = "error"
                    # Context7: Для ошибок обновляем last_parsed_at через async SQLAlchemy
                    await self._update_last_parsed_at_async(channel['id'], db_session)
                    return result
                elif result is None:
                    logger.warning("CHANNEL_PARSE_END",
                                 channel_id=channel['id'],
                                 status="failed",
                                 reason="all_retries_exhausted",
                                 mode=mode)
                    parser_runs_total.labels(mode=mode, status="failed").inc()
                    status = "failed"
                    # Context7: Для failed обновляем last_parsed_at через async SQLAlchemy
                    await self._update_last_parsed_at_async(channel['id'], db_session)
                    return {"status": "failed", "reason": "all_retries_exhausted"}
                else:
                    logger.warning("CHANNEL_PARSE_END",
                                 channel_id=channel['id'],
                                 status="failed",
                                 reason="unexpected_format",
                                 result_keys=list(result.keys()) if isinstance(result, dict) else type(result),
                                 mode=mode)
                    parser_runs_total.labels(mode=mode, status="failed").inc()
                    status = "failed"
                    # Context7: Для failed обновляем last_parsed_at через async SQLAlchemy
                    await self._update_last_parsed_at_async(channel['id'], db_session)
                    return {"status": "failed", "reason": "unexpected_format"}
            
            # Context7: Если telegram_client_manager недоступен, просто возвращаем skipped
            status = "skipped"
            logger.info("CHANNEL_PARSE_END",
                       channel_id=channel['id'],
                       status="skipped",
                       reason="no_client_manager",
                       mode=mode)
            return {"status": "skipped", "reason": "no_client_manager", "parsed": 0, "max_message_date": None}
            
        except Exception as e:
            status = "error"
            logger.error("CHANNEL_PARSE_ERROR",
                        channel_id=channel['id'],
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True)
            # Context7: Для исключений обновляем last_parsed_at через async SQLAlchemy (если db_session доступен)
            # Используем try/except, так как db_session может быть недоступен
            try:
                async with self.async_session_factory() as db_session:
                    await self._update_last_parsed_at_async(channel['id'], db_session)
            except Exception as update_error:
                logger.warning("Failed to update last_parsed_at after exception",
                             channel_id=channel['id'],
                             error=str(update_error))
            return {"status": "error", "error": str(e), "error_type": type(e).__name__}
        finally:
            # Context7: Записываем метрику времени обработки ОДИН РАЗ в finally
            # Убрали дублирование из except блока
            process_duration = (datetime.now(timezone.utc) - process_start_time).total_seconds()
            parser_channel_processing_seconds.labels(mode=mode or "unknown", status=status).observe(process_duration)
    
    async def _run_tick(self):
        """Run scheduler tick with lock protection.
        
        Context7: Улучшенная обработка ошибок для предотвращения падения процесса.
        Все исключения логируются, но не прерывают выполнение scheduler'а.
        """
        if not await self._acquire_lock():
            logger.info("Lock held by another instance, skipping tick")
            return
        
        tick_start_time = datetime.now(timezone.utc)
        lock_acquired = True
        try:
            logger.info("Running scheduler tick (lock acquired)")
            
            # Получение активных каналов с обработкой ошибок
            try:
                channels = self._get_active_channels()
            except Exception as e:
                logger.error("Failed to get active channels in tick",
                           error=str(e),
                           error_type=type(e).__name__,
                           exc_info=True)
                channels = []
            
            logger.info(
                "Starting scheduler tick",
                channels_count=len(channels),
                tick_interval_sec=self.interval_sec
            )
            
            if not channels:
                logger.warning("No active channels found for parsing")
                # Context7: Не делаем return здесь, чтобы finally блок освободил lock
                # Просто пропускаем парсинг каналов
            
            # Context7: Safety-guard для времени tick'а (вторичный механизм)
            # Динамическое время не требуется, так как число каналов ограничено CHANNELS_PER_TICK
            max_tick_duration = min(self.interval_sec * 0.8, 400)  # Фиксированный лимит как safety-guard
            
            # Context7: Приоритизация уже реализована в SQL через ORDER BY last_parsed_at NULLS FIRST
            # channels уже отсортированы из БД, дополнительная сортировка не требуется
            channels_sorted = channels
            
            # Context7: Логируем выбор каналов для тика
            for channel in channels_sorted:
                is_new_channel = channel.get('last_parsed_at') is None
                logger.info("CHANNEL_SELECTED_FOR_TICK",
                           channel_id=channel['id'],
                           channel_title=channel.get('title'),
                           channel_username=channel.get('username'),
                           is_new_channel=is_new_channel,
                           last_parsed_at=channel.get('last_parsed_at'))
            
            # Context7: Параллельная обработка только выбранных N каналов (не всех!)
            # Context7: Используем новый parse_single_channel с отдельными DB sessions
            async def process_channel_wrapper(channel, tick_start_time, max_tick_duration):
                """Wrapper для обработки одного канала с проверкой времени"""
                # Context7: Проверяем время ПЕРЕД началом обработки канала
                # Это предотвращает запуск задач, которые заведомо не успеют завершиться
                elapsed = (datetime.now(timezone.utc) - tick_start_time).total_seconds()
                if elapsed >= max_tick_duration:
                    logger.debug("Skipping channel due to tick time limit",
                                channel_id=channel['id'],
                                elapsed_seconds=elapsed,
                                max_duration_seconds=max_tick_duration)
                    return {"status": "skipped", "reason": "tick_time_limit", "channel_id": channel.get('id')}
                # Context7: Вызываем parse_single_channel, который сам имеет таймаут individual_task_timeout
                return await self.parse_single_channel(channel, tick_start_time)
            
            # Создаем задачи только для выбранных N каналов (не всех!)
            # Context7: Создаем Task объекты для возможности отмены при таймауте
            # Context7: Добавляем индивидуальный таймаут для каждой задачи
            # Увеличено до 180 секунд, так как парсинг канала может занимать время
            # (получение клиента, создание parser'а, сам парсинг, обработка медиа)
            # Особенно важно для больших каналов с большим количеством постов
            individual_task_timeout = 180.0  # 180 секунд на обработку одного канала
            
            async def process_channel_with_timeout(channel, tick_start_time, max_tick_duration):
                """Wrapper с индивидуальным таймаутом для каждого канала"""
                try:
                    return await asyncio.wait_for(
                        process_channel_wrapper(channel, tick_start_time, max_tick_duration),
                        timeout=individual_task_timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning("CHANNEL_PARSE_TIMEOUT",
                                 channel_id=channel.get('id'),
                                 channel_title=channel.get('title'),
                                 timeout_seconds=individual_task_timeout)
                    return {"status": "timeout", "channel_id": channel.get('id')}
                except Exception as e:
                    logger.error("CHANNEL_PARSE_ERROR",
                               channel_id=channel.get('id'),
                               error=str(e),
                               error_type=type(e).__name__,
                               exc_info=True)
                    return {"status": "error", "channel_id": channel.get('id'), "error": str(e)}
            
            channel_batch_size = int(os.getenv("CHANNEL_BATCH_SIZE", "20"))
            channel_batch_size = max(1, channel_batch_size)
            
            results: List[Tuple[Dict[str, Any], Any]] = []
            channels_processed = 0
            
            for batch_start in range(0, len(channels_sorted), channel_batch_size):
                elapsed_before_batch = (datetime.now(timezone.utc) - tick_start_time).total_seconds()
                if elapsed_before_batch >= max_tick_duration:
                    logger.debug("Stopping batching due to tick time budget",
                                elapsed_seconds=elapsed_before_batch,
                                max_duration_seconds=max_tick_duration,
                                batch_start=batch_start)
                    break
                
                batch = channels_sorted[batch_start:batch_start + channel_batch_size]
                batch_tasks = [
                    asyncio.create_task(process_channel_with_timeout(channel, tick_start_time, max_tick_duration))
                    for channel in batch
                ]
                
                try:
                    # Context7: Вычисляем оставшееся время для батча
                    # Используем min(remaining_timeout, individual_task_timeout + 10) для безопасности
                    # +10 секунд - запас на завершение задач после таймаута
                    remaining_timeout = max(1.0, max_tick_duration - elapsed_before_batch)
                    # Context7: Ограничиваем таймаут батча, чтобы не превышать individual_task_timeout
                    # Это предотвращает ситуации, когда батч ждет дольше, чем может работать одна задача
                    batch_timeout = min(remaining_timeout, individual_task_timeout + 10.0)
                    batch_results = await asyncio.wait_for(
                        asyncio.gather(*batch_tasks, return_exceptions=True),
                        timeout=batch_timeout
                    )
                except asyncio.TimeoutError:
                    logger.error("Channel batch processing timeout",
                                batch_size=len(batch),
                                timeout_seconds=remaining_timeout,
                                elapsed_since_tick_start=elapsed_before_batch)
                    for task in batch_tasks:
                        if not task.done():
                            task.cancel()
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*batch_tasks, return_exceptions=True),
                            timeout=5.0
                        )
                    except Exception:
                        pass
                    batch_results = []
                except Exception as e:
                    logger.error("Failed to gather channel batch results",
                               error=str(e),
                               error_type=type(e).__name__,
                               batch_size=len(batch),
                               exc_info=True)
                    for task in batch_tasks:
                        if not task.done():
                            task.cancel()
                    batch_results = []
                
                for idx, result in enumerate(batch_results):
                    results.append((batch[idx], result))
                
                elapsed_after_batch = (datetime.now(timezone.utc) - tick_start_time).total_seconds()
                if elapsed_after_batch >= max_tick_duration:
                    logger.debug("Stopping batching after batch due to tick time budget",
                                elapsed_seconds=elapsed_after_batch,
                                max_duration_seconds=max_tick_duration,
                                batch_start=batch_start)
                    break
            
            for channel, result in results:
                if isinstance(result, Exception):
                    logger.error("Channel processing exception",
                               channel_id=channel['id'],
                               error=str(result),
                               error_type=type(result).__name__,
                               exc_info=True)
                    # Context7: Обновляем last_parsed_at через async SQLAlchemy для ошибок
                    try:
                        async with self.async_session_factory() as db_session:
                            await self._update_last_parsed_at_async(channel['id'], db_session)
                    except Exception as update_error:
                        logger.warning("Failed to update last_parsed_at after exception",
                                     channel_id=channel['id'],
                                     error=str(update_error))
                    channels_processed += 1
                elif result is not None:
                    status = result.get("status", "unknown")
                    if status in ["timeout", "error", "failed"]:
                        # Context7: Обновляем last_parsed_at через async SQLAlchemy для ошибок
                        try:
                            async with self.async_session_factory() as db_session:
                                await self._update_last_parsed_at_async(channel['id'], db_session)
                        except Exception as update_error:
                            logger.warning("Failed to update last_parsed_at after error",
                                         channel_id=channel['id'],
                                         error=str(update_error))
                    channels_processed += 1
                else:
                    continue
            
            # Context7: Логируем прогресс после обработки всех каналов
            elapsed = (datetime.now(timezone.utc) - tick_start_time).total_seconds()
            logger.info(
                "Tick completed",
                channels_processed=channels_processed,
                channels_total=len(channels),
                elapsed_seconds=elapsed
            )
            
            # Update scheduler freshness metric
            now_ts = datetime.now(timezone.utc).timestamp()
            scheduler_last_tick_ts_seconds.set(now_ts)
            
            # Update app_state if available
            if self.app_state:
                self.app_state["scheduler"]["last_tick_ts"] = datetime.now(timezone.utc).isoformat()
                self.app_state["scheduler"]["status"] = "running"
            
            tick_duration = (datetime.now(timezone.utc) - tick_start_time).total_seconds()
            logger.info(
                "Scheduler tick completed",
                channels_processed=channels_processed,
                channels_total=len(channels) if channels else 0,
                duration_seconds=tick_duration
            )
            
        except Exception as e:
            # Context7: Обработка всех неожиданных ошибок в тике для предотвращения падения процесса
            logger.error("Unexpected error in scheduler tick",
                       error=str(e),
                       error_type=type(e).__name__,
                       exc_info=True)
            # Context7: Обновляем метрику даже при ошибке для отслеживания активности scheduler'а
            try:
                now_ts = datetime.now(timezone.utc).timestamp()
                scheduler_last_tick_ts_seconds.set(now_ts)
            except Exception:
                pass  # Игнорируем ошибки обновления метрики
            # Не прерываем выполнение - scheduler продолжит работу в следующем тике
        finally:
            # Context7: Всегда освобождаем lock в finally блоке
            # Context7: Логируем освобождение lock для диагностики
            try:
                await self._release_lock()
                logger.debug("Lock released after tick", 
                           tick_duration_seconds=(datetime.now(timezone.utc) - tick_start_time).total_seconds() if 'tick_start_time' in locals() else None)
            except Exception as release_error:
                logger.error("Failed to release lock in finally block", 
                           error=str(release_error), 
                           error_type=type(release_error).__name__,
                           exc_info=True)
    
    async def _check_and_trigger_backfill(self, channel: Dict[str, Any]):
        """
        Context7: [C7-ID: backfill-missing-posts-002] Проверка и запуск backfill с адаптивными порогами.
        
        Использует адаптивный порог на основе статистики канала и применяет locking/idempotency
        для предотвращения дублирующихся backfill операций.
        
        Args:
            channel: данные канала
        """
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            
            channel_id = channel['id']
            
            # Проверка Redis lock для предотвращения параллельных backfill
            lock_key = f"lock:backfill:{channel_id}"
            instance_id = os.getenv("HOSTNAME", "unknown")
            
            # Попытка получить lock (SET NX EX 3600)
            lock_acquired = await self.redis.set(
                lock_key,
                instance_id,
                nx=True,
                ex=3600  # TTL 1 час
            )
            
            if not lock_acquired:
                logger.debug(
                    "Backfill lock already held, skipping",
                    channel_id=channel_id
                )
                return
            
            try:
                # Получаем реальное время последнего поста из БД (High Watermark)
                conn = psycopg2.connect(self.db_url)
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                
                cursor.execute("""
                    SELECT MAX(posted_at) as max_posted_at
                    FROM posts
                    WHERE channel_id = %s
                """, (channel_id,))
                
                row = cursor.fetchone()
                cursor.close()
                conn.close()
                
                if not row or not row['max_posted_at']:
                    # Нет постов в БД, это нормально для новых каналов
                    return
                
                last_post_date = ensure_dt_utc(row['max_posted_at'])
                if not last_post_date:
                    return
                
                # Вычисляем gap: время с момента последнего поста (всегда >= 0)
                now = datetime.now(timezone.utc)
                gap_seconds = max(0, int((now - last_post_date).total_seconds()))
                
                # Получаем адаптивный порог (через parser, если доступен)
                # Для упрощения используем фиксированный порог, если parser недоступен
                threshold_seconds = 3600  # Fallback: 1 час
                
                # Context7: Используем parser для получения адаптивного порога, если доступен
                # Parser может быть не инициализирован в момент первой проверки
                if self.parser and hasattr(self.parser, '_compute_adaptive_threshold'):
                    try:
                        threshold_seconds = await self.parser._compute_adaptive_threshold(channel_id)
                    except Exception as e:
                        logger.debug("Failed to get adaptive threshold, using fixed",
                                   channel_id=channel_id,
                                   error=str(e))
                
                # Проверяем, превышен ли адаптивный порог
                if gap_seconds > threshold_seconds:
                    # Определяем контекст времени для логирования
                    is_quiet = False
                    quiet_reason = "normal"
                    if self.parser and hasattr(self.parser, '_is_quiet_hours'):
                        try:
                            is_quiet, quiet_reason = self.parser._is_quiet_hours(now)
                        except Exception:
                            pass
                    
                    gap_hours = gap_seconds / 3600
                    threshold_hours = threshold_seconds / 3600
                    
                    # Проверка idempotency: не запускали ли уже backfill для этого окна
                    window_start = last_post_date.isoformat()
                    window_end = now.isoformat()
                    idempotency_key = f"backfill_job:{channel_id}:{window_start}:{window_end}"
                    
                    # Для упрощения используем более простой ключ на основе временного окна
                    # Округлим до 10 минут для группировки
                    window_start_rounded = int(last_post_date.timestamp() // 600) * 600
                    idempotency_key_simple = f"backfill_job:{channel_id}:{window_start_rounded}"
                    
                    # Проверка, не выполнялся ли уже backfill
                    existing_job = await self.redis.get(idempotency_key_simple)
                    if existing_job:
                        logger.debug(
                            "Backfill job already enqueued/completed, skipping",
                            channel_id=channel_id,
                            idempotency_key=idempotency_key_simple
                        )
                        return
                    
                    logger.warning(
                        "Missing posts detected, triggering backfill",
                        channel_id=channel_id,
                        channel_username=channel.get('username'),
                        last_post_date=last_post_date.isoformat(),
                        gap_seconds=gap_seconds,
                        gap_hours=gap_hours,
                        threshold_seconds=threshold_seconds,
                        threshold_hours=threshold_hours,
                        context=quiet_reason,
                        is_quiet=is_quiet
                    )
                    
                    # Обновляем метрики
                    posts_backfill_triggered_total.labels(
                        channel_id=channel_id,
                        reason="missing_posts_gap"
                    ).inc()
                    backfill_jobs_total.labels(
                        channel_id=channel_id,
                        status="enqueued"
                    ).inc()
                    
                    # Устанавливаем idempotency ключ с TTL 24 часа
                    await self.redis.setex(
                        idempotency_key_simple,
                        86400,  # 24 часа
                        "1"
                    )
                    
                    # Context7: Запускаем исторический парсинг с since_date = последний пост
                    # Это безопасно, так как парсер использует идемпотентность
                    if self.telegram_client_manager:
                        try:
                            logger.info(
                                "Backfill will be triggered in next tick",
                                channel_id=channel_id,
                                since_date=last_post_date.isoformat(),
                                gap_hours=gap_hours
                            )
                            
                            # Context7: Обновляем Low Watermark после планирования backfill
                            # Это позволяет отслеживать, с какого времени гарантированно спарсили всё
                            if self.parser and hasattr(self.parser, '_update_low_watermark'):
                                try:
                                    await self.parser._update_low_watermark(channel_id, last_post_date)
                                except Exception as e:
                                    logger.debug("Failed to update low watermark",
                                               channel_id=channel_id,
                                               error=str(e))
                            
                            # Можно добавить флаг для принудительного historical режима в следующем тике
                            # Пока просто логируем
                        except Exception as e:
                            logger.error(
                                "Failed to trigger backfill",
                                channel_id=channel_id,
                                error=str(e)
                            )
                            backfill_jobs_total.labels(
                                channel_id=channel_id,
                                status="failed"
                            ).inc()
            
            finally:
                # Освобождаем lock (опционально, так как TTL освободит автоматически)
                # Но лучше освободить явно после завершения
                try:
                    await self.redis.delete(lock_key)
                except Exception:
                    pass  # Lock освободится по TTL
            
        except Exception as e:
            # Не критичная ошибка, логируем и продолжаем
            logger.warning(
                "Failed to check for missing posts",
                channel_id=channel.get('id'),
                error=str(e)
            )
    
    def _get_active_channels(self) -> List[Dict[str, Any]]:
        """Получение активных каналов из БД с ограничением количества за тик.
        
        Context7 best practice: 
        - Ограничение через CHANNELS_PER_TICK для фиксированного объёма работы за тик
        - Фильтр blocked_until для пропуска каналов в cooldown
        - Приоритет новым каналам (NULLS FIRST для last_parsed_at)
        - Fairness через сортировку по tenant_id/user_id
        
        Context7: Исправление проблемы с async соединениями - гарантированное закрытие соединений
        через try/finally для предотвращения RuntimeError: coroutine ignored GeneratorExit
        """
        conn = None
        cursor = None
        try:
            # Context7: Фиксированное количество каналов за тик - главный механизм масштабирования
            channels_per_tick = int(os.getenv("CHANNELS_PER_TICK", "50"))
            
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Context7: Выбираем только N каналов с учетом blocked_until и fairness
            # Получаем tenant_id через JOIN с users через user_channel
            cursor.execute("""
                SELECT c.id,
                       c.tg_channel_id,
                       c.username,
                       c.title,
                       c.last_parsed_at,
                       c.is_active,
                       c.blocked_until,
                       COALESCE(u.tenant_id::text, '00000000-0000-0000-0000-000000000000') as tenant_id,
                       COALESCE(uc.user_id::text, '0') as user_id
                FROM channels c
                LEFT JOIN user_channel uc ON c.id = uc.channel_id AND uc.is_active = true
                LEFT JOIN users u ON uc.user_id = u.id
                WHERE c.is_active = true
                  AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
                ORDER BY
                  (c.last_parsed_at IS NULL) DESC,  -- Явный приоритет NULL (TRUE идет первым)
                  c.last_parsed_at NULLS FIRST,     -- Дополнительная гарантия приоритета NULL
                  COALESCE(u.tenant_id::text, '00000000-0000-0000-0000-000000000000'),  -- Fairness между tenant'ами
                  COALESCE(uc.user_id::text, '0'),  -- Fairness между пользователями
                  c.created_at DESC
                LIMIT %s
            """, (channels_per_tick,))
            
            channels = cursor.fetchall()
            channels_list = [dict(ch) for ch in channels]
            
            # Context7: Логируем статистику для диагностики
            new_channels_count = sum(1 for ch in channels_list if ch.get('last_parsed_at') is None)
            logger.info(
                "Active channels retrieved (limited per tick)",
                total=len(channels_list),
                new_channels=new_channels_count,
                channels_per_tick_limit=channels_per_tick
            )
            
            return channels_list
            
        except Exception as e:
            logger.error(f"Failed to get active channels: {str(e)}", error=str(e), exc_info=True)
            return []
        finally:
            # Context7: Гарантированное закрытие соединений для предотвращения утечек и RuntimeError
            if cursor is not None:
                try:
                    cursor.close()
                except Exception as e:
                    logger.warning(f"Failed to close cursor: {str(e)}", error=str(e))
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"Failed to close connection: {str(e)}", error=str(e))
    
    def _decide_mode(self, channel: Dict[str, Any]) -> str:
        """Автоопределение режима парсинга."""
        override = self.config.mode_override
        
        if override == "historical":
            return "historical"
        elif override == "incremental":
            return "incremental"
        elif override == "auto":
            # Context7 best practice: безопасная обработка last_parsed_at через ensure_dt_utc
            lpa_dt = ensure_dt_utc(channel.get('last_parsed_at'))
            if lpa_dt is None:
                logger.info(f"No last_parsed_at for channel {channel['id']}, using historical mode")
                return "historical"
            else:
                # LPA safeguard: если last_parsed_at слишком старый, форсим historical
                age_hours = (datetime.now(timezone.utc) - lpa_dt).total_seconds() / 3600
                if age_hours > self.config.lpa_max_age_hours:
                    parser_mode_forced_total.labels(reason="stale_lpa").inc()
                    logger.warning(f"LPA too old for channel {channel['id']} (age={age_hours:.1f}h), forcing historical mode")
                    return "historical"
                return "incremental"
        else:
            logger.warning("Unknown mode_override, defaulting to auto", mode_override=override)
            lpa_dt = ensure_dt_utc(channel.get('last_parsed_at'))
            return "historical" if lpa_dt is None else "incremental"
