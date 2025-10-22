"""Redis Streams Event Bus для Telegram Assistant.

Реализует event-driven архитектуру с использованием Redis Streams и outbox-паттерна.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Histogram, Gauge

logger = structlog.get_logger()

# Prometheus метрики
EVENTS_PUBLISHED = Counter("events_published_total", "Published events", ["event_type", "source"])
EVENTS_CONSUMED = Counter("events_consumed_total", "Consumed events", ["event_type", "consumer"])
EVENTS_PROCESSING_DURATION = Histogram("events_processing_duration_seconds", "Event processing duration", ["event_type", "consumer"])
EVENTS_FAILED = Counter("events_failed_total", "Failed events", ["event_type", "consumer", "error_type"])
OUTBOX_EVENTS_PENDING = Gauge("outbox_events_pending", "Pending outbox events")
OUTBOX_EVENTS_PROCESSED = Counter("outbox_events_processed_total", "Processed outbox events", ["status"])


class EventBus:
    """Redis Streams Event Bus с outbox-паттерном."""
    
    def __init__(self, redis_url: str, db_connection=None):
        self.redis_client = redis.from_url(redis_url)
        self.db_connection = db_connection
        self.running = False
        self.consumers = {}
    
    async def start(self):
        """Запуск event bus."""
        self.running = True
        logger.info("Event bus started")
        
        # Запуск outbox processor
        asyncio.create_task(self._outbox_processor())
        
        # Запуск consumer groups
        for consumer_name, handler in self.consumers.items():
            asyncio.create_task(self._consumer_loop(consumer_name, handler))
    
    async def stop(self):
        """Остановка event bus."""
        self.running = False
        await self.redis_client.close()
        logger.info("Event bus stopped")
    
    def register_consumer(self, consumer_name: str, handler):
        """Регистрация consumer'а для обработки событий."""
        self.consumers[consumer_name] = handler
        logger.info("Consumer registered", consumer=consumer_name)
    
    async def publish_event(self, event: Dict[str, Any], stream_key: str = None) -> str:
        """Публикация события через outbox-паттерн."""
        try:
            # Генерация event_id если не указан
            if "event_id" not in event:
                event["event_id"] = str(uuid.uuid4())
            
            # Установка occurred_at если не указано
            if "occurred_at" not in event:
                event["occurred_at"] = datetime.now(timezone.utc).isoformat()
            
            # Определение stream_key
            if not stream_key:
                tenant_id = event.get("tenant_id", "system")
                stream_key = f"events:{tenant_id}"
            
            # Сохранение в outbox
            outbox_id = await self._save_to_outbox(event, stream_key)
            
            logger.info("Event saved to outbox", 
                       event_id=event["event_id"], 
                       event_type=event["event_type"],
                       outbox_id=outbox_id)
            
            return event["event_id"]
            
        except Exception as e:
            logger.error("Failed to publish event", 
                        event_id=event.get("event_id"), 
                        error=str(e))
            raise
    
    async def _save_to_outbox(self, event: Dict[str, Any], stream_key: str) -> str:
        """Сохранение события в outbox таблицу."""
        if not self.db_connection:
            # Если нет БД подключения, публикуем напрямую в Redis
            return await self._publish_to_redis(event, stream_key)
        
        outbox_id = str(uuid.uuid4())
        
        # Сохранение в outbox_events таблицу
        async with self.db_connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO outbox_events 
                (id, event_id, event_type, tenant_id, stream_key, event_data, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    outbox_id,
                    event["event_id"],
                    event["event_type"],
                    event.get("tenant_id"),
                    stream_key,
                    json.dumps(event),
                    "pending",
                    datetime.now(timezone.utc)
                )
            )
            await self.db_connection.commit()
        
        OUTBOX_EVENTS_PENDING.inc()
        return outbox_id
    
    async def _publish_to_redis(self, event: Dict[str, Any], stream_key: str) -> str:
        """Прямая публикация в Redis Stream."""
        event_id = await self.redis_client.xadd(
            stream_key,
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "tenant_id": event.get("tenant_id", ""),
                "user_id": event.get("user_id", ""),
                "correlation_id": event.get("correlation_id", ""),
                "occurred_at": event["occurred_at"],
                "version": event.get("version", "1.0"),
                "source": event.get("source", ""),
                "payload": json.dumps(event.get("payload", {}))
            }
        )
        
        EVENTS_PUBLISHED.labels(
            event_type=event["event_type"],
            source=event.get("source", "unknown")
        ).inc()
        
        return event_id
    
    async def _outbox_processor(self):
        """Процессор outbox событий."""
        logger.info("Outbox processor started")
        
        while self.running:
            try:
                if not self.db_connection:
                    await asyncio.sleep(1)
                    continue
                
                # Получение pending событий из outbox
                async with self.db_connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT id, event_id, event_type, tenant_id, stream_key, event_data
                        FROM outbox_events 
                        WHERE status = 'pending' 
                        ORDER BY created_at ASC 
                        LIMIT 100
                        """
                    )
                    events = await cursor.fetchall()
                
                for event_row in events:
                    outbox_id, event_id, event_type, tenant_id, stream_key, event_data = event_row
                    
                    try:
                        # Публикация в Redis Stream
                        event = json.loads(event_data)
                        await self._publish_to_redis(event, stream_key)
                        
                        # Обновление статуса в outbox
                        async with self.db_connection.cursor() as cursor:
                            await cursor.execute(
                                "UPDATE outbox_events SET status = 'sent', sent_at = %s WHERE id = %s",
                                (datetime.now(timezone.utc), outbox_id)
                            )
                            await self.db_connection.commit()
                        
                        OUTBOX_EVENTS_PROCESSED.labels(status="sent").inc()
                        OUTBOX_EVENTS_PENDING.dec()
                        
                        logger.debug("Outbox event processed", 
                                   outbox_id=outbox_id, 
                                   event_id=event_id)
                        
                    except Exception as e:
                        # Обновление статуса на failed
                        async with self.db_connection.cursor() as cursor:
                            await cursor.execute(
                                """
                                UPDATE outbox_events 
                                SET status = 'failed', 
                                    retry_count = retry_count + 1,
                                    last_error = %s,
                                    next_retry_at = %s
                                WHERE id = %s
                                """,
                                (
                                    str(e),
                                    datetime.now(timezone.utc).replace(second=0, microsecond=0),
                                    outbox_id
                                )
                            )
                            await self.db_connection.commit()
                        
                        OUTBOX_EVENTS_PROCESSED.labels(status="failed").inc()
                        
                        logger.error("Failed to process outbox event", 
                                   outbox_id=outbox_id, 
                                   event_id=event_id,
                                   error=str(e))
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error("Outbox processor error", error=str(e))
                await asyncio.sleep(5)
    
    async def _consumer_loop(self, consumer_name: str, handler):
        """Цикл обработки событий для consumer'а."""
        logger.info("Consumer loop started", consumer=consumer_name)
        
        # Создание consumer group если не существует
        try:
            await self.redis_client.xgroup_create(
                "events:system", 
                consumer_name, 
                id="0", 
                mkstream=True
            )
        except redis.ResponseError:
            # Consumer group уже существует
            pass
        
        while self.running:
            try:
                # Чтение событий из stream
                messages = await self.redis_client.xreadgroup(
                    consumer_name,
                    f"{consumer_name}-worker",
                    {"events:system": ">"},
                    count=10,
                    block=1000
                )
                
                for stream, msgs in messages:
                    for msg_id, fields in msgs:
                        try:
                            # Парсинг события
                            event = {
                                "event_id": fields[b"event_id"].decode(),
                                "event_type": fields[b"event_type"].decode(),
                                "tenant_id": fields[b"tenant_id"].decode(),
                                "user_id": fields[b"user_id"].decode() if fields[b"user_id"] else None,
                                "correlation_id": fields[b"correlation_id"].decode(),
                                "occurred_at": fields[b"occurred_at"].decode(),
                                "version": fields[b"version"].decode(),
                                "source": fields[b"source"].decode(),
                                "payload": json.loads(fields[b"payload"].decode())
                            }
                            
                            # Обработка события
                            start_time = datetime.now()
                            await handler(event)
                            processing_time = (datetime.now() - start_time).total_seconds()
                            
                            # Подтверждение обработки
                            await self.redis_client.xack("events:system", consumer_name, msg_id)
                            
                            # Метрики
                            EVENTS_CONSUMED.labels(
                                event_type=event["event_type"],
                                consumer=consumer_name
                            ).inc()
                            
                            EVENTS_PROCESSING_DURATION.labels(
                                event_type=event["event_type"],
                                consumer=consumer_name
                            ).observe(processing_time)
                            
                            logger.debug("Event processed", 
                                       event_id=event["event_id"],
                                       event_type=event["event_type"],
                                       consumer=consumer_name,
                                       processing_time=processing_time)
                            
                        except Exception as e:
                            EVENTS_FAILED.labels(
                                event_type=fields[b"event_type"].decode(),
                                consumer=consumer_name,
                                error_type=type(e).__name__
                            ).inc()
                            
                            logger.error("Failed to process event", 
                                       msg_id=msg_id,
                                       consumer=consumer_name,
                                       error=str(e))
                
            except Exception as e:
                logger.error("Consumer loop error", 
                           consumer=consumer_name,
                           error=str(e))
                await asyncio.sleep(5)


