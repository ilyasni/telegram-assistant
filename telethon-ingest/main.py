"""Основной модуль telethon-ingest сервиса с отложенной инициализацией."""

import asyncio
import os
import logging
import faulthandler
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import structlog
from services.telegram_client import TelegramIngestionService
from services.qr_auth import QrAuthService
from services.telegram_client_manager import TelegramClientManager
from services.atomic_db_saver import AtomicDBSaver
from services.rate_limiter import RateLimiter
from config import settings

# Context7 best practice: настройка логирования с faulthandler
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Context7: Поддержка keyword arguments в логах
        structlog.processors.dict_tracebacks,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# Context7 best practice: включаем детальное логирование Telethon
import logging
logging.basicConfig(
    format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
    level=logging.DEBUG  # Context7: DEBUG для диагностики
)

logger = structlog.get_logger()

def debug_logger_kind(logger, name):
    print(f"[LOGCHECK] {name}: {type(logger)} module={type(logger).__module__}")

def safe_log(callable_):
    """Безопасный логгер для смешанного режима логирования."""
    try:
        callable_()
    except TypeError as e:
        # Нейтрализуем только «unexpected keyword argument ...»
        if "unexpected keyword argument" in str(e):
            try:
                # fallback без KV
                logger.error("log_kv_failed_typeerror")
            except Exception:
                pass
        else:
            raise

# Глобальное состояние для health check
app_state = {
    "status": "starting",
    "phase": "A0",
    "db": "unknown",
    "redis": "unknown", 
    "mtproto": "unknown",
    "telegram_client_manager": None,
    "atomic_db_saver": None,
    "rate_limiter": None,
    "last_mtproto_ok_at": None,
    "scheduler": {
        "status": "starting",
        "last_tick_ts": None,
        "lock_owner": None
    },
    "parser": {
        "initialized": False,
        "version": "unknown"
    },
    "telegram_service": None,  # Reference to TelegramIngestionService
    "telegram_client": None     # Reference to TelegramClient from service
}

