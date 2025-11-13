"""Задачи для индексации и обработки событий."""

import asyncio
import structlog
import redis
from typing import Dict, Any
from datetime import datetime

from config import settings
from tasks.embeddings import EmbeddingService
from tasks.events import (
    read_post_created,
    ensure_stream_group,
    read_post_created_group,
    ack_post_created,
    publish_post_tagged,
    publish_post_indexed,
    read_post_tagged_group,
    ack_post_tagged,
    read_group_message_created_group,
    ack_group_message_created,
    STREAM_GROUP_MESSAGE_CREATED,
    GROUP_GROUP_MESSAGE,
)

logger = structlog.get_logger()


class IndexingService:
    """Сервис для обработки событий индексации."""
    
    def __init__(self):
        self.redis_client = None
        self.embedding_service = None
        self.is_running = False
        
    async def initialize(self):
        """Инициализация сервиса."""
        try:
            print("Connecting to Redis...", flush=True)
            # Подключение к Redis
            self.redis_client = redis.from_url(settings.redis_url)
            logger.info("Connected to Redis", url=settings.redis_url)
            print("Redis connected", flush=True)
            
            print("Initializing embedding service...", flush=True)
            # Инициализация сервиса embeddings
            self.embedding_service = EmbeddingService()
            await self.embedding_service.initialize()
            print("Embedding service initialized", flush=True)

            print("Setting up consumer groups...", flush=True)
            # Обеспечим Consumer Group для надежного чтения по обоим потокам
            ensure_stream_group(self.redis_client, "events:post.created", "worker")
            ensure_stream_group(self.redis_client, "events:post.tagged", "worker")
            ensure_stream_group(self.redis_client, STREAM_GROUP_MESSAGE_CREATED, GROUP_GROUP_MESSAGE)
            # зарегистрируем консюмера
            from tasks.events import ensure_consumer
            ensure_consumer(self.redis_client, "events:post.created", "worker", "worker-1")
            ensure_consumer(self.redis_client, "events:post.tagged", "worker", "worker-1")
            ensure_consumer(self.redis_client, STREAM_GROUP_MESSAGE_CREATED, GROUP_GROUP_MESSAGE, "worker-1")
            print("Consumer groups set up", flush=True)
            
            logger.info("Indexing service initialized")
            print("Indexing service initialized", flush=True)
            
        except Exception as e:
            logger.error("Failed to initialize indexing service", error=str(e))
            print(f"Failed to initialize indexing service: {e}", flush=True)
            raise
    
    async def start_processing(self):
        """Запуск обработки событий."""
        self.is_running = True
        logger.info("Started indexing service")
        
        while self.is_running:
            try:
                # Обработка событий post.created
                await self._process_post_created_events()
                
                # Обработка групповых сообщений
                await self._process_group_message_events()
                
                # Обработка событий post.tagged
                await self._process_post_tagged_events()
                
                # Обработка событий post.indexed
                await self._process_post_indexed_events()
                
                # Пауза между циклами
                await asyncio.sleep(settings.processing_interval)
                
            except Exception as e:
                logger.error("Error in indexing loop", error=str(e))
                await asyncio.sleep(settings.retry_delay)
    
    async def _process_post_created_events(self):
        """Обработка событий создания постов."""
        try:
            logger.info("Checking for post.created events...")
            # Чтение событий из Redis Stream с коротким таймаутом для диагностики
            items = read_post_created_group(self.redis_client, "worker", consumer="worker-1", count=settings.batch_size, block_ms=500)
            logger.info("Read items from stream", count=len(items) if items else 0)
            
            if not items:
                logger.debug("No post.created events found")
                return

            for msg_id, event_data in items:
                try:
                    logger.info("Processing post.created event",
                               post_id=event_data['post_id'],
                               tenant_id=event_data['tenant_id'])

                    # Создание записи в indexing_status
                    await self._create_indexing_status(event_data['post_id'], event_data['tenant_id'])

                    # Обработка embeddings
                    await self.embedding_service.process_post_embeddings(
                        event_data['post_id'],
                        event_data['tenant_id']
                    )

                    # Публикация события post.tagged
                    publish_post_tagged(self.redis_client, {
                        'post_id': event_data['post_id'],
                        'tenant_id': event_data['tenant_id'],
                        'status': 'completed'
                    })

                    # ACK сообщения
                    ack_post_created(self.redis_client, "worker", msg_id)

                except Exception as e:
                    logger.error("Failed to process post.created event",
                               msg_id=msg_id,
                               error=str(e))
                        
        except Exception as e:
            logger.error("Failed to process post.created events", error=str(e))
    
    async def _process_post_tagged_events(self):
        """Обработка событий тегирования постов."""
        try:
            items = read_post_tagged_group(self.redis_client, "worker", consumer="worker-1", count=settings.batch_size, block_ms=500)
            if not items:
                return

            for msg_id, event_data in items:
                try:
                    logger.info("Processing post.tagged event", 
                               post_id=event_data.get('post_id'),
                               status=event_data.get('status'))

                    # Публикация события post.indexed
                    publish_post_indexed(self.redis_client, {
                        'post_id': event_data.get('post_id'),
                        'tenant_id': event_data.get('tenant_id'),
                        'status': 'completed'
                    })

                    # ACK сообщения
                    ack_post_tagged(self.redis_client, "worker", msg_id)

                except Exception as e:
                    logger.error("Failed to process post.tagged event", 
                               msg_id=msg_id, 
                               error=str(e))
                        
        except Exception as e:
            logger.error("Failed to process post.tagged events", error=str(e))
    
    async def _process_post_indexed_events(self):
        """Обработка событий индексации постов."""
        try:
            stream_name = "events:post.indexed"
            messages = self.redis_client.xread({stream_name: "$"}, count=settings.batch_size, block=1000)
            
            for stream, msgs in messages:
                for msg_id, fields in msgs:
                    try:
                        event_data = {
                            'post_id': fields.get(b'post_id', b'').decode(),
                            'tenant_id': fields.get(b'tenant_id', b'').decode(),
                            'status': fields.get(b'status', b'').decode()
                        }
                        
                        logger.info("Processing post.indexed event", 
                                   post_id=event_data['post_id'],
                                   status=event_data['status'])
                        
                        # Обновление статуса в БД
                        await self._update_post_processed_status(event_data['post_id'], True)
                        
                    except Exception as e:
                        logger.error("Failed to process post.indexed event", 
                                   msg_id=msg_id, 
                                   error=str(e))
                        
        except Exception as e:
            logger.error("Failed to process post.indexed events", error=str(e))
    
    async def _create_indexing_status(self, post_id: str, tenant_id: str):
        """Создание записи статуса индексации."""
        try:
            # Здесь должна быть логика создания записи в БД
            # Пока что просто логируем
            logger.info("Created indexing status", post_id=post_id, tenant_id=tenant_id)
            
        except Exception as e:
            logger.error("Failed to create indexing status", error=str(e))
    
    async def _update_post_processed_status(self, post_id: str, is_processed: bool):
        """Обновление статуса обработки поста."""
        try:
            # Здесь должна быть логика обновления БД
            # Пока что просто логируем
            logger.info("Updated post processed status", 
                       post_id=post_id, 
                       is_processed=is_processed)
            
        except Exception as e:
            logger.error("Failed to update post processed status", error=str(e))
    
    async def _publish_event(self, event_type: str, data: Dict[str, Any]):
        """Публикация события в Redis Stream."""
        try:
            stream_name = f"events:{event_type}"
            self.redis_client.xadd(stream_name, data)
            logger.debug("Published event", event_type=event_type, stream=stream_name)
            
        except Exception as e:
            logger.error("Failed to publish event", error=str(e))
    
    async def stop(self):
        """Остановка сервиса."""
        self.is_running = False
        if self.embedding_service:
            await self.embedding_service.close()
        logger.info("Indexing service stopped")

    async def _process_group_message_events(self):
        """Обработка событий создания сообщений в группах."""
        try:
            items = read_group_message_created_group(
                self.redis_client,
                GROUP_GROUP_MESSAGE,
                consumer="worker-1",
                count=settings.batch_size,
                block_ms=500,
            )

            if not items:
                return

            for msg_id, event_data in items:
                try:
                    group_message_id = event_data.get("group_message_id")
                    tenant_id = event_data.get("tenant_id")

                    if not group_message_id or not tenant_id:
                        logger.warning(
                            "Invalid group message event payload",
                            msg_id=msg_id,
                            payload=event_data,
                        )
                        await asyncio.sleep(0)  # yield control
                        continue

                    await self.embedding_service.process_group_message_embeddings(
                        group_message_id,
                        tenant_id,
                    )

                    ack_group_message_created(self.redis_client, GROUP_GROUP_MESSAGE, msg_id)

                except Exception as e:
                    logger.error(
                        "Failed to process group message event",
                        msg_id=msg_id,
                        error=str(e),
                    )
        except Exception as e:
            logger.error("Failed to process group message events", error=str(e))
