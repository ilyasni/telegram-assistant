"""
Event Bus для надёжной доставки событий через Redis Streams
Поддерживает consumer groups, DLQ, retries и идемпотентность
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Dict, List, Optional, Any, AsyncGenerator
from datetime import datetime, date, timezone
from dataclasses import dataclass
import os

import redis.asyncio as redis
from pydantic import BaseModel, Field
from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# Context7: Метрики для мониторинга пайплайна
stream_messages_total = Counter(
    'stream_messages_total',
    'Total messages processed by stream',
    ['stream', 'phase', 'status']
)

redis_xack_total = Counter(
    'redis_xack_total',
    'Total XACK operations',
    ['stream']
)

consumer_loop_iterations_total = Counter(
    'consumer_loop_iterations_total',
    'Total consumer loop iterations',
    ['task']
)

consumer_handle_latency_seconds = Histogram(
    'consumer_handle_latency_seconds',
    'Message handling latency',
    ['task']
)

redis_claim_batch_size = Histogram(
    'redis_claim_batch_size',
    'Batch size for XAUTOCLAIM operations',
    ['stream']
)

stream_pending_size = Gauge(
    'stream_pending_size',
    'Number of pending messages in stream',
    ['stream']
)

# Context7: Метрики lag и pending из XINFO GROUPS (для всех стримов)
stream_consumer_lag = Gauge(
    'stream_consumer_lag',
    'Consumer group lag (messages not yet delivered to any consumer)',
    ['stream', 'group']
)

stream_consumer_pending = Gauge(
    'stream_consumer_pending',
    'Number of pending messages in PEL (Pending Entry List)',
    ['stream', 'group']
)

posts_in_queue_total = Gauge(
    'posts_in_queue_total',
    'Current posts in queue',
    ['queue', 'status']
)

# ============================================================================
# КОНФИГУРАЦИЯ СТРИМОВ
# ============================================================================

# [C7-ID: EVENTS-DLQ-ALIAS-001] DLQ алиасы добавлены в STREAMS
STREAMS = {
    'posts.parsed': 'stream:posts:parsed',
    'posts.tagged': 'stream:posts:tagged', 
    'posts.enriched': 'stream:posts:enriched',
    'posts.indexed': 'stream:posts:indexed',
    'posts.crawl': 'stream:posts:crawl',  # Новый stream для crawling задач
    'posts.deleted': 'stream:posts:deleted',
    # Context7: Vision и Retagging стримы
    'posts.vision.uploaded': 'stream:posts:vision:uploaded',
    'posts.vision.analyzed': 'stream:posts:vision:analyzed',
    # Context7: Album стримы (Phase 2)
    'albums.parsed': 'stream:albums:parsed',
    'album.assembled': 'stream:album:assembled',
    # Context7: Digest generation pipeline
    'digests.generate': 'stream:digests:generate',
    # DLQ алиасы
    'posts.parsed.dlq': 'stream:posts:parsed:dlq',
    'posts.tagged.dlq': 'stream:posts:tagged:dlq',
    'posts.enriched.dlq': 'stream:posts:enriched:dlq',
    'posts.indexed.dlq': 'stream:posts:indexed:dlq',
    'posts.crawl.dlq': 'stream:posts:crawl:dlq',
    'posts.deleted.dlq': 'stream:posts:deleted:dlq',
    'posts.vision.analyzed.dlq': 'stream:posts:vision:analyzed:dlq',
    'albums.parsed.dlq': 'stream:albums:parsed:dlq',
    'album.assembled.dlq': 'stream:album:assembled:dlq',
    'digests.generate.dlq': 'stream:digests:generate:dlq',
}

# DLQ стримы (legacy, для обратной совместимости)
DLQ_STREAMS = {
    'posts.parsed': 'stream:posts:parsed:dlq',
    'posts.tagged': 'stream:posts:tagged:dlq',
    'posts.enriched': 'stream:posts:enriched:dlq', 
    'posts.indexed': 'stream:posts:indexed:dlq',
    'posts.deleted': 'stream:posts:deleted:dlq',
    # Context7: DLQ для Vision и Retagging
    'posts.vision.analyzed': 'stream:posts:vision:analyzed:dlq',
    # Context7: DLQ для альбомов (Phase 2)
    'albums.parsed': 'stream:albums:parsed:dlq',
    'album.assembled': 'stream:album:assembled:dlq',
    'digests.generate': 'stream:digests:generate:dlq',
}

# ============================================================================
# СХЕМЫ СОБЫТИЙ (Pydantic)
# ============================================================================

class BaseEvent(BaseModel):
    """Базовый класс для всех событий."""
    schema_version: str = Field(default="v1", description="Версия схемы события")
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(..., description="Ключ идемпотентности")

class PostParsedEvent(BaseEvent):
    """Событие: пост распарсен."""
    user_id: str
    channel_id: str
    post_id: str
    tenant_id: str
    text: str
    urls: List[str] = Field(default_factory=list)
    posted_at: datetime
    content_hash: Optional[str] = None
    link_count: int = 0

class PostTaggedEvent(BaseEvent):
    """Событие: пост протегирован."""
    post_id: str
    tags: List[Dict[str, Any]] = Field(default_factory=list)
    provider: str  # gigachat | openrouter
    model: Optional[str] = None
    latency_ms: int
    token_count: Optional[int] = None

class PostEnrichedEvent(BaseEvent):
    """Событие: пост обогащён."""
    post_id: str
    enrichment_data: Dict[str, Any] = Field(default_factory=dict)
    source_urls: List[str] = Field(default_factory=list)
    word_count: Optional[int] = None
    skipped: bool = False
    skip_reason: Optional[str] = None

class PostIndexedEvent(BaseEvent):
    """Событие: пост проиндексирован."""
    post_id: str
    vector_id: str
    embedding_provider: str
    embedding_dim: int
    qdrant_collection: str
    neo4j_nodes_created: int = 0

class PostDeletedEvent(BaseEvent):
    """Событие: пост удалён."""
    post_id: str
    tenant_id: str
    channel_id: str
    reason: str  # ttl | user | admin
    qdrant_cleaned: bool = False
    neo4j_cleaned: bool = False


class DigestGenerateEvent(BaseEvent):
    """Событие: запрос генерации дайджеста."""
    user_id: str
    tenant_id: str
    digest_date: date
    trigger: str = "scheduler"
    history_id: Optional[str] = None
    requested_by: Optional[str] = None

# ============================================================================
# REDIS STREAMS CLIENT
# ============================================================================

class RedisStreamsClient:
    """Клиент для работы с Redis Streams."""
    
    def __init__(self, redis_url: str = "redis://redis:6379"):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
        
    async def connect(self):
        """Подключение к Redis."""
        if not self.client:
            self.client = redis.from_url(self.redis_url, decode_responses=True)
            # redis.from_url() уже создает подключенный клиент
            # Проверяем подключение через ping
            try:
                await self.client.ping()
                logger.info("Connected to Redis")
            except Exception as e:
                logger.error("Failed to ping Redis", error=str(e))
                raise
    
    async def disconnect(self):
        """Отключение от Redis."""
        if self.client:
            await self.client.close()
            self.client = None
            logger.info("Disconnected from Redis")

# ============================================================================
# EVENT PUBLISHER
# ============================================================================

class EventPublisher:
    """Публикатор событий в Redis Streams."""
    
    def __init__(self, client: RedisStreamsClient):
        self.client = client
    
    # [C7-ID: EVENTBUS-PUB-UNIFY-001]
    @staticmethod
    def _to_payload_dict(event: Any) -> Any:
        """Унифицировать событие к dict/list для сериализации."""
        # pydantic v2
        if hasattr(event, "model_dump"):
            try:
                return event.model_dump()
            except Exception:
                pass
        # pydantic v1
        if hasattr(event, "dict"):
            try:
                return event.dict()
            except Exception:
                pass
        # уже dict/list
        if isinstance(event, (dict, list)):
            return event
        raise TypeError(f"Unsupported event type: {type(event)}")

    @staticmethod
    def _to_json_bytes(obj: Any) -> bytes:
        import uuid as _uuid
        import datetime as _dt
        def norm(o):
            if o is None:
                return None
            if isinstance(o, (str, int, float, bool)):
                return o
            if isinstance(o, _uuid.UUID):
                return str(o)
            if isinstance(o, (_dt.datetime, _dt.date)):
                return o.isoformat()
            if isinstance(o, dict):
                out = {}
                for k, v in o.items():
                    nv = norm(v)
                    if nv is not None:
                        out[k] = nv
                return out
            if isinstance(o, (list, tuple, set)):
                out = [norm(v) for v in o]
                return [x for x in out if x is not None]
            return str(o)
        cleaned = norm(obj)
        if not cleaned:
            raise ValueError("normalized payload is empty")
        return json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
    
    async def publish_event(self, stream_name: str, event: Any) -> str:
        """
        Публикация события в стрим.
        
        Args:
            stream_name: Имя стрима (например, 'posts.parsed')
            event: Событие для публикации
            
        Returns:
            Message ID в стриме
        """
        # [C7-ID: EVENTBUS-PUB-TRACE-001] Логирование в EventPublisher
        logger.info("event_pub_enter", extra={"stream": stream_name, "type": str(type(event))})
        
        # Проверка, что stream_name - это короткое имя, а не полное
        if stream_name.startswith("stream:"):
            logger.error(f"Invalid stream_name: {stream_name}. Expected short name like 'posts.tagged', got full name.")
            raise ValueError(f"Invalid stream_name: {stream_name}")
        
        if isinstance(event, dict):
            # покажи, что именно мы кладём в Redis
            sample = {k: (f"bytes[{len(v)}]" if isinstance(v, (bytes, bytearray)) else str(type(v)))
                      for k, v in list(event.items())[:5]}
            logger.info("event_pub_payload_shape", extra={"stream": stream_name, "shape": sample})
        
        stream_key = STREAMS[stream_name]
        
        # Унифицированная публикация: событие → dict/list → json bytes → {"data": bytes}
        payload = self._to_payload_dict(event)
        data = self._to_json_bytes(payload)
        event_data = {"data": data}
        logger.info("event_pub_xadd", extra={"stream": stream_name, "has_data": True, "data_type": "bytes"})
        
        # Context7: [C7-ID: debug-redis-client-001] - Отладка типа self.client
        logger.info("event_pub_debug_client", extra={
            "client_type": str(type(self.client)),
            "client_value": str(self.client)[:100],
            "has_client_attr": hasattr(self.client, 'client') if self.client else False
        })
        
        message_id = await self.client.client.xadd(
            stream_key,
            event_data,
            maxlen=10000
        )
        logger.info(f"Published event {stream_name} with ID {message_id}")
        return message_id

    async def publish_json(self, stream_name: str, payload: Any) -> str:
        stream_key = STREAMS[stream_name]
        data = self._to_json_bytes(payload)
        maxlen = int(os.getenv("REDIS_STREAM_MAXLEN", "100000"))
        return await self.client.client.xadd(stream_key, {"data": data}, maxlen=maxlen)

    async def publish_bytes(self, stream_name: str, data: bytes) -> str:
        stream_key = STREAMS[stream_name]
        maxlen = int(os.getenv("REDIS_STREAM_MAXLEN", "100000"))
        return await self.client.client.xadd(stream_key, {"data": data}, maxlen=maxlen)
    
    async def publish_batch(self, events: List[tuple[str, BaseEvent]]) -> List[str]:
        """
        Батчевая публикация событий.
        
        Args:
            events: Список кортежей (stream_name, event)
            
        Returns:
            Список Message ID
        """
        message_ids = []
        
        # Группировка по стримам для оптимизации
        streams_data = {}
        for stream_name, event in events:
            if stream_name not in streams_data:
                streams_data[stream_name] = []
            
            try:
                model_dump = event.model_dump(mode='json')
            except Exception:
                model_dump = event.dict()
                if isinstance(model_dump.get('occurred_at'), datetime):
                    model_dump['occurred_at'] = model_dump['occurred_at'].isoformat()
                if isinstance(model_dump.get('posted_at'), datetime):
                    model_dump['posted_at'] = model_dump['posted_at'].isoformat()

            raw = {
                'schema_version': model_dump.get('schema_version', 'v1'),
                'trace_id': model_dump.get('trace_id', str(uuid.uuid4())),
                'occurred_at': model_dump.get('occurred_at', datetime.now(timezone.utc).isoformat()),
                'idempotency_key': model_dump['idempotency_key'],
                **{k: v for k, v in model_dump.items() if k not in ['schema_version', 'trace_id', 'occurred_at', 'idempotency_key']}
            }
            # Сериализация сложных типов
            serialized: Dict[str, Any] = {}
            for k, v in raw.items():
                if isinstance(v, (list, dict)):
                    serialized[k] = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, bool):
                    serialized[k] = 'true' if v else 'false'
                else:
                    serialized[k] = v
            streams_data[stream_name].append(serialized)
        
        # Публикация по стримам
        for stream_name, events_data in streams_data.items():
            stream_key = STREAMS[stream_name]
            for event_data in events_data:
                message_id = await self.client.client.xadd(
                    stream_key,
                    event_data,
                    maxlen=10000
                )
                message_ids.append(message_id)
        
        logger.info(f"Published {len(message_ids)} events in batch")
        return message_ids

    async def to_dlq(
        self, 
        base_event_name: str, 
        payload: dict, 
        reason: str, 
        details: str = "", 
        retry_count: int = 0
    ) -> str:
        """
        [C7-ID: EVENTBUS-DLQ-001] Публикация в DLQ stream с метаданными и legacy-fallback.
        
        Args:
            base_event_name: Базовое имя события (например, 'posts.enriched' или 'posts.enriched.dlq')
            payload: Данные события
            reason: Код причины из DLQReason enum
            details: Детали ошибки (усекаются по DLQ_MAX_REASON_DETAILS)
            retry_count: Количество попыток обработки
            
        Returns:
            Message ID в DLQ stream
        """
        from datetime import datetime, timezone
        import os
        
        # Защита от случайной передачи .dlq суффикса
        base = base_event_name.removesuffix(".dlq")
        dlq_event = f"{base}.dlq"
        
        # Обогащение payload метаданными DLQ с корреляцией
        dlq_payload = {
            **payload,
            "dlq_reason": reason,
            "dlq_reason_details": (details or "")[:int(os.getenv("DLQ_MAX_REASON_DETAILS", "500"))],
            "dlq_timestamp": datetime.now(timezone.utc).isoformat(),
            "retry_count": int(retry_count),
            "origin_stream": STREAMS.get(base),
            "origin_msg_id": payload.get("_msg_id"),  # Корреляция с исходным сообщением
            "correlation_id": payload.get("correlation_id"),  # Сквозная трассировка
        }
        
        # Определение DLQ stream: основной путь → STREAMS, fallback → legacy DLQ_STREAMS
        stream_key = STREAMS.get(dlq_event) or DLQ_STREAMS.get(base)
        
        if not stream_key:
            raise ValueError(f"No DLQ stream configured for {base_event_name}")
        
        # Единый путь публикации через publish_json (сохраняет формат и middlewares)
        msg_id = await self.publish_json(dlq_event, dlq_payload)
        
        logger.warning(
            "event_published_to_dlq",
            extra={
                "dlq_event": dlq_event,
                "msg_id": msg_id,
                "reason": reason,
                "retry_count": retry_count
            }
        )
        return msg_id

# ============================================================================
# EVENT CONSUMER
# ============================================================================

@dataclass
class ConsumerConfig:
    """Конфигурация consumer."""
    group_name: str
    consumer_name: str
    batch_size: int = 10
    block_time: int = 1000  # мс
    max_retries: int = 3
    retry_delay: int = 5  # секунд
    idle_timeout: int = 300  # секунд

class EventConsumer:
    """Consumer событий из Redis Streams с поддержкой групп и DLQ."""
    
    def __init__(self, client: RedisStreamsClient, config: ConsumerConfig):
        self.client = client
        self.config = config
        self.running = False
        self.last_activity = time.time()
    
    async def _ensure_consumer_group(self, stream_name: str):
        """Создание consumer group и DLQ (идемпотентно)."""
        # Context7: Валидация stream_name и получение ключей
        if stream_name not in STREAMS:
            error_msg = f"Stream name '{stream_name}' not found in STREAMS. Available: {list(STREAMS.keys())}"
            logger.error(error_msg)
            raise KeyError(error_msg)
        
        stream_key = STREAMS[stream_name]
        dlq_key = DLQ_STREAMS.get(stream_name)  # DLQ может отсутствовать, это OK
        
        # Создание consumer group (идемпотентно)
        try:
            await self.client.client.xgroup_create(
                stream_key, 
                self.config.group_name, 
                id='0', 
                mkstream=True
            )
            logger.info(f"Created consumer group {self.config.group_name} for {stream_name} (key: {stream_key})")
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"Consumer group {self.config.group_name} already exists for {stream_name}")
            else:
                logger.error(f"Failed to create consumer group for {stream_name}: {e}")
                raise
        
        # Создание DLQ группы (опционально, если есть DLQ конфигурация)
        if dlq_key:
            try:
                await self.client.client.xgroup_create(
                    dlq_key,
                    f"{self.config.group_name}-dlq",
                    id='0',
                    mkstream=True
                )
                logger.debug(f"Created DLQ consumer group for {stream_name} (key: {dlq_key})")
            except redis.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    logger.debug(f"DLQ consumer group already exists for {stream_name}")
                else:
                    logger.warning(f"Failed to create DLQ consumer group for {stream_name}: {e}")
                    # DLQ не критично, продолжаем работу

    async def consume_batch(self, stream_name: str, handler_func) -> int:
        """
        Обработать один батч сообщений. Возвращает количество обработанных.
        
        Args:
            stream_name: Имя стрима для потребления
            handler_func: Функция-обработчик события
            
        Returns:
            int: Количество обработанных сообщений
        """
        # Context7: Валидация stream_name перед использованием
        if stream_name not in STREAMS:
            error_msg = f"Stream name '{stream_name}' not found in STREAMS. Available: {list(STREAMS.keys())}"
            logger.error(error_msg)
            raise KeyError(error_msg)
        
        stream_key = STREAMS[stream_name]
        dlq_key = DLQ_STREAMS.get(stream_name)  # DLQ может отсутствовать
        
        try:
            processed = 0
            
            # Context7: Фаза 1: Обработка pending сообщений (reclaim зависших)
            pending_processed = await self.claim_pending(stream_name, handler_func)
            processed += pending_processed
            
            # Context7: Фаза 2: Чтение новых сообщений
            new_processed = await self.read_new(stream_name, handler_func)
            processed += new_processed
            
            if processed > 0:
                self.last_activity = time.time()
            
            return processed
                
        except Exception as e:
            logger.error(f"Error in consumer {self.config.consumer_name}: {e}")
            raise

    async def claim_pending(self, stream_name: str, handler_func) -> int:
        """
        Context7: Обработка pending сообщений с XAUTOCLAIM.
        
        Args:
            stream_name: Имя стрима для потребления
            handler_func: Функция-обработчик события
            
        Returns:
            int: Количество обработанных pending сообщений
        """
        # Context7: Валидация stream_name
        if stream_name not in STREAMS:
            raise KeyError(f"Stream name '{stream_name}' not found in STREAMS")
        
        stream_key = STREAMS[stream_name]
        dlq_key = DLQ_STREAMS.get(stream_name)  # DLQ может отсутствовать
        
        try:
            # XAUTOCLAIM для получения зависших сообщений
            result = await self.client.client.xautoclaim(
                stream_key,
                self.config.group_name,
                self.config.consumer_name,
                min_idle_time=60000,  # 60 секунд
                count=self.config.batch_size
            )
            
            if result and len(result) > 1:
                messages = result[1]  # Список сообщений
                if messages:
                    logger.debug(f"Claimed {len(messages)} pending messages from {stream_name}")
                    # Context7: Метрики для pending сообщений
                    stream_messages_total.labels(stream=stream_name, phase='pending', status='ok').inc(len(messages))
                    redis_claim_batch_size.labels(stream=stream_name).observe(len(messages))
                    posts_in_queue_total.labels(queue=stream_name, status='pending').set(len(messages))
                    await self._process_messages(stream_key, dlq_key, [(stream_key, messages)], handler_func)
                    return len(messages)
            
            return 0
            
        except Exception as e:
            logger.error(f"Error claiming pending messages from {stream_name}: {e}")
            return 0

    async def read_new(self, stream_name: str, handler_func) -> int:
        """
        Context7: Чтение новых сообщений с XREADGROUP.
        
        Args:
            stream_name: Имя стрима для потребления
            handler_func: Функция-обработчик события
            
        Returns:
            int: Количество обработанных новых сообщений
        """
        # Context7: Валидация stream_name
        if stream_name not in STREAMS:
            raise KeyError(f"Stream name '{stream_name}' not found in STREAMS")
        
        stream_key = STREAMS[stream_name]
        dlq_key = DLQ_STREAMS.get(stream_name)  # DLQ может отсутствовать
        
        try:
            # XREADGROUP для новых сообщений
            messages = await self.client.client.xreadgroup(
                self.config.group_name,
                self.config.consumer_name,
                {stream_key: '>'},
                count=self.config.batch_size,
                block=self.config.block_time
            )
            
            if messages:
                # Context7: Метрики для новых сообщений
                message_count = len(messages[0][1]) if messages else 0
                stream_messages_total.labels(stream=stream_name, phase='new', status='ok').inc(message_count)
                redis_claim_batch_size.labels(stream=stream_name).observe(message_count)
                posts_in_queue_total.labels(queue=stream_name, status='new').set(message_count)
                await self._process_messages(stream_key, dlq_key, messages, handler_func)
                return message_count
            
            return 0
            
        except Exception as e:
            logger.error(f"Error reading new messages from {stream_name}: {e}")
            return 0

    async def _update_queue_metrics(self, stream_name: str):
        """
        Context7: Обновление метрик размера очереди, lag и pending из XINFO GROUPS.
        Вызывается периодически для актуальных метрик.
        """
        try:
            stream_key = STREAMS.get(stream_name)
            if not stream_key:
                return
            
            # Получаем реальный размер стрима
            stream_length = await self.client.client.xlen(stream_key)
            
            # Обновляем метрику posts_in_queue_total
            posts_in_queue_total.labels(queue=stream_name, status='total').set(stream_length)
            
            # Context7: Обновляем метрики lag и pending из XINFO GROUPS
            try:
                groups_info = await self.client.client.xinfo_groups(stream_key)
                for group_info in groups_info:
                    group_name_bytes = group_info.get(b'name', b'')
                    if group_name_bytes:
                        group_name = group_name_bytes.decode() if isinstance(group_name_bytes, bytes) else str(group_name_bytes)
                        
                        # Context7: Lag - количество сообщений, не доставленных ни одному consumer
                        lag_bytes = group_info.get(b'lag', 0)
                        lag = lag_bytes if isinstance(lag_bytes, int) else (int(lag_bytes.decode()) if isinstance(lag_bytes, bytes) else 0)
                        stream_consumer_lag.labels(stream=stream_name, group=group_name).set(lag)
                        
                        # Context7: Pending - количество сообщений в PEL
                        pending_bytes = group_info.get(b'pending', 0)
                        pending = pending_bytes if isinstance(pending_bytes, int) else (int(pending_bytes.decode()) if isinstance(pending_bytes, bytes) else 0)
                        stream_consumer_pending.labels(stream=stream_name, group=group_name).set(pending)
                        
                        # Совместимость: также обновляем старую метрику stream_pending_size
                        stream_pending_size.labels(stream=stream_name).set(pending)
                        posts_in_queue_total.labels(queue=stream_name, status='pending').set(pending)
            except Exception as e:
                logger.debug(f"Could not get groups info for {stream_name}: {e}")
                
                # Fallback: получаем pending через XPENDING для обратной совместимости
                try:
                    pending_info = await self.client.client.xpending_range(
                        stream_key,
                        self.config.group_name,
                        min='-',
                        max='+',
                        count=1000
                    )
                    pending_count = len(pending_info) if pending_info else 0
                    stream_pending_size.labels(stream=stream_name).set(pending_count)
                    posts_in_queue_total.labels(queue=stream_name, status='pending').set(pending_count)
                except Exception as e2:
                    logger.debug(f"Could not get pending info for {stream_name}: {e2}")
                
        except Exception as e:
            logger.debug(f"Error updating queue metrics for {stream_name}: {e}")
    
    async def consume_forever(self, stream_name: str, handler_func):
        """
        Context7: Бесконечный цикл потребления с правильным паттерном pending → новые.
        
        Args:
            stream_name: Имя стрима для потребления
            handler_func: Функция-обработчик события
        """
        # Context7: Валидация stream_name перед началом работы
        if stream_name not in STREAMS:
            error_msg = f"Stream name '{stream_name}' not found in STREAMS. Available: {list(STREAMS.keys())}"
            logger.error(error_msg)
            raise KeyError(error_msg)
        
        self.running = True
        
        # Context7: Создание consumer group с обработкой ошибок
        try:
            await self._ensure_consumer_group(stream_name)
            logger.info(
                f"Started consuming {stream_name} with group {self.config.group_name}",
                extra={
                    "stream_name": stream_name,
                    "stream_key": STREAMS[stream_name],
                    "group_name": self.config.group_name
                }
            )
        except KeyError as ke:
            logger.error(
                "Stream name not found in _ensure_consumer_group",
                extra={
                    "stream_name": stream_name,
                    "error": str(ke)
                },
                exc_info=True
            )
            raise
        except Exception as e:
            logger.error(
                "Failed to ensure consumer group in consume_forever",
                extra={
                    "stream_name": stream_name,
                    "error": str(e),
                    "error_type": type(e).__name__
                },
                exc_info=True
            )
            raise
        
        # Счётчик для периодического обновления метрик
        iteration_count = 0
        
        # Context7: Периодическая обработка подвисших сообщений (PEL) через XAUTOCLAIM
        pel_reclaim_interval = int(os.getenv("PEL_RECLAIM_INTERVAL", "30"))  # XAUTOCLAIM каждые N секунд
        pel_min_idle_ms = int(os.getenv("PEL_MIN_IDLE_MS", "60000"))  # Минимальное время простоя для XAUTOCLAIM (60 сек)
        last_pel_reclaim_time = time.time()
        
        while self.running:
            try:
                processed = 0
                
                # Context7: Периодическая обработка подвисших сообщений (PEL) через XAUTOCLAIM
                # Выполняем независимо от основного цикла для гарантированной обработки зависших сообщений
                current_time = time.time()
                if current_time - last_pel_reclaim_time >= pel_reclaim_interval:
                    # Context7: Дополнительный XAUTOCLAIM для обработки зависших сообщений
                    # Это гарантирует, что даже если в основном цикле нет pending, мы всё равно проверяем
                    try:
                        stream_key = STREAMS[stream_name]
                        result = await self.client.client.xautoclaim(
                            stream_key,
                            self.config.group_name,
                            self.config.consumer_name,
                            min_idle_time=pel_min_idle_ms,
                            start_id="0-0",
                            count=self.config.batch_size,
                            justid=False
                        )
                        
                        if result and len(result) > 1:
                            messages = result[1] if result[1] else []
                            if messages:
                                logger.debug(f"Periodic XAUTOCLAIM claimed {len(messages)} pending messages from {stream_name}")
                                dlq_key = DLQ_STREAMS.get(stream_name)
                                await self._process_messages(stream_key, dlq_key, [(stream_key, messages)], handler_func)
                                processed += len(messages)
                    except Exception as e:
                        logger.debug(f"Periodic XAUTOCLAIM failed for {stream_name}: {e}")
                    
                    last_pel_reclaim_time = current_time
                
                # Context7: Фаза 1: pending (reclaim зависших) - основной цикл
                processed += await self.claim_pending(stream_name, handler_func)
                
                # Context7: Фаза 2: новые (>)
                processed += await self.read_new(stream_name, handler_func)
                
                # Context7: Метрики для итераций цикла
                consumer_loop_iterations_total.labels(task=self.config.consumer_name).inc()
                
                # Обновляем метрики очереди каждые 10 итераций (примерно каждые 2 секунды)
                iteration_count += 1
                if iteration_count >= 10:
                    await self._update_queue_metrics(stream_name)
                    iteration_count = 0
                
                if processed == 0:
                    await asyncio.sleep(0.2)  # Короткий backoff, чтобы не жечь CPU
                    
            except asyncio.CancelledError:
                logger.info(f"Consumer {self.config.consumer_name} cancelled")
                break
            except Exception as e:
                logger.error(f"Error in consumer {self.config.consumer_name}: {e}")
                await asyncio.sleep(self.config.retry_delay)

    async def start_consuming(self, stream_name: str, handler_func):
        """
        Запуск потребления событий (обратная совместимость).
        
        Args:
            stream_name: Имя стрима для потребления
            handler_func: Функция-обработчик события
        """
        await self.consume_forever(stream_name, handler_func)
    
    async def _process_messages(self, stream_key: str, dlq_key: str, messages: List, handler_func):
        """Обработка батча сообщений."""
        for stream, stream_messages in messages:
            try:
                print("dispatch_enter", stream, "msg_count", len(stream_messages), flush=True)
            except Exception:
                pass
            for message_id, fields in stream_messages:
                        try:
                            # Парсинг события
                            event_data = self._parse_event_data(fields)
                            
                            # [C7:DISPATCH-SMOKE-001] — дым на уровне диспетчера
                            try:
                                if os.getenv("DISPATCH_SMOKE", "false").lower() == "true" and stream == STREAMS.get("posts.enriched"):
                                    from event_bus import EventPublisher
                                    publisher = EventPublisher(self.client)
                                    mid = await publisher.publish_event("posts.indexed", {
                                        "post_id": "dispatch-smoke",
                                        "indexed_at": datetime.now(timezone.utc).isoformat(),
                                        "note": "dispatch_before_routing"
                                    })
                                    print("dispatch_smoke_published", mid, stream, flush=True)
                            except Exception as _e:
                                print("dispatch_smoke_failed", str(_e), flush=True)

                            try:
                                print("dispatch_call", stream, getattr(handler_func, "__name__", str(handler_func)), flush=True)
                            except Exception:
                                pass

                            # [GUARD-ENTRY] Диагностика входа в хендлер
                            logger.info("dispatch_enter", extra={"stream_key": stream_key, "msg_id": message_id})
                            
                            # Обработка события
                            await handler_func(event_data)
                            logger.info("dispatch_ok", extra={"stream_key": stream_key, "msg_id": message_id})
                            
                            # Подтверждение обработки
                            await self.client.client.xack(
                                stream_key,
                                self.config.group_name,
                                message_id
                            )
                            
                            # Context7: Метрики для XACK
                            redis_xack_total.labels(stream=stream_key).inc()
                            
                            logger.debug(f"Processed message {message_id}")
                            
                        except Exception as e:
                            import traceback as _tb
                            logger.error(f"dispatch_fail stream={stream_key} msg_id={message_id} err={e}")
                            print("dispatch_traceback:\n" + _tb.format_exc(), flush=True)
                            await self._handle_failed_message(stream_key, dlq_key, message_id, fields, str(e))
    
    async def _handle_failed_message(self, stream_key: str, dlq_key: str, message_id: str, fields: Dict, error: str):
        """Обработка неудачных сообщений (retry → DLQ)."""
        # Получить информацию о сообщении
        message_info = await self.client.client.xpending_range(
            stream_key,
            self.config.group_name,
            min=message_id,
            max=message_id,
            count=1
        )
        
        if message_info:
            retry_count = message_info[0].get('times_delivered', 0)
            
            if retry_count < self.config.max_retries:
                # Повторная попытка через некоторое время
                logger.warning(f"Retrying message {message_id} (attempt {retry_count + 1})")
                await asyncio.sleep(self.config.retry_delay)
            else:
                # Отправка в DLQ
                logger.error(f"Moving message {message_id} to DLQ after {retry_count} retries")
                await self.client.client.xadd(
                    dlq_key,
                    {
                        'original_stream': stream_key,
                        'original_message_id': message_id,
                        'error': error,
                        'retry_count': retry_count,
                        'failed_at': datetime.now(timezone.utc).isoformat(),
                        **fields
                    }
                )
                
                # Подтверждение для удаления из основного стрима
                await self.client.client.xack(stream_key, self.config.group_name, message_id)
    
    async def _reclaim_pending_messages(self, stream_key: str):
        """Пере-claim зависших сообщений."""
        # Получить зависшие сообщения старше 5 минут
        pending = await self.client.client.xpending_range(
            stream_key,
            self.config.group_name,
            min='-',
            max='+',
            count=100,
            idle=5000  # 5 секунд в мс (ускоренный ребаланс для dev/worker)
        )
        
        if pending:
            logger.info(f"Reclaiming {len(pending)} stuck messages")
            for message in pending:
                await self.client.client.xclaim(
                    stream_key,
                    self.config.group_name,
                    self.config.consumer_name,
                    min_idle_time=300000,
                    message_ids=[message['message_id']]
                )
    
    def _parse_event_data(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Парсинг данных события из Redis Streams.
        Нормализация enriched: {"data": bytes(JSON)} → payload: dict.
        """
        # [C7-ID: EVENTBUS-NORM-ENRICHED] Унификация payload
        # 1) Попытка извлечь из поля data/payload «сырое» тело
        raw = fields.get('data') or fields.get('payload')
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode('utf-8', 'replace')
            except Exception:
                pass
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                # оставим строкой; ниже сформируем event_data
                pass
        
        event_data: Dict[str, Any] = {}
        # Если raw уже dict — это наш payload
        if isinstance(raw, dict):
            event_data['payload'] = raw
            # Остальные поля (заголовки) сохраним для отладки
            event_data['headers'] = {k: v for k, v in fields.items() if k not in ('data', 'payload')}
            return event_data
        
        # Иначе — старый формат: разворачиваем поля как раньше
        for key, value in fields.items():
            if key in ['tags', 'enrichment_data', 'source_urls', 'urls']:
                try:
                    event_data[key] = json.loads(value)
                except Exception:
                    event_data[key] = value
            elif key in ['occurred_at', 'posted_at']:
                try:
                    event_data[key] = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                except Exception:
                    event_data[key] = value
            else:
                event_data[key] = value
        return event_data
    
    async def stop(self):
        """Остановка consumer."""
        self.running = False
        logger.info(f"Stopping consumer {self.config.consumer_name}")

