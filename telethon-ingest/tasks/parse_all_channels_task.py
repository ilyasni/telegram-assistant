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

import structlog
import redis.asyncio as redis
import psycopg2
from psycopg2.extras import RealDictCursor
from prometheus_client import Counter, Histogram, Gauge

from config import settings

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
                self.redis = redis.from_url(settings.redis_url)
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
            
            # Context7: set() - синхронная функция в redis-py
            acquired = self.redis.set(
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
            # Context7: delete() - синхронная функция в redis-py
            self.redis.delete("scheduler:lock")
            if self.app_state:
                self.app_state["scheduler"]["lock_owner"] = None
        except Exception as e:
            logger.error(f"Failed to release lock: {str(e)}")
    
    async def _update_hwm(self, channel_id: str, max_message_date: datetime):
        """Update Redis HWM watermark"""
        try:
            hwm_key = f"parse_hwm:{channel_id}"
            # Context7: set() - синхронная функция в redis-py
            self.redis.set(
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
            # Context7: delete() - синхронная функция в redis-py
            self.redis.delete(hwm_key)
            logger.debug("Cleared HWM", extra={"channel_id": channel_id})
        except Exception as e:
            logger.error(f"Failed to clear HWM for channel {channel_id}: {str(e)}")
    
    async def _get_system_user_and_tenant(self) -> Tuple[str, str]:
        """Get system user_id and tenant_id from the first authorized session."""
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT u.telegram_id as telegram_id, u.tenant_id as tenant_id
                FROM users u
                WHERE u.telegram_auth_status = 'authorized'
                ORDER BY u.telegram_auth_created_at DESC
                LIMIT 1
            """)
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result:
                return str(result['telegram_id']), str(result['tenant_id'])
            else:
                # Fallback to system values
                return "00000000-0000-0000-0000-000000000000", "00000000-0000-0000-0000-000000000000"
                
        except Exception as e:
            logger.error(f"Failed to get system user/tenant: {str(e)}")
            return "00000000-0000-0000-0000-000000000000", "00000000-0000-0000-0000-000000000000"
    
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
        
        # Get user_id and tenant_id from database
        user_id, tenant_id = await self._get_system_user_and_tenant()
        
        # Get telegram client from manager
        telegram_client = await self.telegram_client_manager.get_client(user_id)
        if not telegram_client:
            logger.warning(f"No telegram client available for user {user_id}, skipping parsing")
            return {"status": "skipped", "reason": "no_client", "parsed": 0, "max_message_date": None}
        
        for attempt in range(max_retries):
            try:
                async with self.semaphore:
                    logger.info(f"Parsing channel {channel['id']} with retry - mode={mode}, attempt={attempt + 1}")
                    
                    # Initialize parser if needed
                    if not self.parser:
                        logger.info(f"Initializing ChannelParser for channel {channel['id']}")
                        # Initialize ChannelParser with required components
                        from services.channel_parser import ChannelParser
                        from services.atomic_db_saver import AtomicDBSaver
                        from services.rate_limiter import RateLimiter
                        
                        # Create required components
                        atomic_db_saver = AtomicDBSaver(self.db_url)
                        rate_limiter = RateLimiter()
                        
                        # Initialize parser
                        self.parser = ChannelParser(
                            telegram_client=telegram_client,
                            atomic_db_saver=atomic_db_saver,
                            rate_limiter=rate_limiter,
                            redis_client=self.redis
                        )
                    
                    # Call actual parser
                    result = await self.parser.parse_channel_messages(
                        channel_id=channel['id'],
                        user_id=user_id,
                        tenant_id=tenant_id,
                        mode=mode
                    )
                    
                    # Update HWM if we have max_message_date
                    if result.get("max_message_date"):
                        max_date = datetime.fromisoformat(result["max_message_date"])
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
                    # Context7: get() - синхронная функция в redis-py
                    hwm_str = self.redis.get(hwm_key)
                    
                    if hwm_str:
                        hwm_ts = datetime.fromisoformat(hwm_str)
                        age_seconds = (datetime.now(timezone.utc) - hwm_ts).total_seconds()
                        parser_hwm_age_seconds.labels(channel_id=channel['id']).set(age_seconds)
                    
                    # Определение режима
                    mode = self._decide_mode(channel)
                    
                    # Логирование статуса
                    logger.info(f"Channel {channel['id']} status: {channel.get('title')} ({channel.get('username')}) - mode={mode}, last_parsed_at={channel.get('last_parsed_at').isoformat() if channel.get('last_parsed_at') else 'null'}")
                    
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
                    
                    # Gauge для возраста watermark
                    if channel.get('last_parsed_at'):
                        age_seconds = (datetime.now(timezone.utc) - channel['last_parsed_at']).total_seconds()
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
            # Автоматический выбор: если есть last_parsed_at → incremental
            if channel.get('last_parsed_at') is None:
                logger.info(f"No last_parsed_at for channel {channel['id']}, using historical mode")
                return "historical"
            else:
                # LPA safeguard: если last_parsed_at слишком старый, форсим historical
                age_hours = (datetime.now(timezone.utc) - channel['last_parsed_at']).total_seconds() / 3600
                if age_hours > self.config.lpa_max_age_hours:
                    parser_mode_forced_total.labels(reason="stale_lpa").inc()
                    logger.warning(f"LPA too old for channel {channel['id']} (age={age_hours:.1f}h), forcing historical mode")
                    return "historical"
                return "incremental"
        else:
            logger.warning("Unknown mode_override, defaulting to auto", mode_override=override)
            return "historical" if channel.get('last_parsed_at') is None else "incremental"
