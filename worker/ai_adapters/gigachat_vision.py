"""
GigaChat Vision API Adapter
Context7 best practice: идемпотентность через SHA256, кэширование, error handling
"""

import io
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, BinaryIO

import structlog
from gigachat import GigaChat
from gigachat.exceptions import GigaChatException, AuthenticationError, ResponseError

from prometheus_client import Counter, Histogram

# Context7: Импорт S3StorageService из api (временное исключение для архитектурной границы)
# TODO: Переместить в shared-пакет в будущем
import sys
import os

# Добавляем пути для импорта api модуля (поддержка dev и production)
# Важно: добавляем РОДИТЕЛЬСКУЮ директорию, чтобы импорт "from api.services..." работал
project_root = '/opt/telegram-assistant'
if project_root not in sys.path and os.path.exists(os.path.join(project_root, 'api')):
    sys.path.insert(0, project_root)

# Fallback: относительный путь (dev)
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if parent_dir not in sys.path and os.path.exists(os.path.join(parent_dir, 'api')):
    sys.path.insert(0, parent_dir)

from api.services.s3_storage import S3StorageService
from services.budget_gate import BudgetGateService
from services.retry_policy import create_retry_decorator, DEFAULT_RETRY_CONFIG

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

vision_analysis_requests_total = Counter(
    'vision_analysis_requests_total',
    'Total vision analysis requests',
    ['status', 'provider', 'tenant_id', 'reason']
)

# Context7: Детализированные метрики Vision анализа
# provider: gigachat, ocr (fallback)
# has_ocr: true, false (есть ли OCR текст в результате)
vision_analysis_duration_seconds = Histogram(
    'vision_analysis_duration_seconds',
    'Vision analysis latency',
    ['provider', 'has_ocr']  # provider: gigachat|ocr, has_ocr: true|false
)

vision_tokens_used_total = Counter(
    'vision_tokens_used_total',
    'Total tokens used by Vision API',
    ['provider', 'tenant_id', 'model']
)

vision_file_uploads_total = Counter(
    'vision_file_uploads_total',
    'File uploads to GigaChat',
    ['status']
)

vision_cache_hits_total = Counter(
    'vision_cache_hits_total',
    'Vision cache hits',
    ['cache_type']  # s3 | redis
)

vision_parsed_total = Counter(
    'vision_parsed_total',
    'Total vision response parsing attempts',
    ['status', 'method']  # status: success|fallback|error, method: direct|bracket_extractor|partial_fallback|keyword
)


