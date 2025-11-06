"""
OpenRouter Vision Adapter для анализа изображений через OpenRouter API.

Context7 best practice: использование OpenRouter как fallback для Vision анализа
с моделью qwen/qwen2.5-vl-32b-instruct:free для OCR и классификации изображений.
"""

import asyncio
import base64
import json
import os
import time
from typing import Dict, Any, Optional
import structlog
import httpx
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

openrouter_vision_requests_total = Counter(
    'openrouter_vision_requests_total',
    'OpenRouter Vision API requests',
    ['status', 'model']
)

openrouter_vision_duration_seconds = Histogram(
    'openrouter_vision_duration_seconds',
    'OpenRouter Vision API latency',
    ['model']
)

openrouter_vision_tokens_total = Counter(
    'openrouter_vision_tokens_total',
    'OpenRouter Vision tokens used',
    ['model', 'tenant_id']
)


class OpenRouterVisionAdapter:
    """
    OpenRouter Vision API адаптер для анализа изображений.
    
    Использует модель qwen/qwen2.5-vl-32b-instruct:free для:
    - OCR (извлечение текста из изображений)
    - Классификация контента
    - Описание изображений
    
    Context7 best practice: совместимый интерфейс с GigaChatVisionAdapter
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen/qwen2.5-vl-32b-instruct:free",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: int = 120,
        max_retries: int = 3,
        circuit_breaker: Optional[Any] = None
    ):
        """
        Инициализация OpenRouter Vision Adapter.
        
        Args:
            api_key: OpenRouter API key (из OPENROUTER_API_KEY env)
            model: Модель для Vision анализа (по умолчанию qwen/qwen2.5-vl-32b-instruct:free)
            base_url: Базовый URL OpenRouter API
            timeout: Timeout для запросов в секундах
            max_retries: Максимальное количество повторных попыток
            circuit_breaker: CircuitBreaker экземпляр (создаётся автоматически если не передан)
        """
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        
        if not self.api_key:
            logger.warning(
                "OpenRouter API key not provided",
                model=model,
                hint="Set OPENROUTER_API_KEY environment variable"
            )
        
        # Context7: Circuit breaker для защиты от каскадных сбоев
        if circuit_breaker is None:
            from shared.utils.circuit_breaker import CircuitBreaker
            failure_threshold = int(os.getenv("OPENROUTER_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
            recovery_timeout = int(os.getenv("OPENROUTER_CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "60"))
            self.circuit_breaker = CircuitBreaker(
                name="openrouter_vision",
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                expected_exception=Exception
            )
        else:
            self.circuit_breaker = circuit_breaker
        
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10),
            headers={
                "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com/telegram-assistant"),
                "X-Title": "Telegram Assistant Vision"
            }
        )
        
        logger.info(
            "OpenRouterVisionAdapter initialized",
            model=model,
            base_url=base_url,
            has_api_key=bool(self.api_key)
        )
    
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
        Анализ медиа через OpenRouter Vision API.
        
        Context7: Совместимый интерфейс с GigaChatVisionAdapter
        
        Args:
            sha256: SHA256 хеш файла (для логирования)
            file_content: Содержимое файла
            mime_type: MIME тип
            tenant_id: ID tenant
            trace_id: Trace ID для корреляции
            analysis_prompt: Кастомный промпт для анализа
            
        Returns:
            Vision analysis results в формате совместимом с GigaChatVisionAdapter
        """
        import time
        start_time = time.time()
        
        if not self.api_key:
            raise ValueError("OpenRouter API key not configured")
        
        try:
            # Context7: Конвертация изображения в base64 для OpenRouter API
            image_base64 = base64.b64encode(file_content).decode('utf-8')
            
            # Определение MIME типа для data URL
            mime_to_data_type = {
                'image/jpeg': 'jpeg',
                'image/png': 'png',
                'image/gif': 'gif',
                'image/webp': 'webp'
            }
            image_type = mime_to_data_type.get(mime_type.lower(), 'jpeg')
            data_url = f"data:image/{image_type};base64,{image_base64}"
            
            # Context7: Промпт для анализа с явным запросом OCR
            if not analysis_prompt:
                analysis_prompt = """Проанализируй изображение или документ и предоставь структурированный JSON со следующей схемой:

{
  "classification": "photo" | "meme" | "document" | "screenshot" | "infographic" | "other",
  "description": "краткое текстовое описание содержимого (минимум 5 символов)",
  "is_meme": true/false,
  "labels": ["класс1", "класс2", ...],  // максимум 20 классов/атрибутов
  "objects": ["объект1", "объект2", ...],  // максимум 10 объектов на изображении
  "scene": "описание сцены/окружения" или null,
  "ocr": {"text": "извлечённый текст из изображения", "engine": "openrouter", "confidence": 0.0-1.0} или null,
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
            
            # Context7: Формирование запроса к OpenRouter API
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": analysis_prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        }
                    ]
                }
            ]
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.1
            }
            
            # Context7: Выполнение запроса с circuit breaker и retry логикой
            async def _make_request():
                """Внутренняя функция для выполнения запроса с retry."""
                response_data = None
                last_error = None
                
                for attempt in range(self.max_retries):
                    try:
                        response = await self.client.post(
                            f"{self.base_url}/chat/completions",
                            json=payload
                        )
                        response.raise_for_status()
                        response_data = response.json()
                        return response_data
                    except httpx.HTTPStatusError as e:
                        last_error = e
                        error_body = {}
                        try:
                            error_body = e.response.json()
                        except:
                            pass
                        
                        if e.response.status_code == 429:
                            # Context7: Rate limit - используем заголовки X-RateLimit-Reset или Retry-After
                            headers = e.response.headers
                            retry_after = None
                            
                            # Проверяем Retry-After заголовок
                            if "Retry-After" in headers:
                                retry_after = int(headers["Retry-After"])
                            # Проверяем X-RateLimit-Reset (timestamp в миллисекундах)
                            elif "X-RateLimit-Reset" in headers:
                                reset_timestamp = int(headers["X-RateLimit-Reset"]) / 1000  # Конвертируем в секунды
                                retry_after = max(0, reset_timestamp - time.time())
                            
                            # Если quota exhausted для free моделей - не повторяем
                            error_code = error_body.get("error", {}).get("code", "")
                            if "free-models-per-day" in str(error_body) or error_code == "free_quota_exceeded":
                                logger.warning(
                                    "OpenRouter free quota exhausted, not retrying",
                                    model=self.model,
                                    error_code=error_code,
                                    trace_id=trace_id
                                )
                                raise Exception(f"OpenRouter free quota exhausted: {error_body.get('error', {}).get('message', '')}")
                            
                            # Используем retry_after или exponential backoff с jitter
                            if retry_after and retry_after > 0:
                                wait_time = min(retry_after, 300)  # Максимум 5 минут
                            else:
                                import random
                                base_delay = 2 ** attempt
                                jitter = random.uniform(0, 0.3 * base_delay)
                                wait_time = base_delay + jitter
                            
                            if attempt < self.max_retries - 1:
                                logger.warning(
                                    "OpenRouter rate limit, retrying",
                                    attempt=attempt + 1,
                                    wait_seconds=int(wait_time),
                                    retry_after=retry_after,
                                    trace_id=trace_id
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                raise Exception(f"OpenRouter rate limit exceeded after {self.max_retries} attempts")
                        elif e.response.status_code >= 500:
                            # Server error - повторяем с exponential backoff
                            import random
                            base_delay = 2 ** attempt
                            jitter = random.uniform(0, 0.3 * base_delay)
                            wait_time = base_delay + jitter
                            
                            if attempt < self.max_retries - 1:
                                logger.warning(
                                    "OpenRouter server error, retrying",
                                    attempt=attempt + 1,
                                    wait_seconds=int(wait_time),
                                    status_code=e.response.status_code,
                                    trace_id=trace_id
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                raise
                        else:
                            # Client error (400-499) - не повторяем
                            error_msg = error_body.get("error", {}).get("message", str(e))
                            raise Exception(f"OpenRouter client error ({e.response.status_code}): {error_msg}")
                    except Exception as e:
                        last_error = e
                        if attempt < self.max_retries - 1:
                            import random
                            base_delay = 2 ** attempt
                            jitter = random.uniform(0, 0.3 * base_delay)
                            wait_time = base_delay + jitter
                            
                            logger.warning(
                                "OpenRouter request failed, retrying",
                                attempt=attempt + 1,
                                wait_seconds=int(wait_time),
                                error=str(e),
                                trace_id=trace_id
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise
                
                if not response_data:
                    raise Exception(f"OpenRouter API request failed after {self.max_retries} attempts: {last_error}")
                return response_data
            
            # Context7: Используем circuit breaker для защиты от каскадных сбоев
            try:
                from shared.utils.circuit_breaker import CircuitBreakerOpenError
                response_data = await self.circuit_breaker.call_async(_make_request)
            except CircuitBreakerOpenError:
                logger.error(
                    "OpenRouter Vision circuit breaker is OPEN, skipping request",
                    sha256=sha256[:16] + "..." if sha256 else None,
                    trace_id=trace_id
                )
                raise Exception("OpenRouter Vision circuit breaker is OPEN - too many failures")
            
            # Context7: Извлечение результата из ответа OpenRouter
            content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if not content:
                raise Exception("Empty response from OpenRouter API")
            
            # Парсинг JSON ответа
            try:
                analysis_result = json.loads(content.strip())
            except json.JSONDecodeError:
                # Пытаемся извлечь JSON из текста
                import re
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    analysis_result = json.loads(json_match.group(0))
                else:
                    raise Exception(f"Failed to parse JSON from OpenRouter response: {content[:200]}")
            
            # Context7: Нормализация результата в формат совместимый с GigaChatVisionAdapter
            usage = response_data.get("usage", {})
            tokens_used = usage.get("total_tokens", 0)
            
            # Учёт токенов для метрик
            if tokens_used > 0:
                openrouter_vision_tokens_total.labels(
                    model=self.model,
                    tenant_id=tenant_id
                ).inc(tokens_used)
            
            duration = time.time() - start_time
            openrouter_vision_duration_seconds.labels(model=self.model).observe(duration)
            openrouter_vision_requests_total.labels(status="success", model=self.model).inc()
            
            # Context7: Формирование результата в формате GigaChatVisionAdapter
            result = {
                "provider": "openrouter",
                "model": self.model,
                "classification": analysis_result.get("classification", "other"),
                "description": analysis_result.get("description", "Изображение без описания"),
                "is_meme": analysis_result.get("is_meme", False),
                "labels": analysis_result.get("labels", []),
                "objects": analysis_result.get("objects", []),
                "scene": analysis_result.get("scene"),
                "ocr": analysis_result.get("ocr"),  # Может быть None или dict
                "nsfw_score": analysis_result.get("nsfw_score"),
                "aesthetic_score": analysis_result.get("aesthetic_score"),
                "dominant_colors": analysis_result.get("dominant_colors", []),
                "context": analysis_result.get("context", {}),
                "tokens_used": tokens_used,
                "cost_microunits": 0,  # Free model
                "file_id": None,  # OpenRouter не использует file_id
                "analysis_reason": "openrouter_fallback"
            }
            
            logger.info(
                "OpenRouter Vision analysis completed",
                sha256=sha256[:16] + "...",
                tenant_id=tenant_id,
                tokens_used=tokens_used,
                duration_ms=int(duration * 1000),
                has_ocr=bool(result.get("ocr") and result["ocr"].get("text")),
                trace_id=trace_id
            )
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            openrouter_vision_duration_seconds.labels(model=self.model).observe(duration)
            openrouter_vision_requests_total.labels(status="error", model=self.model).inc()
            
            logger.error(
                "OpenRouter Vision analysis failed",
                sha256=sha256[:16] + "..." if sha256 else None,
                tenant_id=tenant_id,
                error=str(e),
                error_type=type(e).__name__,
                trace_id=trace_id
            )
            raise
    
    async def close(self):
        """Закрытие HTTP клиента."""
        await self.client.aclose()

