"""
Vision Analysis Task
Context7 best practice: обработка событий stream:posts:vision с идемпотентностью, trace propagation
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update

from events.schemas import VisionUploadedEventV1, VisionAnalyzedEventV1, MediaFile
from ai_adapters.gigachat_vision import GigaChatVisionAdapter
from services.vision_policy_engine import VisionPolicyEngine
from services.budget_gate import BudgetGateService
from services.ocr_fallback import OCRFallbackService
from services.storage_quota import StorageQuotaService
from services.retry_policy import create_retry_decorator, DEFAULT_RETRY_CONFIG, DLQService, should_retry, classify_error

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
        consumer_name: str = None
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
        
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"vision-worker-{os.getenv('HOSTNAME', 'default')}"
        
        self.running = False
        self.processed_count = 0
        self.error_count = 0
        
        logger.info(
            "VisionAnalysisTask initialized",
            stream=stream_name,
            consumer_group=consumer_group,
            consumer_name=self.consumer_name
        )
    
    async def start(self):
        """Запуск Vision Analysis Task."""
        self.running = True
        
        # Создание consumer group (идемпотентно)
        try:
            await self.redis.xgroup_create(
                self.stream_name,
                self.consumer_group,
                id='0',
                mkstream=True
            )
            logger.info("Created consumer group", stream=self.stream_name, group=self.consumer_group)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        
        logger.info("VisionAnalysisTask started", stream=self.stream_name)
        
        while self.running:
            try:
                # Чтение событий из стрима
                messages = await self.redis.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    {self.stream_name: '>'},
                    count=10,
                    block=2000
                )
                
                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, fields in stream_messages:
                            try:
                                await self._process_event(message_id, fields)
                                # ACK после успешной обработки
                                await self.redis.xack(
                                    self.stream_name,
                                    self.consumer_group,
                                    message_id
                                )
                            except Exception as e:
                                await self._handle_error(message_id, fields, e)
                                # ACK даже при ошибке, чтобы не застрять
                                await self.redis.xack(
                                    self.stream_name,
                                    self.consumer_group,
                                    message_id
                                )
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("VisionAnalysisTask error", error=str(e))
                await asyncio.sleep(5)
    
    async def stop(self):
        """Остановка Vision Analysis Task."""
        self.running = False
        logger.info("VisionAnalysisTask stopped")
    
    async def _process_event(self, message_id: str, fields: Dict[str, str]):
        """
        Обработка одного события Vision Upload.
        
        Event: stream:posts:vision (VisionUploadedEventV1)
        """
        import time
        start_time = time.time()
        
        try:
            # Парсинг события
            event_data = self._parse_event_fields(fields)
            
            # Валидация через Pydantic
            try:
                event = VisionUploadedEventV1(**event_data)
            except Exception as e:
                logger.error("Failed to parse VisionUploadedEventV1", error=str(e), fields=fields)
                vision_worker_processed_total.labels(status="error", reason="parse_error").inc()
                return
            
            tenant_id = event.tenant_id
            post_id = event.post_id
            trace_id = event.trace_id
            
            # Проверка идемпотентности
            dedupe_keys = [
                VisionAnalyzedEventV1.build_dedupe_key(tenant_id, post_id, media.sha256)
                for media in event.media_files
            ]
            
            idempotent = await self._check_idempotency(dedupe_keys)
            if idempotent:
                vision_worker_idempotency_hits.inc()
                logger.debug("Event already processed (idempotency)", post_id=post_id, trace_id=trace_id)
                vision_worker_processed_total.labels(status="skipped", reason="idempotency").inc()
                return
            
            # Обработка каждого медиа файла
            analysis_results = []
            
            for media_file in event.media_files:
                try:
                    # Проверка политики Vision
                    policy_result = self.policy_engine.evaluate_media_for_vision(
                        media_file={
                            "mime_type": media_file.mime_type,
                            "size_bytes": media_file.size_bytes
                        },
                        channel_username=None,  # TODO: получить из БД
                        quota_exhausted=False  # TODO: проверить через budget_gate
                    )
                    
                    if not policy_result["allowed"] or policy_result["skip"]:
                        logger.debug(
                            "Media skipped by policy",
                            post_id=post_id,
                            sha256=media_file.sha256,
                            reason=policy_result["reason"]
                        )
                        continue
                    
                    # Проверка budget gate
                    if self.budget_gate:
                        budget_check = await self.budget_gate.check_budget(
                            tenant_id=tenant_id,
                            estimated_tokens=1792
                        )
                        if not budget_check.allowed:
                            # Fallback на OCR если разрешено
                            if policy_result["use_ocr"] and self.ocr_fallback:
                                result = await self._process_with_ocr(media_file, tenant_id, post_id, trace_id)
                                if result:
                                    analysis_results.append(result)
                            continue
                    
                    # Vision анализ через GigaChat
                    result = await self._analyze_media(
                        media_file=media_file,
                        tenant_id=tenant_id,
                        post_id=post_id,
                        trace_id=trace_id
                    )
                    
                    if result:
                        analysis_results.append(result)
                        # Отметка идемпотентности
                        dedupe_key = VisionAnalyzedEventV1.build_dedupe_key(
                            tenant_id, post_id, media_file.sha256
                        )
                        await self._mark_as_processed([dedupe_key])
                    
                except Exception as e:
                    logger.error(
                        "Failed to process media file",
                        post_id=post_id,
                        sha256=media_file.sha256,
                        error=str(e),
                        trace_id=trace_id
                    )
                    continue
            
            # Сохранение результатов в БД
            if analysis_results:
                await self._save_to_db(post_id, analysis_results, trace_id)
                
                # Эмиссия события stream:posts:vision:analyzed
                await self._emit_analyzed_event(post_id, tenant_id, event.media_files, analysis_results, trace_id)
            
            duration = time.time() - start_time
            vision_worker_duration_seconds.labels(status="success").observe(duration)
            vision_worker_processed_total.labels(status="success", reason="completed").inc()
            self.processed_count += 1
            
            logger.info(
                "Vision event processed",
                post_id=post_id,
                media_count=len(event.media_files),
                analyzed_count=len(analysis_results),
                duration_ms=int(duration * 1000),
                trace_id=trace_id
            )
            
        except Exception as e:
            duration = time.time() - start_time
            vision_worker_duration_seconds.labels(status="error").observe(duration)
            vision_worker_processed_total.labels(status="error", reason="exception").inc()
            self.error_count += 1
            logger.error("Failed to process vision event", message_id=message_id, error=str(e))
            raise
    
    async def _analyze_media(
        self,
        media_file: MediaFile,
        tenant_id: str,
        post_id: str,
        trace_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Анализ медиа через GigaChat Vision API.
        
        Workflow:
        1. Загрузка из S3
        2. Vision анализ
        3. Сохранение результатов
        """
        try:
            # Загрузка файла из S3
            file_content = await self.s3_service.get_object(media_file.s3_key)
            if not file_content:
                logger.warning("File not found in S3", s3_key=media_file.s3_key, sha256=media_file.sha256)
                return None
            
            # Vision анализ
            analysis_result = await self.vision_adapter.analyze_media(
                sha256=media_file.sha256,
                file_content=file_content,
                mime_type=media_file.mime_type,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return {
                "sha256": media_file.sha256,
                "s3_key": media_file.s3_key,
                "analysis": analysis_result,
            }
            
        except Exception as e:
            logger.error(
                "Vision analysis failed",
                sha256=media_file.sha256,
                error=str(e),
                trace_id=trace_id
            )
            return None
    
    async def _process_with_ocr(
        self,
        media_file: MediaFile,
        tenant_id: str,
        post_id: str,
        trace_id: str
    ) -> Optional[Dict[str, Any]]:
        """Обработка медиа через OCR fallback."""
        if not self.ocr_fallback:
            return None
        
        try:
            # Загрузка из S3
            file_content = await self.s3_service.get_object(media_file.s3_key)
            if not file_content:
                logger.warning("File not found in S3 for OCR", s3_key=media_file.s3_key)
                return None
            
            # OCR извлечение текста
            ocr_text = await self.ocr_fallback.extract_text(file_content)
            
            # Классификация по тексту
            classification = await self.ocr_fallback.classify_content_type(ocr_text)
            
            return {
                "sha256": media_file.sha256,
                "s3_key": media_file.s3_key,
                "analysis": {
                    "provider": "ocr_fallback",
                    "model": self.ocr_fallback.engine,
                    "classification": classification,
                    "ocr_text": ocr_text,
                    "description": None,
                    "is_meme": classification.get("is_meme", False),
                    "context": {},
                    "tokens_used": 0,
                    "file_id": None,
                }
            }
            
        except Exception as e:
            logger.error("OCR fallback failed", sha256=media_file.sha256, error=str(e))
            return None
    
    async def _save_to_db(
        self,
        post_id: str,
        analysis_results: List[Dict[str, Any]],
        trace_id: str
    ):
        """
        Context7: Сохранение результатов Vision анализа в БД через EnrichmentRepository.
        Использует единую модель с kind='vision' и структурированное JSONB поле data.
        """
        # Context7: Импорт shared репозитория
        import sys
        import os
        shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
        if shared_path not in sys.path:
            sys.path.insert(0, shared_path)
        
        from shared.repositories.enrichment_repository import EnrichmentRepository
        
        try:
            if not analysis_results:
                return
            
            # Context7: Агрегация результатов от нескольких медиа
            first_result = analysis_results[0]["analysis"]
            analyzed_at = datetime.now(timezone.utc)
            
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
                # OCR данные
                "ocr": {
                    "text": first_result.get("ocr_text"),
                    "engine": "tesseract" if first_result.get("provider") == "ocr_fallback" else "gigachat",
                    "confidence": first_result.get("ocr", {}).get("confidence") if isinstance(first_result.get("ocr"), dict) else None
                } if first_result.get("ocr_text") or first_result.get("ocr") else None,
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
            
            # Вычисляем params_hash для идемпотентности
            repo = EnrichmentRepository(self.db)
            params_hash = repo.compute_params_hash(
                model=first_result.get("model"),
                version=None,  # TODO: добавить версию
                inputs={"provider": first_result.get("provider")}
            )
            
            # Используем единый репозиторий для upsert
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
            
            logger.debug("Vision results saved to DB via EnrichmentRepository", post_id=post_id, trace_id=trace_id)
            
            # Context7: Синхронизация Vision результатов в Neo4j
            if self.neo4j_client and analysis_results:
                try:
                    for result in analysis_results:
                        analysis = result.get("analysis", {})
                        sha256 = result.get("sha256")
                        s3_key = result.get("s3_key")
                        
                        if sha256:
                            await self.neo4j_client.create_image_content_node(
                                post_id=post_id,
                                sha256=sha256,
                                s3_key=s3_key,
                                mime_type=None,  # Можно извлечь из media_file
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
            await self.db.rollback()
            logger.error("Failed to save vision results to DB", post_id=post_id, error=str(e))
            raise
    
    async def _emit_analyzed_event(
        self,
        post_id: str,
        tenant_id: str,
        media_files: List[MediaFile],
        analysis_results: List[Dict[str, Any]],
        trace_id: str
    ):
        """Эмиссия события stream:posts:vision:analyzed."""
        try:
            # Строим VisionAnalyzedEventV1
            if not analysis_results:
                return
            
            first_analysis = analysis_results[0]["analysis"]
            
            from events.schemas.posts_vision_v1 import VisionAnalysisResult
            
            vision_result = VisionAnalysisResult(
                provider=first_analysis.get("provider", "gigachat"),
                model=first_analysis.get("model", "GigaChat-Pro"),
                classification=first_analysis.get("classification", {}),
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
                analysis_duration_ms=0,  # TODO: вычислить
                idempotency_key=VisionAnalyzedEventV1.build_dedupe_key(
                    tenant_id, post_id, media_files[0].sha256
                ),
                trace_id=trace_id
            )
            
            # Отправка в Redis Stream
            event_json = analyzed_event.model_dump_json()
            await self.redis.xadd(
                "stream:posts:vision:analyzed",
                {
                    "event": "posts.vision.analyzed",
                    "data": event_json
                }
            )
            
            logger.debug("Vision analyzed event emitted", post_id=post_id, trace_id=trace_id)
            
        except Exception as e:
            logger.error("Failed to emit vision analyzed event", post_id=post_id, error=str(e))
    
    async def _check_idempotency(self, dedupe_keys: List[str]) -> bool:
        """Проверка идемпотентности через Redis."""
        for key in dedupe_keys:
            cache_key = f"vision_processed:{key}"
            exists = await self.redis.exists(cache_key)
            if exists:
                return True
        return False
    
    async def _mark_as_processed(self, dedupe_keys: List[str], ttl_hours: int = 24):
        """Отметка события как обработанного."""
        for key in dedupe_keys:
            cache_key = f"vision_processed:{key}"
            await self.redis.setex(cache_key, ttl_hours * 3600, "1")
    
    def _parse_event_fields(self, fields: Dict[str, str]) -> Dict[str, Any]:
        """Парсинг полей события из Redis Stream."""
        # Поддержка разных форматов
        if "data" in fields:
            data_str = fields["data"]
            if isinstance(data_str, str):
                return json.loads(data_str)
            return data_str
        
        # Прямые поля
        return dict(fields)
    
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
    ocr_fallback = None
    if vision_config.get("ocr_fallback_enabled", True):
        ocr_fallback = OCRFallbackService(
            engine=vision_config.get("ocr_engine", "tesseract"),
            languages=vision_config.get("ocr_languages", "rus+eng")
        )
    
    # DLQ Service
    dlq_service = DLQService(redis_client) if vision_config.get("dlq_enabled", True) else None
    
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
        dlq_service=dlq_service
    )
    
    return task

