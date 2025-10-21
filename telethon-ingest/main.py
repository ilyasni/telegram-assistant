"""Основной модуль telethon-ingest сервиса."""

import asyncio
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