# ============================================================================
# FACTORY FUNCTIONS
# ============================================================================

async def create_publisher(redis_url: str = "redis://redis:6379") -> EventPublisher:
    """Создание publisher для событий."""
    client = RedisStreamsClient(redis_url)
    await client.connect()
    return EventPublisher(client)

async def create_consumer(
    stream_name: str,
    group_name: str,
    consumer_name: str,
    redis_url: str = "redis://redis:6379"
) -> EventConsumer:
    """Создание consumer для событий."""
    client = RedisStreamsClient(redis_url)
    await client.connect()
    
    config = ConsumerConfig(
        group_name=group_name,
        consumer_name=consumer_name
    )
    
    return EventConsumer(client, config)

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

async def get_stream_info(stream_name: str, redis_url: str = "redis://redis:6379") -> Dict[str, Any]:
    """Получение информации о стриме."""
    client = RedisStreamsClient(redis_url)
    await client.connect()
    
    stream_key = STREAMS[stream_name]
    info = await client.client.xinfo_stream(stream_key)
    
    await client.disconnect()
    return info

async def get_consumer_group_info(stream_name: str, group_name: str, redis_url: str = "redis://redis:6379") -> Dict[str, Any]:
    """Получение информации о consumer group."""
    client = RedisStreamsClient(redis_url)
    await client.connect()
    
    stream_key = STREAMS[stream_name]
    groups = await client.client.xinfo_groups(stream_key)
    
    group_info = next((g for g in groups if g['name'] == group_name), None)
    
    await client.disconnect()
    return group_info

