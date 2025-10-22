"""Основной модуль telethon-ingest сервиса."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import signal
import sys
import structlog
from services.telegram_client import TelegramIngestionService
from config import settings

# Настройка логирования
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

logger = structlog.get_logger()


class TelegramIngestionApp:
    """Основное приложение для парсинга Telegram."""
    
    def __init__(self):
        self.service = TelegramIngestionService()
        self.is_running = False
    
    async def start(self):
        """Запуск приложения."""
        try:
            logger.info("Starting Telegram ingestion service", 
                       environment=settings.environment,
                       log_level=settings.log_level)
            
            await self.service.start()
            self.is_running = True

            # HTTP-сервер для health/metrics
            self._start_http_server()
            
            # Ожидание сигналов завершения
            await self._wait_for_shutdown()
            
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error("Fatal error", error=str(e))
            sys.exit(1)
        finally:
            await self.stop()
    
    async def stop(self):
        """Остановка приложения."""
        if self.is_running:
            logger.info("Stopping Telegram ingestion service")
            await self.service.stop()
            self.is_running = False

    def _start_http_server(self):
        request_count = Counter('ingest_http_requests_total', 'Total HTTP requests', ['path'])
        request_latency = Histogram('ingest_http_request_duration_seconds', 'HTTP request duration seconds', ['path'])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self_inner):
                path = self_inner.path
                with request_latency.labels(path).time():
                    if path == '/health':
                        self_inner.send_response(200)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.end_headers()
                        self_inner.wfile.write(b'{"status":"healthy"}')
                    elif path == '/metrics':
                        data = generate_latest()
                        self_inner.send_response(200)
                        self_inner.send_header('Content-Type', CONTENT_TYPE_LATEST)
                        self_inner.end_headers()
                        self_inner.wfile.write(data)
                    else:
                        self_inner.send_response(404)
                        self_inner.end_headers()
                request_count.labels(path).inc()

            def log_message(self_inner, format, *args):  # noqa: N802
                return

        def run_server():
            httpd = HTTPServer(('0.0.0.0', 8000), Handler)
            httpd.serve_forever()

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
    
    async def _wait_for_shutdown(self):
        """Ожидание сигналов завершения."""
        def signal_handler(signum, frame):
            logger.info("Received signal", signal=signum)
            asyncio.create_task(self.stop())
        
        # Регистрация обработчиков сигналов
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Ожидание завершения
        while self.is_running:
            await asyncio.sleep(1)


async def main():
    """Точка входа в приложение."""
    app = TelegramIngestionApp()
    await app.start()


if __name__ == "__main__":
    asyncio.run(main())
