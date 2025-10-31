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

# Context7: Импорт S3StorageService из shared модуля worker
try:
    from shared.s3_storage import S3StorageService
except ImportError:
    # Fallback для обратной совместимости
    import sys
    import os
    parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    try:
        from api.services.s3_storage import S3StorageService
    except ImportError:
        from shared.s3_storage import S3StorageService
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

vision_analysis_duration_seconds = Histogram(
    'vision_analysis_duration_seconds',
    'Vision analysis latency',
    ['provider', 'status']
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
        self.model = model
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
        purpose: str = "general"
    ) -> str:
        """
        Загрузка файла в GigaChat хранилище.
        
        Args:
            file_content: Содержимое файла
            filename: Имя файла (опционально)
            purpose: Назначение файла (general)
            
        Returns:
            GigaChat file_id
        """
        try:
            client = self._get_client()
            
            # Создаём file-like object
            file_obj = io.BytesIO(file_content)
            if filename:
                file_obj.name = filename
            
            # Загрузка файла
            uploaded_file = client.upload_file(file_obj, purpose=purpose)
            
            vision_file_uploads_total.labels(status="success").inc()
            
            logger.debug(
                "File uploaded to GigaChat",
                file_id=uploaded_file.id,
                filename=filename,
                size_bytes=len(file_content)
            )
            
            return uploaded_file.id
            
        except (GigaChatException, Exception) as e:
            vision_file_uploads_total.labels(status="error").inc()
            logger.error(
                "Failed to upload file to GigaChat",
                error=str(e),
                error_type=type(e).__name__
            )
            raise
    
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
                # Загрузка файла в GigaChat
                file_id = await self.upload_file(file_content)
                
                # Промпт для анализа
                if not analysis_prompt:
                    analysis_prompt = """Проанализируй изображение или документ и предоставь:
1. Тип контента (мем / документ / фото / инфографика / скриншот / текст)
2. Определи, является ли это мемом (юмористический контент с текстом на изображении)
3. Описание содержимого
4. Извлеки текст с изображения (OCR)
5. Ключевые объекты, эмоциональная окраска, темы

Ответь в структурированном формате JSON."""
                
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
                vision_analysis_duration_seconds.labels(
                    provider="gigachat",
                    status="success"
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
            vision_analysis_duration_seconds.labels(
                provider="gigachat",
                status="error"
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
        Парсинг ответа от GigaChat Vision API.
        
        Упрощённая версия - в production нужен более надёжный парсинг JSON.
        """
        import json
        import re
        
        # Попытка извлечь JSON из ответа
        try:
            # Ищем JSON в ответе
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
        
        # Fallback: структурированный анализ текста
        # TODO: Использовать LLM для структурирования ответа или улучшить парсинг
        
        # Простая классификация по ключевым словам
        content_lower = content.lower()
        is_meme = any(word in content_lower for word in ['мем', 'юмор', 'шутка', 'meme'])
        
        classification_type = "unknown"
        if is_meme:
            classification_type = "meme"
        elif "документ" in content_lower or "document" in content_lower:
            classification_type = "document"
        elif "фото" in content_lower or "photo" in content_lower:
            classification_type = "photo"
        elif "скриншот" in content_lower or "screenshot" in content_lower:
            classification_type = "screenshot"
        
        return {
            "classification": {
                "type": classification_type,
                "confidence": 0.7,  # Упрощённая оценка
                "tags": []
            },
            "description": content,
            "ocr_text": content,  # Упрощение: весь текст как OCR
            "is_meme": is_meme,
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
    
    async def close(self):
        """Закрытие адаптера (cleanup)."""
        if self._client:
            # GigaChat клиент обычно не требует явного закрытия
            self._client = None

