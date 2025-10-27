"""
Indexing Task - Consumer для posts.enriched событий
[C7-ID: WORKER-INDEXING-002]

Обрабатывает события posts.enriched → эмбеддинги → Qdrant + Neo4j → публикация posts.indexed
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import structlog
from prometheus_client import Counter, Histogram, Gauge

# Context7: Импорты для обработки ошибок и DLQ
class DLQReason:
    """Причины отправки в Dead Letter Queue."""
    SCHEMA_INVALID = "schema_invalid"
    NO_TEXT = "no_text"
    EMBED_GEN_FAIL = "embed_gen_fail"
    EMBED_DIM = "embed_dim_mismatch"
    QDRANT_FAIL = "qdrant_fail"
    NEO4J_FAIL = "neo4j_fail"
    UNHANDLED = "unhandled"

class PermanentError(Exception):
    """Перманентная ошибка - сообщение отправляется в DLQ."""
    def __init__(self, reason_code: str, details: str = ""):
        self.reason_code = reason_code
        self.details = details
        super().__init__(f"Permanent error: {reason_code} - {details}")

class TransientError(Exception):
    """Транзиентная ошибка - сообщение будет повторно обработано."""
    pass

from ai_providers.gigachain_adapter import GigaChainAdapter, create_gigachain_adapter
from ai_providers.embedding_service import create_embedding_service
from event_bus import EventConsumer, RedisStreamsClient
from events.schemas.posts_enriched_v1 import PostEnrichedEventV1
from events.schemas.posts_indexed_v1 import PostIndexedEventV1
from integrations.qdrant_client import QdrantClient
from integrations.neo4j_client import Neo4jClient
from feature_flags import feature_flags
from config import settings

logger = structlog.get_logger()

# Debug banner to confirm module load
from datetime import datetime as _dt_banner, timezone as _tz_banner
print("indexing_task_loaded:", __file__, _dt_banner.now(_tz_banner.utc).isoformat(), flush=True)

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

# [C7-ID: WORKER-INDEXING-002] - Метрики индексации
indexing_processed_total = Counter(
    'indexing_processed_total',
    'Total posts processed for indexing',
    ['status']
)

indexing_latency_seconds = Histogram(
    'indexing_latency_seconds',
    'Indexing processing latency',
    ['operation']
)

embedding_generation_seconds = Histogram(
    'embedding_generation_seconds',
    'Embedding generation latency',
    ['provider']
)

qdrant_indexing_seconds = Histogram(
    'qdrant_indexing_seconds',
    'Qdrant indexing latency'
)

neo4j_indexing_seconds = Histogram(
    'neo4j_indexing_seconds',
    'Neo4j indexing latency'
)

# [C7-ID: WORKER-QDRANT-SWEEP-001] - Sweeper метрики
qdrant_sweep_total = Counter(
    'qdrant_sweep_total',
    'Total Qdrant sweep operations',
    ['status']
)

qdrant_expired_vectors_deleted = Counter(
    'qdrant_expired_vectors_deleted',
    'Total expired vectors deleted from Qdrant'
)

# [C7-ID: INDEXING-DLQ-REASONS-001] Фиксированные коды для метрик (не взрывная кардинальность)
class DLQReason:
    NO_TEXT = "NO_TEXT"
    EMBED_DIM = "EMBED_DIM"
    EMBED_GEN_FAIL = "EMBED_GEN_FAIL"
    QDRANT_FAIL = "QDRANT_FAIL"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    UNHANDLED = "UNHANDLED"

# Метрики для DLQ с фиксированными лейблами
indexing_dlq_total = Counter(
    'indexing_dlq_total',
    'Total messages sent to DLQ',
    ['reason']  # только из DLQReason
)

# [C7-ID: INDEXING-ERROR-TYPES-001] Типизированные исключения
class TransientError(Exception):
    """Транзиентная ошибка — retry без DLQ."""
    pass

class PermanentError(Exception):
    """Перманентная ошибка — DLQ с ACK."""
    def __init__(self, reason_code: str, details: str):
        self.reason_code = reason_code
        self.details = details
        super().__init__(f"{reason_code}: {details}")

# [C7-ID: INDEXING-TEXT-NORM-001] Нормализация текста для эмбеддингов
def normalize_text(s: str) -> str:
    """Нормализация текста: NFC, удаление zero-width, схлопывание пробелов."""
    s = unicodedata.normalize("NFC", s.replace("\u200b", ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s

# [C7-ID: INDEXING-TOKEN-LIMIT-001] Эвристическая оценка токенов
def approx_tokens(s: str) -> int:
    """Эвристика: 4 символа ≈ 1 токен (смешанный текст)."""
    return max(1, len(s) // 4)

def truncate_by_tokens(text: str, max_tokens: int = 8192) -> str:
    """Обрезка текста по токен-лимиту (эвристика)."""
    if approx_tokens(text) > max_tokens:
        truncated = text[: max_tokens * 4]
        logger.warning("embedding_text_truncated_tokens", 
                      original_tokens=approx_tokens(text),
                      truncated_tokens=max_tokens)
        return truncated
    return text

# [C7-ID: INDEXING-IDEMPOTENCY-001] Идемпотентность по хешу текста
def calc_text_hash(text: str) -> str:
    """SHA256 хеш текста для идемпотентности."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