class GigaChatVisionAdapter:
    """
    GigaChat Vision API адаптер для анализа изображений и документов.
    
    Features:
    - Загрузка файлов через upload_file
    - Vision анализ через chat с attachments
    - Кэширование результатов по SHA256
    - Интеграция с S3 и Budget Gate
    - Error handling и retry логика
    """
    
    def __init__(
        self,
        credentials: str,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat-Pro",
        base_url: Optional[str] = None,
        s3_service: Optional[S3StorageService] = None,
        budget_gate: Optional[BudgetGateService] = None,
        verify_ssl: bool = False,
        timeout: int = 600,
        circuit_breaker: Optional[Any] = None
    ):
        """
        Инициализация GigaChat Vision Adapter.
        
        Args:
            credentials: GIGACHAT_CREDENTIALS (base64 encoded "client_id:client_secret")
                        или можно использовать client_id/client_secret напрямую
            scope: GigaChat scope (по умолчанию GIGACHAT_API_PERS)
            model: Модель для Vision анализа (GigaChat-Pro)
            base_url: Базовый URL GigaChat API (опционально)
            s3_service: S3 сервис для кэширования
            budget_gate: Budget Gate для контроля токенов
            verify_ssl: Проверять SSL сертификаты
            timeout: Таймаут запросов
        """
        # Context7: credentials уже в формате base64 (как в gpt2giga-proxy)
        # Библиотека gigachat принимает credentials напрямую
        self.credentials = credentials
        self.scope = scope
        
        # Context7: Поддержка динамического выбора модели через env
        # Приоритет: переданный параметр > GIGACHAT_VISION_MODEL > GIGACHAT_MODEL > default
        self.model = (
            model or 
            os.getenv('GIGACHAT_VISION_MODEL') or 
            os.getenv('GIGACHAT_MODEL') or 
            "GigaChat-Pro"
        )
        self.base_url = base_url
        self.s3_service = s3_service
        self.budget_gate = budget_gate
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        
        # Context7: Circuit breaker для защиты от каскадных сбоев
        if circuit_breaker is None:
            from shared.python.shared.utils.circuit_breaker import CircuitBreaker
            import os
            failure_threshold = int(os.getenv("GIGACHAT_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
            recovery_timeout = int(os.getenv("GIGACHAT_CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "60"))
            self.circuit_breaker = CircuitBreaker(
                name="gigachat_vision",
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                expected_exception=Exception
            )
        else:
            self.circuit_breaker = circuit_breaker
        
        # GigaChat клиент (создаётся при первом использовании)
        self._client: Optional[GigaChat] = None
        
        logger.info(
            "GigaChatVisionAdapter initialized",
            model=model,
            scope=scope,
            base_url=base_url or "default",
            s3_enabled=s3_service is not None,
            budget_gate_enabled=budget_gate is not None,
            credentials_set=bool(credentials)
        )
    
    def _get_client(self) -> GigaChat:
        """Lazy инициализация GigaChat клиента."""
        if self._client is None:
            self._client = GigaChat(
                credentials=self.credentials,
                scope=self.scope,
                base_url=self.base_url,
                verify_ssl_certs=self.verify_ssl,
                timeout=self.timeout,
                model=self.model
            )
        return self._client
    
    async def upload_file(
        self,
        file_content: bytes,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        purpose: str = "general"
    ) -> str:
        """
        Загрузка файла в GigaChat хранилище.
        
        Context7: GigaChat требует правильный filename с расширением для определения MIME типа.
        Если filename не передан, генерируется из mime_type.
        
        Args:
            file_content: Содержимое файла
            filename: Имя файла с расширением (опционально)
            mime_type: MIME тип файла (используется для генерации filename если не передан)
            purpose: Назначение файла (general)
            
        Returns:
            GigaChat file_id
        """
        try:
            client = self._get_client()
            
            # Context7: Генерируем filename из mime_type если не передан
            # GigaChat API требует правильное расширение для определения типа файла
            if not filename and mime_type:
                extension = self._get_extension_from_mime(mime_type)
                filename = f"file.{extension}"
            elif not filename:
                # Fallback: используем generic имя
                filename = "file.bin"
            
            # Создаём file-like object
            file_obj = io.BytesIO(file_content)
            file_obj.name = filename
            
            # Загрузка файла
            uploaded_file = client.upload_file(file_obj, purpose=purpose)
            
            # Context7: GigaChat библиотека возвращает id_ (с подчеркиванием), не id
            file_id = uploaded_file.id_
            
            vision_file_uploads_total.labels(status="success").inc()
            
            logger.debug(
                "File uploaded to GigaChat",
                file_id=file_id,
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(file_content)
            )
            
            return file_id
            
        except (GigaChatException, Exception) as e:
            vision_file_uploads_total.labels(status="error").inc()
            logger.error(
                "Failed to upload file to GigaChat",
                error=str(e),
                error_type=type(e).__name__,
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(file_content)
            )
            raise
    
    def _get_extension_from_mime(self, mime_type: str) -> str:
        """
        Context7: Определение расширения файла из MIME типа для GigaChat API.
        
        Args:
            mime_type: MIME тип файла
            
        Returns:
            Расширение файла (без точки)
        """
        mime_to_ext = {
            'image/jpeg': 'jpg',
            'image/jpg': 'jpg',
            'image/png': 'png',
            'image/gif': 'gif',
            'image/webp': 'webp',
            'image/tiff': 'tiff',
            'image/bmp': 'bmp',
            'application/pdf': 'pdf',
            'application/msword': 'doc',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
            'text/plain': 'txt',
            'application/epub+zip': 'epub',
            'video/mp4': 'mp4',
            'audio/mpeg': 'mp3',
            'audio/wav': 'wav',
        }
        return mime_to_ext.get(mime_type.lower(), 'bin')
    
    async def analyze_media(
        self,
        sha256: str,
        file_content: bytes,
        mime_type: str,
        tenant_id: str,
        trace_id: str,
        analysis_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Анализ медиа через GigaChat Vision API.
        
        Args:
            sha256: SHA256 хеш файла (для кэширования)
            file_content: Содержимое файла
            mime_type: MIME тип
            tenant_id: ID tenant
            trace_id: Trace ID для корреляции
            analysis_prompt: Кастомный промпт для анализа
            
        Returns:
            Vision analysis results
        """
        import time
        start_time = time.time()
        
        try:
            # Проверка кэша в S3 (если доступен)
            cache_key = None
            if self.s3_service:
                cache_key = self.s3_service.build_vision_key(
                    tenant_id=tenant_id,
                    sha256=sha256,
                    provider="gigachat",
                    model=self.model,
                    schema_version="1.0"
                )
                
                cached_result = await self.s3_service.head_object(cache_key)
                if cached_result:
                    # Context7: Загружаем из S3 кэша и возвращаем результат
                    try:
                        cached_data = await self.s3_service.get_json(cache_key)
                        if cached_data:
                            # Добавляем метаданные для метрик
                            cached_data.setdefault("provider", "gigachat")
                            cached_data.setdefault("model", self.model)
                            cached_data.setdefault("file_id", None)  # file_id не сохраняется в кэш
                            
                            vision_cache_hits_total.labels(cache_type="s3").inc()
                            logger.debug(
                                "Vision result loaded from S3 cache",
                                sha256=sha256,
                                has_ocr=bool(cached_data.get("ocr") and cached_data["ocr"].get("text")),
                                ocr_text_length=len(cached_data.get("ocr", {}).get("text", "")) if cached_data.get("ocr") else 0,
                                trace_id=trace_id
                            )
                            return cached_data
                        else:
                            logger.warning("S3 cache object exists but failed to parse JSON", sha256=sha256, cache_key=cache_key)
                    except Exception as e:
                        logger.warning(
                            "Failed to load from S3 cache, will analyze fresh",
                            sha256=sha256,
                            cache_key=cache_key,
                            error=str(e),
                            trace_id=trace_id
                        )
                    # Если загрузка из кэша не удалась, продолжаем с анализом
            
            # Проверка budget gate
            if self.budget_gate:
                budget_check = await self.budget_gate.check_budget(
                    tenant_id=tenant_id,
                    estimated_tokens=1792  # Максимум для изображения
                )
                if not budget_check.allowed:
                    vision_analysis_requests_total.labels(
                        status="blocked",
                        provider="gigachat",
                        tenant_id=tenant_id,
                        reason=budget_check.reason
                    ).inc()
                    raise Exception(f"Budget gate blocked: {budget_check.reason}")
            
            # Получение concurrent slot
            slot_acquired = False
            if self.budget_gate:
                slot_acquired = await self.budget_gate.acquire_concurrent_slot(tenant_id)
                if not slot_acquired:
                    vision_analysis_requests_total.labels(
                        status="blocked",
                        provider="gigachat",
                        tenant_id=tenant_id,
                        reason="rate_limited"
                    ).inc()
                    raise Exception("Concurrent request limit exceeded")
            
            try:
                # Context7: Загрузка файла в GigaChat с правильным filename
                # Передаем mime_type для автоматической генерации filename с расширением
                file_id = await self.upload_file(
                    file_content=file_content,
                    mime_type=mime_type
                )
                
                # Context7 best practice: Улучшенный промпт с явным запросом OCR
                # Явно указываем, что OCR обязателен, если на изображении есть текст
                if not analysis_prompt:
                    analysis_prompt = """Проанализируй изображение или документ и предоставь структурированный JSON со следующей схемой:

{
  "classification": "photo" | "meme" | "document" | "screenshot" | "infographic" | "other",
  "description": "краткое текстовое описание содержимого (минимум 5 символов)",
  "is_meme": true/false,
  "labels": ["класс1", "класс2", ...],  // максимум 20 классов/атрибутов
  "objects": ["объект1", "объект2", ...],  // максимум 10 объектов на изображении
  "scene": "описание сцены/окружения" или null,
  "ocr": {"text": "извлечённый текст из изображения", "engine": "gigachat", "confidence": 0.0-1.0} или null,
  "nsfw_score": 0.0-1.0 или null,
  "aesthetic_score": 0.0-1.0 или null,
  "dominant_colors": ["#hex1", "#hex2", ...],  // максимум 5 цветов
  "context": {"emotions": [...], "themes": [...], "relationships": [...]}
}

ВАЖНО:
- Если на изображении есть ЛЮБОЙ текст (надписи, подписи, текст в документе, скриншоты), ОБЯЗАТЕЛЬНО заполни поле "ocr" с извлечённым текстом
- Если текста нет, установи "ocr": null
- Поле "ocr.text" должно содержать ВЕСЬ видимый текст с изображения, включая мелкий текст
- Не пропускай текст даже если он частично виден или размыт

Обязательные поля: classification, description, is_meme.
Ответь ТОЛЬКО валидным JSON без дополнительного текста."""
                
                # Context7: Vision анализ через chat с attachments с circuit breaker защитой
                client = self._get_client()
                
                async def _call_gigachat_api():
                    """Внутренняя функция для вызова GigaChat API через circuit breaker."""
                    return client.chat({
                        "messages": [
                            {
                                "role": "user",
                                "content": analysis_prompt,
                                "attachments": [file_id]
                            }
                        ],
                        "temperature": 0.1,
                        "function_call": "auto"  # Для обработки файлов
                    })
                
                try:
                    response = await self.circuit_breaker.call_async(_call_gigachat_api)
                except Exception as cb_error:
                    # Context7: Circuit breaker открыт или произошла ошибка
                    # Логируем и пробрасываем исключение для обработки выше
                    logger.warning(
                        "GigaChat API call blocked by circuit breaker or failed",
                        sha256=sha256,
                        error=str(cb_error),
                        circuit_breaker_state=self.circuit_breaker.get_state(),
                        trace_id=trace_id
                    )
                    raise
                
                # Извлечение результата
                content = response.choices[0].message.content
                
                # Context7: Логируем сырой ответ для диагностики OCR
                logger.debug(
                    "GigaChat Vision API response received",
                    sha256=sha256,
                    content_length=len(content),
                    content_preview=content[:200] if content else None,
                    trace_id=trace_id
                )
                
                # Парсинг ответа (упрощённый, реальный должен парсить JSON)
                analysis_result = self._parse_vision_response(content, file_id)
                
                # Context7: Логируем результат парсинга OCR для диагностики
                has_ocr = bool(analysis_result.get("ocr") and analysis_result["ocr"].get("text"))
                logger.debug(
                    "Vision analysis result parsed",
                    sha256=sha256,
                    has_ocr=has_ocr,
                    ocr_text_length=len(analysis_result.get("ocr", {}).get("text", "")) if analysis_result.get("ocr") else 0,
                    ocr_engine=analysis_result.get("ocr", {}).get("engine") if analysis_result.get("ocr") else None,
                    trace_id=trace_id
                )
                
                # Учёт токенов
                tokens_used = getattr(response.usage, 'total_tokens', 0) if hasattr(response, 'usage') else 0
                if self.budget_gate and tokens_used > 0:
                    await self.budget_gate.record_token_usage(
                        tenant_id=tenant_id,
                        tokens_used=tokens_used,
                        provider="gigachat",
                        model=self.model
                    )
                
                # Context7: Сохранение в S3 кэш (включая OCR данные)
                if self.s3_service and cache_key:
                    try:
                        size_bytes = await self.s3_service.put_json(
                            data=analysis_result,
                            s3_key=cache_key,
                            compress=True
                        )
                        logger.debug(
                            "Vision result saved to S3 cache",
                            sha256=sha256,
                            cache_key=cache_key,
                            size_bytes=size_bytes,
                            has_ocr=bool(analysis_result.get("ocr") and analysis_result["ocr"].get("text")),
                            ocr_text_length=len(analysis_result.get("ocr", {}).get("text", "")) if analysis_result.get("ocr") else 0,
                            trace_id=trace_id
                        )
                    except Exception as e:
                        # Не критичная ошибка - логируем но продолжаем
                        logger.warning(
                            "Failed to save vision result to S3 cache",
                            sha256=sha256,
                            cache_key=cache_key,
                            error=str(e),
                            trace_id=trace_id
                        )
                
                duration = time.time() - start_time
                # Context7: Определяем has_ocr на основе результата
                has_ocr = bool(analysis_result.get("ocr_text") or (analysis_result.get("ocr") and analysis_result["ocr"].get("text")))
                vision_analysis_duration_seconds.labels(
                    provider="gigachat",
                    has_ocr=str(has_ocr).lower()
                ).observe(duration)
                
                vision_analysis_requests_total.labels(
                    status="success",
                    provider="gigachat",
                    tenant_id=tenant_id,
                    reason="new"
                ).inc()
                
                logger.info(
                    "Vision analysis completed",
                    sha256=sha256,
                    tenant_id=tenant_id,
                    tokens_used=tokens_used,
                    duration_ms=int(duration * 1000),
                    trace_id=trace_id
                )
                
                return analysis_result
                
            finally:
                # Освобождение concurrent slot
                if slot_acquired and self.budget_gate:
                    await self.budget_gate.release_concurrent_slot(tenant_id)
            
        except (GigaChatException, Exception) as e:
            duration = time.time() - start_time
            # При ошибке has_ocr неизвестен, используем false
            vision_analysis_duration_seconds.labels(
                provider="gigachat",
                has_ocr="false"
            ).observe(duration)
            
            error_type = type(e).__name__
            vision_analysis_requests_total.labels(
                status="error",
                provider="gigachat",
                tenant_id=tenant_id,
                reason=error_type
            ).inc()
            
            logger.error(
                "Vision analysis failed",
                sha256=sha256,
                tenant_id=tenant_id,
                error=str(e),
                error_type=error_type,
                trace_id=trace_id
            )
            raise
    
    def _parse_vision_response(self, content: str, file_id: str) -> Dict[str, Any]:
        """
        Парсинг ответа от GigaChat Vision API с жёстким structured-output.
        
        Context7 best practice: двухшаговый протокол с валидацией через Pydantic.
        Приоритет 1: Парсинг полного JSON
        Приоритет 2: Tolerant-parser (скобочный экстрактор для неполных ответов)
        Приоритет 3: Repair-prompt для исправления невалидного JSON
        Приоритет 4: Fallback классификация по ключевым словам
        """
        import json
        import re
        
        # Context7: Импорт VisionEnrichment для валидации
        try:
            from events.schemas.posts_vision_v1 import VisionEnrichment
        except ImportError:
            # Fallback для случая если схема не доступна
            VisionEnrichment = None
        
        # Приоритет 1: Попытка прямого парсинга JSON
        if VisionEnrichment:
            try:
                parsed = json.loads(content.strip())
                # Context7: Улучшенная проверка OCR - проверяем все возможные варианты
                ocr_data = parsed.get("ocr")
                has_ocr_text = False
                
                if ocr_data:
                    if isinstance(ocr_data, dict):
                        has_ocr_text = bool(ocr_data.get("text") and str(ocr_data.get("text", "")).strip())
                    elif isinstance(ocr_data, str):
                        has_ocr_text = bool(ocr_data.strip())
                
                # Если OCR отсутствует или пустой, пытаемся извлечь из content
                if not has_ocr_text:
                    ocr_extracted = self._extract_ocr_from_content(content)
                    if ocr_extracted:
                        parsed["ocr"] = ocr_extracted
                        logger.debug("OCR extracted from content after parsing", ocr_length=len(ocr_extracted.get("text", "")))
                    else:
                        # Context7: Проверяем, может быть текст в description (для документов)
                        description = parsed.get("description", "")
                        if description and len(description.strip()) > 20:
                            # Если description длинный и похож на OCR текст, пробуем использовать его
                            logger.debug("No OCR found, but description is long - might contain text", description_length=len(description))
                
                # Валидация через Pydantic
                try:
                    enrichment = VisionEnrichment(**parsed)
                    vision_parsed_total.labels(status="success", method="direct").inc()
                    return self._enrichment_to_dict(enrichment, file_id)
                except Exception as e:
                    logger.debug("Direct JSON parsing failed validation, trying repair", error=str(e))
                    # Пробуем repair-prompt (если возможно)
            except json.JSONDecodeError:
                pass
        
        # Приоритет 2: Tolerant-parser - скобочный экстрактор для неполных ответов
        if VisionEnrichment:
            try:
                # Ищем JSON блок с балансом скобок
                json_match = self._extract_json_with_balance(content)
                if json_match:
                    parsed = json.loads(json_match)
                    # Context7: Убеждаемся что OCR извлечён
                    if not parsed.get("ocr") or not parsed.get("ocr", {}).get("text"):
                        ocr_extracted = self._extract_ocr_from_content(content)
                        if ocr_extracted:
                            parsed["ocr"] = ocr_extracted
                    
                    try:
                        enrichment = VisionEnrichment(**parsed)
                        vision_parsed_total.labels(status="success", method="bracket_extractor").inc()
                        return self._enrichment_to_dict(enrichment, file_id)
                    except Exception as e:
                        logger.debug("Bracket extractor result failed validation", error=str(e))
            except (json.JSONDecodeError, Exception) as e:
                logger.debug("Bracket extractor failed", error=str(e))
        
        # Приоритет 3: Попытка извлечь частичный JSON и дополнить дефолтами
        if VisionEnrichment:
            try:
                partial_data = self._extract_partial_json(content)
                if partial_data:
                    # Context7: Специальная обработка OCR - извлекаем из вложенного объекта
                    if "ocr" not in partial_data:
                        ocr_data = self._extract_ocr_from_content(content)
                        if ocr_data:
                            partial_data["ocr"] = ocr_data
                    
                    # Заполняем обязательные поля дефолтами
                    if "description" not in partial_data or len(str(partial_data.get("description", "")).strip()) < 5:
                        partial_data["description"] = content[:200] if len(content) >= 5 else f"Изображение: {content[:195]}"
                    if "classification" not in partial_data:
                        partial_data["classification"] = self._classify_by_keywords(content)
                    if "is_meme" not in partial_data:
                        partial_data["is_meme"] = self._detect_meme_by_keywords(content)
                    
                    try:
                        enrichment = VisionEnrichment(**partial_data)
                        vision_parsed_total.labels(status="success", method="partial_fallback").inc()
                        return self._enrichment_to_dict(enrichment, file_id)
                    except Exception as e:
                        logger.debug("Partial JSON with defaults failed validation", error=str(e))
            except Exception as e:
                logger.debug("Partial extraction failed", error=str(e))
        
        # Приоритет 4: Fallback - классификация по ключевым словам
        vision_parsed_total.labels(status="fallback", method="keyword").inc()
        logger.warning("Vision parsing fallback to keyword classification", content_preview=content[:100])
        
        classification_type = self._classify_by_keywords(content)
        is_meme = self._detect_meme_by_keywords(content)
        
        # Context7: Пытаемся извлечь OCR даже в fallback режиме
        ocr_extracted = self._extract_ocr_from_content(content)
        if not ocr_extracted and len(content.strip()) > 20:
            # Если OCR не найден, но есть длинный текст - возможно это весь ответ и есть OCR
            ocr_extracted = {
                "text": content.strip(),
                "engine": "gigachat",
                "confidence": 0.5  # Низкая уверенность для fallback
            }
        
        # Создаём минимальный валидный VisionEnrichment
        fallback_description = content[:200] if len(content.strip()) >= 5 else f"Изображение (не удалось извлечь описание): {content[:150]}"
        if len(fallback_description.strip()) < 5:
            fallback_description = "Изображение без текстового описания"
        
        if VisionEnrichment:
            try:
                enrichment = VisionEnrichment(
                    classification=classification_type,
                    description=fallback_description,
                    is_meme=is_meme,
                    labels=[],
                    objects=[],
                    ocr=ocr_extracted
                )
                return self._enrichment_to_dict(enrichment, file_id)
            except Exception as e:
                logger.error("Failed to create fallback VisionEnrichment", error=str(e))
        
        # Последний резерв - возвращаем минимальный dict
        return {
            "classification": {
                "type": classification_type,
                "confidence": 0.7,
                "tags": []
            },
            "description": fallback_description,
            "is_meme": is_meme,
            "labels": [],
            "objects": [],
            "ocr_text": content if content.strip() else None,
            "context": {
                "objects": [],
                "emotions": [],
                "themes": []
            },
            "file_id": file_id,
            "provider": "gigachat",
            "model": self.model,
            "schema_version": "1.0"
        }
    
    def _extract_json_with_balance(self, content: str) -> Optional[str]:
        """
        Извлечение JSON из текста с проверкой баланса скобок.
        Tolerant-parser для неполных ответов.
        """
        import re
        # Ищем первую открывающую скобку
        start_idx = content.find('{')
        if start_idx == -1:
            return None
        
        # Подсчитываем баланс скобок
        balance = 0
        in_string = False
        escape_next = False
        
        for i in range(start_idx, len(content)):
            char = content[i]
            
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
            
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            
            if not in_string:
                if char == '{':
                    balance += 1
                elif char == '}':
                    balance -= 1
                    if balance == 0:
                        # Найдена закрывающая скобка
                        return content[start_idx:i+1]
        
        # Если баланс не сходится, возвращаем None (неполный JSON)
        return None
    
    def _extract_partial_json(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Извлечение частичных JSON данных из текста.
        Ищет пары ключ-значение даже в невалидном JSON.
        Context7: Поддержка вложенных объектов (ocr: {...}).
        
        Context7 best practice: защита от catastrophic backtracking:
        - Ограничение длины content
        - Ограниченные квантификаторы {1,500} вместо +
        - Non-greedy квантификаторы где возможно
        - Обработка ошибок regex
        """
        import json
        import re
        
        # Context7: Ограничение длины для предотвращения зависания
        MAX_CONTENT_LENGTH = 50000  # 50KB максимум
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]
            logger.debug(f"Content truncated for partial JSON extraction: {len(content)} chars")
        
        result = {}
        
        try:
            # Context7: Попытка извлечь вложенные объекты (ocr: {...})
            # Используем ограниченный квантификатор {1,500} вместо +
            nested_obj_pattern = r'["\']?ocr["\']?\s*:\s*\{([^}]{1,500})\}'
            nested_match = re.search(nested_obj_pattern, content, re.IGNORECASE)
            if nested_match:
                ocr_content = nested_match.group(1)
                ocr_data = {}
                # Извлекаем поля из вложенного объекта (ограниченный паттерн)
                ocr_field_pattern = r'["\']?([a-zA-Z_][a-zA-Z0-9_]*)["\']?\s*:\s*([^,}]{1,200})'
                ocr_matches = re.findall(ocr_field_pattern, ocr_content)
                for ocr_key, ocr_value in ocr_matches:
                    ocr_value = ocr_value.strip().rstrip(',').strip('"\'')
                    if ocr_key == "text":
                        ocr_data["text"] = ocr_value
                    elif ocr_key == "engine":
                        ocr_data["engine"] = ocr_value
                    elif ocr_key == "confidence":
                        try:
                            ocr_data["confidence"] = float(ocr_value)
                        except (ValueError, TypeError):
                            pass
                if ocr_data:
                    result["ocr"] = ocr_data
            
            # Попытка найти JSON-подобные пары
            # Context7: Ограниченный квантификатор {1,200} для значений
            key_value_pattern = r'["\']?([a-zA-Z_][a-zA-Z0-9_]*)["\']?\s*:\s*([^,}\]]{1,200})'
            matches = re.findall(key_value_pattern, content)
            
            # Context7: Ограничиваем количество обрабатываемых пар для производительности
            MAX_PAIRS = 100
            for key, value in matches[:MAX_PAIRS]:
                # Пропускаем уже обработанные ключи
                if key.lower() == "ocr" and "ocr" in result:
                    continue
                    
                value = value.strip().rstrip(',').rstrip('}')
                # Пытаемся распарсить значение
                try:
                    # Если это строка в кавычках
                    if value.startswith('"') and value.endswith('"'):
                        result[key] = json.loads(value)
                    # Если это boolean
                    elif value.lower() in ['true', 'false']:
                        result[key] = value.lower() == 'true'
                    # Если это число
                    elif value.replace('.', '', 1).replace('-', '', 1).isdigit():
                        result[key] = float(value) if '.' in value else int(value)
                    # Если это null
                    elif value.lower() == 'null':
                        result[key] = None
                    # Если это массив (начинается с [)
                    elif value.strip().startswith('[') and value.strip().endswith(']'):
                        try:
                            result[key] = json.loads(value)
                        except json.JSONDecodeError:
                            # Пытаемся извлечь элементы массива (ограниченный паттерн)
                            array_items = re.findall(r'["\']([^"\']{1,200})["\']', value)
                            if array_items:
                                result[key] = array_items[:50]  # Максимум 50 элементов
                            else:
                                result[key] = value.strip('"\'')
                    else:
                        result[key] = value.strip('"\'')
                except Exception:
                    result[key] = value.strip('"\'')
        
        except re.error as e:
            # Context7: Обработка ошибок regex
            logger.warning(f"Regex error in partial JSON extraction: {e}", exc_info=False)
        except Exception as e:
            logger.debug(f"Error in partial JSON extraction: {e}", exc_info=False)
        
        return result if result else None
    
    def _extract_ocr_from_content(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Context7: Специальная обработка OCR из ответа GigaChat.
        Извлекает OCR данные даже если основной парсинг не удался.
        
        Context7 best practice: защита от catastrophic backtracking через:
        - Ограничение длины content (max 50KB)
        - Non-greedy квантификаторы
        - Ограниченные квантификаторы {0,1000} вместо *
        - Избегание re.DOTALL на больших текстах
        
        Ищет паттерны:
        - "ocr": {"text": "...", "engine": "...", "confidence": ...}
        - "ocr": {"text": "..."}
        - ocr text: "..." (неформатированный)
        """
        import re
        
        # Context7: Ограничение длины content для предотвращения зависания
        MAX_CONTENT_LENGTH = 50000  # 50KB максимум для regex операций
        if len(content) > MAX_CONTENT_LENGTH:
            # Ограничиваем до первых 50KB и последних 10KB (для поиска OCR)
            content = content[:MAX_CONTENT_LENGTH] + content[-10000:]
            logger.debug(f"Content truncated for OCR extraction: {len(content)} chars")
        
        try:
            # Паттерн 1: Стандартный JSON формат (non-greedy, ограниченный)
            # Context7: Используем {0,1000} вместо * для предотвращения backtracking
            # Улучшенный паттерн для поддержки многострочного текста в OCR
            ocr_pattern1 = r'["\']?ocr["\']?\s*:\s*\{[^}]{0,2000}?["\']text["\']?\s*:\s*["\']((?:[^"\\]|\\.){0,10000})["\']'
            match1 = re.search(ocr_pattern1, content, re.IGNORECASE | re.DOTALL)
            if match1:
                ocr_text = match1.group(1)
                # Декодируем escape-последовательности
                try:
                    ocr_text = ocr_text.encode().decode('unicode_escape')
                except:
                    pass
                
                # Ищем engine в пределах 200 символов от найденного OCR
                start_pos = max(0, match1.start() - 100)
                end_pos = min(len(content), match1.end() + 100)
                context_snippet = content[start_pos:end_pos]
                
                engine_match = re.search(r'["\']engine["\']?\s*:\s*["\']([^"\']{1,50})["\']', context_snippet, re.IGNORECASE)
                engine = engine_match.group(1) if engine_match else "gigachat"
                
                confidence_match = re.search(r'["\']confidence["\']?\s*:\s*([0-9.]+)', context_snippet, re.IGNORECASE)
                confidence = float(confidence_match.group(1)) if confidence_match else None
                
                ocr_text_cleaned = ocr_text.strip()
                if ocr_text_cleaned and len(ocr_text_cleaned) > 0:
                    return {
                        "text": ocr_text_cleaned,
                        "engine": engine,
                        "confidence": confidence
                    }
            
            # Паттерн 2: Неформатированный текст после "ocr" или "текст" (ограниченный)
            # Улучшенный паттерн для поддержки различных форматов
            ocr_pattern2 = r'(?:ocr|текст|извлечённый\s+текст|распознанный\s+текст)[:：]\s*["\']((?:[^"\\]|\\.){10,10000})["\']'
            match2 = re.search(ocr_pattern2, content, re.IGNORECASE | re.DOTALL)
            if match2:
                ocr_text = match2.group(1)
                # Декодируем escape-последовательности
                try:
                    ocr_text = ocr_text.encode().decode('unicode_escape')
                except:
                    pass
                ocr_text = ocr_text.strip()
                if len(ocr_text) > 10:  # Минимальная длина для OCR текста
                    return {
                        "text": ocr_text,
                        "engine": "gigachat",
                        "confidence": 0.8  # Средняя уверенность для извлечённого текста
                    }
            
            # Паттерн 3: Текст в блоке code или markdown (если GigaChat обернул в код)
            ocr_pattern3 = r'```(?:json)?\s*\{[^}]*"ocr"[^}]*"text"\s*:\s*["\']((?:[^"\\]|\\.){10,10000})["\']'
            match3 = re.search(ocr_pattern3, content, re.IGNORECASE | re.DOTALL)
            if match3:
                ocr_text = match3.group(1)
                try:
                    ocr_text = ocr_text.encode().decode('unicode_escape')
                except:
                    pass
                ocr_text = ocr_text.strip()
                if len(ocr_text) > 10:
                    return {
                        "text": ocr_text,
                        "engine": "gigachat",
                        "confidence": 0.75
                    }
            
        except re.error as e:
            # Context7: Обработка ошибок regex (например, при невалидных паттернах)
            logger.warning(f"Regex error in OCR extraction: {e}", exc_info=False)
        except Exception as e:
            # Другие ошибки - логируем но не падаем
            logger.debug(f"Error in OCR extraction: {e}", exc_info=False)
        
        return None
    
    def _classify_by_keywords(self, content: str) -> str:
        """Классификация по ключевым словам (fallback)."""
        content_lower = content.lower()
        
        if any(word in content_lower for word in ['мем', 'юмор', 'шутка', 'meme']):
            return "meme"
        elif any(word in content_lower for word in ['документ', 'document', 'pdf', 'текст']):
            return "document"
        elif any(word in content_lower for word in ['скриншот', 'screenshot', 'screen']):
            return "screenshot"
        elif any(word in content_lower for word in ['инфографика', 'infographic', 'диаграмм']):
            return "infographic"
        elif any(word in content_lower for word in ['фото', 'photo', 'изображение', 'image']):
            return "photo"
        else:
            return "other"
    
    def _detect_meme_by_keywords(self, content: str) -> bool:
        """Определение мема по ключевым словам (fallback)."""
        content_lower = content.lower()
        meme_keywords = ['мем', 'юмор', 'шутка', 'meme', 'humor', 'funny', 'comic']
        return any(word in content_lower for word in meme_keywords)
    
    def _enrichment_to_dict(self, enrichment, file_id: str) -> Dict[str, Any]:
        """
        Конвертация VisionEnrichment в dict для обратной совместимости.
        
        Args:
            enrichment: VisionEnrichment Pydantic модель или dict
            file_id: GigaChat file_id
        """
        # Если это уже dict (fallback случай), возвращаем как есть
        if isinstance(enrichment, dict):
            return enrichment
        
        result = {
            "classification": enrichment.classification,
            "description": enrichment.description,
            "is_meme": enrichment.is_meme,
            "labels": enrichment.labels,
            "objects": enrichment.objects,
            "scene": enrichment.scene,
            "ocr": enrichment.ocr,
            "nsfw_score": enrichment.nsfw_score,
            "aesthetic_score": enrichment.aesthetic_score,
            "dominant_colors": enrichment.dominant_colors,
            "context": enrichment.context,
            "s3_keys": enrichment.s3_keys,
            "file_id": file_id,
            "provider": "gigachat",
            "model": self.model,
            "schema_version": "1.0"
        }
        
        # Добавляем legacy поля для обратной совместимости
        result["classification"] = {
            "type": enrichment.classification,
            "confidence": 1.0,
            "tags": enrichment.labels
        }
        result["ocr_text"] = enrichment.ocr.get("text") if enrichment.ocr else None
        
        return result
    
    async def close(self):
        """Закрытие адаптера (cleanup)."""
        if self._client:
            # GigaChat клиент обычно не требует явного закрытия
            self._client = None

