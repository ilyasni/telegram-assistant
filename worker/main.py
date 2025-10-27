"""
Worker для обработки событий Telegram Assistant.

Реализует event-driven архитектуру с использованием Redis Streams.
[C7-ID: WORKER-MAIN-001]
"""

import asyncio
import os
import signal
import structlog
from typing import Dict, Any
import psycopg2
from psycopg2.extras import RealDictCursor

from event_bus import init_event_bus, get_event_publisher
from config import settings
from feature_flags import feature_flags
from health_server import start_health_server, stop_health_server

# Импорты задач
from tasks.tagging_task import TaggingTask
from tasks.enrichment_task import EnrichmentTask
from tasks.indexing_task import IndexingTask
from tasks.cleanup_task import CleanupTask

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


class EventWorker:
    """Worker для обработки событий."""
    
    def __init__(self):
        self.running = False
        self.db_connection = None
        self.event_bus = None
        
        # Инициализация задач
        self.tagging_task = None
        self.enrichment_task = None
        self.indexing_task = None
        self.cleanup_task = None
    
    async def start(self):
        """Запуск worker'а."""
        self.running = True
        
        # BOOT логи для диагностики
        import os
        logger.info("BOOT", {
            "stream": "posts.parsed",
            "group": "telegram-assistant", 
            "consumer": "worker-1",
            "mode": ">",
            "proxy_url": os.getenv("GIGACHAT_PROXY_URL", "http://gpt2giga-proxy:8090"),
            "use_proxy": os.getenv("USE_GIGACHAT_PROXY", "true"),
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "features": {
                "neo4j": os.getenv("FEATURE_NEO4J_ENABLED", "true"),
                "gigachat": os.getenv("FEATURE_GIGACHAT_ENABLED", "true"),
                "openrouter": os.getenv("FEATURE_OPENROUTER_ENABLED", "true"),
                "crawl4ai": os.getenv("FEATURE_CRAWL4AI_ENABLED", "true")
            }
        })
        
        logger.info("Event worker starting...")
        
        try:
            # Запуск health сервера
            start_health_server()
            logger.info("Health server started")
            
            # Инициализация БД подключения
            await self._init_db()
            
            # Инициализация event bus
            self.event_bus = get_event_publisher()
            
            # Инициализация задач
            await self._init_tasks()
            
            # Запуск задач
            await self._start_tasks()
            
            logger.info("Event worker started successfully")
            
            # Основной цикл
            while self.running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error("Failed to start event worker", error=str(e))
            raise
        finally:
            await self.stop()
    
    async def stop(self):
        """Остановка worker'а."""
        self.running = False
        logger.info("Event worker stopping...")
        
        # Остановка health сервера
        stop_health_server()
        logger.info("Health server stopped")
        
        # Остановка задач
        await self._stop_tasks()
        
        if self.db_connection:
            self.db_connection.close()
        
        logger.info("Event worker stopped")
    
    async def _init_db(self):
        """Инициализация подключения к БД."""
        try:
            self.db_connection = psycopg2.connect(settings.database_url)
            logger.info("Database connection established")
        except Exception as e:
            logger.error("Failed to connect to database", error=str(e))
            raise
    
    async def _init_tasks(self):
        """Инициализация задач."""
        try:
            # Tagging Task
            self.tagging_task = TaggingTask(
                redis_url=settings.redis_url,
                qdrant_url=settings.qdrant_url,
                neo4j_url=settings.neo4j_url
            )
            
            # Enrichment Task
            self.enrichment_task = EnrichmentTask(
                redis_url=settings.redis_url
            )
            
            # Indexing Task
            self.indexing_task = IndexingTask(
                redis_url=settings.redis_url,
                qdrant_url=settings.qdrant_url,
                neo4j_url=settings.neo4j_url
            )
            
            # Cleanup Task
            self.cleanup_task = CleanupTask(
                redis_url=settings.redis_url,
                qdrant_url=settings.qdrant_url,
                neo4j_url=settings.neo4j_url
            )
            
            logger.info("Tasks initialized successfully")
            
        except Exception as e:
            logger.error("Failed to initialize tasks", error=str(e))
            raise
    
    async def _start_tasks(self):
        """Запуск задач."""
        try:
            # Запуск задач в фоне
            tasks = []
            
            # Tagging Task
            if feature_flags.get_available_ai_providers():
                tasks.append(asyncio.create_task(self.tagging_task.start()))
                logger.info("Tagging task started")
            else:
                logger.warning("No AI providers available, skipping tagging task")
            
            # Enrichment Task
            if feature_flags.crawl4ai_enabled:
                tasks.append(asyncio.create_task(self.enrichment_task.start()))
                logger.info("Enrichment task started")
            else:
                logger.warning("Crawl4AI disabled, skipping enrichment task")
            
            # Indexing Task
            tasks.append(asyncio.create_task(self.indexing_task.start()))
            logger.info("Indexing task started")
            
            # Cleanup Task
            tasks.append(asyncio.create_task(self.cleanup_task.start()))
            logger.info("Cleanup task started")
            
            # Ожидание завершения задач
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error("Failed to start tasks", error=str(e))
            raise
    
    async def _stop_tasks(self):
        """Остановка задач."""
        try:
            # Остановка задач
            if self.tagging_task:
                await self.tagging_task.stop()
            
            if self.enrichment_task:
                await self.enrichment_task.stop()
            
            if self.indexing_task:
                await self.indexing_task.stop()
            
            if self.cleanup_task:
                await self.cleanup_task.stop()
            
            logger.info("All tasks stopped")
            
        except Exception as e:
            logger.error("Error stopping tasks", error=str(e))
    
    async def get_health_status(self) -> Dict[str, Any]:
        """Получение статуса здоровья worker'а."""
        try:
            health_status = {
                'worker': 'running' if self.running else 'stopped',
                'database': 'connected' if self.db_connection else 'disconnected',
                'event_bus': 'connected' if self.event_bus else 'disconnected',
                'tasks': {}
            }
            
            # Статус задач
            if self.tagging_task:
                health_status['tasks']['tagging'] = await self.tagging_task.health_check()
            
            if self.enrichment_task:
                health_status['tasks']['enrichment'] = await self.enrichment_task.health_check()
            
            if self.indexing_task:
                health_status['tasks']['indexing'] = await self.indexing_task.health_check()
            
            if self.cleanup_task:
                health_status['tasks']['cleanup'] = await self.cleanup_task.health_check()
            
            return health_status
            
        except Exception as e:
            logger.error("Error getting health status", error=str(e))
            return {
                'worker': 'error',
                'error': str(e)
            }


async def main():
    """Главная функция."""
    worker = EventWorker()
    
    # Обработка сигналов
    def signal_handler(signum, frame):
        logger.info("Received signal, shutting down...", signal=signum)
        asyncio.create_task(worker.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error("Worker failed", error=str(e))
        raise
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())