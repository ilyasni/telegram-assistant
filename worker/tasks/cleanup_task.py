"""
Cleanup Task - Consumer для posts.deleted событий
[C7-ID: WORKER-CLEANUP-002]

Обрабатывает события posts.deleted → деиндексация из Qdrant/Neo4j с checkpointing
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import structlog
from prometheus_client import Counter, Histogram, Gauge

from worker.event_bus import EventConsumer, RedisStreamsClient
from worker.events.schemas import PostDeletedEventV1
from worker.integrations.qdrant_client import QdrantClient
from worker.integrations.neo4j_client import Neo4jClient
from worker.feature_flags import feature_flags

logger = structlog.get_logger()

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

# [C7-ID: WORKER-CLEANUP-002] - Метрики cleanup
cleanup_processed_total = Counter(
    'cleanup_processed_total',
    'Total posts processed for cleanup',
    ['status']
)

cleanup_latency_seconds = Histogram(
    'cleanup_latency_seconds',
    'Cleanup processing latency',
    ['operation']
)

qdrant_cleanup_seconds = Histogram(
    'qdrant_cleanup_seconds',
    'Qdrant cleanup latency'
)

neo4j_cleanup_seconds = Histogram(
    'neo4j_cleanup_seconds',
    'Neo4j cleanup latency'
)

# [C7-ID: WORKER-ORPHAN-002] - Orphan cleanup метрики
orphan_cleanup_total = Counter(
    'orphan_cleanup_total',
    'Total orphan cleanup operations',
    ['type']
)

# ============================================================================
# CLEANUP TASK
# ============================================================================

class CleanupTask:
    """
    Consumer для обработки posts.deleted событий.
    
    Поддерживает:
    - Деиндексацию из Qdrant и Neo4j
    - Checkpointing для больших операций
    - Ограничение времени работы
    - Очистку висячих узлов
    - Метрики и мониторинг
    """
    
    def __init__(
        self,
        redis_url: str = "redis://redis:6379",
        qdrant_url: str = "http://localhost:6333",
        neo4j_url: str = "bolt://localhost:7687",
        consumer_group: str = "cleanup_workers",
        consumer_name: str = "cleanup_worker_1",
        max_job_duration_minutes: int = 30
    ):
        self.redis_url = redis_url
        self.qdrant_url = qdrant_url
        self.neo4j_url = neo4j_url
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.max_job_duration_minutes = max_job_duration_minutes
        
        # Redis клиенты
        self.redis_client: Optional[RedisStreamsClient] = None
        self.event_consumer: Optional[EventConsumer] = None
        
        # Интеграции
        self.qdrant_client: Optional[QdrantClient] = None
        self.neo4j_client: Optional[Neo4jClient] = None
        
        # [C7-ID: WORKER-CLEANUP-002] - Checkpointing
        self._checkpoint_key = f"cleanup_checkpoint:{consumer_name}"
        self._last_processed_post_id: Optional[str] = None
        
        logger.info("CleanupTask initialized", 
                   consumer_group=consumer_group,
                   consumer_name=consumer_name,
                   max_job_duration_minutes=max_job_duration_minutes)
    
    async def start(self):
        """Запуск cleanup task."""
        try:
            # Подключение к Redis
            self.redis_client = RedisStreamsClient(self.redis_url)
            await self.redis_client.connect()
            
            # Инициализация EventConsumer
            self.event_consumer = EventConsumer(
                self.redis_client,
                stream_name="posts.deleted",
                consumer_group=self.consumer_group,
                consumer_name=self.consumer_name
            )
            
            # Инициализация Qdrant клиента
            self.qdrant_client = QdrantClient(self.qdrant_url)
            await self.qdrant_client.connect()
            
            # Инициализация Neo4j клиента
            if feature_flags.neo4j_enabled:
                self.neo4j_client = Neo4jClient(self.neo4j_url)
                await self.neo4j_client.connect()
            
            # Загрузка checkpoint
            await self._load_checkpoint()
            
            logger.info("CleanupTask started successfully")
            
            # Context7: Запуск периодического TTL cleanup в фоне
            # Запускаем каждые 6 часов (настраивается через ENV)
            import os
            ttl_cleanup_interval_hours = int(os.getenv("CLEANUP_TTL_INTERVAL_HOURS", "6"))
            ttl_cleanup_interval_seconds = ttl_cleanup_interval_hours * 3600
            
            async def periodic_ttl_cleanup():
                """Периодический запуск TTL cleanup."""
                while True:
                    try:
                        await asyncio.sleep(ttl_cleanup_interval_seconds)
                        logger.info("Starting periodic TTL cleanup", interval_hours=ttl_cleanup_interval_hours)
                        results = await self.run_expired_cleanup()
                        logger.info("Periodic TTL cleanup completed", results=results)
                    except Exception as e:
                        logger.error("Error in periodic TTL cleanup", error=str(e))
                        await asyncio.sleep(60)  # Пауза при ошибке
            
            # Запуск периодического TTL cleanup в фоне
            asyncio.create_task(periodic_ttl_cleanup())
            logger.info("Periodic TTL cleanup scheduled", interval_hours=ttl_cleanup_interval_hours)
            
            # Основной цикл обработки
            while True:
                try:
                    await self._process_messages()
                    await asyncio.sleep(1)  # Пауза между циклами
                except Exception as e:
                    logger.error("Error in cleanup processing loop", error=str(e))
                    await asyncio.sleep(5)  # Пауза при ошибке
                    
        except Exception as e:
            logger.error("Failed to start CleanupTask", error=str(e))
            raise
    
    async def _process_messages(self):
        """Обработка сообщений из стрима."""
        try:
            messages = await self.event_consumer.consume_messages(max_messages=10)
            
            if not messages:
                return
            
            logger.info("Processing cleanup batch", batch_size=len(messages))
            
            # Обработка каждого сообщения
            for message in messages:
                try:
                    await self._process_single_message(message)
                except Exception as e:
                    logger.error("Error processing message", 
                               message_id=message.get('id'),
                               error=str(e))
                    cleanup_processed_total.labels(status='error').inc()
            
        except Exception as e:
            logger.error("Error in message processing", error=str(e))
            cleanup_processed_total.labels(status='batch_error').inc()
    
    async def _process_single_message(self, message: Dict[str, Any]):
        """Обработка одного сообщения."""
        try:
            # Парсинг события
            event_data = json.loads(message['data'])
            deleted_event = PostDeletedEventV1(**event_data)
            
            # Проверка checkpoint
            if (self._last_processed_post_id and 
                deleted_event.post_id <= self._last_processed_post_id):
                logger.debug("Message already processed", 
                           post_id=deleted_event.post_id)
                return
            
            # Cleanup
            start_time = time.time()
            success = await self._cleanup_post(deleted_event)
            processing_time = time.time() - start_time
            
            if success:
                # Сохранение checkpoint
                await self._save_checkpoint(deleted_event.post_id)
                
                # Метрики
                cleanup_processed_total.labels(status='success').inc()
                cleanup_latency_seconds.labels(operation='full').observe(processing_time)
                
                logger.info("Post cleanup completed successfully",
                           post_id=deleted_event.post_id,
                           processing_time=processing_time)
            else:
                # Обработка неудачи
                cleanup_processed_total.labels(status='failed').inc()
                logger.warning("Post cleanup failed", post_id=deleted_event.post_id)
                
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in message", 
                        message_id=message.get('id'),
                        error=str(e))
            cleanup_processed_total.labels(status='json_error').inc()
            
        except Exception as e:
            logger.error("Unexpected error processing message",
                        message_id=message.get('id'),
                        error=str(e))
            cleanup_processed_total.labels(status='unexpected_error').inc()
    
    async def _cleanup_post(self, deleted_event: PostDeletedEventV1) -> bool:
        """Cleanup поста из Qdrant и Neo4j."""
        try:
            # Cleanup из Qdrant
            qdrant_start = time.time()
            qdrant_success = await self._cleanup_from_qdrant(deleted_event)
            qdrant_time = time.time() - qdrant_start
            
            if not qdrant_success:
                logger.error("Failed to cleanup from Qdrant", post_id=deleted_event.post_id)
                return False
            
            # Cleanup из Neo4j
            neo4j_success = True
            neo4j_time = 0
            if self.neo4j_client:
                neo4j_start = time.time()
                neo4j_success = await self._cleanup_from_neo4j(deleted_event)
                neo4j_time = time.time() - neo4j_start
                
                if not neo4j_success:
                    logger.error("Failed to cleanup from Neo4j", post_id=deleted_event.post_id)
                    return False
            
            # Метрики
            qdrant_cleanup_seconds.observe(qdrant_time)
            if neo4j_time > 0:
                neo4j_cleanup_seconds.observe(neo4j_time)
            
            return True
            
        except Exception as e:
            logger.error("Error cleaning up post", 
                        post_id=deleted_event.post_id,
                        error=str(e))
            return False
    
    async def _cleanup_from_qdrant(self, deleted_event: PostDeletedEventV1) -> bool:
        """Cleanup из Qdrant."""
        try:
            if not self.qdrant_client:
                logger.warning("Qdrant client not available")
                return True  # Не критично для работы
            
            # Определение коллекции (per-user)
            collection_name = f"user_{deleted_event.tenant_id}_posts"
            
            # Удаление вектора
            success = await self.qdrant_client.delete_vector(
                collection_name=collection_name,
                vector_id=deleted_event.post_id
            )
            
            if success:
                logger.debug("Post deleted from Qdrant",
                           post_id=deleted_event.post_id,
                           collection=collection_name)
            
            return success
            
        except Exception as e:
            logger.error("Error cleaning up from Qdrant",
                        post_id=deleted_event.post_id,
                        error=str(e))
            return False
    
    async def _cleanup_from_neo4j(self, deleted_event: PostDeletedEventV1) -> bool:
        """Cleanup из Neo4j."""
        try:
            if not self.neo4j_client:
                logger.warning("Neo4j client not available")
                return True  # Не критично для работы
            
            # Удаление узла поста
            success = await self.neo4j_client.delete_post_node(deleted_event.post_id)
            
            if success:
                logger.debug("Post deleted from Neo4j",
                           post_id=deleted_event.post_id)
            
            return success
            
        except Exception as e:
            logger.error("Error cleaning up from Neo4j",
                        post_id=deleted_event.post_id,
                        error=str(e))
            return False
    
    async def _load_checkpoint(self):
        """Загрузка checkpoint из Redis."""
        try:
            if not self.redis_client:
                return
            
            checkpoint_data = await self.redis_client.get(self._checkpoint_key)
            if checkpoint_data:
                self._last_processed_post_id = checkpoint_data
                logger.info("Checkpoint loaded", 
                          last_processed_post_id=self._last_processed_post_id)
            
        except Exception as e:
            logger.error("Error loading checkpoint", error=str(e))
    
    async def _save_checkpoint(self, post_id: str):
        """Сохранение checkpoint в Redis."""
        try:
            if not self.redis_client:
                return
            
            await self.redis_client.setex(
                self._checkpoint_key,
                86400,  # 24 часа TTL
                post_id
            )
            
            self._last_processed_post_id = post_id
            logger.debug("Checkpoint saved", post_id=post_id)
            
        except Exception as e:
            logger.error("Error saving checkpoint", error=str(e))
    
    async def run_orphan_cleanup(self) -> Dict[str, int]:
        """
        [C7-ID: WORKER-ORPHAN-002] - Еженедельная очистка висячих узлов.
        
        Запускается отдельно от основного цикла.
        """
        try:
            start_time = time.time()
            results = {}
            
            # Очистка висячих тегов в Neo4j
            if self.neo4j_client:
                orphan_tags_count = await self.neo4j_client.cleanup_orphan_tags()
                results['orphan_tags'] = orphan_tags_count
                orphan_cleanup_total.labels(type='tags').inc(orphan_tags_count)
            
            # Sweep expired векторов в Qdrant
            if self.qdrant_client:
                sweep_results = await self.qdrant_client.sweep_all_collections()
                total_swept = sum(sweep_results.values())
                results['expired_vectors'] = total_swept
                orphan_cleanup_total.labels(type='vectors').inc(total_swept)
            
            processing_time = time.time() - start_time
            
            logger.info("Orphan cleanup completed",
                       results=results,
                       processing_time=processing_time)
            
            return results
            
        except Exception as e:
            logger.error("Error in orphan cleanup", error=str(e))
            return {}
    
    async def run_expired_cleanup(self) -> Dict[str, int]:
        """Очистка expired постов по TTL."""
        try:
            start_time = time.time()
            results = {}
            
            # Очистка expired постов в Neo4j
            if self.neo4j_client:
                expired_posts_count = await self.neo4j_client.cleanup_expired_posts()
                results['expired_posts'] = expired_posts_count
            
            # Sweep expired векторов в Qdrant
            if self.qdrant_client:
                sweep_results = await self.qdrant_client.sweep_all_collections()
                total_swept = sum(sweep_results.values())
                results['expired_vectors'] = total_swept
            
            processing_time = time.time() - start_time
            
            logger.info("Expired cleanup completed",
                       results=results,
                       processing_time=processing_time)
            
            return results
            
        except Exception as e:
            logger.error("Error in expired cleanup", error=str(e))
            return {}
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики cleanup task."""
        return {
            'redis_connected': self.redis_client is not None,
            'qdrant_connected': self.qdrant_client is not None,
            'neo4j_connected': self.neo4j_client is not None,
            'last_processed_post_id': self._last_processed_post_id,
            'max_job_duration_minutes': self.max_job_duration_minutes,
            'feature_flags': {
                'neo4j_enabled': feature_flags.neo4j_enabled
            }
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check для cleanup task."""
        try:
            # Проверка Redis
            redis_healthy = False
            if self.redis_client:
                await self.redis_client.ping()
                redis_healthy = True
            
            # Проверка Qdrant
            qdrant_healthy = False
            if self.qdrant_client:
                qdrant_healthy = await self.qdrant_client.health_check()
            
            # Проверка Neo4j
            neo4j_healthy = True  # Не критично
            if self.neo4j_client:
                neo4j_healthy = await self.neo4j_client.health_check()
            
            return {
                'status': 'healthy' if (redis_healthy and qdrant_healthy) else 'unhealthy',
                'redis': 'connected' if redis_healthy else 'disconnected',
                'qdrant': 'connected' if qdrant_healthy else 'disconnected',
                'neo4j': 'connected' if neo4j_healthy else 'disconnected',
                'stats': await self.get_stats()
            }
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return {
                'status': 'unhealthy',
                'error': str(e),
                'stats': await self.get_stats()
            }