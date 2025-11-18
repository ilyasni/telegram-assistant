"""
Vision Analysis Task
Context7 best practice: обработка событий stream:posts:vision с идемпотентностью, trace propagation
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update, text

from events.schemas import VisionUploadedEventV1, VisionAnalyzedEventV1, MediaFile
from ai_adapters.gigachat_vision import GigaChatVisionAdapter
from services.vision_policy_engine import VisionPolicyEngine
from services.budget_gate import BudgetGateService
from services.ocr_fallback import OCRFallbackService
from services.storage_quota import StorageQuotaService
from services.retry_policy import create_retry_decorator, DEFAULT_RETRY_CONFIG, DLQService, should_retry, classify_error
from services.experiment_manager import VisionExperimentManager
from shared.utils.phash import compute_phash, PhashResult

# Context7: Импорты из api (ВРЕМЕННОЕ ИСКЛЮЧЕНИЕ для архитектурной границы)
# ⚠️ КРИТИЧЕСКОЕ ПРАВИЛО: Worker НЕ должен импортировать из API
# TODO: [ARCH-SHARED-001] Переместить S3StorageService в shared-пакет в будущем
# Context7: Настройка путей для cross-service импортов (только для S3StorageService)
import sys
import os
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from api.services.s3_storage import S3StorageService
from botocore.exceptions import ClientError
# Context7: PostEnrichment НЕ импортируем - используем прямые SQL запросы через async БД

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

vision_worker_processed_total = Counter(
    'vision_worker_processed_total',
    'Total vision events processed',
    ['status', 'reason']
)

vision_worker_duration_seconds = Histogram(
    'vision_worker_duration_seconds',
    'Vision worker processing duration',
    ['status']
)

vision_worker_idempotency_hits = Counter(
    'vision_worker_idempotency_hits',
    'Idempotency cache hits'
)

# Context7: Расширенные метрики для observability
vision_events_total = Counter(
    'vision_events_total',
    'Total vision events processed',
    ['status', 'reason']  # status: processed, skipped, failed; reason: policy, budget, idempotency, etc.
)

vision_media_total = Counter(
    'vision_media_total',
    'Total media files processed',
    ['result', 'reason']  # result: ok, skipped, failed; reason: policy, budget, idempotency, s3_missing, etc.
)

vision_retries_total = Counter(
    'vision_retries_total',
    'Total retries by stage',
    ['stage']  # stage: s3, vision_api, db
)

vision_event_duration_seconds = Histogram(
    'vision_event_duration_seconds',
    'End-to-end vision event processing duration',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

vision_media_duration_seconds = Histogram(
    'vision_media_duration_seconds',
    'Single media file processing duration',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

vision_experiment_assignments_total = Counter(
    'vision_experiment_assignments_total',
    'Vision experiment assignments per tenant',
    ['experiment', 'variant']
)

vision_low_priority_enqueued_total = Counter(
    'vision_low_priority_enqueued_total',
    'Vision media enqueued to low priority queue',
    ['reason']
)

vision_low_priority_processed_total = Counter(
    'vision_low_priority_processed_total',
    'Low priority vision processing outcomes',
    ['status']  # processed | pending | error | budget_exhausted
)

from prometheus_client import Gauge
vision_pel_size = Gauge(
    'vision_pel_size',
    'Pending Entry List size for vision workers',
    ['consumer_group']
)

vision_pending_older_than_seconds = Gauge(
    'vision_pending_older_than_seconds',
    'Age of oldest pending message in seconds',
    ['percentile', 'consumer_group']  # percentile: 95, 99
)

ocr_local_latency_seconds = Histogram(
    'ocr_local_latency_seconds',
    'Latency of local OCR processing before/after Vision',
    ['engine', 'mode']  # mode: primary | fallback
)

# Context7: Дополнительные метрики для мониторинга Vision анализа
vision_analysis_duration_seconds = Histogram(
    'vision_analysis_duration_seconds',
    'Vision analysis duration (API call time)',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

vision_analysis_tokens_total = Counter(
    'vision_analysis_tokens_total',
    'Total tokens used for Vision analysis',
    ['provider', 'model']
)

vision_analysis_errors_total = Counter(
    'vision_analysis_errors_total',
    'Total Vision analysis errors',
    ['error_type']  # error_type: parse_error, api_error, timeout, quota_exceeded, etc.
)

vision_albums_processed_total = Counter(
    'vision_albums_processed_total',
    'Total albums processed by Vision',
    ['status']  # status: success, failed, skipped
)


class VisionAnalysisTask:
    """
    Vision Analysis Task для обработки медиа через GigaChat Vision API.
    
    Features:
    - Обработка событий stream:posts:vision
    - Идемпотентность через SHA256 + Redis
    - Интеграция с S3, Budget Gate, Policy Engine
    - Retry logic и DLQ для failed events
    - Trace propagation
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        db_session: AsyncSession,
        s3_service: S3StorageService,
        budget_gate: BudgetGateService,
        storage_quota: StorageQuotaService,
        vision_adapter: GigaChatVisionAdapter,
        policy_engine: VisionPolicyEngine,
        ocr_fallback: Optional[OCRFallbackService] = None,
        dlq_service: Optional[DLQService] = None,
        neo4j_client: Optional = None,  # Neo4jClient для синхронизации графа
        stream_name: str = "stream:posts:vision",
        consumer_group: str = "vision_workers",
        consumer_name: str = None,
        local_ocr_primary_enabled: bool = False,
        experiment_manager: Optional[VisionExperimentManager] = None
    ):
        self.redis = redis_client
        self.db = db_session
        self.s3_service = s3_service
        self.budget_gate = budget_gate
        self.storage_quota = storage_quota
        self.vision_adapter = vision_adapter
        self.policy_engine = policy_engine
        self.ocr_fallback = ocr_fallback
        self.dlq_service = dlq_service
        self.neo4j_client = neo4j_client
        self.local_ocr_primary_enabled = local_ocr_primary_enabled
        self.experiment_manager = experiment_manager
        
        # Context7: Валидация stream name - должен быть stream:posts:vision
        expected_stream = "stream:posts:vision"
        if stream_name != expected_stream:
            logger.warning(
                "Stream name mismatch",
                expected=expected_stream,
                actual=stream_name,
                using=stream_name
            )
        
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"vision-worker-{os.getenv('HOSTNAME', 'default')}"
        
        self.running = False
        self.processed_count = 0
        self.error_count = 0
        
        # Context7: Конфигурация для обработки pending сообщений
        self.max_deliveries = int(os.getenv("VISION_MAX_DELIVERIES", "5"))
        self.pel_min_idle_ms = int(os.getenv("VISION_PEL_MIN_IDLE_MS", "60000"))  # 1 минута
        self.pel_batch_size = int(os.getenv("VISION_PEL_BATCH_SIZE", "10"))
        self.pending_check_interval = int(os.getenv("VISION_PENDING_CHECK_INTERVAL", "60"))  # секунды
        self.phash_enabled = os.getenv("VISION_PHASH_ENABLED", "true").lower() in {"1", "true", "yes"}
        self.roi_crop_enabled = os.getenv("VISION_ROI_CROP_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.roi_crop_max_dim = int(os.getenv("VISION_ROI_MAX_DIM", "1024"))
        self.low_priority_enabled = os.getenv("VISION_LOW_PRIORITY_QUEUE_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.low_priority_stream_name = os.getenv("VISION_LOW_PRIORITY_STREAM", "stream:posts:vision:low")
        self.low_priority_consumer_group = os.getenv("VISION_LOW_PRIORITY_CONSUMER_GROUP", "vision_low_priority_workers")
        self.low_priority_retry_delay_seconds = int(os.getenv("VISION_LOW_PRIORITY_RETRY_DELAY_SECONDS", "300"))
        self.low_priority_max_retries = int(os.getenv("VISION_LOW_PRIORITY_MAX_RETRIES", "3"))
        self.low_priority_stream_maxlen = int(os.getenv("VISION_LOW_PRIORITY_MAXLEN", "1000"))
        self.phash_hash_size = int(os.getenv("VISION_PHASH_HASH_SIZE", "16"))
        default_ttl = str(7 * 24 * 3600)
        self.phash_cache_ttl_seconds = int(os.getenv("VISION_PHASH_CACHE_TTL_SECONDS", default_ttl))
        self.phash_redis_prefix = os.getenv("VISION_PHASH_REDIS_PREFIX", "vision:phash")
        self._low_priority_backlog_processed = False
        
        logger.info(
            "VisionAnalysisTask initialized",
            stream=stream_name,
            consumer_group=consumer_group,
            consumer_name=self.consumer_name
        )
    
    async def start(self):
        """Запуск Vision Analysis Task."""
        self.running = True
        
        # Context7: Создание consumer group (идемпотентно)
        try:
            await self.redis.xgroup_create(
                self.stream_name,
                self.consumer_group,
                id='0',
                mkstream=True
            )
            logger.info(
                "Created consumer group",
                stream=self.stream_name,
                group=self.consumer_group,
                consumer_name=self.consumer_name
            )
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                logger.error(
                    "Failed to create consumer group",
                    stream=self.stream_name,
                    group=self.consumer_group,
                    error=str(e)
                )
                raise
            else:
                logger.debug(
                    "Consumer group already exists",
                    stream=self.stream_name,
                    group=self.consumer_group
                )
        
        if self.low_priority_enabled:
            try:
                await self.redis.xgroup_create(
                    self.low_priority_stream_name,
                    self.low_priority_consumer_group,
                    id='0',
                    mkstream=True
                )
                logger.info(
                    "Created low priority consumer group",
                    stream=self.low_priority_stream_name,
                    group=self.low_priority_consumer_group,
                    consumer_name=self.consumer_name
                )
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    logger.error(
                        "Failed to create low priority consumer group",
                        stream=self.low_priority_stream_name,
                        group=self.low_priority_consumer_group,
                        error=str(e)
                    )
                    raise
                else:
                    logger.debug(
                        "Low priority consumer group already exists",
                        stream=self.low_priority_stream_name,
                        group=self.low_priority_consumer_group
                    )

        logger.info(
            "VisionAnalysisTask started",
            stream=self.stream_name,
            consumer_group=self.consumer_group,
            consumer_name=self.consumer_name
        )
        
        # Context7: Флаг для обработки backlog при первом запуске
        backlog_processed = False
        last_pending_check = time.time()
        
        while self.running:
            try:
                # Context7: Redis Streams best practice - при первом запуске читаем backlog ('0')
                # Затем переключаемся на новые сообщения ('>')
                start_id = '0' if not backlog_processed else '>'
                
                try:
                    messages = await self.redis.xreadgroup(
                        self.consumer_group,
                        self.consumer_name,
                        {self.stream_name: start_id},
                        count=10,
                        block=2000 if start_id == '>' else 100  # Блокируем только для новых сообщений
                    )
                    
                    if messages:
                        for stream_name, stream_messages in messages:
                            logger.info(
                                "Reading vision events from stream",
                                stream=str(stream_name),
                                count=len(stream_messages),
                                start_id=start_id,
                                backlog_mode=not backlog_processed
                            )
                            
                            for message_id, fields in stream_messages:
                                try:
                                    logger.debug(
                                        "Processing vision event",
                                        extra={
                                            "message_id": message_id,
                                            "start_id": start_id,
                                            "fields_keys": list(fields.keys()) if isinstance(fields, dict) else "non-dict",
                                            "trace_id": fields.get('trace_id') if isinstance(fields, dict) else None
                                        }
                                    )
                                    # Context7: Правильный порядок: XADD (в _process_event) → XACK (здесь)
                                    # _process_event возвращает True только если событие успешно эмитировано
                                    event_processed = await self._process_event(message_id, fields)
                                    
                                    if event_processed:
                                        # ACK только после успешной эмиссии события (XADD)
                                        await self.redis.xack(
                                            self.stream_name,
                                            self.consumer_group,
                                            message_id
                                        )
                                        logger.debug("Vision event ACKed", extra={"message_id": message_id})
                                    else:
                                        # Событие не обработано - не делаем ACK, оставляем для retry
                                        logger.warning(
                                            "Vision event processing failed, not ACKing",
                                            extra={"message_id": message_id}
                                        )
                                        # Обработка ошибки (DLQ и т.д.)
                                        try:
                                            event_data = self._parse_event_fields(fields)
                                            error = Exception("Event processing failed - no event emitted")
                                            await self._handle_error(message_id, fields, error)
                                        except Exception as handle_error:
                                            logger.error(
                                                "Error handling failed event",
                                                extra={
                                                    "message_id": message_id,
                                                    "error": str(handle_error)
                                                },
                                                exc_info=True
                                            )
                                except Exception as e:
                                    logger.error(
                                        "Error processing vision event",
                                        extra={
                                            "message_id": message_id,
                                            "error": str(e),
                                            "error_type": type(e).__name__
                                        },
                                        exc_info=True
                                    )
                                    await self._handle_error(message_id, fields, e)
                                    # Не делаем ACK при исключении - оставляем для retry
                        
                        # После обработки backlog переключаемся на новые сообщения
                        if start_id == '0':
                            backlog_processed = True
                            logger.info("Backlog processed, switching to new messages")
                    else:
                        # Если backlog пуст, переключаемся на новые сообщения
                        if start_id == '0':
                            backlog_processed = True
                            logger.debug("No backlog, switching to new messages")
                    
                    if self.low_priority_enabled:
                        await self._process_low_priority_queue()

                    # Context7: Периодическая проверка pending сообщений (каждые N секунд)
                    current_time = time.time()
                    if current_time - last_pending_check >= self.pending_check_interval:
                        try:
                            await self._process_pending_messages()
                            last_pending_check = current_time
                        except Exception as e:
                            logger.warning(
                                "Error processing pending messages",
                                extra={
                                    "error": str(e),
                                    "error_type": type(e).__name__
                                },
                                exc_info=True
                            )
                
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        "Error reading from stream",
                        start_id=start_id,
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True
                    )
                    # Продолжаем работу, не прерываем цикл
                    await asyncio.sleep(1)
                    continue
                
            except asyncio.CancelledError:
                break
    
    async def stop(self):
        """Остановка Vision Analysis Task."""
        self.running = False
        logger.info("VisionAnalysisTask stopped")
    
    async def _process_event(self, message_id: str, fields: Dict[str, str]) -> bool:
        """
        Обработка одного события Vision Upload.
        
        Context7: Возвращает True если событие успешно обработано и эмитировано (analyzed/skipped),
        False если обработка не удалась. ACK делается в вызывающем коде только после успешной эмиссии.
        
        Event: stream:posts:vision (VisionUploadedEventV1)
        
        Returns:
            bool: True если событие успешно обработано и эмитировано, False в случае ошибки
        """
        import time
        start_time = time.time()
        
        try:
            priority = self._extract_field_value(fields, "priority")
            retry_count_str = self._extract_field_value(fields, "retry_count")
            original_message_id = self._extract_field_value(fields, "original_message_id")
            retry_count = int(retry_count_str) if retry_count_str and retry_count_str.isdigit() else 0
            is_low_priority = priority == "low"
        except Exception:
            priority = None
            retry_count = 0
            original_message_id = None
            is_low_priority = False

        try:
            # Парсинг события
            event_data = self._parse_event_fields(fields)
            
            # Context7: Валидация через Pydantic с детальным логированием
            try:
                event = VisionUploadedEventV1(**event_data)
            except Exception as e:
                logger.error(
                    "Failed to parse VisionUploadedEventV1",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "fields_keys": list(fields.keys()) if isinstance(fields, dict) else "non-dict",
                        "message_id": message_id
                    },
                    exc_info=True
                )
                vision_worker_processed_total.labels(status="error", reason="parse_error").inc()
                vision_analysis_errors_total.labels(error_type="parse_error").inc()
                # Context7: Не падаем на одном плохом событии - возвращаем False, чтобы не делать ACK
                return False
            
            tenant_id = event.tenant_id
            post_id = event.post_id
            trace_id = event.trace_id
            
            # Context7: Определяем, является ли это альбомом (post_id начинается с "album:")
            is_album = post_id.startswith("album:") if post_id else False
            
            # Context7: Получение tenant_id и channel_username из БД для оптимизации
            channel_username = None
            if not tenant_id or tenant_id == 'default':
                try:
                    from sqlalchemy import text
                    channel_info_result = await self.db.execute(
                        text("""
                            SELECT 
                                COALESCE(
                                    (SELECT u.tenant_id::text FROM users u 
                                     JOIN user_channel uc ON uc.user_id = u.id 
                                     WHERE uc.channel_id = c.id 
                                     LIMIT 1),
                                    CAST(pe_tags.data->>'tenant_id' AS text),
                                    CAST(c.settings->>'tenant_id' AS text),
                                    'default'
                                ) as tenant_id,
                                c.username as channel_username
                            FROM posts p
                            JOIN channels c ON c.id = p.channel_id
                            LEFT JOIN post_enrichment pe_tags 
                                ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
                            WHERE p.id = :post_id
                            LIMIT 1
                        """),
                        {"post_id": post_id}
                    )
                    row = channel_info_result.fetchone()
                    if row:
                        if row[0] and row[0] != 'default':
                            tenant_id = str(row[0])
                        channel_username = row[1] if row[1] else None
                        logger.debug(
                            "Using tenant_id and channel_username from DB in vision_analysis_task",
                            post_id=post_id,
                            tenant_id=tenant_id,
                            channel_username=channel_username,
                            trace_id=trace_id
                        )
                except Exception as e:
                    logger.debug(
                        "Failed to get tenant_id and channel_username from DB in vision_analysis_task",
                        post_id=post_id,
                        error=str(e),
                        trace_id=trace_id
                    )
            
            # Context7: Fallback на 'default' только если все еще не найден
            if not tenant_id or tenant_id == 'default':
                tenant_id = 'default'
                logger.warning(
                    "tenant_id not found or is 'default' in vision_analysis_task, using 'default'",
                    post_id=post_id,
                    tenant_id_from_event=event.tenant_id,
                    trace_id=trace_id
                )
            
            # Context7: Нормализация post_id на раннем этапе для консистентности
            # Используем тот же подход, что и в EnrichmentTask
            original_post_id = post_id
            if os.getenv("FEATURE_ALLOW_NON_UUID_IDS", "false").lower() == "true":
                import uuid
                def normalize_post_id(raw: str) -> str:
                    """Детерминированное приведение post_id к UUID для DEV режима."""
                    try:
                        uuid.UUID(raw)
                        return raw
                    except (ValueError, TypeError):
                        NAMESPACE = uuid.UUID("11111111-1111-1111-1111-111111111111")
                        return str(uuid.uuid5(NAMESPACE, raw))
                
                post_id = normalize_post_id(post_id)
                if original_post_id != post_id:
                    logger.debug(
                        "Post ID normalized at event processing stage",
                        extra={
                            "original": original_post_id,
                            "normalized": post_id,
                            "trace_id": trace_id
                        }
                    )
            
            experiment_variants = self._get_experiment_variants(tenant_id)
            wave_a_variant = experiment_variants.get(
                "wave_a",
                "experiment" if self.vision_adapter.preprocess_enabled else "control"
            )
            wave_a_active = self.vision_adapter.preprocess_enabled and wave_a_variant == "experiment"
            wave_b_variant = experiment_variants.get(
                "wave_b",
                "experiment" if (self.local_ocr_primary_enabled or self.phash_enabled) else "control"
            )
            wave_b_local_ocr_enabled = self.local_ocr_primary_enabled and wave_b_variant == "experiment"
            wave_b_phash_enabled = self.phash_enabled and wave_b_variant == "experiment"
            wave_c_variant = experiment_variants.get(
                "wave_c",
                "experiment" if (self.roi_crop_enabled or self.low_priority_enabled) else "control"
            )
            wave_c_roi_enabled = self.roi_crop_enabled and wave_c_variant == "experiment"
            wave_c_low_priority_enabled = self.low_priority_enabled and wave_c_variant == "experiment"

            if experiment_variants:
                logger.debug(
                    "Vision experiments assigned",
                    extra={
                        "tenant_id": tenant_id,
                        "variants": experiment_variants,
                        "trace_id": trace_id
                    }
                )

            max_output_tokens_override = (
                self.vision_adapter.max_output_tokens if wave_a_active else None
            )

            # Context7: Идемпотентность на медиа-уровне
            # Проверяем каждый медиа файл отдельно, возвращаем dict {sha256: bool}
            media_idempotency_status = await self._check_media_idempotency(tenant_id, post_id, event.media_files)
            
            # Обработка каждого медиа файла
            analysis_results = []
            skipped_reasons = []  # Для skipped событий
            
            for media_file in event.media_files:
                media_id = media_file.sha256
                
                # Context7: Проверка идемпотентности на уровне медиа
                if media_idempotency_status.get(media_id, False):
                    vision_worker_idempotency_hits.inc()
                    skipped_reasons.append({
                        "media_id": media_id,
                        "reason": "idempotency",
                        "details": {"dedupe_key": f"vision:processed:{post_id}:{media_id}"}
                    })
                    vision_media_total.labels(result="skipped", reason="idempotency").inc()
                    logger.debug(
                        "Media already processed (idempotency)",
                        extra={
                            "post_id": post_id,
                            "sha256": media_id[:16] + "...",
                            "trace_id": trace_id
                        }
                    )
                    continue
                
                try:
                    # Context7: Проверка политики Vision с детальным логированием
                    # Проверяем budget gate для определения quota_exhausted (один раз для оптимизации)
                    quota_exhausted = False
                    budget_check = None
                    if self.budget_gate:
                        budget_check = await self.budget_gate.check_budget(
                            tenant_id=tenant_id,
                            estimated_tokens=1792
                        )
                        quota_exhausted = not budget_check.allowed
                    
                    policy_result = self.policy_engine.evaluate_media_for_vision(
                        media_file={
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes
                        },
                        channel_username=channel_username,  # Context7: получено из БД выше
                        quota_exhausted=quota_exhausted
                    )
                    prompt_template = self.policy_engine.get_prompt_template(policy_result.get("prompt_key"))
                    if wave_a_active:
                        analysis_prompt = prompt_template or self.vision_adapter.default_prompt
                    else:
                        analysis_prompt = self.vision_adapter.legacy_prompt
                    
                    # Context7: Детальное логирование причин пропуска медиа
                    if not policy_result["allowed"] or policy_result["skip"]:
                        reason_detail = policy_result.get("reason", "unknown")
                        logger.info(
                            "Media skipped by policy",
                            extra={
                                "post_id": post_id,
                                "sha256": media_id[:16] + "...",
                                "mime_type": media_file.mime_type,
                                "size_bytes": media_file.size_bytes,
                                "reason": reason_detail,
                                "allowed": policy_result.get("allowed", False),
                                "skip": policy_result.get("skip", False),
                                "trace_id": trace_id
                            }
                        )
                        skipped_reasons.append({
                            "media_id": media_id,
                            "reason": "policy",
                            "details": {
                                "policy_name": reason_detail,
                                "mime_type": media_file.mime_type,
                                "size_bytes": media_file.size_bytes
                            }
                        })
                        vision_media_total.labels(result="skipped", reason="policy").inc()
                        continue
                    
                    if (
                        wave_b_local_ocr_enabled
                        and policy_result.get("use_ocr")
                        and self.ocr_fallback
                    ):
                        logger.info(
                            "Processing media via local OCR before Vision",
                            extra={
                                "post_id": post_id,
                                "sha256": media_id[:16] + "...",
                                "mime_type": media_file.mime_type,
                                "trace_id": trace_id,
                            },
                        )
                        primary_result = await self._process_with_ocr(
                            media_file, tenant_id, post_id, trace_id, mode="primary"
                        )
                        if primary_result:
                            primary_result = self._attach_experiment_context(primary_result, experiment_variants)
                            analysis_results.append(primary_result)
                            await self._mark_media_as_processed(post_id, media_id)
                            vision_media_total.labels(result="ok", reason="ocr_primary").inc()
                            if is_low_priority:
                                vision_low_priority_processed_total.labels(status="processed").inc()
                            continue
                        logger.warning(
                            "Local OCR primary processing returned no result",
                            extra={
                                "post_id": post_id,
                                "sha256": media_id[:16] + "...",
                                "trace_id": trace_id,
                            },
                        )

                    # Context7: Проверка budget gate с детальным логированием (используем уже проверенный budget_check)
                    if self.budget_gate:
                        # Используем уже проверенный budget_check из проверки выше (оптимизация)
                        if budget_check is None:
                            budget_check = await self.budget_gate.check_budget(
                                tenant_id=tenant_id,
                                estimated_tokens=1792
                            )
                        if not budget_check.allowed:
                            budget_reason = getattr(budget_check, 'reason', 'quota_exhausted')
                            logger.info(
                                "Budget gate blocked vision analysis",
                                extra={
                                    "post_id": post_id,
                                    "sha256": media_id[:16] + "...",
                                    "tenant_id": tenant_id,
                                    "estimated_tokens": 1792,
                                    "reason": budget_reason,
                                    "priority": priority or "default",
                                    "retry_count": retry_count,
                                    "trace_id": trace_id
                                }
                            )
                            # Fallback на OCR если разрешено
                            # Context7: Проверяем, что OCR fallback доступен (self.ocr_fallback не None)
                            # Если OCR fallback отключен через ocr_fallback_enabled=false, self.ocr_fallback = None
                            if wave_b_local_ocr_enabled and policy_result.get("use_ocr") and self.ocr_fallback:
                                logger.info(
                                    "Falling back to OCR",
                                    extra={
                                        "post_id": post_id,
                                        "sha256": media_id[:16] + "...",
                                        "trace_id": trace_id
                                    }
                                )
                                result = await self._process_with_ocr(
                                    media_file, tenant_id, post_id, trace_id, mode="fallback"
                                )
                                if result:
                                    result = self._attach_experiment_context(result, experiment_variants)
                                    analysis_results.append(result)
                                    vision_media_total.labels(result="ok", reason="ocr_fallback").inc()
                                    if is_low_priority:
                                        vision_low_priority_processed_total.labels(status="processed").inc()
                            else:
                                # OCR fallback недоступен или не разрешен политикой
                                if wave_c_low_priority_enabled and not is_low_priority:
                                    enqueued = await self._enqueue_low_priority_event(
                                        event=event,
                                        media_file=media_file,
                                        reason=budget_reason,
                                        trace_id=trace_id,
                                        retry_count=0,
                                        original_message_id=message_id
                                    )
                                    if enqueued:
                                        skipped_reasons.append({
                                            "media_id": media_id,
                                            "reason": "queued_low_priority",
                                            "details": {
                                                "budget_bucket": budget_reason,
                                                "estimated_tokens": 1792,
                                                "stream": self.low_priority_stream_name
                                            }
                                        })
                                        vision_media_total.labels(result="skipped", reason="queued_low_priority").inc()
                                        continue

                                if wave_c_low_priority_enabled and is_low_priority and retry_count < self.low_priority_max_retries:
                                    enqueued = await self._enqueue_low_priority_event(
                                        event=event,
                                        media_file=media_file,
                                        reason=budget_reason,
                                        trace_id=trace_id,
                                        retry_count=retry_count,
                                        original_message_id=message_id
                                    )
                                    if enqueued:
                                        skipped_reasons.append({
                                            "media_id": media_id,
                                            "reason": "requeued_low_priority",
                                            "details": {
                                                "budget_bucket": budget_reason,
                                                "estimated_tokens": 1792,
                                                "retry_count": retry_count + 1,
                                                "stream": self.low_priority_stream_name
                                            }
                                        })
                                        vision_media_total.labels(result="skipped", reason="queued_low_priority").inc()
                                        vision_low_priority_processed_total.labels(status="pending").inc()
                                        continue

                                skip_reason = "budget_low_priority_exhausted" if (is_low_priority and wave_c_low_priority_enabled) else "budget"
                                skip_details = {
                                    "budget_bucket": budget_reason,
                                    "estimated_tokens": 1792,
                                    "low_priority": is_low_priority,
                                    "retry_count": retry_count
                                }
                                if not self.ocr_fallback:
                                    skip_details["ocr_fallback_disabled"] = True
                                if wave_c_low_priority_enabled and is_low_priority and retry_count >= self.low_priority_max_retries:
                                    vision_low_priority_processed_total.labels(status="budget_exhausted").inc()
                                skipped_reasons.append({
                                    "media_id": media_id,
                                    "reason": skip_reason,
                                    "details": skip_details
                                })
                                vision_media_total.labels(result="skipped", reason=skip_reason).inc()
                            continue
                    
                    # Vision анализ через GigaChat
                    media_start_time = time.time()
                    analysis_start_time = time.time()
                    result = await self._analyze_media(
                        media_file=media_file,
                        tenant_id=tenant_id,
                        post_id=post_id,
                        trace_id=trace_id,
                        analysis_prompt=analysis_prompt,
                        preprocess_enabled=wave_a_active,
                        roi_crop_enabled=wave_c_roi_enabled,
                        max_output_tokens_override=max_output_tokens_override,
                        phash_enabled=wave_b_phash_enabled,
                        experiment_variants=experiment_variants
                    )
                    media_duration = time.time() - media_start_time
                    analysis_duration = time.time() - analysis_start_time
                    vision_media_duration_seconds.observe(media_duration)
                    vision_analysis_duration_seconds.observe(analysis_duration)
                    
                    if result:
                        result = self._attach_experiment_context(result, experiment_variants)
                        analysis_results.append(result)
                        # Отметка идемпотентности на уровне медиа
                        await self._mark_media_as_processed(post_id, media_id)
                        vision_media_total.labels(result="ok", reason="success").inc()
                        # Context7: Учет токенов в метриках
                        if result.get("analysis") and result["analysis"].get("tokens_used"):
                            provider = result["analysis"].get("provider", "unknown")
                            model = result["analysis"].get("model", "unknown")
                            tokens_used = result["analysis"].get("tokens_used", 0)
                            vision_analysis_tokens_total.labels(provider=provider, model=model).inc(tokens_used)
                        if is_low_priority:
                            vision_low_priority_processed_total.labels(status="processed").inc()
                    else:
                        # Context7: Результат None - анализ не удался (S3 missing, parse error, etc.)
                        # _analyze_media возвращает None с установленным skip_reason в случае ошибок
                        # Извлекаем причину из skip_reason (устанавливается в _analyze_media)
                        skip_reason = getattr(self, '_last_skip_reason', 'analysis_failed')
                        skip_details = getattr(self, '_last_skip_details', {
                            "s3_key": media_file.s3_key,
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes
                        })
                        
                        skipped_reasons.append({
                            "media_id": media_id,
                            "reason": skip_reason,
                            "details": skip_details
                        })
                        vision_media_total.labels(result="skipped" if skip_reason in ["s3_missing", "s3_forbidden"] else "failed", reason=skip_reason).inc()
                        
                        # Очистка временных атрибутов
                        if hasattr(self, '_last_skip_reason'):
                            delattr(self, '_last_skip_reason')
                        if hasattr(self, '_last_skip_details'):
                            delattr(self, '_last_skip_details')
                    
                except Exception as e:
                    logger.error(
                        "Failed to process media file",
                        extra={
                            "post_id": post_id,
                            "sha256": media_id,
                            "error": str(e),
                            "trace_id": trace_id
                        },
                        exc_info=True
                    )
                    skipped_reasons.append({
                        "media_id": media_id,
                        "reason": "exception",
                        "details": {"error": str(e)[:200]}
                    })
                    vision_media_total.labels(result="failed", reason="exception").inc()
                    vision_analysis_errors_total.labels(error_type="exception").inc()
                    continue
            
            # Context7: Всегда эмитить событие (analyzed или skipped)
            # Правильный порядок: XADD → только затем XACK (в вызывающем коде)
            event_emitted = False
            
            if analysis_results:
                # Сохранение результатов в БД (может быть пропущено для невалидных UUID)
                try:
                    await self._save_to_db(post_id, analysis_results, trace_id, event.media_files)
                except Exception as db_error:
                    # Context7: Если сохранение в БД не удалось (например, невалидный UUID),
                    # логируем но продолжаем обработку - событие все равно должно быть эмитировано
                    error_str = str(db_error).lower()
                    if 'invalid uuid' in error_str or 'invalid input for query argument' in error_str:
                        logger.warning(
                            "DB save skipped due to invalid post_id, but emitting event",
                            extra={
                                "post_id": post_id,
                                "error": str(db_error),
                                "trace_id": trace_id
                            }
                        )
                    else:
                        # Другие ошибки - логируем как warning, но не прерываем обработку
                        logger.warning(
                            "DB save failed, but continuing with event emission",
                            extra={
                                "post_id": post_id,
                                "error": str(db_error),
                                "trace_id": trace_id
                            }
                        )
                
                # Context7: Вычисление длительности анализа для события
                analysis_duration = time.time() - start_time
                analysis_duration_ms = int(analysis_duration * 1000)
                
                # Эмиссия analyzed события (всегда, даже если БД save пропущен)
                event_emitted = await self._emit_analyzed_event(
                    post_id, tenant_id, event.media_files, analysis_results, trace_id, analysis_duration_ms
                )
            
            # Context7: Эмиссия skipped события, если ничего не проанализировано
            # По feature-флагу FEATURE_VISION_EMIT_SKIPPED_EVENTS (по умолчанию true)
            emit_skipped = os.getenv("FEATURE_VISION_EMIT_SKIPPED_EVENTS", "true").lower() == "true"
            
            if not event_emitted and (skipped_reasons or not analysis_results):
                if emit_skipped:
                    event_emitted = await self._emit_skipped_event(
                        post_id, tenant_id, event.media_files, skipped_reasons, trace_id
                    )
                else:
                    logger.warning(
                        "All media files skipped, but skipped events disabled",
                        extra={
                            "post_id": post_id,
                            "media_count": len(event.media_files),
                            "skipped_count": len(skipped_reasons),
                            "trace_id": trace_id
                        }
                    )
            
            duration = time.time() - start_time
            vision_event_duration_seconds.observe(duration)
            
            if event_emitted:
                vision_worker_duration_seconds.labels(status="success").observe(duration)
                vision_worker_processed_total.labels(status="success", reason="completed").inc()
                self.processed_count += 1
                
                # Context7: Учет альбомов в метриках
                if is_album:
                    vision_albums_processed_total.labels(status="success").inc()
                
                logger.info(
                    "Vision event processed",
                    extra={
                        "post_id": post_id,
                        "media_count": len(event.media_files),
                        "analyzed_count": len(analysis_results),
                        "skipped_count": len(skipped_reasons),
                        "duration_ms": int(duration * 1000),
                        "trace_id": trace_id
                    }
                )
            else:
                vision_worker_duration_seconds.labels(status="error").observe(duration)
                vision_worker_processed_total.labels(status="error", reason="emit_failed").inc()
                vision_analysis_errors_total.labels(error_type="emit_failed").inc()
                if is_album:
                    vision_albums_processed_total.labels(status="failed").inc()
                logger.error(
                    "Failed to emit vision event (analyzed or skipped)",
                    extra={
                        "post_id": post_id,
                        "trace_id": trace_id
                    }
                )
            
            return event_emitted
            
        except Exception as e:
            duration = time.time() - start_time
            vision_worker_duration_seconds.labels(status="error").observe(duration)
            vision_worker_processed_total.labels(status="error", reason="exception").inc()
            vision_analysis_errors_total.labels(error_type="exception").inc()
            self.error_count += 1
            logger.error(
                "Failed to process vision event",
                extra={
                    "message_id": message_id,
                    "error": str(e),
                    "trace_id": fields.get("trace_id") if isinstance(fields, dict) else None
                },
                exc_info=True
            )
            # Возвращаем False, чтобы не делать ACK при ошибке
            return False
    
    async def _analyze_media(
        self,
        media_file: MediaFile,
        tenant_id: str,
        post_id: str,
        trace_id: str,
        analysis_prompt: Optional[str] = None,
        preprocess_enabled: Optional[bool] = None,
        roi_crop_enabled: Optional[bool] = None,
        max_output_tokens_override: Optional[int] = None,
        phash_enabled: Optional[bool] = None,
        experiment_variants: Optional[Dict[str, str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Анализ медиа через GigaChat Vision API.
        Context7: S3 префлайт (HEAD/stat) + корректная семантика ошибок.
        
        Workflow:
        1. Префлайт: HEAD объект через SDK (404 → skipped, 5xx → retry)
        2. Загрузка из S3 с экспоненциальным backoff (1s → 3s → 10s)
        3. Vision анализ с обработкой poison-pattern (невалидный JSON)
        4. Сохранение результатов
        """
        try:
            should_preprocess = (
                self.vision_adapter.preprocess_enabled if preprocess_enabled is None else preprocess_enabled
            )
            roi_enabled = (
                self.vision_adapter.roi_crop_enabled if roi_crop_enabled is None else roi_crop_enabled
            )
            effective_max_tokens = max_output_tokens_override
            phash_active = self.phash_enabled if phash_enabled is None else phash_enabled
            experiment_context = experiment_variants or {}

            # Context7: S3 префлайт - проверка существования объекта перед скачиванием
            # Это позволяет правильно категоризировать ошибки и избежать лишних скачиваний
            max_retries = 3
            retry_delays = [1, 3, 10]  # Экспоненциальный backoff: 1s → 3s → 10s
            
            # Префлайт: HEAD объект
            s3_object_metadata = None
            s3_error_category = None
            
            for attempt in range(max_retries):
                try:
                    s3_object_metadata = await self.s3_service.head_object(media_file.s3_key)
                    if s3_object_metadata:
                        s3_error_category = None  # Сбрасываем категорию при успехе
                        break  # Объект существует
                    else:
                        # head_object вернул None - объект не существует
                        s3_error_category = 's3_missing'
                        self._last_skip_reason = 's3_missing'
                        self._last_skip_details = {
                            "s3_key": media_file.s3_key,
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes,
                            "error_code": "NoSuchKey",
                            "http_status": 404
                        }
                        vision_media_total.labels(result="skipped", reason="s3_missing").inc()
                        logger.warning(
                            "S3 object not found (head_object returned None), skipping vision analysis",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "post_id": post_id,
                                "trace_id": trace_id,
                                "reason": "s3_missing"
                            }
                        )
                        return None  # Не retry для missing
                except ClientError as e:
                    error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                    http_status = e.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
                    
                    # Context7: Категоризация ошибок
                    if error_code == '404' or error_code == 'NoSuchKey' or http_status == 404:
                        # 404 → skipped (warning, не error) - файл может быть удален или еще не загружен
                        s3_error_category = 's3_missing'
                        vision_media_total.labels(result="skipped", reason="s3_missing").inc()
                        logger.warning(
                            "S3 object not found (404), skipping vision analysis",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "post_id": post_id,
                                "trace_id": trace_id,
                                "attempt": attempt + 1,
                                "reason": "s3_missing"
                            }
                        )
                        # Сохраняем причину пропуска для использования в _process_event
                        self._last_skip_reason = 's3_missing'
                        self._last_skip_details = {
                            "s3_key": media_file.s3_key,
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes,
                            "error_code": error_code,
                            "http_status": http_status
                        }
                        return None  # Не retry для 404
                    elif error_code == '403' or http_status == 403:
                        # 403 → skipped (forbidden)
                        s3_error_category = 's3_forbidden'
                        vision_media_total.labels(result="skipped", reason="s3_forbidden").inc()
                        logger.warning(
                            "S3 object forbidden (403), skipping vision analysis",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "post_id": post_id,
                                "trace_id": trace_id,
                                "reason": "s3_forbidden"
                            }
                        )
                        # Сохраняем причину пропуска
                        self._last_skip_reason = 's3_forbidden'
                        self._last_skip_details = {
                            "s3_key": media_file.s3_key,
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes,
                            "error_code": error_code,
                            "http_status": http_status
                        }
                        return None  # Не retry для 403
                    elif http_status >= 500 or error_code in ['InternalError', 'ServiceUnavailable', 'SlowDown']:
                        # 5xx → retry с экспоненциальным backoff
                        s3_error_category = 's3_server_error'
                        if attempt < max_retries - 1:
                            retry_delay = retry_delays[attempt]
                            vision_retries_total.labels(stage="s3").inc()
                            logger.warning(
                                "S3 server error, retrying",
                                extra={
                                    "s3_key": media_file.s3_key,
                                    "sha256": media_file.sha256[:16] + "...",
                                    "attempt": attempt + 1,
                                    "max_retries": max_retries,
                                    "error_code": error_code,
                                    "http_status": http_status,
                                    "retry_delay": retry_delay,
                                    "trace_id": trace_id
                                }
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            # Превышен лимит retries для 5xx
                            logger.error(
                                "S3 server error after retries",
                                extra={
                                    "s3_key": media_file.s3_key,
                                    "sha256": media_file.sha256[:16] + "...",
                                    "attempts": max_retries,
                                    "error_code": error_code,
                                    "http_status": http_status,
                                    "trace_id": trace_id
                                }
                            )
                            return None
                    else:
                        # Другие ошибки (например, timeout)
                        s3_error_category = 's3_timeout' if 'timeout' in str(e).lower() else 's3_error'
                        if attempt < max_retries - 1:
                            retry_delay = retry_delays[attempt]
                            vision_retries_total.labels(stage="s3").inc()
                            logger.warning(
                                "S3 error, retrying",
                                extra={
                                    "s3_key": media_file.s3_key,
                                    "sha256": media_file.sha256[:16] + "...",
                                    "attempt": attempt + 1,
                                    "error_code": error_code,
                                    "error": str(e)[:200],
                                    "retry_delay": retry_delay,
                                    "trace_id": trace_id
                                }
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            logger.error(
                                "S3 error after retries",
                                extra={
                                    "s3_key": media_file.s3_key,
                                    "sha256": media_file.sha256[:16] + "...",
                                    "attempts": max_retries,
                                    "error_code": error_code,
                                    "error": str(e)[:200],
                                    "trace_id": trace_id
                                }
                            )
                            return None
                except Exception as e:
                    # Не ClientError - другие исключения
                    if attempt < max_retries - 1:
                        retry_delay = retry_delays[attempt]
                        vision_retries_total.labels(stage="s3").inc()
                        logger.warning(
                            "S3 exception, retrying",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "attempt": attempt + 1,
                                "error": str(e)[:200],
                                "retry_delay": retry_delay,
                                "trace_id": trace_id
                            }
                        )
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        logger.error(
                            "S3 exception after retries",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "attempts": max_retries,
                                "error": str(e)[:200],
                                "trace_id": trace_id
                            }
                        )
                        return None
            
            # Если префлайт не прошел после retries, пропускаем
            if not s3_object_metadata:
                # Сохраняем причину пропуска (s3_error_category уже установлен при ошибках)
                if s3_error_category == 's3_missing':
                    self._last_skip_reason = 's3_missing'
                elif s3_error_category == 's3_forbidden':
                    self._last_skip_reason = 's3_forbidden'
                else:
                    # Если s3_error_category не установлен (другие ошибки), используем generic
                    self._last_skip_reason = 's3_error'
                
                if not hasattr(self, '_last_skip_details'):
                    self._last_skip_details = {
                        "s3_key": media_file.s3_key,
                        "mime_type": media_file.mime_type,
                        "size_bytes": media_file.size_bytes,
                        "error_category": s3_error_category or "unknown"
                    }
                return None
            
            # Context7: Загрузка файла (уже проверили через HEAD, но возможны race conditions)
            file_content = None
            for attempt in range(max_retries):
                try:
                    file_content = await self.s3_service.get_object(media_file.s3_key)
                    if file_content:
                        break
                except ClientError as e:
                    error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                    http_status = e.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
                    
                    if error_code == '404' or http_status == 404:
                        # Race condition: файл удален между HEAD и GET
                        logger.warning(
                            "S3 object deleted between HEAD and GET (404)",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "post_id": post_id,
                                "trace_id": trace_id
                            }
                        )
                        # Сохраняем причину пропуска
                        self._last_skip_reason = 's3_missing'
                        self._last_skip_details = {
                            "s3_key": media_file.s3_key,
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes,
                            "error_code": error_code,
                            "http_status": http_status,
                            "race_condition": True
                        }
                        return None
                    elif attempt < max_retries - 1:
                        retry_delay = retry_delays[attempt]
                        vision_retries_total.labels(stage="s3").inc()
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        logger.error(
                            "S3 GET error after retries",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "error_code": error_code,
                                "trace_id": trace_id
                            }
                        )
                        return None
                except Exception as e:
                    if attempt < max_retries - 1:
                        retry_delay = retry_delays[attempt]
                        vision_retries_total.labels(stage="s3").inc()
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        logger.error(
                            "S3 GET exception after retries",
                            extra={
                                "s3_key": media_file.s3_key,
                                "sha256": media_file.sha256[:16] + "...",
                                "error": str(e)[:200],
                                "trace_id": trace_id
                            }
                        )
                        return None
            
            if not file_content:
                logger.warning(
                    "File not found in S3 after HEAD check (race condition)",
                    extra={
                        "s3_key": media_file.s3_key,
                        "sha256": media_file.sha256[:16] + "...",
                        "post_id": post_id,
                        "trace_id": trace_id
                    }
                )
                # Сохраняем причину пропуска
                if not hasattr(self, '_last_skip_reason'):
                    self._last_skip_reason = 's3_missing'
                    self._last_skip_details = {
                        "s3_key": media_file.s3_key,
                        "mime_type": media_file.mime_type,
                        "size_bytes": media_file.size_bytes,
                        "race_condition": True
                    }
                return None
            
            cache_key = None
            phash_result: Optional[PhashResult] = None
            if (
                phash_active
                and media_file.mime_type
                and media_file.mime_type.lower().startswith("image/")
            ):
                phash_result = self._compute_phash_safe(
                    file_content=file_content,
                    sha256=media_file.sha256,
                    trace_id=trace_id,
                )

            if phash_result:
                cached_result, cache_meta = await self._get_phash_cached_result(
                    tenant_id=tenant_id,
                    phash_hex=phash_result.hash_hex,
                    trace_id=trace_id,
                )
                if cached_result:
                    cache_key = cache_meta.get("cache_key") if cache_meta else None
                    source = cache_meta.get("source") if cache_meta else "phash_cache"
                    metric_label = cache_meta.get("metric_label") if cache_meta else "phash_cache"
                    self._attach_phash_to_result(
                        cached_result,
                        phash_hex=phash_result.hash_hex,
                        cache_key=cache_key,
                        source=source,
                        hit=True,
                    )
                    vision_media_total.labels(result="ok", reason="phash_cache").inc()
                    try:
                        vision_cache_hits_total.labels(cache_type=metric_label).inc()
                    except Exception:
                        vision_cache_hits_total.labels(cache_type="phash_cache").inc()
                    await self._mark_media_as_processed(post_id, media_file.sha256)
                    logger.info(
                        "Vision analysis skipped via phash cache hit",
                        extra={
                            "post_id": post_id,
                            "sha256": media_file.sha256[:16] + "...",
                            "phash": phash_result.hash_hex,
                            "cache_source": source,
                            "trace_id": trace_id,
                        },
                    )
                    return self._attach_experiment_context(cached_result, experiment_context)

            # Context7: Vision анализ с обработкой poison-pattern (невалидный JSON)
            # Лимит ретраев для parse errors: VISION_API_MAX_RETRIES=3
            vision_api_max_retries = int(os.getenv("VISION_API_MAX_RETRIES", "3"))
            vision_api_parse_errors = 0
            vision_api_invalid_json_errors = 0
            
            analysis_result = None
            
            for vision_attempt in range(vision_api_max_retries):
                try:
                    analysis_result = await self.vision_adapter.analyze_media(
                        sha256=media_file.sha256,
                        file_content=file_content,
                        mime_type=media_file.mime_type,
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                        analysis_prompt=analysis_prompt,
                        preprocess_enabled=should_preprocess,
                        roi_crop_enabled=roi_enabled,
                        max_output_tokens_override=effective_max_tokens
                    )
                    
                    if analysis_result:
                        break  # Успешно
                        
                except json.JSONDecodeError as e:
                    # Parse error - может быть временной проблемой
                    vision_api_parse_errors += 1
                    vision_retries_total.labels(stage="vision_api").inc()
                    
                    if vision_attempt < vision_api_max_retries - 1:
                        logger.warning(
                            "Vision API parse error, retrying",
                            extra={
                                "sha256": media_file.sha256[:16] + "...",
                                "attempt": vision_attempt + 1,
                                "max_retries": vision_api_max_retries,
                                "error": str(e)[:200],
                                "trace_id": trace_id
                            }
                        )
                        await asyncio.sleep(1 * (vision_attempt + 1))  # Экспоненциальный backoff
                        continue
                    else:
                        # Превышен лимит ретраев для parse errors
                        logger.error(
                            "Vision API parse error after retries",
                            extra={
                                "sha256": media_file.sha256[:16] + "...",
                                "attempts": vision_api_max_retries,
                                "error": str(e)[:200],
                                "trace_id": trace_id
                            }
                        )
                        return None
                        
                except Exception as e:
                    error_str = str(e).lower()
                    
                    # Проверка на стабильно невалидный JSON (poison-pattern)
                    if 'invalid json' in error_str or 'json decode' in error_str or 'malformed json' in error_str:
                        vision_api_invalid_json_errors += 1
                        vision_retries_total.labels(stage="vision_api").inc()
                        
                        if vision_api_invalid_json_errors >= 2:
                            # Стабильно невалидный JSON → DLQ (provider_invalid_response)
                            logger.error(
                                "Stable invalid JSON from Vision API (poison-pattern)",
                                extra={
                                    "sha256": media_file.sha256[:16] + "...",
                                    "attempt": vision_attempt + 1,
                                    "error": str(e)[:200],
                                    "trace_id": trace_id
                                }
                            )
                            return None
                        elif vision_attempt < vision_api_max_retries - 1:
                            await asyncio.sleep(1 * (vision_attempt + 1))
                            continue
                    
                    # Другие ошибки - логируем и пробуем retry
                    if vision_attempt < vision_api_max_retries - 1:
                        vision_retries_total.labels(stage="vision_api").inc()
                        logger.warning(
                            "Vision API error, retrying",
                            extra={
                                "sha256": media_file.sha256[:16] + "...",
                                "attempt": vision_attempt + 1,
                                "error": str(e)[:200],
                                "error_type": type(e).__name__,
                                "trace_id": trace_id
                            }
                        )
                        await asyncio.sleep(1 * (vision_attempt + 1))
                        continue
                    else:
                        logger.error(
                            "Vision API error after retries",
                            extra={
                                "sha256": media_file.sha256[:16] + "...",
                                "attempts": vision_api_max_retries,
                                "error": str(e)[:200],
                                "error_type": type(e).__name__,
                                "trace_id": trace_id
                            }
                        )
                        return None
            
            if not analysis_result:
                # Context7: Fallback на OpenRouter Vision при ошибках GigaChat
                # Если GigaChat Vision API не смог проанализировать медиа, пробуем OpenRouter Vision
                if self.ocr_fallback and hasattr(self.ocr_fallback, 'analyze_image'):
                    logger.info(
                        "GigaChat Vision failed after retries, falling back to OpenRouter Vision",
                        extra={
                            "sha256": media_file.sha256[:16] + "...",
                            "post_id": post_id,
                            "parse_errors": vision_api_parse_errors,
                            "invalid_json_errors": vision_api_invalid_json_errors,
                            "trace_id": trace_id
                        }
                    )
                    try:
                        fallback_result = await self._process_with_ocr(
                            media_file, tenant_id, post_id, trace_id, mode="fallback"
                        )
                        if fallback_result:
                            if phash_result:
                                self._attach_phash_to_result(
                                    fallback_result,
                                    phash_hex=phash_result.hash_hex,
                                    cache_key=None,
                                    source="fallback",
                                    hit=False,
                                )
                                await self._store_phash_cache(
                                    tenant_id=tenant_id,
                                    phash_hex=phash_result.hash_hex,
                                    result=fallback_result,
                                    cache_key=None,
                                )
                            logger.info(
                                "OpenRouter Vision fallback succeeded",
                                extra={
                                    "sha256": media_file.sha256[:16] + "...",
                                    "post_id": post_id,
                                    "trace_id": trace_id
                                }
                            )
                            return self._attach_experiment_context(fallback_result, experiment_context)
                    except Exception as fallback_error:
                        logger.warning(
                            "OpenRouter Vision fallback also failed",
                            extra={
                                "sha256": media_file.sha256[:16] + "...",
                                "post_id": post_id,
                                "error": str(fallback_error),
                                "trace_id": trace_id
                            }
                        )
                
                logger.error(
                    "Vision analysis failed after retries and fallback",
                    extra={
                        "sha256": media_file.sha256[:16] + "...",
                        "post_id": post_id,
                        "trace_id": trace_id,
                        "parse_errors": vision_api_parse_errors,
                        "invalid_json_errors": vision_api_invalid_json_errors,
                        "fallback_available": bool(self.ocr_fallback and hasattr(self.ocr_fallback, 'analyze_image'))
                    }
                )
                return None
            
            final_result = {
                "sha256": media_file.sha256,
                "s3_key": media_file.s3_key,
                "analysis": analysis_result,
            }

            if phash_result:
                self._attach_phash_to_result(
                    final_result,
                    phash_hex=phash_result.hash_hex,
                    cache_key=cache_key,
                    source="fresh",
                    hit=False,
                )
                await self._store_phash_cache(
                    tenant_id=tenant_id,
                    phash_hex=phash_result.hash_hex,
                    result=final_result,
                    cache_key=cache_key,
                )

            final_result = self._attach_experiment_context(final_result, experiment_context)
            return final_result
            
        except Exception as e:
            # Context7: Fallback на OpenRouter Vision при исключениях GigaChat Vision API
            # Если произошло исключение при анализе через GigaChat, пробуем OpenRouter Vision
            logger.warning(
                "GigaChat Vision analysis exception, trying OpenRouter Vision fallback",
                extra={
                    "sha256": media_file.sha256[:16] + "..." if media_file.sha256 else None,
                    "post_id": post_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "trace_id": trace_id
                }
            )
            
            if self.ocr_fallback and hasattr(self.ocr_fallback, 'analyze_image'):
                try:
                    fallback_result = await self._process_with_ocr(
                        media_file, tenant_id, post_id, trace_id, mode="fallback"
                    )
                    if fallback_result:
                        if phash_result:
                            self._attach_phash_to_result(
                                fallback_result,
                                phash_hex=phash_result.hash_hex,
                                cache_key=None,
                                source="fallback",
                                hit=False,
                            )
                            await self._store_phash_cache(
                                tenant_id=tenant_id,
                                phash_hex=phash_result.hash_hex,
                                result=fallback_result,
                                cache_key=None,
                            )
                        logger.info(
                            "OpenRouter Vision fallback succeeded after GigaChat exception",
                            extra={
                                "sha256": media_file.sha256[:16] + "..." if media_file.sha256 else None,
                                "post_id": post_id,
                                "trace_id": trace_id
                            }
                        )
                        return self._attach_experiment_context(fallback_result, experiment_context)
                except Exception as fallback_error:
                    logger.warning(
                        "OpenRouter Vision fallback also failed after GigaChat exception",
                        extra={
                            "sha256": media_file.sha256[:16] + "..." if media_file.sha256 else None,
                            "post_id": post_id,
                            "gigachat_error": str(e),
                            "fallback_error": str(fallback_error),
                            "trace_id": trace_id
                        }
                    )
            
            logger.error(
                "Vision analysis failed with exception",
                extra={
                    "sha256": media_file.sha256[:16] + "..." if media_file.sha256 else None,
                    "post_id": post_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "trace_id": trace_id,
                    "fallback_available": bool(self.ocr_fallback and hasattr(self.ocr_fallback, 'analyze_image'))
                },
                exc_info=True
            )
            return None
    
    async def _process_with_ocr(
        self,
        media_file: MediaFile,
        tenant_id: str,
        post_id: str,
        trace_id: str,
        mode: str = "fallback"
    ) -> Optional[Dict[str, Any]]:
        """
        Обработка медиа через OCR fallback (OpenRouter Vision).
        
        Context7: Использует OpenRouter Vision API с моделью qwen/qwen2.5-vl-32b-instruct:free
        для полноценного Vision анализа вместо простого OCR.
        """
        if not self.ocr_fallback:
            return None
        
        ocr_engine = getattr(self.ocr_fallback, 'engine', 'unknown')
        provider_name = 'paddleocr' if ocr_engine == 'paddle' else 'ocr_fallback'
        ocr_start_time = time.time()
        try:
            # Загрузка из S3
            file_content = await self.s3_service.get_object(media_file.s3_key)
            if not file_content:
                logger.warning("File not found in S3 for OCR", s3_key=media_file.s3_key)
                return None
            
            # Context7: Используем новый метод analyze_image для полноценного Vision анализа
            # Если доступен метод analyze_image (OpenRouter), используем его
            if hasattr(self.ocr_fallback, 'analyze_image'):
                analysis_result = await self.ocr_fallback.analyze_image(
                    image_bytes=file_content,
                    mime_type=media_file.mime_type or "image/jpeg",
                    tenant_id=tenant_id,
                    trace_id=trace_id
                )
                
                if analysis_result:
                    # Context7: Формируем результат в формате совместимом с GigaChatVisionAdapter
                    ocr_data = analysis_result.get("ocr")
                    ocr_text = ocr_data.get("text") if ocr_data and isinstance(ocr_data, dict) else None
                    
                    classification = analysis_result.get("classification", {})
                    if isinstance(classification, dict):
                        classification_type = classification.get("type", "other")
                        labels = classification.get("tags", [])
                    else:
                        classification_type = classification if classification else "other"
                        labels = analysis_result.get("labels", [])
                    
                    duration = time.time() - ocr_start_time
                    ocr_local_latency_seconds.labels(engine=ocr_engine, mode=mode).observe(duration)
                    logger.info(
                        "Local OCR analysis completed",
                        engine=ocr_engine,
                        mode=mode,
                        duration_ms=int(duration * 1000),
                        sha256=media_file.sha256[:16] + "..." if media_file.sha256 else None,
                        trace_id=trace_id,
                    )

                    return {
                        "sha256": media_file.sha256,
                        "s3_key": media_file.s3_key,
                        "analysis": {
                            "provider": provider_name,
                            "model": analysis_result.get("model", "qwen/qwen2.5-vl-32b-instruct:free"),
                            "classification": classification_type,
                            "labels": labels,
                            "description": analysis_result.get("description"),
                            "is_meme": analysis_result.get("is_meme", False),
                            "objects": analysis_result.get("objects", []),
                            "scene": analysis_result.get("scene"),
                            "ocr": ocr_data,  # Полный OCR объект или None
                            "ocr_text": ocr_text,  # Для обратной совместимости
                            "context": analysis_result.get("context", {}),
                            "tokens_used": analysis_result.get("tokens_used", 0),
                            "file_id": None,
                        }
                    }
            
            # Fallback на старый метод extract_text + classify_content_type
            # (для обратной совместимости с локальными OCR библиотеками)
            ocr_text = await self.ocr_fallback.extract_text(file_content)
            
            # Context7: Логирование результата OCR для отладки
            if ocr_text and ocr_text.strip():
                logger.debug(
                    "OCR text extracted successfully",
                    sha256=media_file.sha256[:16] + "...",
                    text_length=len(ocr_text),
                    text_preview=ocr_text[:100] if len(ocr_text) > 100 else ocr_text,
                    post_id=post_id,
                    trace_id=trace_id
                )
            else:
                logger.warning(
                    "OCR text is empty or whitespace only",
                    sha256=media_file.sha256[:16] + "...",
                    s3_key=media_file.s3_key,
                    engine=self.ocr_fallback.engine,
                    post_id=post_id,
                    trace_id=trace_id
                )
            
            duration = time.time() - ocr_start_time
            ocr_local_latency_seconds.labels(engine=ocr_engine, mode=mode).observe(duration)
            logger.info(
                "Local OCR extraction completed",
                engine=ocr_engine,
                mode=mode,
                duration_ms=int(duration * 1000),
                text_length=len(ocr_text or ""),
                sha256=media_file.sha256[:16] + "..." if media_file.sha256 else None,
                trace_id=trace_id,
            )

            # Классификация по тексту
            classification = await self.ocr_fallback.classify_content_type(ocr_text)
            
            return {
                "sha256": media_file.sha256,
                "s3_key": media_file.s3_key,
                "analysis": {
                    "provider": provider_name,
                    "model": ocr_engine,
                    "classification": classification,
                    "ocr_text": ocr_text if ocr_text and ocr_text.strip() else None,  # Context7: Сохраняем None вместо пустой строки
                    "description": None,
                    "is_meme": classification.get("is_meme", False),
                    "context": {},
                    "tokens_used": 0,
                    "file_id": None,
                }
            }
            
        except Exception as e:
            duration = time.time() - ocr_start_time
            ocr_local_latency_seconds.labels(engine=ocr_engine, mode=mode).observe(duration)
            logger.error(
                "OCR fallback failed",
                sha256=media_file.sha256,
                error=str(e),
                error_type=type(e).__name__,
                trace_id=trace_id
            )
            return None
    
    def _extract_ocr_data(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Context7: Извлечение OCR данных из результата анализа.
        Поддерживает оба формата: ocr_text (legacy) и ocr объект (новый формат).
        
        Args:
            result: Результат анализа из GigaChat Vision API или ocr_fallback
            
        Returns:
            OCR объект с полями {text, engine, confidence} или None
        """
        ocr_data = None
        
        # Формат 1: ocr_text как строка (legacy, используется ocr_fallback)
        ocr_text = result.get("ocr_text")
        if ocr_text is not None:
            # Context7: Проверяем, что текст не пустой и не только пробелы
            ocr_text_stripped = str(ocr_text).strip() if ocr_text else ""
            if ocr_text_stripped and len(ocr_text_stripped) > 0:
                ocr_data = {
                    "text": ocr_text_stripped,
                    "engine": "openrouter" if result.get("provider") == "ocr_fallback" else "gigachat",
                    "confidence": None
                }
        elif result.get("ocr"):
            # Формат 2: ocr как объект (новый формат, используется GigaChat)
            ocr_obj = result.get("ocr")
            if isinstance(ocr_obj, dict):
                ocr_text_obj = ocr_obj.get("text")
                # Context7: Проверяем, что текст не пустой и не только пробелы
                if ocr_text_obj is not None:
                    ocr_text_stripped = str(ocr_text_obj).strip() if ocr_text_obj else ""
                    if ocr_text_stripped and len(ocr_text_stripped) > 0:
                        ocr_data = {
                            "text": ocr_text_stripped,
                            "engine": ocr_obj.get("engine", "gigachat"),
                            "confidence": ocr_obj.get("confidence")
                        }
                        # Если engine не указан, определяем по provider
                        if not ocr_data.get("engine"):
                            ocr_data["engine"] = "openrouter" if result.get("provider") == "ocr_fallback" else "gigachat"
            elif isinstance(ocr_obj, str):
                # Если ocr как строка (fallback)
                ocr_text_stripped = ocr_obj.strip() if ocr_obj else ""
                if ocr_text_stripped and len(ocr_text_stripped) > 0:
                    ocr_data = {
                        "text": ocr_text_stripped,
                        "engine": "gigachat",
                        "confidence": None
                    }
        
        # Возвращаем OCR только если есть валидный текст
        return ocr_data if (ocr_data and ocr_data.get("text") and len(ocr_data.get("text", "").strip()) > 0) else None
    
    async def _save_to_db(
        self,
        post_id: str,
        analysis_results: List[Dict[str, Any]],
        trace_id: str,
        media_files: Optional[List[MediaFile]] = None
    ):
        """
        Context7: Сохранение результатов Vision анализа в БД через EnrichmentRepository.
        Использует единую модель с kind='vision' и структурированное JSONB поле data.
        """
        # Context7: Импорт shared репозитория с правильной обработкой путей
        import sys
        import os
        import uuid
        
        try:
            # Попытка 1: Прямой импорт (если пакет установлен через pip install -e)
            from shared.repositories.enrichment_repository import EnrichmentRepository
        except ImportError:
            # Попытка 2: Добавление пути из worker контейнера
            shared_paths = [
                '/app/shared/python',  # Production путь в контейнере (из Dockerfile)
                os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared', 'python')),  # Dev путь от worker/tasks
                os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python')),  # Альтернативный путь
            ]
            
            for shared_path in shared_paths:
                if os.path.exists(shared_path) and shared_path not in sys.path:
                    sys.path.insert(0, shared_path)
                    logger.debug(f"Added shared path to sys.path: {shared_path}")
            
            try:
                from shared.repositories.enrichment_repository import EnrichmentRepository
            except ImportError as e:
                logger.error(f"Failed to import EnrichmentRepository: {e}", extra={
                    "sys_path": sys.path[:5],
                    "shared_paths_tried": shared_paths
                })
                raise
        
        try:
            if not analysis_results:
                return
            
            # Context7: Нормализация post_id для тестовых событий (non-UUID)
            # Используем тот же подход и namespace, что и в EnrichmentTask для консистентности
            def normalize_post_id(raw: str) -> str:
                """Детерминированное приведение post_id к UUID для DEV режима."""
                try:
                    # Проверка, является ли строка валидным UUID
                    uuid.UUID(raw)
                    return raw  # Уже валидный UUID
                except (ValueError, TypeError):
                    # Для тестовых post_id (например, "p-test-1001") конвертируем в UUID5
                    # Context7: Используем тот же namespace, что и в EnrichmentTask для консистентности
                    NAMESPACE = uuid.UUID("11111111-1111-1111-1111-111111111111")  # зафиксировано в .env/константах
                    normalized_uuid = uuid.uuid5(NAMESPACE, raw)
                    return str(normalized_uuid)
            
            # Нормализация post_id если включен feature flag
            original_post_id = post_id
            if os.getenv("FEATURE_ALLOW_NON_UUID_IDS", "false").lower() == "true":
                post_id = normalize_post_id(post_id)
                if original_post_id != post_id:
                    logger.debug(
                        "Post ID normalized for DB",
                        extra={
                            "original": original_post_id,
                            "normalized": post_id,
                            "trace_id": trace_id
                        }
                    )
            
            # Context7: Агрегация результатов от нескольких медиа
            first_result = analysis_results[0]["analysis"]
            analyzed_at = datetime.now(timezone.utc)
            
            # Context7: Для ocr_fallback provider, проверяем наличие ocr_text в результатах
            # ocr_fallback возвращает ocr_text в analysis["ocr_text"], но может быть пустым
            provider = first_result.get("provider", "unknown")
            
            # Структурируем данные для JSONB поля data с использованием VisionEnrichment структуры
            # Извлекаем classification type из classification dict или напрямую
            classification = first_result.get("classification")
            if isinstance(classification, dict):
                classification_type = classification.get("type", "other")
                labels_from_classification = classification.get("tags", [])
            else:
                classification_type = classification if classification else "other"
                labels_from_classification = []
            
            # Формируем s3_keys для vision_data
            s3_keys_dict = {}
            s3_keys_list = []
            for r in analysis_results:
                sha256 = r.get("sha256")
                s3_key = r.get("s3_key")
                if sha256 and s3_key:
                    s3_keys_dict["image"] = s3_key  # Основное изображение
                    s3_keys_list.append({
                        "sha256": sha256,
                        "s3_key": s3_key,
                        "analyzed_at": analyzed_at.isoformat()
                    })
            
            # Используем новую структуру VisionEnrichment с обязательными полями
            # OCR данные - Context7: Поддержка двух форматов (ocr_text и ocr объект)
            # Сохраняем None вместо пустого объекта, если OCR отсутствует
            # ВАЖНО: Для ocr_fallback provider, ocr_text находится в first_result["ocr_text"]
            ocr_extracted = self._extract_ocr_data(first_result)
            # Context7: Проверяем, что OCR текст не пустой и не только пробелы
            ocr_value = None
            if ocr_extracted and ocr_extracted.get("text"):
                ocr_text = ocr_extracted.get("text", "").strip()
                if ocr_text and len(ocr_text) > 0:
                    ocr_value = ocr_extracted
                    logger.debug(
                        "OCR value set for vision_data",
                        post_id=post_id,
                        ocr_text_length=len(ocr_text),
                        ocr_engine=ocr_extracted.get("engine"),
                        provider=first_result.get("provider"),
                        trace_id=trace_id
                    )
                else:
                    logger.debug(
                        "OCR text is empty after strip, setting ocr_value to None",
                        post_id=post_id,
                        provider=first_result.get("provider"),
                        ocr_extracted_keys=list(ocr_extracted.keys()) if ocr_extracted else None,
                        trace_id=trace_id
                    )
            else:
                logger.debug(
                    "No OCR data extracted, setting ocr_value to None",
                    post_id=post_id,
                    provider=first_result.get("provider"),
                    has_ocr_text=bool(first_result.get("ocr_text")),
                    has_ocr_obj=bool(first_result.get("ocr")),
                    trace_id=trace_id
                )
            
            vision_data = {
                "model": first_result.get("model", "unknown"),
                "model_version": None,  # TODO: добавить версию модели если доступна
                "provider": first_result.get("provider", "unknown"),
                "analyzed_at": analyzed_at.isoformat(),
                # Обязательные поля VisionEnrichment
                "classification": classification_type,
                "description": first_result.get("description") or "Изображение без описания",
                "is_meme": first_result.get("is_meme", False),
                # Labels из classification.tags или напрямую из labels
                "labels": labels_from_classification or first_result.get("labels", []),
                # Objects и scene
                "objects": first_result.get("objects", []),
                "scene": first_result.get("scene"),
                # Context7: OCR данные - ВАЖНО: сохраняем даже если None, чтобы поле было в JSON
                "ocr": ocr_value,  # Может быть None, dict или отсутствовать - все варианты валидны
                # NSFW и aesthetic scores
                "nsfw_score": first_result.get("nsfw_score"),
                "aesthetic_score": first_result.get("aesthetic_score"),
                # Dominant colors
                "dominant_colors": first_result.get("dominant_colors", []),
                # Context (emotions, themes, relationships)
                "context": first_result.get("context", {}),
                # S3 keys (новый формат)
                "s3_keys": s3_keys_dict,
                # Legacy поля для обратной совместимости
                "file_id": first_result.get("file_id"),
                "tokens_used": first_result.get("tokens_used", 0),
                "cost_microunits": first_result.get("cost_microunits", 0),
                "analysis_reason": first_result.get("analysis_reason", "new"),
                # Legacy s3_keys как список для обратной совместимости
                "s3_keys_list": s3_keys_list
            }
            
            # Context7: Вычисляем params_hash для идемпотентности
            repo = EnrichmentRepository(self.db)
            model_name = first_result.get("model") or "unknown"
            provider_name = first_result.get("provider") or "unknown"
            params_hash = repo.compute_params_hash(
                model=model_name,
                version=None,  # TODO: добавить версию
                inputs={"provider": provider_name}
            )
            
            # Context7: Логируем вычисление params_hash для диагностики
            logger.debug(
                "Computed params_hash for vision enrichment",
                post_id=post_id,
                model=model_name,
                provider=provider_name,
                params_hash=params_hash,
                trace_id=trace_id
            )
            
            # Context7: Валидация данных перед сохранением
            try:
                from shared.schemas.enrichment_validation import validate_vision_enrichment
                validated_data = validate_vision_enrichment(vision_data)
                # Конвертируем обратно в dict для сохранения
                vision_data = validated_data.model_dump(exclude_none=False)
                logger.debug(
                    "Vision enrichment data validated successfully",
                    post_id=post_id,
                    trace_id=trace_id
                )
            except Exception as validation_error:
                # Context7: Валидация не критична - логируем но продолжаем
                logger.warning(
                    "Vision enrichment validation failed, continuing without validation",
                    post_id=post_id,
                    error=str(validation_error),
                    error_type=type(validation_error).__name__,
                    trace_id=trace_id
                )
                # Продолжаем с оригинальными данными
            
            # Context7: Используем единый репозиторий для upsert
            # Логируем перед сохранением для диагностики OCR
            logger.debug(
                "Saving vision enrichment to DB",
                post_id=post_id,
                provider=first_result.get("provider", "unknown"),
                has_ocr=bool(ocr_value),
                ocr_text_length=len(ocr_value.get("text", "")) if ocr_value else 0,
                ocr_engine=ocr_value.get("engine") if ocr_value else None,
                trace_id=trace_id
            )
            
            await repo.upsert_enrichment(
                post_id=post_id,
                kind='vision',
                provider=first_result.get("provider", "unknown"),
                data=vision_data,
                params_hash=params_hash,
                status='ok',
                error=None,
                trace_id=trace_id
            )
            
            # Context7: Логируем после сохранения для подтверждения
            logger.debug(
                "Vision results saved to DB via EnrichmentRepository",
                post_id=post_id,
                provider=first_result.get("provider", "unknown"),
                has_ocr=bool(ocr_value),
                ocr_text_length=len(ocr_value.get("text", "")) if ocr_value else 0,
                trace_id=trace_id
            )
            
            # Context7: Синхронизация Vision результатов в Neo4j
            if self.neo4j_client and analysis_results:
                try:
                    # Context7: Создаем mapping sha256 -> media_file для извлечения mime_type
                    media_file_map = {}
                    if media_files:
                        media_file_map = {mf.sha256: mf for mf in media_files if hasattr(mf, 'sha256') and hasattr(mf, 'mime_type')}
                    
                    for result in analysis_results:
                        analysis = result.get("analysis", {})
                        sha256 = result.get("sha256")
                        s3_key = result.get("s3_key")
                        
                        # Context7: Извлекаем mime_type из media_file
                        mime_type = None
                        if sha256 and sha256 in media_file_map:
                            mime_type = media_file_map[sha256].mime_type
                        
                        if sha256:
                            await self.neo4j_client.create_image_content_node(
                                post_id=post_id,
                                sha256=sha256,
                                s3_key=s3_key,
                                mime_type=mime_type,
                                vision_classification=analysis.get("classification"),
                                is_meme=analysis.get("is_meme"),
                                provider=analysis.get("provider"),
                                trace_id=trace_id
                            )
                    
                    logger.debug("Vision results synced to Neo4j", post_id=post_id, trace_id=trace_id)
                except Exception as e:
                    # Не критичная ошибка - логируем но не прерываем
                    logger.warning("Failed to sync vision results to Neo4j", 
                                 post_id=post_id,
                                 error=str(e),
                                 trace_id=trace_id)
            
        except Exception as e:
            # Context7: Обработка ошибок UUID - если post_id невалидный UUID, логируем и пропускаем
            error_str = str(e).lower()
            if 'invalid uuid' in error_str or 'invalid input for query argument' in error_str:
                logger.warning(
                    "Invalid post_id format (not UUID), skipping DB save",
                    extra={
                        "post_id": post_id,
                        "error": str(e),
                        "trace_id": trace_id,
                        "suggestion": "Check FEATURE_ALLOW_NON_UUID_IDS env var or use valid UUID post_id"
                    }
                )
                # Не падаем, просто пропускаем сохранение в БД
                # Событие будет эмитировано в вызывающем коде
                return  # Выходим без ошибки
            
            # Context7: Другие ошибки БД - логируем и пробрасываем
            # Вызывающий код обработает ошибку и все равно эмитит событие
            if hasattr(self, 'db') and self.db:
                try:
                    await self.db.rollback()
                except Exception:
                    pass  # Игнорируем ошибки rollback
            
            logger.error(
                "Failed to save vision results to DB",
                extra={
                    "post_id": post_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "trace_id": trace_id
                },
                exc_info=True
            )
            raise
    
    async def _emit_analyzed_event(
        self,
        post_id: str,
        tenant_id: str,
        media_files: List[MediaFile],
        analysis_results: List[Dict[str, Any]],
        trace_id: str,
        analysis_duration_ms: int = 0
    ) -> bool:
        """
        Эмиссия события stream:posts:vision:analyzed.
        
        Context7: Возвращает True если событие успешно эмитировано, False в случае ошибки.
        Правильный порядок: XADD → только затем XACK (в вызывающем коде).
        """
        try:
            # Строим VisionAnalyzedEventV1
            if not analysis_results:
                logger.warning(
                    "No analysis results to emit",
                    extra={"post_id": post_id, "trace_id": trace_id}
                )
                return False
            
            first_analysis = analysis_results[0]["analysis"]
            classification_value = first_analysis.get("classification", {})
            if isinstance(classification_value, str):
                classification_value = {"label": classification_value}

            from events.schemas.posts_vision_v1 import VisionAnalysisResult
            
            vision_result = VisionAnalysisResult(
                provider=first_analysis.get("provider", "gigachat"),
                model=first_analysis.get("model", "GigaChat-Pro"),
                classification=classification_value,
                description=first_analysis.get("description"),
                ocr_text=first_analysis.get("ocr_text"),
                is_meme=first_analysis.get("is_meme", False),
                context=first_analysis.get("context", {}),
                tokens_used=first_analysis.get("tokens_used", 0),
                file_id=first_analysis.get("file_id"),
                analyzed_at=datetime.now(timezone.utc)
            )
            
            analyzed_event = VisionAnalyzedEventV1(
                tenant_id=tenant_id,
                post_id=post_id,
                media=media_files,
                vision=vision_result.model_dump(),
                analysis_duration_ms=analysis_duration_ms,
                idempotency_key=VisionAnalyzedEventV1.build_dedupe_key(
                    tenant_id, post_id, media_files[0].sha256
                ),
                trace_id=trace_id
            )
            
            # Context7: Детерминированный idempotency key для защиты от дублей
            event_idempotency_key = analyzed_event.idempotency_key
            
            # Отправка в Redis Stream (XADD)
            event_json = analyzed_event.model_dump_json()
            message_id = await self.redis.xadd(
                "stream:posts:vision:analyzed",
                {
                    "event": "posts.vision.analyzed",
                    "data": event_json,
                    "idempotency_key": event_idempotency_key  # Для downstream дедупа
                }
            )
            
            logger.info(
                "Vision analyzed event emitted",
                extra={
                    "post_id": post_id,
                    "trace_id": trace_id,
                    "message_id": message_id,
                    "idempotency_key": event_idempotency_key
                }
            )
            return True
            
        except Exception as e:
            logger.error(
                "Failed to emit vision analyzed event",
                extra={
                    "post_id": post_id,
                    "trace_id": trace_id,
                    "error": str(e)
                },
                exc_info=True
            )
            return False
    
    async def _emit_skipped_event(
        self,
        post_id: str,
        tenant_id: str,
        media_files: List[MediaFile],
        skipped_reasons: List[Dict[str, Any]],
        trace_id: str
    ) -> bool:
        """
        Context7: Эмиссия skipped события - "ничего не проанализировано" как бизнес-факт.
        
        Payload vision_skipped:
        {
            post_id, tenant_id, trace_id,
            reasons: [{media_id, reason, details}],
            occurred_at, idempotency_key
        }
        
        Эмитируется в stream:posts:vision:analyzed с маркером skipped=true.
        
        Returns:
            bool: True если событие успешно эмитировано, False в случае ошибки
        """
        try:
            # Context7: Создаем skipped событие как структурированный JSON
            skipped_event_data = {
                "event_type": "posts.vision.skipped",
                "schema_version": "1.0",
                "producer": "vision-worker",
                "tenant_id": tenant_id,
                "post_id": post_id,
                "trace_id": trace_id,
                "reasons": skipped_reasons,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                # Детерминированный idempotency key
                "idempotency_key": f"{tenant_id}:{post_id}:vision_skipped:{hashlib.sha256(','.join([r.get('media_id', '') for r in skipped_reasons]).encode()).hexdigest()[:16]}"
            }
            
            # Эмиссия в stream:posts:vision:analyzed с маркером skipped=true
            event_json = json.dumps(skipped_event_data, default=str)
            message_id = await self.redis.xadd(
                "stream:posts:vision:analyzed",
                {
                    "event": "posts.vision.skipped",
                    "data": event_json,
                    "skipped": "true",  # Маркер для downstream consumers
                    "idempotency_key": skipped_event_data["idempotency_key"]
                }
            )
            
            logger.info(
                "Vision skipped event emitted",
                extra={
                    "post_id": post_id,
                    "trace_id": trace_id,
                    "message_id": message_id,
                    "skipped_count": len(skipped_reasons),
                    "idempotency_key": skipped_event_data["idempotency_key"]
                }
            )
            
            vision_events_total.labels(status="skipped", reason="all_media_skipped").inc()
            return True
            
        except Exception as e:
            logger.error(
                "Failed to emit vision skipped event",
                extra={
                    "post_id": post_id,
                    "trace_id": trace_id,
                    "error": str(e)
                },
                exc_info=True
            )
            return False
    
    async def _check_idempotency(self, dedupe_keys: List[str]) -> bool:
        """
        Проверка идемпотентности через Redis (legacy метод для совместимости).
        Context7: Используйте _check_media_idempotency для медиа-уровня.
        """
        for key in dedupe_keys:
            cache_key = f"vision_processed:{key}"
            exists = await self.redis.exists(cache_key)
            if exists:
                return True
        return False
    
    async def _check_media_idempotency(
        self,
        tenant_id: str,
        post_id: str,
        media_files: List[MediaFile]
    ) -> Dict[str, bool]:
        """
        Context7: Проверка идемпотентности на медиа-уровне.
        
        Ключ идемпотентности: vision:processed:<post_id>:<sha256>
        Для альбомов (grouped_id) - отдельный ключ на каждый медиа файл.
        
        Returns:
            Dict[str, bool]: {sha256: bool} - True если медиа уже обработано
        """
        result = {}
        for media_file in media_files:
            sha256 = media_file.sha256
            cache_key = f"vision:processed:{post_id}:{sha256}"
            exists = await self.redis.exists(cache_key)
            result[sha256] = bool(exists)
        return result
    
    async def _mark_as_processed(self, dedupe_keys: List[str], ttl_hours: int = 24):
        """
        Отметка события как обработанного (legacy метод для совместимости).
        Context7: Используйте _mark_media_as_processed для медиа-уровня.
        """
        for key in dedupe_keys:
            cache_key = f"vision_processed:{key}"
            await self.redis.setex(cache_key, ttl_hours * 3600, "1")
    
    async def _mark_media_as_processed(
        self,
        post_id: str,
        sha256: str,
        ttl_hours: int = 24
    ):
        """
        Context7: Отметка медиа файла как обработанного.
        
        Ключ: vision:processed:<post_id>:<sha256>
        TTL: 24 часа по умолчанию (можно переопределить через ENV)
        """
        cache_key = f"vision:processed:{post_id}:{sha256}"
        await self.redis.setex(cache_key, ttl_hours * 3600, "1")
    
    def _get_experiment_variants(self, tenant_id: str) -> Dict[str, str]:
        if not tenant_id or not self.experiment_manager:
            return {}
        variants = self.experiment_manager.assign_all(tenant_id)
        for name, variant in variants.items():
            vision_experiment_assignments_total.labels(
                experiment=name,
                variant=variant
            ).inc()
        return variants

    def _attach_experiment_context(
        self,
        result: Optional[Dict[str, Any]],
        experiment_variants: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        if not result or not experiment_variants:
            return result
        analysis_block = result.get("analysis") if isinstance(result, dict) else None
        if isinstance(analysis_block, dict):
            context = analysis_block.setdefault("context", {})
            context["experiments"] = experiment_variants
        return result

    async def _enqueue_low_priority_event(
        self,
        event: VisionUploadedEventV1,
        media_file: MediaFile,
        reason: str,
        trace_id: str,
        retry_count: int,
        original_message_id: Optional[str]
    ) -> bool:
        if not self.low_priority_enabled:
            return False
        try:
            payload = event.model_dump(mode="json")
            payload["media_files"] = [media_file.model_dump(mode="json")]
            next_retry = retry_count + 1
            payload["idempotency_key"] = f"{event.idempotency_key}:lp:{media_file.sha256[:12]}:{next_retry}"
            payload["producer"] = "vision-worker"
            payload["occurred_at"] = datetime.now(timezone.utc).isoformat()
            not_before_dt = datetime.now(timezone.utc) + timedelta(seconds=self.low_priority_retry_delay_seconds * next_retry)

            message_fields = {
                "event": payload.get("event_type", "posts.vision.uploaded"),
                "data": json.dumps(payload, ensure_ascii=False),
                "priority": "low",
                "reason": reason,
                "retry_count": str(next_retry),
                "sha256": media_file.sha256,
                "not_before": not_before_dt.isoformat(),
            }
            if original_message_id:
                message_fields["original_message_id"] = original_message_id

            await self.redis.xadd(
                self.low_priority_stream_name,
                message_fields,
                maxlen=self.low_priority_stream_maxlen,
                approximate=True
            )
            vision_low_priority_enqueued_total.labels(reason=reason).inc()
            logger.info(
                "Enqueued media to low priority vision queue",
                extra={
                    "post_id": event.post_id,
                    "sha256": media_file.sha256[:16] + "...",
                    "reason": reason,
                    "retry_count": next_retry,
                    "stream": self.low_priority_stream_name,
                    "trace_id": trace_id
                }
            )
            return True
        except Exception as exc:
            vision_low_priority_enqueued_total.labels(reason="error").inc()
            logger.error(
                "Failed to enqueue low priority vision media",
                extra={
                    "post_id": event.post_id,
                    "sha256": media_file.sha256[:16] + "...",
                    "reason": reason,
                    "retry_count": retry_count,
                    "trace_id": trace_id,
                    "error": str(exc)
                },
                exc_info=True
            )
            return False

    async def _process_low_priority_queue(self) -> None:
        if not self.low_priority_enabled:
            return

        start_id = '0' if not self._low_priority_backlog_processed else '>'
        block_ms = 1

        try:
            messages = await self.redis.xreadgroup(
                self.low_priority_consumer_group,
                self.consumer_name,
                {self.low_priority_stream_name: start_id},
                count=5,
                block=block_ms
            )
        except redis.ResponseError as e:
            if "NOGROUP" in str(e):
                try:
                    await self.redis.xgroup_create(
                        self.low_priority_stream_name,
                        self.low_priority_consumer_group,
                        id='0',
                        mkstream=True
                    )
                except redis.ResponseError:
                    pass
            else:
                logger.debug(
                    "Low priority queue read error",
                    extra={
                        "stream": self.low_priority_stream_name,
                        "error": str(e)
                    }
                )
            return

        if not messages:
            if start_id == '0':
                self._low_priority_backlog_processed = True
            return

        for _, stream_messages in messages:
            for message_id, fields in stream_messages:
                message_id_str = message_id.decode() if isinstance(message_id, bytes) else message_id
                try:
                    not_before_value = self._extract_field_value(fields, "not_before")
                    if not_before_value:
                        try:
                            not_before_dt = datetime.fromisoformat(not_before_value)
                        except ValueError:
                            not_before_dt = None
                        if not_before_dt:
                            now = datetime.now(timezone.utc)
                            delay = (not_before_dt - now).total_seconds()
                            if delay > 0:
                                await asyncio.sleep(min(delay, self.low_priority_retry_delay_seconds))

                    processed = await self._process_event(message_id_str, fields)
                    if processed:
                        await self.redis.xack(self.low_priority_stream_name, self.low_priority_consumer_group, message_id)
                        vision_low_priority_processed_total.labels(status="processed").inc()
                    else:
                        vision_low_priority_processed_total.labels(status="pending").inc()
                except Exception as exc:
                    vision_low_priority_processed_total.labels(status="error").inc()
                    logger.error(
                        "Error processing low priority vision message",
                        extra={
                            "message_id": message_id_str,
                            "error": str(exc)
                        },
                        exc_info=True
                    )
                    await self._handle_error(message_id_str, fields, exc)

        if start_id == '0':
            self._low_priority_backlog_processed = False

    def _compute_phash_safe(
        self,
        file_content: bytes,
        sha256: Optional[str],
        trace_id: str,
    ) -> Optional[PhashResult]:
        try:
            return compute_phash(file_content, hash_size=self.phash_hash_size)
        except Exception as exc:
            logger.debug(
                "Failed to compute phash",
                extra={
                    "sha256": (sha256[:16] + "...") if sha256 else None,
                    "trace_id": trace_id,
                    "error": str(exc),
                },
            )
            return None

    def _attach_phash_to_result(
        self,
        result: Dict[str, Any],
        phash_hex: str,
        cache_key: Optional[str],
        source: str,
        hit: bool,
    ) -> None:
        analysis = result.setdefault("analysis", {})
        context = analysis.setdefault("context", {})
        context["phash"] = phash_hex
        context["phash_hash_size"] = self.phash_hash_size
        context["phash_source"] = source
        context["phash_hit"] = hit
        if cache_key:
            context["phash_cache_key"] = cache_key

    async def _store_phash_cache(
        self,
        tenant_id: str,
        phash_hex: str,
        result: Dict[str, Any],
        cache_key: Optional[str],
    ) -> None:
        if not self.phash_enabled:
            return
        redis_key = self._build_phash_redis_key(tenant_id, phash_hex)
        payload: Dict[str, Any] = {
            "sha256": result.get("sha256"),
            "cache_key": cache_key,
            "hash_size": self.phash_hash_size,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        if not cache_key:
            payload["analysis_result"] = result
        try:
            await self.redis.setex(
                redis_key,
                self.phash_cache_ttl_seconds,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            logger.debug(
                "Failed to store phash cache",
                extra={
                    "tenant_id": tenant_id,
                    "phash": phash_hex,
                    "error": str(exc),
                },
            )

    async def _get_phash_cached_result(
        self,
        tenant_id: str,
        phash_hex: str,
        trace_id: str,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        if not self.phash_enabled:
            return None, {}

        redis_key = self._build_phash_redis_key(tenant_id, phash_hex)
        try:
            cached_value = await self.redis.get(redis_key)
        except Exception as exc:
            logger.debug(
                "Failed to read phash cache",
                extra={
                    "tenant_id": tenant_id,
                    "phash": phash_hex,
                    "error": str(exc),
                },
            )
            cached_value = None

        if cached_value:
            try:
                payload = json.loads(cached_value)
                cache_key = payload.get("cache_key")
                if cache_key and self.s3_service:
                    try:
                        cached_analysis = await self.s3_service.get_json(cache_key)
                        if cached_analysis:
                            return (
                                {
                                    "sha256": payload.get("sha256"),
                                    "s3_key": cache_key,
                                    "analysis": cached_analysis,
                                },
                                {"source": "redis", "cache_key": cache_key, "metric_label": "phash_redis"},
                            )
                    except Exception as exc:
                        logger.debug(
                            "Failed to load phash cache payload from S3",
                            extra={
                                "cache_key": cache_key,
                                "phash": phash_hex,
                                "error": str(exc),
                                "trace_id": trace_id,
                            },
                        )
                analysis_result = payload.get("analysis_result")
                if analysis_result:
                    return analysis_result, {"source": "redis", "cache_key": analysis_result.get("s3_key"), "metric_label": "phash_redis"}
            except Exception as exc:
                logger.debug(
                    "Failed to deserialize phash cache payload",
                    extra={
                        "phash": phash_hex,
                        "error": str(exc),
                        "trace_id": trace_id,
                    },
                )

        try:
            result = await self.db.execute(
                text(
                    """
                    SELECT data
                    FROM post_enrichment
                    WHERE kind = 'vision' AND (data->'context'->>'phash') = :phash
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ),
                {"phash": phash_hex},
            )
            row = result.fetchone()
        except Exception as exc:
            logger.debug(
                "Failed to fetch phash cache from Postgres",
                extra={
                    "phash": phash_hex,
                    "error": str(exc),
                    "trace_id": trace_id,
                },
            )
            row = None

        if row:
            vision_data = row[0]
            if isinstance(vision_data, str):
                try:
                    vision_data = json.loads(vision_data)
                except Exception:
                    vision_data = {}
            context = vision_data.get("context") if isinstance(vision_data, dict) else None
            cache_key = context.get("phash_cache_key") if isinstance(context, dict) else None
            if cache_key and self.s3_service:
                try:
                    cached_analysis = await self.s3_service.get_json(cache_key)
                    if cached_analysis:
                        result_dict = {
                            "sha256": None,
                            "s3_key": cache_key,
                            "analysis": cached_analysis,
                        }
                        await self._store_phash_cache(
                            tenant_id=tenant_id,
                            phash_hex=phash_hex,
                            result=result_dict,
                            cache_key=cache_key,
                        )
                        return result_dict, {"source": "postgres", "cache_key": cache_key, "metric_label": "phash_pg"}
                except Exception as exc:
                    logger.debug(
                        "Failed to load phash PG cache from S3",
                        extra={
                            "cache_key": cache_key,
                            "phash": phash_hex,
                            "error": str(exc),
                            "trace_id": trace_id,
                        },
                    )
            if isinstance(vision_data, dict):
                result_dict = {
                    "sha256": None,
                    "s3_key": cache_key,
                    "analysis": vision_data,
                }
                await self._store_phash_cache(
                    tenant_id=tenant_id,
                    phash_hex=phash_hex,
                    result=result_dict,
                    cache_key=cache_key,
                )
                return result_dict, {"source": "postgres", "cache_key": cache_key, "metric_label": "phash_pg"}

        return None, {}

    def _build_phash_redis_key(self, tenant_id: str, phash_hex: str) -> str:
        return f"{self.phash_redis_prefix}:{tenant_id}:{phash_hex}"

    def _extract_field_value(self, fields: Dict[str, Any], key: str) -> Optional[str]:
        if not isinstance(fields, dict):
            return None
        value = fields.get(key)
        if value is None:
            value = fields.get(key.encode())
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8')
            except UnicodeDecodeError:
                return None
        return str(value)

    async def _process_pending_messages(self) -> int:
        """
        Context7: Обработка pending сообщений через XAUTOCLAIM с лимитами доставок и DLQ.
        
        Логика:
        - XAUTOCLAIM для получения зависших сообщений из PEL
        - Проверка delivery_count (vision:deliveries:<stream_id>)
        - Если deliveries >= MAX_DELIVERIES → XCLAIM в DLQ, XACK оригинала
        - Иначе → обработка сообщения
        
        Returns:
            int: Количество обработанных pending сообщений
        """
        try:
            # XAUTOCLAIM для получения зависших сообщений
            result = await self.redis.xautoclaim(
                name=self.stream_name,
                groupname=self.consumer_group,
                consumername=self.consumer_name,
                min_idle_time=self.pel_min_idle_ms,
                start_id="0-0",
                count=self.pel_batch_size,
                justid=False
            )
            
            # xautoclaim возвращает список [next_id, messages] или [next_id, messages, other_data]
            if isinstance(result, (list, tuple)) and len(result) >= 2:
                next_id, messages = result[0], result[1]
            else:
                messages = result if isinstance(result, list) else []
                next_id = None
            
            if not messages:
                return 0
            
            processed = 0
            for msg_id, fields in messages:
                try:
                    # Context7: Получение delivery_count из Redis
                    delivery_key = f"vision:deliveries:{msg_id.decode() if isinstance(msg_id, bytes) else msg_id}"
                    delivery_count = await self.redis.incr(delivery_key)
                    await self.redis.expire(delivery_key, 86400)  # TTL 24 часа
                    
                    if delivery_count >= self.max_deliveries:
                        # Превышен лимит доставок → отправка в DLQ
                        logger.error(
                            "Message exceeded max deliveries, sending to DLQ",
                            extra={
                                "message_id": msg_id.decode() if isinstance(msg_id, bytes) else msg_id,
                                "delivery_count": delivery_count,
                                "max_deliveries": self.max_deliveries
                            }
                        )
                        
                        # Отправка в DLQ
                        await self._send_to_dlq_from_pending(msg_id, fields, delivery_count)
                        
                        # XACK оригинала для удаления из PEL
                        await self.redis.xack(
                            self.stream_name,
                            self.consumer_group,
                            msg_id
                        )
                        
                        vision_events_total.labels(status="failed", reason="max_deliveries_exceeded").inc()
                    else:
                        # Обработка сообщения
                        event_processed = await self._process_event(
                            msg_id.decode() if isinstance(msg_id, bytes) else msg_id,
                            fields
                        )
                        
                        if event_processed:
                            await self.redis.xack(self.stream_name, self.consumer_group, msg_id)
                            processed += 1
                            # Сброс delivery_count при успешной обработке
                            await self.redis.delete(delivery_key)
                        else:
                            # Не обработано - оставляем в PEL для следующей попытки
                            logger.warning(
                                "Pending message processing failed, keeping in PEL",
                                extra={
                                    "message_id": msg_id.decode() if isinstance(msg_id, bytes) else msg_id,
                                    "delivery_count": delivery_count
                                }
                            )
                        
                except Exception as e:
                    logger.error(
                        "Error processing pending message",
                        extra={
                            "message_id": str(msg_id),
                            "error": str(e),
                            "error_type": type(e).__name__
                        },
                        exc_info=True
                    )
                    # Не ACK - оставляем в PEL для повторной обработки
            
            if processed > 0:
                logger.info(
                    "Processed pending messages",
                    extra={
                        "count": processed,
                        "total_claimed": len(messages)
                    }
                )
            
            # Обновление метрик PEL
            await self._update_pel_metrics()
            
            return processed
            
        except Exception as e:
            logger.error(
                "Error in _process_pending_messages",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__
                },
                exc_info=True
            )
            return 0
    
    async def _send_to_dlq_from_pending(
        self,
        message_id: str,
        fields: Dict[str, str],
        delivery_count: int
    ):
        """
        Context7: Отправка pending сообщения в DLQ после превышения лимита доставок.
        
        В DLQ payload включается: последняя ошибка, стектрейс, delivery_count, все причины.
        """
        try:
            event_data = self._parse_event_fields(fields)
            trace_id = event_data.get("trace_id", str(message_id))
            tenant_id = event_data.get("tenant_id")
            
            # Создание ошибки с информацией о превышении лимита
            error = Exception(
                f"Message exceeded max deliveries ({delivery_count}/{self.max_deliveries})"
            )
            
            if self.dlq_service:
                await self.dlq_service.send_to_dlq(
                    base_event_type="posts.vision.uploaded",
                    payload={
                        **event_data,
                        "delivery_count": delivery_count,
                        "max_deliveries": self.max_deliveries,
                        "original_message_id": message_id.decode() if isinstance(message_id, bytes) else message_id
                    },
                    error=error,
                    retry_count=delivery_count,
                    trace_id=trace_id,
                    tenant_id=tenant_id
                )
            else:
                # Fallback: прямая отправка в DLQ stream
                dlq_stream = "stream:posts:vision:dlq"
                await self.redis.xadd(
                    dlq_stream,
                    {
                        "original_stream": self.stream_name,
                        "original_message_id": message_id.decode() if isinstance(message_id, bytes) else message_id,
                        "error": str(error),
                        "delivery_count": str(delivery_count),
                        "max_deliveries": str(self.max_deliveries),
                        "trace_id": trace_id,
                        "data": json.dumps(event_data, default=str)
                    }
                )
            
            logger.info(
                "Pending message sent to DLQ",
                extra={
                    "message_id": message_id.decode() if isinstance(message_id, bytes) else message_id,
                    "delivery_count": delivery_count,
                    "trace_id": trace_id
                }
            )
            
        except Exception as e:
            logger.error(
                "Failed to send pending message to DLQ",
                extra={
                    "message_id": str(message_id),
                    "error": str(e)
                },
                exc_info=True
            )
    
    async def _update_pel_metrics(self):
        """Context7: Обновление метрик PEL для мониторинга."""
        try:
            # Получение информации о PEL
            pending_info = await self.redis.xpending(self.stream_name, self.consumer_group)
            
            if pending_info and isinstance(pending_info, dict):
                pel_size = pending_info.get('pending', 0)
                vision_pel_size.labels(consumer_group=self.consumer_group).set(pel_size)
                
                # Получение возраста самого старого pending сообщения
                if pel_size > 0:
                    pending_messages = await self.redis.xpending_range(
                        self.stream_name,
                        self.consumer_group,
                        min="-",
                        max="+",
                        count=100  # Берем первые 100 для вычисления перцентиля
                    )
                    
                    if pending_messages:
                        # Вычисление возраста в секундах
                        import time
                        current_time_ms = int(time.time() * 1000)
                        ages = [
                            current_time_ms - msg.get('time_since_delivered', 0)
                            for msg in pending_messages
                        ]
                        ages_ms = [age for age in ages if age > 0]
                        
                        if ages_ms:
                            ages_seconds = [age / 1000.0 for age in ages_ms]
                            # 95-й перцентиль
                            ages_seconds_sorted = sorted(ages_seconds)
                            if len(ages_seconds_sorted) > 0:
                                p95_idx = int(len(ages_seconds_sorted) * 0.95)
                                p95_age = ages_seconds_sorted[min(p95_idx, len(ages_seconds_sorted) - 1)]
                                vision_pending_older_than_seconds.labels(
                                    percentile="95",
                                    consumer_group=self.consumer_group
                                ).set(p95_age)
                                
                                # 99-й перцентиль
                                p99_idx = int(len(ages_seconds_sorted) * 0.99)
                                p99_age = ages_seconds_sorted[min(p99_idx, len(ages_seconds_sorted) - 1)]
                                vision_pending_older_than_seconds.labels(
                                    percentile="99",
                                    consumer_group=self.consumer_group
                                ).set(p99_age)
                                
        except Exception as e:
            logger.debug(
                "Failed to update PEL metrics",
                extra={"error": str(e)}
            )
    
    def _parse_event_fields(self, fields: Dict[str, str]) -> Dict[str, Any]:
        """
        Парсинг полей события из Redis Stream.
        Context7: Поддержка разных форматов событий (data как JSON string, прямые поля, bytes).
        """
        # Обработка bytes ключей и значений (redis-py может возвращать bytes)
        decoded_fields = {}
        for k, v in fields.items():
            key = k.decode('utf-8') if isinstance(k, bytes) else k
            if isinstance(v, bytes):
                try:
                    # Пытаемся декодировать как UTF-8
                    decoded_fields[key] = v.decode('utf-8')
                except UnicodeDecodeError:
                    # Если не UTF-8, оставляем как bytes
                    decoded_fields[key] = v
            else:
                decoded_fields[key] = v
        
        # Поддержка разных форматов
        if "data" in decoded_fields:
            data_str = decoded_fields["data"]
            if isinstance(data_str, str):
                try:
                    return json.loads(data_str)
                except json.JSONDecodeError as e:
                    logger.error(
                        "Failed to parse JSON data field",
                        error=str(e),
                        data_preview=data_str[:200] if len(str(data_str)) > 200 else data_str
                    )
                    raise
            return data_str
        
        # Прямые поля - проверяем, что есть необходимые поля для VisionUploadedEventV1
        if "event_type" in decoded_fields or "post_id" in decoded_fields:
            return decoded_fields
        
        # Если формат неизвестен, логируем для диагностики
        logger.warning(
            "Unknown event format",
            fields_keys=list(decoded_fields.keys()),
            fields_sample={k: str(v)[:100] for k, v in list(decoded_fields.items())[:5]}
        )
        return decoded_fields
    
    async def _handle_error(
        self,
        message_id: str,
        fields: Dict[str, str],
        error: Exception
    ):
        """Обработка ошибок с отправкой в DLQ при необходимости."""
        try:
            error_category = classify_error(error)
            
            # Retryable ошибки - не отправляем в DLQ сразу
            if should_retry(error_category):
                logger.warning(
                    "Retryable error, will retry",
                    message_id=message_id,
                    error=str(error),
                    category=error_category.value
                )
                return
            
            # Non-retryable - отправляем в DLQ
            if self.dlq_service:
                event_data = self._parse_event_fields(fields)
                trace_id = event_data.get("trace_id", message_id)
                tenant_id = event_data.get("tenant_id")
                
                await self.dlq_service.send_to_dlq(
                    base_event_type="posts.vision.uploaded",
                    payload=event_data,
                    error=error,
                    retry_count=0,
                    trace_id=trace_id,
                    tenant_id=tenant_id
                )
            
        except Exception as e:
            logger.error("Failed to handle error", message_id=message_id, error=str(e))


async def create_vision_analysis_task(
    redis_url: str,
    database_url: str,
    s3_config: Dict[str, Any],
    vision_config: Dict[str, Any]
) -> VisionAnalysisTask:
    """Factory функция для создания VisionAnalysisTask."""
    # Инициализация компонентов
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session = async_session()
    
    # Context7: Создание asyncpg pool для StorageQuotaService (LRU eviction)
    import asyncpg
    db_pool = None
    try:
        # Конвертируем SQLAlchemy URL в asyncpg DSN
        dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        db_pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=5,
            command_timeout=30
        )
        logger.info("AsyncPG pool created for StorageQuotaService")
    except Exception as e:
        logger.warning("Failed to create asyncpg pool for StorageQuotaService", error=str(e))
        # Продолжаем без db_pool - LRU eviction будет работать без БД интеграции
    
    # S3 Service
    s3_service = S3StorageService(
        endpoint_url=s3_config["endpoint_url"],
        access_key_id=s3_config["access_key_id"],
        secret_access_key=s3_config["secret_access_key"],
        bucket_name=s3_config["bucket_name"],
        region=s3_config.get("region", "ru-central-1"),
        use_compression=s3_config.get("use_compression", True)
    )
    
    # Context7: StorageQuotaService с db_pool для LRU eviction
    # Проверяем наличие параметра через inspect для совместимости
    import inspect
    sig = inspect.signature(StorageQuotaService.__init__)
    init_params = {
        's3_service': s3_service,
        'limits': s3_config.get("limits", {
            "total_gb": 15.0,
            "emergency_threshold_gb": 14.0,
            "per_tenant_gb": 2.0
        })
    }
    if 'db_pool' in sig.parameters:
        init_params['db_pool'] = db_pool
    
    storage_quota = StorageQuotaService(**init_params)
    
    # Budget Gate
    budget_gate = BudgetGateService(
        redis_url=redis_url,
        max_daily_tokens_per_tenant=vision_config.get("max_daily_tokens", 250000),
        max_concurrent_requests=vision_config.get("max_concurrent", 3)
    )
    await budget_gate.start()
    
    # Vision Adapter
    vision_adapter = GigaChatVisionAdapter(
        credentials=vision_config["credentials"],
        scope=vision_config.get("scope", "GIGACHAT_API_PERS"),
        model=vision_config.get("model", "GigaChat-Pro"),
        base_url=vision_config.get("base_url"),
        s3_service=s3_service,
        budget_gate=budget_gate,
        storage_quota=storage_quota,  # Context7: Передаем для tenant usage tracking
        verify_ssl=vision_config.get("verify_ssl", False),
        timeout=vision_config.get("timeout", 600)
    )
    
    # Policy Engine
    # Context7: Правильный путь к конфигу политики (абсолютный или относительно /app)
    policy_config_path = vision_config.get("policy_config_path", "/app/config/vision_policy.yml")
    policy_engine = VisionPolicyEngine(
        policy_config_path=policy_config_path
    )
    
    # OCR Fallback (опционально)
    # Context7: Проверяем флаг из enrichment_policy.yml
    ocr_fallback = None
    ocr_fallback_enabled = vision_config.get("ocr_fallback_enabled", True)
    
    # Загружаем enrichment_policy.yml для проверки глобального флага
    try:
        import yaml
        enrichment_policy_path = os.getenv(
            "ENRICHMENT_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "..", "config", "enrichment_policy.yml")
        )
        if os.path.exists(enrichment_policy_path):
            with open(enrichment_policy_path, 'r', encoding='utf-8') as f:
                enrichment_policy = yaml.safe_load(f)
                # Флаг из enrichment_policy.yml имеет приоритет
                policy_ocr_enabled = enrichment_policy.get("enrichment", {}).get("ocr_fallback_enabled")
                if policy_ocr_enabled is not None:
                    ocr_fallback_enabled = policy_ocr_enabled
                    logger.info(
                        f"OCR fallback flag from enrichment_policy.yml: ocr_fallback_enabled={ocr_fallback_enabled}, path={enrichment_policy_path}"
                    )
    except Exception as e:
        logger.warning(
            "Failed to load enrichment_policy.yml for OCR fallback flag",
            error=str(e),
            using_default=ocr_fallback_enabled
        )
    
    ocr_engine = vision_config.get("ocr_engine", "paddle").lower()

    if ocr_fallback_enabled:
        if ocr_engine == "openrouter":
            try:
                from ai_adapters.openrouter_vision import OpenRouterVisionAdapter
                openrouter_adapter = OpenRouterVisionAdapter(
                    model=vision_config.get("openrouter_model", "qwen/qwen2.5-vl-32b-instruct:free")
                )
                ocr_fallback = OCRFallbackService(
                    engine="openrouter",
                    openrouter_adapter=openrouter_adapter
                )
                logger.info(
                    "OCR fallback initialized with OpenRouter Vision",
                    model=vision_config.get("openrouter_model", "qwen/qwen2.5-vl-32b-instruct:free")
                )
            except Exception as e:
                logger.warning(
                    "Failed to initialize OpenRouter Vision adapter, OCR fallback disabled",
                    error=str(e)
                )
                ocr_fallback = None
        elif ocr_engine == "paddle":
            ocr_fallback = OCRFallbackService(
                engine="paddle",
                languages=vision_config.get("ocr_languages", "rus+eng"),
                paddle_endpoint=vision_config.get("paddle_endpoint"),
                paddle_timeout=vision_config.get("paddle_timeout"),
            )
            logger.info(
                "OCR fallback initialized with PaddleOCR",
                endpoint=vision_config.get("paddle_endpoint")
            )
        else:
            logger.warning("Unsupported ocr_engine configured", ocr_engine=ocr_engine)
            ocr_fallback = None
    else:
        logger.info(
            "OCR fallback disabled",
            reason="ocr_fallback_enabled=false in enrichment_policy.yml"
        )
    
    # DLQ Service
    dlq_service = DLQService(redis_client) if vision_config.get("dlq_enabled", True) else None

    experiment_config_path = os.getenv(
        "VISION_EXPERIMENT_CONFIG_PATH",
        os.path.join(os.path.dirname(__file__), "..", "config", "vision_experiments.yml")
    )
    experiment_manager = VisionExperimentManager(experiment_config_path)
    
    # Vision Task
    task = VisionAnalysisTask(
        redis_client=redis_client,
        db_session=db_session,
        s3_service=s3_service,
        budget_gate=budget_gate,
        storage_quota=storage_quota,
        vision_adapter=vision_adapter,
        policy_engine=policy_engine,
        ocr_fallback=ocr_fallback,
        dlq_service=dlq_service,
        local_ocr_primary_enabled=vision_config.get("local_ocr_primary_enabled", False),
        experiment_manager=experiment_manager
    )
    
    return task