class EventPublisher:
    """Публикатор событий."""
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
    
    async def publish_auth_login_started(self, tenant_id: str, correlation_id: str, 
                                       qr_session_id: str, telegram_user_id: str, 
                                       invite_code: str = None, ip_address: str = None,
                                       user_agent: str = None):
        """Публикация события начала авторизации."""
        event = {
            "event_type": "auth.login.started",
            "tenant_id": tenant_id,
            "user_id": None,
            "correlation_id": correlation_id,
            "version": "1.0",
            "source": "qr-auth-service",
            "payload": {
                "qr_session_id": qr_session_id,
                "telegram_user_id": telegram_user_id,
                "invite_code": invite_code,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "client_meta": {
                    "locale": "ru-RU",
                    "timezone": "Europe/Moscow"
                }
            }
        }
        
        return await self.event_bus.publish_event(event)
    
    async def publish_auth_login_authorized(self, tenant_id: str, user_id: str, 
                                          correlation_id: str, qr_session_id: str,
                                          session_id: str, telegram_user_id: str,
                                          invite_code: str = None):
        """Публикация события успешной авторизации."""
        event = {
            "event_type": "auth.login.authorized",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "correlation_id": correlation_id,
            "version": "1.0",
            "source": "qr-auth-service",
            "payload": {
                "qr_session_id": qr_session_id,
                "session_id": session_id,
                "telegram_user_id": telegram_user_id,
                "invite_code": invite_code,
                "user_data": {
                    "username": "user",  # TODO: получить из БД
                    "first_name": "User",
                    "last_name": "Name"
                }
            }
        }
        
        return await self.event_bus.publish_event(event)
    
    async def publish_channel_parsing_completed(self, tenant_id: str, user_id: str,
                                               channel_id: str, telegram_channel_id: str,
                                               posts_parsed: int, posts_indexed: int,
                                               parsing_duration_ms: int):
        """Публикация события завершения парсинга канала."""
        event = {
            "event_type": "channel.parsing.completed",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "correlation_id": str(uuid.uuid4()),
            "version": "1.0",
            "source": "telethon-ingest",
            "payload": {
                "channel_id": channel_id,
                "telegram_channel_id": telegram_channel_id,
                "posts_parsed": posts_parsed,
                "posts_indexed": posts_indexed,
                "parsing_duration_ms": parsing_duration_ms
            }
        }
        
        return await self.event_bus.publish_event(event)


# Глобальный экземпляр event bus
event_bus = None
event_publisher = None


async def init_event_bus(redis_url: str, db_connection=None):
    """Инициализация event bus."""
    global event_bus, event_publisher
    
    event_bus = EventBus(redis_url, db_connection)
    event_publisher = EventPublisher(event_bus)
    
    await event_bus.start()
    logger.info("Event bus initialized")


async def get_event_publisher() -> EventPublisher:
    """Получение event publisher."""
    if not event_publisher:
        raise RuntimeError("Event bus not initialized")
    return event_publisher
