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

# Глобальное состояние для health check
app_state = {
    "status": "starting",
    "phase": "A0",
    "db": "unknown",
    "redis": "unknown", 
    "mtproto": "unknown",
    "last_mtproto_ok_at": None
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
            
            response = {
                "status": app_state["status"],
                "phase": app_state["phase"],
                "db": app_state["db"],
                "redis": app_state["redis"],
                "mtproto": app_state["mtproto"],
                "last_mtproto_ok_at": app_state["last_mtproto_ok_at"]
            }
            self.wfile.write(str(response).encode())
            
        elif self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(generate_latest())
        else:
            self.send_response(404)
            self.end_headers()
    
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
    """Запуск ingest loop."""
    logger.info("Ingest loop starting...")
    
    try:
        ingest_service = TelegramIngestionService()
        await ingest_service.start()
    except Exception as e:
        logger.error("Ingest loop error", error=str(e))


async def main():
    """Основная функция с пошаговой инициализацией."""
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