# ============================================================================
# EXAMPLE USAGE
# ============================================================================

async def example_publisher():
    """Пример использования publisher."""
    publisher = await create_publisher()
    
    # Создание события
    event = PostParsedEvent(
        idempotency_key="tenant123:channel456:msg789",
        user_id="user123",
        channel_id="channel456", 
        post_id="post789",
        tenant_id="tenant123",
        text="Пример поста",
        urls=["https://example.com"],
        posted_at=datetime.now(timezone.utc)
    )
    
    # Публикация
    message_id = await publisher.publish_event('posts.parsed', event)
    print(f"Published event with ID: {message_id}")

async def example_consumer():
    """Пример использования consumer."""
    consumer = await create_consumer(
        stream_name='posts.parsed',
        group_name='tagging-group',
        consumer_name='worker-1'
    )
    
    async def handle_post_parsed(event_data):
        """Обработчик события post.parsed."""
        print(f"Processing post: {event_data['post_id']}")
        # Здесь логика тегирования
    
    # Запуск потребления
    await consumer.start_consuming('posts.parsed', handle_post_parsed)

# ============================================================================
# EVENT BUS CONFIG
# ============================================================================

@dataclass
class EventBusConfig:
    """Конфигурация EventBus."""
    group_name: str = "telegram-assistant"
    consumer_name: str = "worker"
    batch_size: int = 10
    block_time: int = 1000
    max_retries: int = 3
    retry_delay: int = 5
    idle_timeout: int = 30000

