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


class ParseAllChannelsTask:
    """Scheduler для периодического парсинга всех активных каналов."""
    
    def __init__(self, config, db_url: str, redis_client: Optional[Any], parser=None, app_state: Optional[Dict] = None, telegram_client_manager: Optional[Any] = None):
        self.config = config
        self.db_url = db_url
        self.redis: Optional[redis.Redis] = None
        self.parser = parser  # Будет инициализирован при необходимости
        self.app_state = app_state
        self.telegram_client_manager = telegram_client_manager  # TelegramClientManager для парсинга
        self.interval_sec = int(os.getenv("PARSER_SCHEDULER_INTERVAL_SEC", "300"))
        self.enabled = os.getenv("FEATURE_INCREMENTAL_PARSING_ENABLED", "true").lower() == "true"
        
        # Semaphore for concurrency control
        self.semaphore = asyncio.Semaphore(self.config.max_concurrency)
        
        logger.info(
            "ParseAllChannelsTask initialized (simplified version for testing)",
            interval_sec=self.interval_sec,
            enabled=self.enabled,
            max_concurrency=self.config.max_concurrency
        )
    
    async def run_forever(self):
        """Бесконечный цикл парсинга."""
        if not self.enabled:
            logger.info("Incremental parsing disabled, scheduler not started")
            return
        
        # Инициализация Redis, если не передан
        if self.redis is None:
            try:
                # Context7: Создаём async Redis клиент с decode_responses для совместимости с parser
                self.redis = redis.from_url(settings.redis_url, decode_responses=True)
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
        lock_key = "scheduler:lock"
        ttl = self.interval_sec * 2
        
        try:
            # Initialize Redis if not available
            if self.redis is None:
                self.redis = redis.from_url(settings.redis_url)
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
            await self.redis.delete("scheduler:lock")
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
                        
                        engine = create_async_engine(db_url_async, pool_pre_ping=True, pool_size=5)
                        async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
                        db_session = async_session_factory()
                        
                        # Initialize parser with correct parameters
                        self.parser = ChannelParser(
                            config=config,
                            db_session=db_session,
                            event_publisher=None,
                            redis_client=self.redis,
                            telegram_client_manager=self.telegram_client_manager
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
            logger.info(f"Found {len(channels)} active channels")
            
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
                    
                    # Логирование статуса с безопасной обработкой last_parsed_at
                    lpa = channel.get('last_parsed_at')
                    lpa_str = lpa.isoformat() if isinstance(lpa, datetime) else 'null'
                    logger.info(f"Channel {channel['id']} status: {channel.get('title')} ({channel.get('username')}) - mode={mode}, last_parsed_at={lpa_str}")
                    
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
    
    def _get_active_channels(self) -> List[Dict[str, Any]]:
        """Получение активных каналов из БД."""
        try:
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT id, tg_channel_id, username, title, last_parsed_at, is_active
                FROM channels
                WHERE is_active = true
                ORDER BY last_parsed_at NULLS FIRST
                LIMIT 10
            """)
            
            channels = cursor.fetchall()
            cursor.close()
            conn.close()
            
            return [dict(ch) for ch in channels]
            
        except Exception as e:
            logger.error(f"Failed to get active channels: {str(e)}")
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
