"""
Graph Writer Task - Worker для обработки событий и создания графовых связей в Neo4j (Context7 P2).

Читает события из Redis Streams и синхронизирует forwards/replies/author данные в Neo4j.
"""
import asyncio
import os
import sys
from typing import Optional
import structlog

# Добавляем путь для импортов
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from worker.integrations.neo4j_client import Neo4jClient
from worker.services.graph_writer import GraphWriter
from worker.services.retry_policy import DLQService
from worker.config import Settings

logger = structlog.get_logger()

# Redis Stream keys
STREAM_POSTS_PARSED = "stream:posts:parsed"


class GraphWriterTask:
    """
    Task для обработки событий post.parsed и создания графовых связей в Neo4j.
    
    Context7 P2:
    - Читает события из Redis Streams
    - Создаёт графовые связи (forwards/replies/author) в Neo4j
    - Поддерживает Consumer Groups для распределённой обработки
    - Обрабатывает батчами для эффективности
    """
    
    def __init__(
        self,
        redis_url: str,
        neo4j_url: str,
        neo4j_user: str,
        neo4j_password: str,
        consumer_group: str = "graph_writer",
        batch_size: int = 100
    ):
        """
        Инициализация GraphWriterTask.
        
        Args:
            redis_url: URL для подключения к Redis
            neo4j_url: URL для подключения к Neo4j
            neo4j_user: Имя пользователя Neo4j
            neo4j_password: Пароль Neo4j
            consumer_group: Имя consumer group для Redis Streams
            batch_size: Размер батча для обработки событий
        """
        self.redis_url = redis_url
        self.neo4j_url = neo4j_url
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self.consumer_group = consumer_group
        self.batch_size = batch_size
        
        self.neo4j_client: Optional[Neo4jClient] = None
        self.graph_writer: Optional[GraphWriter] = None
        self._running = False
        
        logger.info("GraphWriterTask initialized",
                   consumer_group=consumer_group,
                   batch_size=batch_size)
    
    async def initialize(self):
        """Инициализация подключений."""
        try:
            # Context7 P2: Подключение к Neo4j
            logger.info("Connecting to Neo4j", uri=self.neo4j_url)
            self.neo4j_client = Neo4jClient(
                uri=self.neo4j_url,
                username=self.neo4j_user,
                password=self.neo4j_password
            )
            await self.neo4j_client.connect()
            logger.info("Neo4j connected successfully")
            
            # Context7 P2: Подключение к Redis
            import redis.asyncio as redis
            logger.info("Connecting to Redis", url=self.redis_url)
            redis_client = redis.from_url(self.redis_url, decode_responses=False)
            await redis_client.ping()
            logger.info("Redis connected successfully")
            
            # Context7 P2: Создание DLQService (опционально)
            dlq_enabled = os.getenv("GRAPH_WRITER_DLQ_ENABLED", "true").lower() in {"1", "true", "yes"}
            dlq_service = DLQService(redis_client) if dlq_enabled else None
            
            # Context7 P2: Создание GraphWriter
            max_retries = int(os.getenv("GRAPH_WRITER_MAX_RETRIES", "10"))
            self.graph_writer = GraphWriter(
                neo4j_client=self.neo4j_client,
                redis_client=redis_client,
                dlq_service=dlq_service,
                consumer_group=self.consumer_group,
                batch_size=self.batch_size,
                max_retries=max_retries
            )
            logger.info("GraphWriter created successfully",
                       dlq_enabled=dlq_enabled,
                       max_retries=max_retries)
            
        except Exception as e:
            logger.error("Error initializing GraphWriterTask",
                        error=str(e),
                        exc_info=True)
            raise
    
    async def start(self):
        """Запуск обработки событий."""
        if self._running:
            logger.warning("GraphWriterTask already running")
            return
        
        if not self.graph_writer:
            await self.initialize()
        
        self._running = True
        
        try:
            logger.info("Starting GraphWriterTask consumption",
                       stream_key=STREAM_POSTS_PARSED,
                       consumer_group=self.consumer_group)
            
            # Context7 P2: Запуск consumption событий
            await self.graph_writer.start_consuming(STREAM_POSTS_PARSED)
            
        except KeyboardInterrupt:
            logger.info("GraphWriterTask stopped by user")
            await self.stop()
        except Exception as e:
            logger.error("GraphWriterTask consumption failed",
                        error=str(e),
                        exc_info=True)
            self._running = False
            raise
    
    async def stop(self):
        """Остановка обработки событий."""
        self._running = False
        
        if self.graph_writer:
            await self.graph_writer.stop()
        
        if self.neo4j_client:
            await self.neo4j_client.close()
        
        logger.info("GraphWriterTask stopped")
    
    async def health_check(self) -> dict:
        """
        Context7 P2: Health check для мониторинга GraphWriter.
        
        Проверяет:
        - Статус работы (running)
        - Подключение к Neo4j
        - Подключение к Redis
        - PEL размер (количество pending messages)
        - Возраст самого старого pending сообщения
        """
        try:
            health = {
                'running': self._running,
                'neo4j_connected': False,
                'redis_connected': False,
                'pel_size': 0,
                'pending_older_than_seconds': 0
            }
            
            # Context7 P2: Проверка подключения к Neo4j
            if self.neo4j_client:
                try:
                    health['neo4j_connected'] = await self.neo4j_client.health_check()
                    health['neo4j_stats'] = await self.neo4j_client.get_stats()
                except Exception as e:
                    logger.warning("Neo4j health check failed", error=str(e))
                    health['neo4j_error'] = str(e)
            
            # Context7 P2: Проверка подключения к Redis
            if self.graph_writer and hasattr(self.graph_writer, 'redis_client'):
                try:
                    await self.graph_writer.redis_client.ping()
                    health['redis_connected'] = True
                except Exception as e:
                    health['redis_connected'] = False
                    health['redis_error'] = str(e)
            
            # Context7 P2: Получение статистики PEL
            if self.graph_writer:
                try:
                    stats = await self.graph_writer.get_stats()
                    health['pel_size'] = stats.get('pel_size', 0)
                    health['pending_older_than_seconds'] = stats.get('pending_older_than_seconds', 0)
                    health['retry_count'] = stats.get('retry_count', 0)
                    
                    # Context7 P2: Проверка здоровья на основе PEL
                    if health['pel_size'] > 1000:
                        health['health_status'] = 'degraded'
                        health['health_issue'] = f'High PEL size: {health["pel_size"]}'
                    elif health['pending_older_than_seconds'] > 3600:
                        health['health_status'] = 'degraded'
                        health['health_issue'] = f'Old pending messages: {health["pending_older_than_seconds"]:.0f}s'
                    else:
                        health['health_status'] = 'healthy'
                except Exception as e:
                    logger.warning("Failed to get GraphWriter stats", error=str(e))
                    health['stats_error'] = str(e)
            
            return health
            
        except Exception as e:
            logger.error("Error during health check", error=str(e))
            return {
                'running': self._running,
                'health_status': 'error',
                'error': str(e)
            }


async def main():
    """Главная функция для запуска GraphWriterTask."""
    # Context7: Загрузка настроек из ENV
    settings = Settings()
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    neo4j_url = os.getenv("NEO4J_URI", settings.neo4j_url)
    neo4j_user = os.getenv("NEO4J_USER", settings.neo4j_username)
    neo4j_password = os.getenv("NEO4J_PASSWORD", settings.neo4j_password)
    
    consumer_group = os.getenv("GRAPH_WRITER_CONSUMER_GROUP", "graph_writer")
    batch_size = int(os.getenv("GRAPH_WRITER_BATCH_SIZE", "100"))
    
    task = GraphWriterTask(
        redis_url=redis_url,
        neo4j_url=neo4j_url,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        consumer_group=consumer_group,
        batch_size=batch_size
    )
    
    try:
        await task.start()
    except KeyboardInterrupt:
        logger.info("Shutting down GraphWriterTask")
        await task.stop()
    except Exception as e:
        logger.error("GraphWriterTask failed", error=str(e), exc_info=True)
        await task.stop()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

