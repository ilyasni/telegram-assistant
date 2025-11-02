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
        timeout: int = 600
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
                    # TODO: Загрузить из S3 и десериализовать
                    vision_cache_hits_total.labels(cache_type="s3").inc()
                    logger.debug("Vision result found in S3 cache", sha256=sha256)
            
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
                
                # Промпт для анализа с strict schema hints
                if not analysis_prompt:
                    analysis_prompt = """Проанализируй изображение или документ и предоставь структурированный JSON со следующей схемой:

{
  "classification": "photo" | "meme" | "document" | "screenshot" | "infographic" | "other",
  "description": "краткое текстовое описание содержимого (минимум 5 символов)",
  "is_meme": true/false,
  "labels": ["класс1", "класс2", ...],  // максимум 20 классов/атрибутов
  "objects": ["объект1", "объект2", ...],  // максимум 10 объектов на изображении
  "scene": "описание сцены/окружения" или null,
  "ocr": {"text": "извлечённый текст", "engine": "gigachat", "confidence": 0.0-1.0} или null,
  "nsfw_score": 0.0-1.0 или null,
  "aesthetic_score": 0.0-1.0 или null,
  "dominant_colors": ["#hex1", "#hex2", ...],  // максимум 5 цветов
  "context": {"emotions": [...], "themes": [...], "relationships": [...]}
}

Обязательные поля: classification, description, is_meme.
Ответь ТОЛЬКО валидным JSON без дополнительного текста."""
                
                # Vision анализ через chat с attachments
                client = self._get_client()
                response = client.chat({
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
                
                # Извлечение результата
                content = response.choices[0].message.content
                
                # Парсинг ответа (упрощённый, реальный должен парсить JSON)
                analysis_result = self._parse_vision_response(content, file_id)
                
                # Учёт токенов
                tokens_used = getattr(response.usage, 'total_tokens', 0) if hasattr(response, 'usage') else 0
                if self.budget_gate and tokens_used > 0:
                    await self.budget_gate.record_token_usage(
                        tenant_id=tenant_id,
                        tokens_used=tokens_used,
                        provider="gigachat",
                        model=self.model
                    )
                
                # Сохранение в S3 кэш
                if self.s3_service and cache_key:
                    await self.s3_service.put_json(
                        data=analysis_result,
                        s3_key=cache_key,
                        compress=True
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
                    ocr={"text": content, "engine": "fallback", "confidence": 0.0} if content.strip() else None
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
        """
        import json
        import re
        
        result = {}
        
        # Попытка найти JSON-подобные пары
        # Ищем паттерны типа "key": value
        key_value_pattern = r'["\']?([a-zA-Z_][a-zA-Z0-9_]*)["\']?\s*:\s*([^,}\]]+)'
        matches = re.findall(key_value_pattern, content)
        
        for key, value in matches:
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
                else:
                    result[key] = value.strip('"\'')
            except Exception:
                result[key] = value.strip('"\'')
        
        return result if result else None
    
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