# ============================================================================
# EVENT BUS
# ============================================================================

class EventBus:
    """Основной класс для управления событиями."""
    
    def __init__(self, redis_url: str, config: Optional['EventBusConfig'] = None):
        self.redis_url = redis_url
        self.config = config or EventBusConfig()
        self.client = None
        self.publisher = None
        self.consumer = None
    
    async def initialize(self):
        """Инициализация EventBus."""
        self.client = RedisStreamsClient(self.redis_url)
        await self.client.connect()
        self.publisher = EventPublisher(self.client)
        self.consumer = EventConsumer(self.client, self.config)
    
    def get_publisher(self) -> 'EventPublisher':
        """Получение EventPublisher."""
        if self.publisher is None:
            raise RuntimeError("EventBus not initialized. Call initialize() first.")
        return self.publisher
    
    def get_consumer(self) -> 'EventConsumer':
        """Получение EventConsumer."""
        if self.consumer is None:
            raise RuntimeError("EventBus not initialized. Call initialize() first.")
        return self.consumer

# ============================================================================
# Глобальные экземпляры для инициализации
# ============================================================================

_event_bus: Optional['EventBus'] = None
_event_publisher: Optional['EventPublisher'] = None

async def init_event_bus(redis_url: str, config: Optional['EventBusConfig'] = None) -> 'EventBus':
    """Инициализация глобального EventBus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(redis_url, config)
        await _event_bus.initialize()
    return _event_bus

def get_event_publisher() -> 'EventPublisher':
    """Получение глобального EventPublisher."""
    global _event_publisher
    if _event_publisher is None:
        if _event_bus is None:
            raise RuntimeError("EventBus not initialized. Call init_event_bus() first.")
        _event_publisher = _event_bus.get_publisher()
    return _event_publisher

if __name__ == "__main__":
    # Пример запуска
    asyncio.run(example_publisher())