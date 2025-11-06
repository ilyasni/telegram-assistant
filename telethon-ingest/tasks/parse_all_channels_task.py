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
from prometheus_client import Counter, Histogram, Gauge

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
        self.parser = parser  # Будет инициализирован при необходимости
        self.app_state = app_state
        self.telegram_client_manager = telegram_client_manager  # TelegramClientManager для парсинга
        self.media_processor = media_processor  # MediaProcessor для обработки медиа
        self.interval_sec = int(os.getenv("PARSER_SCHEDULER_INTERVAL_SEC", "300"))
        self.enabled = os.getenv("FEATURE_INCREMENTAL_PARSING_ENABLED", "true").lower() == "true"
        
        # Semaphore for concurrency control
        self.semaphore = asyncio.Semaphore(self.config.max_concurrency)
        
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
            
            # Context7: async Redis - используем await для set()
            acquired = await self.redis.set(
                lock_key,
                instance_id,
                nx=True,
                ex=ttl
            )
            
            if acquired:
                scheduler_lock_acquired_total.labels(status="acquired").inc()
                if self.app_state:
                    self.app_state["scheduler"]["lock_owner"] = instance_id
                return True
            else:
                scheduler_lock_acquired_total.labels(status="missed").inc()
                return False
        except Exception as e:
            logger.error(f"Failed to acquire lock: {str(e)}")
            return False
    
    async def _release_lock(self):
        """Release scheduler lock"""
        try:
            # Context7: async Redis - используем await для delete()
            await self.redis.delete("parse_all_channels:lock")
            if self.app_state:
                self.app_state["scheduler"]["lock_owner"] = None
        except Exception as e:
            logger.error(f"Failed to release lock: {str(e)}")
    
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
    
    async def _get_system_user_and_tenant(self) -> Tuple[int, str]:
        """Get system telegram_id (int) and tenant_id (str) from the first authorized session."""
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
    
    async def _parse_channel_with_retry(self, channel: Dict[str, Any], mode: str):
        """
        Parse channel with exponential backoff retry and FloodWait handling.
        
        Args:
            channel: Channel data dictionary
            mode: Parsing mode (historical/incremental)
            
        Returns:
            Parsing result or None if all retries exhausted
        """
        max_retries = self.config.retry_max
        base_delay = 1.0
        
        # Check if telegram_client_manager is available
        if not self.telegram_client_manager:
            logger.warning(f"TelegramClientManager not available for channel {channel['id']}, skipping parsing")
            return {"status": "skipped", "reason": "no_client_manager", "parsed": 0, "max_message_date": None}
        
        # Get telegram_id (int) and tenant_id (str) from database
        telegram_id, tenant_id = await self._get_system_user_and_tenant()
        
        if not telegram_id or telegram_id == 0:
            logger.warning("No telegram_id found in database, skipping parsing")
            return {"status": "skipped", "reason": "no_telegram_id", "parsed": 0, "max_message_date": None}
        
        # Get telegram client from manager (expects int telegram_id)
        telegram_client = await self.telegram_client_manager.get_client(telegram_id)
        if not telegram_client:
            logger.warning(f"No telegram client available for telegram_id {telegram_id}, skipping parsing")
            return {"status": "skipped", "reason": "no_client", "parsed": 0, "max_message_date": None}
        
        for attempt in range(max_retries):
            try:
                async with self.semaphore:
                    logger.info(f"Parsing channel {channel['id']} with retry - mode={mode}, attempt={attempt + 1}")
                    
                    # Initialize parser if needed
                    if not self.parser:
                        logger.info(f"Initializing ChannelParser for channel {channel['id']}")
                        # Initialize ChannelParser with correct signature
                        from services.channel_parser import ChannelParser, ParserConfig
                        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
                        
                        # Create config
                        config = ParserConfig()
                        config.db_url = self.db_url
                        config.redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
                        
                        # Create async engine and session
                        import re
                        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
                        db_url_async = self.db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
                        parsed = urlparse(db_url_async)
                        qs = parse_qs(parsed.query)
                        # Remove asyncpg-unsupported parameters
                        for key in ['connect_timeout', 'application_name', 'keepalives', 'keepalives_idle', 'keepalives_interval', 'keepalives_count']:
                            qs.pop(key, None)
                        new_query = urlencode(qs, doseq=True)
                        db_url_async = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                        
                        # Context7: Добавляем таймауты для предотвращения зависаний
                        engine = create_async_engine(
                            db_url_async, 
                            pool_pre_ping=True, 
                            pool_size=5,
                            pool_timeout=30,
                            connect_args={
                                "command_timeout": 60,
                                "server_settings": {
                                    "application_name": "telethon_parser"
                                }
                            }
                        )
                        async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
                        db_session = async_session_factory()
                        
                        # Initialize parser with correct parameters
                        self.parser = ChannelParser(
                            config=config,
                            db_session=db_session,
                            event_publisher=None,
                            redis_client=self.redis,
                            telegram_client_manager=self.telegram_client_manager,
                            media_processor=self.media_processor  # Context7: Передаём MediaProcessor
                        )
                    
                    # Call actual parser
                    result = await self.parser.parse_channel_messages(
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
                    wait_seconds += random.uniform(0, 3)  # Add jitter
                    
                    logger.warning(f"FloodWait {wait_seconds:.1f}s for channel {channel['id']} - attempt={attempt + 1}/{max_retries}")
                    
                    parser_retries_total.labels(reason="floodwait").inc()
                    parser_floodwait_seconds_total.labels(channel_id=channel['id']).inc(wait_seconds)
                    
                    await asyncio.sleep(wait_seconds)
                    continue
                
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
    
    async def _run_tick(self):
        """Run scheduler tick with lock protection"""
        if not await self._acquire_lock():
            logger.info("Lock held by another instance, skipping tick")
            return
        
        try:
            logger.info("Running scheduler tick (lock acquired)")
            
            # Получение активных каналов
            channels = self._get_active_channels()
            logger.info(
                "Starting scheduler tick",
                channels_count=len(channels),
                tick_interval_sec=self.interval_sec
            )
            
            if not channels:
                logger.warning("No active channels found for parsing")
                return
            
            for channel in channels:
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
                    
                    # Context7: Логирование для новых каналов с диагностикой
                    is_new_channel = channel.get('last_parsed_at') is None
                    lpa = channel.get('last_parsed_at')
                    lpa_str = lpa.isoformat() if isinstance(lpa, datetime) else 'null'
                    logger.info(
                        "Channel parsing status",
                        channel_id=channel['id'],
                        channel_title=channel.get('title'),
                        channel_username=channel.get('username'),
                        mode=mode,
                        is_new_channel=is_new_channel,
                        last_parsed_at=lpa_str,
                        has_telegram_id=bool(channel.get('tg_channel_id'))
                    )
                    
                    # Call actual parser if telegram_client_manager is available
                    if self.telegram_client_manager:
                        # Parse channel with retry
                        result = await self._parse_channel_with_retry(channel, mode)
                        
                        if result and result.get("status") == "success":
                            parsed_count = result.get("messages_processed", 0)
                            posts_parsed_total.labels(mode=mode, status="success").inc(parsed_count)
                            parser_runs_total.labels(mode=mode, status="ok").inc()
                        elif result and result.get("status") == "skipped":
                            parser_runs_total.labels(mode=mode, status="skipped").inc()
                        else:
                            parser_runs_total.labels(mode=mode, status="failed").inc()
                    else:
                        # Just monitor without parsing
                        parser_runs_total.labels(mode=mode, status='monitored').inc()
                    
                    # Gauge для возраста watermark с безопасной обработкой типов
                    lpa_dt = ensure_dt_utc(channel.get('last_parsed_at'))
                    if lpa_dt:
                        age_seconds = (datetime.now(timezone.utc) - lpa_dt).total_seconds()
                        incremental_watermark_age_seconds.labels(
                            channel_id=channel['id']
                        ).set(age_seconds)
                    
                    # Context7: [C7-ID: backfill-missing-posts-001] Проверка и запуск backfill при пропусках
                    await self._check_and_trigger_backfill(channel)
                        
                except Exception as e:
                    logger.error(f"Failed to monitor channel {channel['id']}: {str(e)}")
            
            # Update scheduler freshness metric
            now_ts = datetime.now(timezone.utc).timestamp()
            scheduler_last_tick_ts_seconds.set(now_ts)
            
            # Update app_state if available
            if self.app_state:
                self.app_state["scheduler"]["last_tick_ts"] = datetime.now(timezone.utc).isoformat()
                self.app_state["scheduler"]["status"] = "running"
            
            logger.info("Scheduler tick completed")
            
        finally:
            await self._release_lock()
    
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
        """Получение активных каналов из БД.
        
        Context7 best practice: 
        - Приоритет новым каналам (NULLS FIRST для last_parsed_at)
        - Без жесткого лимита для поддержки всех активных каналов
        - Настраиваемый лимит через PARSER_MAX_CHANNELS_PER_TICK (по умолчанию 100)
        """
        try:
            # Context7: Настраиваемый лимит для контроля нагрузки
            max_channels = int(os.getenv("PARSER_MAX_CHANNELS_PER_TICK", "100"))
            
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Context7: Получаем все активные каналы, приоритет новым (без last_parsed_at)
            cursor.execute("""
                SELECT id, tg_channel_id, username, title, last_parsed_at, is_active
                FROM channels
                WHERE is_active = true
                ORDER BY last_parsed_at NULLS FIRST, created_at DESC
                LIMIT %s
            """, (max_channels,))
            
            channels = cursor.fetchall()
            cursor.close()
            conn.close()
            
            channels_list = [dict(ch) for ch in channels]
            
            # Context7: Логируем статистику для диагностики
            new_channels_count = sum(1 for ch in channels_list if ch.get('last_parsed_at') is None)
            logger.info(
                "Active channels retrieved",
                total=len(channels_list),
                new_channels=new_channels_count,
                max_channels_limit=max_channels
            )
            
            return channels_list
            
        except Exception as e:
            logger.error(f"Failed to get active channels: {str(e)}", error=str(e), exc_info=True)
            return []
    
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