# Prometheus метрики
request_count = Counter("http_requests_total", "Total HTTP requests", ["path"])
request_duration = Histogram("http_request_duration_seconds", "HTTP request duration", ["path"])


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP handler для health check."""
    
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            status = "healthy" if app_state["status"] == "ready" else "starting"
            response = {"status": status}
            self.wfile.write(str(response).encode())
            
        elif self.path == "/health/details":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            # Calculate scheduler status
            scheduler_info = app_state.get("scheduler", {})
            scheduler_status = self._calculate_scheduler_status(scheduler_info)
            
            # Get parser info
            parser_info = app_state.get("parser", {})
            
            # Context7: Health check с новыми компонентами (БЕЗ приватных ID)
            client_manager = app_state.get("telegram_client_manager")
            atomic_saver = app_state.get("atomic_db_saver")
            rate_limiter = app_state.get("rate_limiter")
            
            response = {
                "status": app_state["status"],
                "phase": app_state["phase"],
                "db": app_state["db"],
                "redis": app_state["redis"],
                "mtproto": app_state["mtproto"],
                "last_mtproto_ok_at": app_state["last_mtproto_ok_at"],
                "telegram_clients": client_manager.health() if client_manager else {"connected": 0, "total": 0},
                "scheduler": {
                    "last_tick_ts": scheduler_info.get("last_tick_ts"),
                    "interval_sec": int(os.getenv("PARSER_SCHEDULER_INTERVAL_SEC", "300")),
                    "lock_owner": scheduler_info.get("lock_owner"),
                    "status": scheduler_status
                },
                "parser": {
                    "initialized": parser_info.get("initialized", False),
                    "version": parser_info.get("version", "unknown")
                },
                "components": {
                    "telegram_client_manager": client_manager is not None,
                    "atomic_db_saver": atomic_saver is not None,
                    "rate_limiter": rate_limiter is not None
                }
            }
            self.wfile.write(str(response).encode())
        elif self.path == "/metrics":
            self._serve_metrics()
        else:
            self.send_response(404)
            self.end_headers()
    
    def _calculate_scheduler_status(self, scheduler_info):
        """Calculate scheduler status based on last_tick age"""
        last_tick_ts = scheduler_info.get("last_tick_ts")
        if not last_tick_ts:
            return "down"
        
        try:
            from datetime import datetime, timezone
            
            # Parse ISO format timestamp
            if last_tick_ts.endswith('Z'):
                last_tick = datetime.fromisoformat(last_tick_ts.replace('Z', '+00:00'))
            else:
                last_tick = datetime.fromisoformat(last_tick_ts)
            
            now = datetime.now(timezone.utc) if last_tick.tzinfo else datetime.utcnow()
            age_seconds = (now - last_tick).total_seconds()
            
            interval = int(os.getenv("PARSER_SCHEDULER_INTERVAL_SEC", "300"))
            
            if age_seconds <= 2 * interval:
                return "ok"
            elif age_seconds <= 4 * interval:
                return "stale"
            else:
                return "down"
        except Exception:
            return "unknown"
            
    def _serve_metrics(self):
        """Serve Prometheus metrics endpoint."""
        self.send_response(200)
        self.send_header("Content-type", CONTENT_TYPE_LATEST)
        self.end_headers()
        self.wfile.write(generate_latest())
    
    def log_message(self, format, *args):
        return


def start_health_server():
    """Запуск HTTP-сервера для health check."""
    health_port = int(os.getenv("INGEST_HEALTH_PORT", "8011"))
    try:
        httpd = HTTPServer(('0.0.0.0', health_port), HealthHandler)
        logger.info(f"Health server started on port {health_port}")
        httpd.serve_forever()
    except OSError as e:
        if e.errno == 98:  # Address already in use
            logger.warning(f"Port {health_port} already in use, health server not started")
        else:
            logger.error("Health server error", error=str(e))


async def init_db():
    """Инициализация базы данных с таймаутом."""
    logger.info("Phase A1: DB init...")
    app_state["phase"] = "A1"
    
    try:
        import psycopg2
        loop = asyncio.get_running_loop()
        
        def sync_db_connect():
            return psycopg2.connect(
                settings.database_url,
                connect_timeout=5
            )
        
        # Context7 best practice: async DB connect через executor
        db_conn = await asyncio.wait_for(
            loop.run_in_executor(None, sync_db_connect),
            timeout=5
        )
        
        # Тест подключения
        cursor = db_conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        db_conn.close()
        
        app_state["db"] = "ok"
        logger.info("Phase A1: DB OK")
        return True
        
    except Exception as e:
        app_state["db"] = "fail"
        logger.error("Phase A1: DB FAILED", error=str(e))
        return False


async def init_redis():
    """Инициализация Redis с таймаутом."""
    logger.info("Phase A2: Redis init...")
    app_state["phase"] = "A2"
    
    try:
        import redis
        redis_client = redis.from_url(settings.redis_url)
        
        # Тест подключения
        await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, redis_client.ping),
            timeout=3
        )
        
        app_state["redis"] = "ok"
        logger.info("Phase A2: Redis OK")
        return redis_client
        
    except Exception as e:
        app_state["redis"] = "fail"
        logger.error("Phase A2: Redis FAILED", error=str(e))
        return None


async def init_mtproto_smoke():
    """Лёгкая проверка MTProto без логина."""
    logger.info("Phase A3: MTProto smoke...")
    app_state["phase"] = "A3"
    
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        
        # Context7 best practice: быстрая проверка MTProto
        client = TelegramClient(
            StringSession(), 
            settings.master_api_id, 
            settings.master_api_hash,
            device_model="TelegramAssistant",
            system_version="Linux", 
            app_version="1.0"
        )
        
        await asyncio.wait_for(client.connect(), timeout=10)
        await client.disconnect()
        
        app_state["mtproto"] = "ok"
        app_state["last_mtproto_ok_at"] = asyncio.get_event_loop().time()
        logger.info("Phase A3: MTProto OK")
        return True
        
    except Exception as e:
        app_state["mtproto"] = "fail"
        logger.error("Phase A3: MTProto FAILED", error=str(e))
        return False


async def run_qr_loop():
    """Запуск QR auth loop."""
    logger.info("QR auth loop starting...")
    
    try:
        qr_service = QrAuthService()
        await qr_service.run()
    except Exception as e:
        logger.error("QR auth loop error", error=str(e))


async def run_ingest_loop():
    """Context7: Запуск ingest loop с новыми компонентами."""
    logger.info("Ingest loop starting...")
    
    try:
        # Инициализация Redis клиента для новых компонентов
        import redis.asyncio as redis
        redis_client = redis.from_url(settings.redis_url)
        
        # Инициализация БД подключения
        import psycopg2
        db_connection = psycopg2.connect(
            settings.database_url,
            connect_timeout=10
        )
        
        # Context7: Инициализация новых компонентов
        logger.info("Initializing TelegramClientManager...")
        client_manager = TelegramClientManager(redis_client, db_connection)
        app_state["telegram_client_manager"] = client_manager
        
        # Запуск watchdog
        await client_manager.start_watchdog()
        logger.info("TelegramClientManager watchdog started")
        
        # Инициализация AtomicDBSaver
        logger.info("Initializing AtomicDBSaver...")
        atomic_saver = AtomicDBSaver()
        app_state["atomic_db_saver"] = atomic_saver
        
        # Инициализация RateLimiter
        logger.info("Initializing RateLimiter...")
        rate_limiter = RateLimiter(redis_client)
        app_state["rate_limiter"] = rate_limiter
        
        # Инициализация старого сервиса для совместимости с TelegramClientManager
        ingest_service = TelegramIngestionService(client_manager=client_manager)
        app_state["telegram_service"] = ingest_service
        
        # Запуск сервиса в фоне (неблокирующий)
        asyncio.create_task(ingest_service.start())
        logger.info("Ingest service started in background")
        
        # Wait for client to be initialized
        max_wait = 30  # Maximum wait time in seconds
        wait_interval = 1  # Check every second
        waited = 0
        
        while not ingest_service.client and waited < max_wait:
            await asyncio.sleep(wait_interval)
            waited += wait_interval
        
        if ingest_service.client:
            app_state["telegram_client"] = ingest_service.client
            logger.info("Telegram client initialized and stored in app_state")
        else:
            logger.warning("Telegram client not initialized after {} seconds".format(max_wait))
        
        logger.info("All components initialized successfully")
        
        # Бесконечный цикл для поддержания работы
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        logger.error("Ingest loop error", error=str(e))
        # Graceful shutdown новых компонентов
        if "telegram_client_manager" in app_state and app_state["telegram_client_manager"]:
            await app_state["telegram_client_manager"].stop_watchdog()
            await app_state["telegram_client_manager"].close_all()


async def run_scheduler_loop():
    """Запуск scheduler loop для incremental парсинга."""
    logger.info("Scheduler loop starting...")
    
    try:
        # Проверка feature flag
        enabled = os.getenv("FEATURE_INCREMENTAL_PARSING_ENABLED", "true").lower() == "true"
        if not enabled:
            logger.info("Incremental parsing disabled via feature flag")
            while True:
                await asyncio.sleep(60)
            return
        
        # Ждём инициализации сервисов
        await asyncio.sleep(10)  # Даём время другим сервисам запуститься
        
        from tasks.parse_all_channels_task import ParseAllChannelsTask
        from services.channel_parser import ParserConfig, ChannelParser
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        
        # Инициализация компонентов
        config = ParserConfig()
        
        # Get telegram_client_manager from app_state
        client_manager = app_state.get("telegram_client_manager")
        if not client_manager:
            logger.warning("TelegramClientManager not available, trying to initialize...")
            # Попытка инициализации TelegramClientManager
            try:
                from services.telegram_client_manager import TelegramClientManager
                client_manager = TelegramClientManager()
                await client_manager.initialize()
                app_state["telegram_client_manager"] = client_manager
                logger.info("TelegramClientManager initialized successfully for scheduler")
            except Exception as e:
                logger.error(f"Failed to initialize TelegramClientManager: {e}")
                logger.warning("Scheduler will run in monitoring mode only")
        
        # Создание AsyncSession engine для ChannelParser
        # Replace postgresql:// with postgresql+asyncpg:// for async driver
        import re
        db_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Parse and clean query string - remove unsupported params
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
        parsed = urlparse(db_url)
        qs = parse_qs(parsed.query)
        # Remove asyncpg-unsupported parameters
        qs.pop('connect_timeout', None)
        qs.pop('application_name', None)
        qs.pop('keepalives', None)
        qs.pop('keepalives_idle', None)
        qs.pop('keepalives_interval', None)
        qs.pop('keepalives_count', None)
        # Rebuild query string
        new_query = urlencode(qs, doseq=True)
        # Reconstruct URL
        db_url = urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))
        logger.info("Creating async engine", db_url=db_url[:100])  # Log first 100 chars to avoid exposing password
        engine = create_async_engine(db_url, pool_pre_ping=True, pool_size=5)
        
        # Создание AsyncSession для парсера
        db_session = AsyncSession(engine)
        
        # Создание ChannelParser с DI
        parser = ChannelParser(
            config=config,
            db_session=db_session,
            event_publisher=None,  # Temporarily disabled
            telegram_client_manager=client_manager  # Передаём TelegramClientManager
        )
        
        # Update app_state with parser info
        app_state["parser"] = {
            "initialized": True,
            "version": "1.0.0"
        }
        
        logger.info("ChannelParser initialized successfully")
        
        # Инициализация scheduler с передачей app_state и parser
        scheduler = ParseAllChannelsTask(
            config=config,
            db_url=settings.database_url,
            redis_client=None,  # Будет инициализирован внутри scheduler
            parser=parser,  # Передаём инициализированный parser
            app_state=app_state,  # Передаём app_state для обновления статуса
            telegram_client_manager=client_manager  # Передаём TelegramClientManager если доступен
        )
        
        if client_manager:
            logger.info("Scheduler initialized with TelegramClientManager and parser, starting run_forever loop")
        else:
            logger.info("Scheduler initialized (monitoring mode), starting run_forever loop")
        
        # Запуск бесконечного цикла scheduler
        await scheduler.run_forever()
            
    except Exception as e:
        logger.error("Scheduler loop error", error=str(e))


async def main():
    """Основная функция с пошаговой инициализацией."""
    # Debug logger types
    debug_logger_kind(logger, "root")
    debug_logger_kind(logging.getLogger(__name__), "__name__ stdlib")
    debug_logger_kind(structlog.get_logger(), "structlog")
    
    logger.info("Phase A0: startup")
    app_state["phase"] = "A0"
    
    # Context7 best practice: "сторожок" зависаний
    faulthandler.dump_traceback_later(30, repeat=True)
    
    # Запуск health server в отдельном потоке
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    try:
        # Phase A1: Инициализация БД
        db_ok = await init_db()
        if not db_ok:
            logger.error("DB initialization failed, continuing without DB")
        
        # Phase A2: Инициализация Redis
        redis_client = await init_redis()
        if not redis_client:
            logger.error("Redis initialization failed, continuing without Redis")
        
        # Phase A3: MTProto smoke test
        mtproto_ok = await init_mtproto_smoke()
        if not mtproto_ok:
            logger.warning("MTProto smoke test failed, continuing anyway")
        
        # Phase B: Запуск основных циклов
        logger.info("Phase B: loops start")
        app_state["phase"] = "B"
        app_state["status"] = "ready"
        
        # Context7 best practice: параллельный запуск циклов
        await asyncio.gather(
            run_qr_loop(),
            run_ingest_loop(),
            run_scheduler_loop(),  # НОВЫЙ: Incremental parsing scheduler
            return_exceptions=True
        )
        
    except Exception as e:
        logger.error("Main loop error", error=str(e))
        app_state["status"] = "failed"
        raise


if __name__ == "__main__":
    # Context7 best practice: без uvloop на время диагностики
    # import uvloop; uvloop.install()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error("Fatal error", error=str(e))
        raise