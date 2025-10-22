"""Worker для обработки событий Telegram Assistant.

Реализует event-driven архитектуру с использованием Redis Streams.
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
    
    async def start(self):
        """Запуск worker'а."""
        self.running = True
        logger.info("Event worker starting...")
        
        try:
            # Инициализация БД подключения
            await self._init_db()
            
            # Инициализация event bus
            await init_event_bus(settings.redis_url, self.db_connection)
            self.event_bus = await get_event_publisher()
            
            # Регистрация consumer'ов
            self._register_consumers()
            
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
    
    def _register_consumers(self):
        """Регистрация consumer'ов для обработки событий."""
        # Consumer для RAG индексации
        self.event_bus.register_consumer("rag-indexer", self._handle_rag_indexing)
        
        # Consumer для webhook уведомлений
        self.event_bus.register_consumer("webhook-notifier", self._handle_webhook_notifications)
        
        # Consumer для тегирования
        self.event_bus.register_consumer("tagging", self._handle_tagging)
        
        # Consumer для аналитики
        self.event_bus.register_consumer("analytics", self._handle_analytics)
        
        logger.info("Event consumers registered")
    
    async def _handle_rag_indexing(self, event: Dict[str, Any]):
        """Обработка событий для RAG индексации."""
        try:
            event_type = event["event_type"]
            
            if event_type == "channel.parsing.completed":
                await self._index_channel_posts(event)
            elif event_type == "rag.query.completed":
                await self._update_query_analytics(event)
            else:
                logger.debug("Unhandled event type for RAG indexing", event_type=event_type)
                
        except Exception as e:
            logger.error("Failed to handle RAG indexing event", 
                        event_id=event.get("event_id"),
                        error=str(e))
            raise
    
    async def _handle_webhook_notifications(self, event: Dict[str, Any]):
        """Обработка событий для webhook уведомлений."""
        try:
            event_type = event["event_type"]
            
            if event_type == "auth.login.authorized":
                await self._notify_user_authorized(event)
            elif event_type == "channel.parsing.completed":
                await self._notify_parsing_completed(event)
            else:
                logger.debug("Unhandled event type for webhook notifications", event_type=event_type)
                
        except Exception as e:
            logger.error("Failed to handle webhook notification event", 
                        event_id=event.get("event_id"),
                        error=str(e))
            raise
    
    async def _handle_tagging(self, event: Dict[str, Any]):
        """Обработка событий для тегирования."""
        try:
            event_type = event["event_type"]
            
            if event_type == "channel.parsing.completed":
                await self._tag_channel_posts(event)
            else:
                logger.debug("Unhandled event type for tagging", event_type=event_type)
                
        except Exception as e:
            logger.error("Failed to handle tagging event", 
                        event_id=event.get("event_id"),
                        error=str(e))
            raise
    
    async def _handle_analytics(self, event: Dict[str, Any]):
        """Обработка событий для аналитики."""
        try:
            # Логирование события для аналитики
            logger.info("Analytics event", 
                       event_type=event["event_type"],
                       tenant_id=event.get("tenant_id"),
                       user_id=event.get("user_id"),
                       correlation_id=event.get("correlation_id"))
            
            # TODO: Реализовать сохранение в аналитическую БД
            
        except Exception as e:
            logger.error("Failed to handle analytics event", 
                        event_id=event.get("event_id"),
                        error=str(e))
            raise
    
    async def _index_channel_posts(self, event: Dict[str, Any]):
        """Индексация постов канала в RAG."""
        payload = event["payload"]
        channel_id = payload["channel_id"]
        posts_parsed = payload["posts_parsed"]
        
        logger.info("Indexing channel posts", 
                   channel_id=channel_id,
                   posts_parsed=posts_parsed)
        
        # TODO: Реализовать индексацию в Qdrant
        # 1. Получить посты из БД
        # 2. Создать embeddings
        # 3. Сохранить в Qdrant
        
        logger.info("Channel posts indexed successfully", channel_id=channel_id)
    
    async def _update_query_analytics(self, event: Dict[str, Any]):
        """Обновление аналитики запросов."""
        payload = event["payload"]
        query_id = payload["query_id"]
        processing_time = payload["processing_time_ms"]
        
        logger.info("Updating query analytics", 
                   query_id=query_id,
                   processing_time=processing_time)
        
        # TODO: Реализовать сохранение аналитики запросов
    
    async def _notify_user_authorized(self, event: Dict[str, Any]):
        """Уведомление о авторизации пользователя."""
        payload = event["payload"]
        user_id = payload.get("user_id")
        tenant_id = event["tenant_id"]
        
        logger.info("Notifying user authorized", 
                   user_id=user_id,
                   tenant_id=tenant_id)
        
        # TODO: Реализовать отправку webhook уведомления
        # 1. Получить webhook URL из настроек tenant
        # 2. Отправить POST запрос с данными события
    
    async def _notify_parsing_completed(self, event: Dict[str, Any]):
        """Уведомление о завершении парсинга."""
        payload = event["payload"]
        channel_id = payload["channel_id"]
        posts_parsed = payload["posts_parsed"]
        
        logger.info("Notifying parsing completed", 
                   channel_id=channel_id,
                   posts_parsed=posts_parsed)
        
        # TODO: Реализовать отправку webhook уведомления
    
    async def _tag_channel_posts(self, event: Dict[str, Any]):
        """Тегирование постов канала."""
        payload = event["payload"]
        channel_id = payload["channel_id"]
        posts_parsed = payload["posts_parsed"]
        
        logger.info("Tagging channel posts", 
                   channel_id=channel_id,
                   posts_parsed=posts_parsed)
        
        # TODO: Реализовать автоматическое тегирование
        # 1. Получить посты из БД
        # 2. Применить ML модели для тегирования
        # 3. Сохранить теги в БД


async def main():
    """Главная функция worker'а."""
    worker = EventWorker()
    
    # Обработка сигналов для graceful shutdown
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