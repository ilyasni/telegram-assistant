"""
Outbox Relay для надёжной доставки событий из БД в Redis Streams
[C7-ID: WORKER-OUTBOX-002]

Поддерживает DLQ, trace_id, идемпотентность и метрики
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import redis.asyncio as redis
from prometheus_client import Counter, Histogram, Gauge
import structlog

from .event_bus import EventPublisher, RedisStreamsClient
from .events.schema_registry import get_schema_registry, validate_event_data

logger = structlog.get_logger()

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

# [C7-ID: WORKER-OUTBOX-002]
outbox_processed_total = Counter(
    'outbox_processed_total',
    'Total processed events from outbox',
    ['event_type', 'status']
)

outbox_failed_total = Counter(
    'outbox_failed_total', 
    'Total failed events from outbox',
    ['event_type', 'reason']
)

outbox_lag_seconds = Histogram(
    'outbox_lag_seconds',
    'Outbox processing lag (created_at to processed_at)',
    ['event_type']
)

outbox_dlq_total = Counter(
    'outbox_dlq_total',
    'Total events moved to DLQ',
    ['event_type']
)

outbox_batch_size = Histogram(
    'outbox_batch_size',
    'Outbox batch processing size'
)

outbox_processing_duration = Histogram(
    'outbox_processing_duration_seconds',
    'Outbox batch processing duration',
    ['event_type']
)

# ============================================================================
# OUTBOX RELAY
# ============================================================================

class OutboxRelay:
    """
    Relay для обработки событий из outbox_events в Redis Streams.
    
    Поддерживает:
    - Идемпотентную обработку
    - DLQ для failed событий
    - Сквозной trace_id
    - Метрики и мониторинг
    """
    
    def __init__(
        self,
        db_connection,
        redis_url: str = "redis://redis:6379",
        batch_size: int = 100,
        max_retries: int = 3,
        processing_interval: int = 30
    ):
        self.db_connection = db_connection
        self.redis_url = redis_url
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.processing_interval = processing_interval
        
        # Redis клиенты
        self.redis_client: Optional[redis.Redis] = None
        self.event_publisher: Optional[EventPublisher] = None
        
        # Schema Registry для валидации
        self.schema_registry = get_schema_registry()
        
        # Статистика
        self.stats = {
            'processed': 0,
            'failed': 0,
            'dlq_moved': 0,
            'last_processed_at': None
        }
        
        logger.info("OutboxRelay initialized", 
                   batch_size=batch_size, 
                   max_retries=max_retries,
                   processing_interval=processing_interval)
    
    async def start(self):
        """Запуск outbox relay."""
        try:
            # Подключение к Redis
            self.redis_client = redis.from_url(self.redis_url, decode_responses=True)
            await self.redis_client.ping()
            
            # Инициализация EventPublisher
            streams_client = RedisStreamsClient(self.redis_url)
            await streams_client.connect()
            self.event_publisher = EventPublisher(streams_client)
            
            logger.info("OutboxRelay started successfully")
            
            # Основной цикл обработки
            while True:
                try:
                    await self._process_batch()
                    await asyncio.sleep(self.processing_interval)
                except Exception as e:
                    logger.error("Error in outbox processing loop", error=str(e))
                    await asyncio.sleep(5)  # Пауза при ошибке
                    
        except Exception as e:
            logger.error("Failed to start OutboxRelay", error=str(e))
            raise
    
    async def _process_batch(self):
        """Обработка батча событий из outbox."""
        start_time = time.time()
        
        try:
            # Получение необработанных событий
            events = await self._get_unprocessed_events()
            
            if not events:
                logger.debug("No unprocessed events found")
                return
            
            logger.info("Processing outbox batch", 
                       batch_size=len(events),
                       event_types=[e['event_type'] for e in events])
            
            # Обработка каждого события
            processed_count = 0
            failed_count = 0
            
            for event in events:
                try:
                    success = await self._process_single_event(event)
                    if success:
                        processed_count += 1
                    else:
                        failed_count += 1
                        
                except Exception as e:
                    logger.error("Error processing event", 
                               event_id=event['id'],
                               error=str(e))
                    failed_count += 1
            
            # Обновление статистики
            self.stats['processed'] += processed_count
            self.stats['failed'] += failed_count
            self.stats['last_processed_at'] = datetime.now(timezone.utc)
            
            # Метрики
            outbox_processed_total.labels(
                event_type='batch',
                status='success'
            ).inc(processed_count)
            
            outbox_failed_total.labels(
                event_type='batch',
                reason='processing_error'
            ).inc(failed_count)
            
            outbox_batch_size.observe(len(events))
            outbox_processing_duration.labels(
                event_type='batch'
            ).observe(time.time() - start_time)
            
            logger.info("Outbox batch processed",
                       processed=processed_count,
                       failed=failed_count,
                       duration_ms=int((time.time() - start_time) * 1000))
            
        except Exception as e:
            logger.error("Error in outbox batch processing", error=str(e))
            outbox_failed_total.labels(
                event_type='batch',
                reason='batch_error'
            ).inc()
    
    async def _get_unprocessed_events(self) -> List[Dict[str, Any]]:
        """Получение необработанных событий из БД."""
        query = """
        SELECT id, event_type, payload, schema_version, trace_id, 
               aggregate_id, content_hash, created_at, retry_count, last_error
        FROM outbox_events 
        WHERE processed_at IS NULL 
        AND (retry_count < %s OR retry_count IS NULL)
        ORDER BY created_at
        LIMIT %s
        """
        
        async with self.db_connection.cursor() as cursor:
            await cursor.execute(query, (self.max_retries, self.batch_size))
            rows = await cursor.fetchall()
            
            events = []
            for row in rows:
                events.append({
                    'id': row[0],
                    'event_type': row[1],
                    'payload': row[2],
                    'schema_version': row[3],
                    'trace_id': row[4],
                    'aggregate_id': row[5],
                    'content_hash': row[6],
                    'created_at': row[7],
                    'retry_count': row[8] or 0,
                    'last_error': row[9]
                })
            
            return events
    
    async def _process_single_event(self, event: Dict[str, Any]) -> bool:
        """Обработка одного события."""
        event_id = event['id']
        event_type = event['event_type']
        
        try:
            # Валидация события через Schema Registry
            validated_event = validate_event_data(
                event['payload'],
                event_type,
                event['schema_version']
            )
            
            if not validated_event:
                logger.error("Event validation failed", 
                           event_id=event_id,
                           event_type=event_type)
                await self._increment_retry_count(event_id, "validation_failed")
                return False
            
            # Публикация в Redis Streams
            stream_name = self._get_stream_name(event_type)
            message_id = await self.event_publisher.publish_event(
                stream_name, 
                validated_event
            )
            
            # Отметка как обработанное (идемпотентно)
            await self._mark_as_processed(event_id, message_id)
            
            # Метрики
            outbox_processed_total.labels(
                event_type=event_type,
                status='success'
            ).inc()
            
            # Лаг-метрика
            lag_seconds = (datetime.now(timezone.utc) - event['created_at']).total_seconds()
            outbox_lag_seconds.labels(event_type=event_type).observe(lag_seconds)
            
            logger.debug("Event processed successfully",
                        event_id=event_id,
                        event_type=event_type,
                        message_id=message_id,
                        lag_seconds=lag_seconds)
            
            return True
            
        except Exception as e:
            logger.error("Error processing event",
                        event_id=event_id,
                        event_type=event_type,
                        error=str(e))
            
            # Увеличение счетчика попыток
            await self._increment_retry_count(event_id, str(e))
            
            # Метрики
            outbox_failed_total.labels(
                event_type=event_type,
                reason='processing_error'
            ).inc()
            
            return False
    
    async def _get_stream_name(self, event_type: str) -> str:
        """Получение имени стрима для типа события."""
        # Маппинг типов событий на стримы
        stream_mapping = {
            'posts.parsed': 'posts.parsed',
            'posts.tagged': 'posts.tagged',
            'posts.enriched': 'posts.enriched',
            'posts.indexed': 'posts.indexed',
            'posts.deleted': 'posts.deleted',
            'channels.subscribed': 'channels.subscribed'
        }
        
        return stream_mapping.get(event_type, event_type)
    
    async def _mark_as_processed(self, event_id: int, message_id: str):
        """Отметка события как обработанного (идемпотентно)."""
        query = """
        UPDATE outbox_events 
        SET processed_at = NOW()
        WHERE id = %s AND processed_at IS NULL
        """
        
        async with self.db_connection.cursor() as cursor:
            await cursor.execute(query, (event_id,))
            if cursor.rowcount == 0:
                logger.warning("Event already processed or not found", event_id=event_id)
    
    async def _increment_retry_count(self, event_id: int, error_message: str):
        """Увеличение счетчика попыток и проверка на DLQ."""
        query = """
        UPDATE outbox_events 
        SET retry_count = COALESCE(retry_count, 0) + 1,
            last_error = %s
        WHERE id = %s
        RETURNING retry_count
        """
        
        async with self.db_connection.cursor() as cursor:
            await cursor.execute(query, (error_message, event_id))
            result = await cursor.fetchone()
            
            if result and result[0] >= self.max_retries:
                # Перемещение в DLQ
                await self._move_to_dlq(event_id)
    
    async def _move_to_dlq(self, event_id: int):
        """Перемещение события в Dead Letter Queue."""
        # Получение события
        select_query = """
        SELECT event_type, payload, retry_count, last_error
        FROM outbox_events 
        WHERE id = %s
        """
        
        # Вставка в DLQ
        insert_query = """
        INSERT INTO outbox_events_dlq (original_id, event_type, payload, retry_count, last_error)
        VALUES (%s, %s, %s, %s, %s)
        """
        
        async with self.db_connection.cursor() as cursor:
            await cursor.execute(select_query, (event_id,))
            event_data = await cursor.fetchone()
            
            if event_data:
                await cursor.execute(insert_query, (
                    event_id,
                    event_data[0],  # event_type
                    event_data[1],  # payload
                    event_data[2],  # retry_count
                    event_data[3]   # last_error
                ))
                
                # Удаление из основной таблицы
                await cursor.execute("DELETE FROM outbox_events WHERE id = %s", (event_id,))
                
                logger.warning("Event moved to DLQ", 
                             event_id=event_id,
                             event_type=event_data[0],
                             retry_count=event_data[2])
                
                # Метрики
                outbox_dlq_total.labels(event_type=event_data[0]).inc()
                self.stats['dlq_moved'] += 1
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики outbox relay."""
        return {
            **self.stats,
            'redis_connected': self.redis_client is not None,
            'schema_registry_available': self.schema_registry is not None
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check для outbox relay."""
        try:
            # Проверка Redis
            redis_healthy = False
            if self.redis_client:
                await self.redis_client.ping()
                redis_healthy = True
            
            # Проверка БД
            db_healthy = False
            if self.db_connection:
                async with self.db_connection.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    db_healthy = True
            
            return {
                'status': 'healthy' if (redis_healthy and db_healthy) else 'unhealthy',
                'redis': 'connected' if redis_healthy else 'disconnected',
                'database': 'connected' if db_healthy else 'disconnected',
                'stats': await self.get_stats()
            }
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return {
                'status': 'unhealthy',
                'error': str(e),
                'stats': await self.get_stats()
            }