EMBED_VERSION = "v1"  # Версия эмбеддинга для инвалидации кеша

# ============================================================================
# INDEXING TASK
# ============================================================================

class IndexingTask:
    """
    Consumer для обработки posts.enriched событий.
    
    Поддерживает:
    - Генерацию эмбеддингов через AI провайдер
    - Индексацию в Qdrant с expires_at в payload
    - Индексацию в Neo4j с expires_at как property
    - Сквозной TTL/retention до всех хранилищ
    - Метрики и мониторинг
    """
    
    def __init__(
        self,
        redis_url: str = "redis://redis:6379",
        qdrant_url: str = "http://localhost:6333",
        neo4j_url: str = "bolt://localhost:7687",
        consumer_group: str = "indexing_workers",
        consumer_name: str = "indexing_worker_1"
    ):
        self.redis_url = redis_url
        self.qdrant_url = qdrant_url
        self.neo4j_url = neo4j_url
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        
        # Redis клиенты
        self.redis_client: Optional[RedisStreamsClient] = None
        self.event_consumer: Optional[EventConsumer] = None
        
        # AI адаптер для эмбеддингов
        self.ai_adapter: Optional[GigaChainAdapter] = None
        
        # Интеграции
        self.qdrant_client: Optional[QdrantClient] = None
        self.neo4j_client: Optional[Neo4jClient] = None
        
        logger.info("IndexingTask initialized", 
                   consumer_group=consumer_group,
                   consumer_name=consumer_name)
    
    async def _initialize(self):
        """Инициализация компонентов task."""
        # Подключение к Redis
        self.redis_client = RedisStreamsClient(self.redis_url)
        await self.redis_client.connect()
        
        # Инициализация EventConsumer
        from event_bus import ConsumerConfig
        consumer_config = ConsumerConfig(
            group_name=self.consumer_group,
            consumer_name=self.consumer_name,
            batch_size=50,
            block_time=1000,
            retry_delay=5,
            idle_timeout=300
        )
        self.event_consumer = EventConsumer(self.redis_client, consumer_config)
        
        # Инициализация AI адаптера
        self.ai_adapter = await create_gigachain_adapter()
        
        # [C7-ID: AI-EMBEDDING-SERVICE-FACTORY-001] Инициализация EmbeddingService
        self.embedding_service = await create_embedding_service(self.ai_adapter)
        
        # Инициализация Qdrant клиента
        self.qdrant_client = QdrantClient(self.qdrant_url)
        await self.qdrant_client.connect()
        
        # [C7-ID: INDEXING-QDRANT-DIM-CHECK-001] Читаем размерность из Qdrant (единый источник правды)
        try:
            collection_info = self.qdrant_client.client.get_collection(settings.qdrant_collection)
            # Для single-vector коллекции
            self.qdrant_vector_size = collection_info.config.params.vectors.size
            
            # Сверяем с конфигом и логируем расхождение
            if self.qdrant_vector_size != settings.EMBED_DIM:
                logger.warning("embed_dim_mismatch_config",
                              qdrant=self.qdrant_vector_size,
                              env=settings.EMBED_DIM,
                              collection=settings.qdrant_collection)
            else:
                logger.info("embed_dim_synchronized",
                           dimension=self.qdrant_vector_size,
                           collection=settings.qdrant_collection)
        except Exception as e:
            logger.error("qdrant_collection_check_failed", error=str(e))
            # Fallback на конфиг
            self.qdrant_vector_size = settings.EMBED_DIM
            logger.warning("using_config_embed_dim", dimension=self.qdrant_vector_size)
        
        # Инициализация EventPublisher для DLQ
        from event_bus import EventPublisher
        self.publisher = EventPublisher(self.redis_client)
        
        logger.info("indexing_embedding_service_initialized", 
                   dimension=self.qdrant_vector_size)
        
        # Инициализация Neo4j клиента
        if feature_flags.neo4j_enabled:
            # Не передаем явные креды: Neo4jClient возьмет их из env (Context7 best practice)
            self.neo4j_client = Neo4jClient(self.neo4j_url)
            await self.neo4j_client.connect()
        
        logger.info("IndexingTask initialized successfully")

    async def start(self):
        """Запуск indexing task с неблокирующей обработкой."""
        try:
            # Инициализация
            await self._initialize()
            
            # Создание consumer group
            await self.event_consumer._ensure_consumer_group("posts.enriched")
            
            logger.info("IndexingTask started successfully", stream="posts.enriched", group=self.consumer_group, consumer=self.consumer_name)
            print("indexing_worker_initialized stream=stream:posts:enriched group=indexing_workers output=stream:posts:indexed", flush=True)
            
            # Используем реальный обработчик
            handler_func = self._process_single_message
            print("INDEXER_REAL_HANDLER_REGISTERED", flush=True)
            
            # Context7: Используем consume_forever для правильного паттерна pending → новые
            await self.event_consumer.consume_forever(
                "posts.enriched", 
                handler_func
            )
                    
        except Exception as e:
            logger.error("Failed to start IndexingTask", error=str(e))
            raise
    
    def _extract_event_dict(self, msg: dict) -> dict:
        """Robust event parsing for IndexingTask."""
        # 1) Нормализуем источник payload
        raw = None
        if isinstance(msg, dict):
            if 'payload' in msg:
                raw = msg['payload']
                # Context7: Если payload - это строка, декодируем
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode('utf-8', errors='replace')
                if isinstance(raw, str):
                    try:
                        import json
                        raw = json.loads(raw)
                    except Exception as e:
                        logger.error("indexing_parse_json_error", err=str(e), sample=str(raw)[:200])
                        raise
                return raw  # Возвращаем сразу после извлечения payload
            elif 'data' in msg:                     # legacy
                raw = msg['data']
            elif 'value' in msg:                    # на случай другой обёртки
                raw = msg['value']
            else:
                # возможно, msg уже есть готовый dict события
                raw = msg

        # 2) Декодируем по типу
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode('utf-8', errors='replace')
        if isinstance(raw, str):
            try:
                import json
                raw = json.loads(raw)
            except Exception as e:
                logger.error("indexing_parse_json_error", err=str(e), sample=str(raw)[:200])
                raise

        if not isinstance(raw, dict):
            logger.error("indexing_unexpected_payload_type", type=str(type(raw)))
            raise ValueError("Unexpected payload type for enriched event")

        return raw

    def _extract_vector(self, event_dict: dict) -> list[float] | None:
        """
        [C7-ID: INDEXING-VECTOR-EXTRACT-002] Извлечение и валидация вектора.
        Строгая проверка размерности, dim mismatch → raise PermanentError.
        """
        # 1) плоское поле
        v = event_dict.get('embedding') or event_dict.get('vector')
        # 2) вложенные - Context7: поддержка enrichment и enrichment_data
        if v is None:
            enr = event_dict.get('enrichment') or event_dict.get('enrichment_data') or event_dict.get('enriched') or {}
            v = enr.get('embedding') or enr.get('vector') or enr.get('qdrant', {}).get('vector')
        # 3) финальная проверка типа
        if isinstance(v, dict) and 'values' in v:  # onnx-style
            v = v['values']
        
        # [C7-ID: INDEXING-EMBED-DIM-VALIDATE-001] Строгая валидация размерности
        if v is not None and len(v) != self.qdrant_vector_size:
            post_id = event_dict.get('post_id', 'unknown')
            raise PermanentError(
                reason_code=DLQReason.EMBED_DIM,
                details=f"post_id={post_id}, got={len(v)}, expected={self.qdrant_vector_size}"
            )
        
        return v


    async def _process_single_message(self, message: Dict[str, Any]):
        """
        [C7-ID: INDEXING-PROCESS-001] Обработка одного сообщения с typсированной обработкой ошибок.
        TransientError → retry, PermanentError → DLQ + ACK.
        """
        post_id = None
        try:
            # Hard entry prints for guaranteed visibility
            try:
                print("indexing_msg_in_raw", type(message).__name__, str(message)[:300], flush=True)
            except Exception:
                pass
            logger.info("indexing_msg_in_marker")
            # [C7-ID: WORKER-INDEXING-DBG-001] Детальная телеметрия входа
            msg_keys = list(message.keys()) if isinstance(message, dict) else []
            msg_type = str(type(message))
            logger.info("indexing_msg_raw",
                keys=msg_keys,
                msg_type=msg_type,
                preview=str(message)[:500])
            
            # Robust парсинг события
            event_dict = self._extract_event_dict(message)
            post_id = event_dict.get('post_id', 'unknown')
            logger.info("indexing_msg_in",
                        post_id=post_id,
                        has_payload=isinstance(message, dict) and ('payload' in message),
                        payload_type=(type(message.get('payload')).__name__ if isinstance(message, dict) and ('payload' in message) else None))
            if not isinstance(event_dict, dict):
                logger.error("indexing_bad_payload", post_id=post_id, type=str(type(event_dict)), sample=str(event_dict)[:200])
                raise PermanentError(DLQReason.SCHEMA_INVALID, "Invalid payload type")
            
            # Снимок «схемы» полезной нагрузки
            logger.info("indexing_payload_shape",
                keys=list(event_dict.keys()),
                types={k: type(event_dict[k]).__name__ for k in list(event_dict.keys())[:15]},
                has_embedding=('embedding' in event_dict),
                has_text=('text' in event_dict),
                has_channel=('channel_id' in event_dict),
                has_post=('post_id' in event_dict))

            # Context7: Проверяем enrichment_data/enrichment в правильном месте
            # Для обратной совместимости поддерживаем оба названия
            if 'enrichment_data' not in event_dict and 'enrichment' not in event_dict:
                logger.error("indexing_no_enrichment", keys=list(event_dict.keys()))
                # ACK сообщение даже при ошибке, чтобы избежать накопления pending
                raise ValueError("Missing enrichment_data or enrichment")
            
            # [C7-ID: WORKER-INDEXING-VAL-001] Строгая валидация + мягкое падение в DLQ
            try:
                enriched_event = PostEnrichedEventV1.model_validate(event_dict)
            except Exception as e:
                logger.error("indexing_schema_validation_error",
                             err=str(e),
                             pydantic_errors=getattr(e, 'errors', lambda: [])(),
                             payload_keys=list(event_dict.keys()))
                # DLQ или skip (если есть publisher)
                if hasattr(self, 'publisher') and self.publisher:
                    await self.publisher.to_dlq("posts.enriched", event_dict, reason=DLQReason.SCHEMA_INVALID, details=str(e))
                # ACK сообщение даже при ошибке, чтобы избежать накопления pending
                return
            
            # [C7-ID: WORKER-INDEXING-MAP-001] Нормализация поля с эмбеддингом
            vec = self._extract_vector(event_dict)  # Может выбросить PermanentError при dim mismatch
            
            # [C7-ID: INDEXING-EMBED-GEN-001] Генерация эмбеддинга, если отсутствует
            if vec is None:
                logger.info("indexing_no_vector_found", post_id=post_id)
                
                if settings.INDEXER_EMBED_IF_MISSING:
                    text = event_dict.get("text") or ""
                    
                    if not text.strip():
                        raise PermanentError(
                            reason_code=DLQReason.NO_TEXT,
                            details=f"post_id={post_id}, empty text"
                        )
                    
                    try:
                        # [C7-ID: INDEXING-EMBED-SERVICE-001] Используем EmbeddingService
                        vec = await self.embedding_service.generate_embedding(text)
                        logger.info("indexing_embedding_generated",
                                   post_id=post_id,
                                   dim=len(vec))
                    except Exception as e:
                        logger.error("indexing_embedding_failed", post_id=post_id, error=str(e))
                        raise PermanentError(
                            reason_code=DLQReason.EMBED_GEN_FAIL,
                            details=f"post_id={post_id}, error={str(e)}"
                        )
                else:
                    raise PermanentError(
                        reason_code=DLQReason.NO_TEXT,
                        details=f"post_id={post_id}, embedding missing and INDEXER_EMBED_IF_MISSING=false"
                    )
            
            # [C7-ID: WORKER-INDEXING-QDRANT-001] Qdrant upsert с идемпотентностью
            text = event_dict.get("text", "")
            text_hash = calc_text_hash(text)
            point_id = str(event_dict.get("post_id") or event_dict.get("idempotency_key"))
            
            payload = {
                "post_id": event_dict.get("post_id"),
                "channel_id": event_dict.get("channel_id"),
                "telegram_post_url": event_dict.get("telegram_post_url"),
                "text": text,
                "text_sha256": text_hash,  # [C7-ID: INDEXING-IDEMPOTENCY-002] Идемпотентность
                "embed_version": EMBED_VERSION,  # Версия эмбеддинга для инвалидации кеша
                "tags": event_dict.get("tags", []),
                "posted_at": str(event_dict.get("posted_at")),
            }
            
            try:
                vector_id = await self.qdrant_client.upsert_vector(
                    collection_name="telegram_posts",
                    vector_id=point_id,
                    vector=vec,
                    payload=payload
                )
                logger.info("indexing_qdrant_upsert_ok",
                           post_id=post_id,
                           point_id=point_id,
                           vector_id=vector_id,
                           text_hash=text_hash[:16])
                
                # [C7-ID: WORKER-INDEXING-NEO4J-001] Индексация в Neo4j
                post_data = {
                    "expires_at": None,
                    "user_id": event_dict.get("user_id", "user_123"),  # Fallback для тестирования
                    "tenant_id": event_dict.get("tenant_id", "tenant_123"),
                    "channel_id": event_dict.get("channel_id", "channel_456")
                }
                
                # Индексация в Neo4j
                neo4j_success = True
                if self.neo4j_client:
                    logger.info("Indexing to Neo4j", post_id=post_id)
                    neo4j_success = await self._index_to_neo4j(enriched_event, post_data)
                    
                    if not neo4j_success:
                        logger.error("Failed to index to Neo4j", post_id=post_id)
                        raise TransientError("Neo4j indexing failed")
                else:
                    logger.warning("Neo4j client not available", post_id=post_id)
                
                # Публикация события posts.indexed
                await self._publish_indexed_event(enriched_event, post_data, 0.0)
                
                # [C7-ID: INDEXING-METRICS-001] Метрики успешной обработки
                indexing_processed_total.labels(status="ok").inc()
                logger.info("indexing_completed_successfully", post_id=post_id)
                
            except Exception as e:
                logger.error("indexing_qdrant_upsert_failed", post_id=post_id, point_id=point_id, error=str(e))
                indexing_processed_total.labels(status="error").inc()
                raise TransientError(f"Qdrant upsert failed: {str(e)}")
            
        except PermanentError as e:
            # [C7-ID: INDEXING-DLQ-001] Перманентная ошибка → DLQ + ACK
            logger.error("indexing_permanent_error", post_id=post_id, reason=e.reason_code, details=e.details)
            indexing_dlq_total.labels(reason=e.reason_code).inc()
            
            # Публикация в DLQ с правильным stream
            if hasattr(self, 'publisher') and self.publisher:
                try:
                    from event_bus import EventPublisher
                    await self.publisher.to_dlq("posts.enriched", event_dict if 'event_dict' in locals() else {}, reason=e.reason_code, details=e.details)
                except Exception as dlq_err:
                    logger.error("dlq_publish_failed", error=str(dlq_err))
            
            # ACK сообщение для предотвращения повторных попыток
            return
            
        except TransientError as e:
            # [C7-ID: INDEXING-RETRY-001] Транзиентная ошибка → пробрасываем для retry
            logger.warning("indexing_transient_error", post_id=post_id, error=str(e))
            raise
            
        except Exception as e:
            # [C7:INDEXING-GUARD-001] Неожиданная ошибка → DLQ с кодом UNHANDLED
            payload = message.get('payload', message) if isinstance(message, dict) else message
            try:
                payload_keys = list(payload.keys()) if isinstance(payload, dict) else None
            except Exception:
                payload_keys = None
            logger.exception("indexing_unhandled_exception",
                             post_id=post_id,
                             err=str(e),
                             payload_type=type(payload).__name__,
                             payload_keys=payload_keys)
            
            # Отправляем в DLQ
            indexing_dlq_total.labels(reason=DLQReason.UNHANDLED).inc()
            if hasattr(self, 'publisher') and self.publisher:
                try:
                    await self.publisher.to_dlq("posts.enriched", payload, reason=DLQReason.UNHANDLED, details=str(e))
                except Exception as dlq_err:
                    logger.error("dlq_publish_failed", error=str(dlq_err))
            
            return

    # legacy batch handler removed
    async def _get_post_data(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Получение данных поста из БД для expires_at."""
        # TODO: Реализовать запрос к БД для получения expires_at
        # Пока возвращаем mock данные
        from datetime import timedelta
        return {
            'post_id': post_id,
            'expires_at': (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),  # Mock expires_at
            'user_id': 'user_123',
            'tenant_id': 'tenant_123',
            'channel_id': 'channel_456'
        }
    
    async def _index_post(self, enriched_event: PostEnrichedEventV1, post_data: Dict[str, Any]) -> bool:
        """Индексация поста в Qdrant и Neo4j."""
        try:
            # Генерация эмбеддинга
            embedding_start = time.time()
            embedding = await self._generate_embedding(enriched_event, post_data)
            embedding_time = time.time() - embedding_start
            
            if not embedding:
                logger.error("Failed to generate embedding", post_id=enriched_event.post_id)
                return False
            
            # Индексация в Qdrant
            qdrant_start = time.time()
            qdrant_success = await self._index_to_qdrant(enriched_event, post_data, embedding)
            qdrant_time = time.time() - qdrant_start
            
            if not qdrant_success:
                logger.error("Failed to index to Qdrant", post_id=enriched_event.post_id)
                return False
            
            # Индексация в Neo4j
            neo4j_success = True
            neo4j_time = 0
            if self.neo4j_client:
                logger.debug("Indexing to Neo4j", post_id=enriched_event.post_id)
                neo4j_start = time.time()
                neo4j_success = await self._index_to_neo4j(enriched_event, post_data)
                neo4j_time = time.time() - neo4j_start
                
                if not neo4j_success:
                    logger.error("Failed to index to Neo4j", post_id=enriched_event.post_id)
                    return False
            else:
                logger.warning("Neo4j client not available", post_id=enriched_event.post_id)
            
            # Метрики
            embedding_generation_seconds.labels(provider='gigachat').observe(embedding_time)
            qdrant_indexing_seconds.observe(qdrant_time)
            if neo4j_time > 0:
                neo4j_indexing_seconds.observe(neo4j_time)
            
            return True
            
        except Exception as e:
            logger.error("Error indexing post", 
                        post_id=enriched_event.post_id,
                        error=str(e))
            return False
    
    async def _get_post_text(self, post_id: str) -> Optional[str]:
        """Получение текста поста из БД."""
        try:
            result = await self.db_session.execute(
                text("SELECT content FROM posts WHERE id = :post_id"),
                {"post_id": post_id}
            )
            row = result.fetchone()
            return row.content if row else None
        except Exception as e:
            logger.error("Failed to get post text", post_id=post_id, error=str(e))
            return None
    
    async def _generate_embedding(self, enriched_event: PostEnrichedEventV1, post_data: Dict[str, Any]) -> Optional[List[float]]:
        """Генерация эмбеддинга для поста."""
        try:
            if not self.ai_adapter:
                logger.error("AI adapter not initialized")
                return None
            
            # Получение текста поста из БД
            post_text = await self._get_post_text(enriched_event.post_id)
            if not post_text:
                logger.warning("No text found for post", post_id=enriched_event.post_id)
                return None
            
            # Подготовка текста для эмбеддинга
            text_parts = [post_text]
            
            # Добавление обогащенного контента если есть
            if enriched_event.enrichment_data:
                for url, data in enriched_event.enrichment_data.items():
                    if isinstance(data, dict) and 'content' in data:
                        text_parts.append(data['content'])
            
            # Генерация эмбеддинга
            embeddings = await self.ai_adapter.generate_embeddings(text_parts)
            
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
            else:
                logger.warning("No embeddings generated", post_id=enriched_event.post_id)
                return None
                
        except Exception as e:
            logger.error("Error generating embedding", 
                        post_id=enriched_event.post_id,
                        error=str(e))
            return None
    
    async def _index_to_qdrant(self, enriched_event: PostEnrichedEventV1, post_data: Dict[str, Any], embedding: List[float]) -> bool:
        """Индексация в Qdrant с expires_at в payload."""
        try:
            # Подготовка payload с expires_at
            payload = {
                'post_id': enriched_event.post_id,
                'user_id': post_data['user_id'],
                'tenant_id': post_data['tenant_id'],
                'channel_id': post_data['channel_id'],
                'expires_at': post_data['expires_at'],  # [C7-ID: WORKER-INDEXING-002] - Сквозной TTL
                'enrichment_data': enriched_event.enrichment_data,
                'source_urls': enriched_event.source_urls,
                'word_count': enriched_event.word_count,
                'indexed_at': datetime.now(timezone.utc).isoformat()
            }
            
            # Определение коллекции (per-user)
            collection_name = f"user_{post_data['user_id']}_posts"
            
            # Создание коллекции если не существует
            await self.qdrant_client.ensure_collection(collection_name, len(embedding))
            
            # Индексация вектора
            print("qdrant_upsert_enter", collection_name, enriched_event.post_id, flush=True)
            
            vector_id = await self.qdrant_client.upsert_vector(
                collection_name=collection_name,
                vector_id=enriched_event.post_id,
                vector=embedding,
                payload=payload
            )
            
            print("qdrant_upsert_ok", vector_id, flush=True)
            
            logger.debug("Post indexed to Qdrant",
                        post_id=enriched_event.post_id,
                        collection=collection_name,
                        vector_id=vector_id)
            
            return True
            
        except Exception as e:
            logger.error("Error indexing to Qdrant", 
                      post_id=enriched_event.post_id,
                      error=str(e))
            return False
    
    async def _index_to_neo4j(self, enriched_event: PostEnrichedEventV1, post_data: Dict[str, Any]) -> bool:
        """Индексация в Neo4j с expires_at как property."""
        try:
            if not self.neo4j_client:
                logger.warning("Neo4j client not available")
                return True  # Не критично для работы
            
            # Создание узлов и связей
            # Context7: Конвертация Pydantic модели в словарь для Neo4j
            enrichment_dict = enriched_event.enrichment_data.model_dump() if hasattr(enriched_event.enrichment_data, 'model_dump') else enriched_event.enrichment_data
            
            await self.neo4j_client.create_post_node(
                post_id=enriched_event.post_id,
                user_id=post_data['user_id'],
                tenant_id=post_data['tenant_id'],
                channel_id=post_data['channel_id'],
                expires_at=post_data['expires_at'],  # [C7-ID: WORKER-INDEXING-002] - Сквозной TTL
                enrichment_data=enrichment_dict,
                indexed_at=datetime.now(timezone.utc).isoformat()
            )
            
            logger.debug("Post indexed to Neo4j",
                        post_id=enriched_event.post_id)
            
            return True
            
        except Exception as e:
            logger.error("Error indexing to Neo4j", 
                        post_id=enriched_event.post_id,
                        error=str(e))
            return False
    
    async def _publish_indexed_event(
        self, 
        enriched_event: PostEnrichedEventV1, 
        post_data: Dict[str, Any],
        processing_time: float
    ):
        """Публикация события posts.indexed."""
        try:
            # Подготовка данных события
            indexed_event = PostIndexedEventV1(
                idempotency_key=f"{enriched_event.post_id}:indexed:v1",
                post_id=enriched_event.post_id,
                vector_id=enriched_event.post_id,  # Используем post_id как vector_id
                embedding_provider="gigachat",
                embedding_dim=1536,  # TODO: получить из AI адаптера
                qdrant_collection=f"user_{post_data['user_id']}_posts",
                neo4j_nodes_created=5,  # TODO: получить из Neo4j клиента
                neo4j_relationships_created=8,  # TODO: получить из Neo4j клиента
                indexing_duration_ms=int(processing_time * 1000),
                embedding_generation_ms=int(processing_time * 1000 * 0.4),  # Примерное распределение
                qdrant_indexing_ms=int(processing_time * 1000 * 0.3),
                neo4j_indexing_ms=int(processing_time * 1000 * 0.3),
                embedding_quality_score=0.92  # TODO: вычислить реальную оценку
            )
            
            # Публикация в Redis Streams через унифицированный EventPublisher
            from event_bus import EventPublisher
            publisher = EventPublisher(self.redis_client)
            msg_id = await publisher.publish_event("posts.indexed", indexed_event)
            logger.info("indexing_publish_indexed_ok", 
                        post_id=enriched_event.post_id, msg_id=msg_id)
            
        except Exception as e:
            logger.error("Error publishing indexed event",
                        post_id=enriched_event.post_id,
                        error=str(e))
            raise
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики indexing task."""
        return {
            'redis_connected': self.redis_client is not None,
            'ai_adapter_available': self.ai_adapter is not None,
            'qdrant_connected': self.qdrant_client is not None,
            'neo4j_connected': self.neo4j_client is not None,
            'feature_flags': {
                'neo4j_enabled': feature_flags.neo4j_enabled,
                'gigachat_enabled': feature_flags.gigachat_enabled
            }
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check для indexing task."""
        try:
            # Проверка Redis
            redis_healthy = False
            if self.redis_client:
                await self.redis_client.ping()
                redis_healthy = True
            
            # Проверка Qdrant
            qdrant_healthy = False
            if self.qdrant_client:
                await self.qdrant_client.health_check()
                qdrant_healthy = True
            
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

# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def main():
    """Основная функция запуска indexing task."""
    logger.info("Starting IndexingTask...")
    
    # Конфигурация
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    neo4j_url = os.getenv("NEO4J_URL", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "changeme")
    
    # Создание task
    task = IndexingTask(redis_url, database_url, qdrant_url, neo4j_url, neo4j_user, neo4j_password)
    
    try:
        # Запуск
        await task.start()
    except KeyboardInterrupt:
        logger.info("IndexingTask stopped by user")
    except Exception as e:
        logger.error("IndexingTask failed", error=str(e))
        raise
    finally:
        await task.stop()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())