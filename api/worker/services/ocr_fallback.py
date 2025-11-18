"""
OCR Fallback Service для случаев quota exhausted
Context7 best practice: предпочтительно использовать локальный OCR перед обращением к дорогим Vision API.
Поддерживаются OpenRouter Vision и локальный PaddleOCR сервис.
"""

import base64
import os
import time
from typing import Dict, Any, Optional

import httpx
import structlog
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

ocr_requests_total = Counter(
    'ocr_requests_total',
    'OCR fallback requests',
    ['engine', 'status']
)

ocr_duration_seconds = Histogram(
    'ocr_duration_seconds',
    'OCR processing latency',
    ['engine']
)


class OCRFallbackService:
    """
    OCR Fallback Service для анализа изображений через OpenRouter Vision API.
    
    Context7: Использует OpenRouter Vision с моделью qwen/qwen2.5-vl-32b-instruct:free
    для полноценного Vision анализа (OCR + классификация + описание).
    
    Совместимый интерфейс с предыдущей версией для обратной совместимости.
    """
    
    def __init__(
        self,
        engine: str = "paddle",
        languages: str = "rus+eng",
        openrouter_adapter: Optional[Any] = None,
        paddle_endpoint: Optional[str] = None,
        paddle_timeout: Optional[float] = None
    ):
        """
        Инициализация OCR Fallback Service.
        
        Args:
            engine: Движок OCR (по умолчанию "openrouter")
            languages: Языки для OCR (не используется для OpenRouter)
            openrouter_adapter: OpenRouterVisionAdapter (создаётся автоматически если не передан)
        """
        self.engine = engine.lower()
        self.languages = languages
        self._http_client: Optional[httpx.AsyncClient] = None
        self.paddle_endpoint = paddle_endpoint or os.getenv("LOCAL_OCR_ENDPOINT", "http://paddleocr:8008/v1/ocr")
        self.paddle_timeout = paddle_timeout or float(os.getenv("LOCAL_OCR_TIMEOUT", "8.0"))
        self.paddle_model = os.getenv("LOCAL_OCR_MODEL_NAME", "paddleocr-cyrillic")
        
        self.openrouter_adapter = None
        if self.engine == "openrouter":
            if openrouter_adapter:
                self.openrouter_adapter = openrouter_adapter
            else:
                try:
                    from ai_adapters.openrouter_vision import OpenRouterVisionAdapter
                    self.openrouter_adapter = OpenRouterVisionAdapter(
                        model="qwen/qwen2.5-vl-32b-instruct:free"
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to initialize OpenRouter Vision Adapter",
                        error=str(e),
                        engine=engine
                    )
                    self.openrouter_adapter = None
        
        logger.info(
            "OCRFallbackService initialized",
            engine=self.engine,
            openrouter_available=bool(self.openrouter_adapter),
            paddle_endpoint=self.paddle_endpoint if self.engine == "paddle" else None
        )
    
    def _check_tesseract(self) -> bool:
        """Проверка доступности Tesseract."""
        try:
            import pytesseract
            self._tesseract_available = True
            return True
        except ImportError:
            logger.warning("pytesseract not available, Tesseract OCR disabled")
            return False
    
    def _check_rapidocr(self) -> bool:
        """Проверка доступности RapidOCR."""
        try:
            import rapidocr_onnxruntime
            self._rapidocr_available = True
            return True
        except ImportError:
            logger.warning("rapidocr_onnxruntime not available, RapidOCR disabled")
            return False
    
    async def extract_text(self, image_bytes: bytes) -> str:
        """
        Извлечение текста из изображения через OpenRouter Vision API.
        
        Context7: Использует OpenRouter Vision для полноценного анализа изображений.
        
        Args:
            image_bytes: Байты изображения
            
        Returns:
            Извлечённый текст (OCR результат)
        """
        import time
        start_time = time.time()
        
        try:
            if self.engine == "openrouter" and self.openrouter_adapter:
                result = await self.openrouter_adapter.analyze_media(
                    sha256="",
                    file_content=image_bytes,
                    mime_type="image/jpeg",
                    tenant_id="fallback",
                    trace_id="ocr_fallback"
                )
                ocr_data = result.get("ocr")
                text = ocr_data.get("text", "") if isinstance(ocr_data, dict) else ""
                duration = time.time() - start_time
                ocr_duration_seconds.labels(engine=self.engine).observe(duration)
                ocr_requests_total.labels(engine=self.engine, status="success").inc()
                logger.debug(
                    "OCR text extracted via OpenRouter",
                    engine=self.engine,
                    text_length=len(text),
                    duration_ms=int(duration * 1000)
                )
                return text
            if self.engine == "paddle":
                analysis = await self._analyze_with_paddle(image_bytes=image_bytes, mime_type="image/jpeg", trace_id="ocr_fallback")
                if not analysis:
                    return ""
                ocr_block = analysis.get("ocr", {})
                return ocr_block.get("text", "") if isinstance(ocr_block, dict) else ""
            logger.warning(
                "OCR engine not available",
                engine=self.engine
            )
            return ""
            
        except Exception as e:
            ocr_requests_total.labels(engine=self.engine, status="error").inc()
            logger.error("OCR extraction failed", engine=self.engine, error=str(e))
            return ""
    
    async def _extract_with_tesseract(self, image_bytes: bytes) -> str:
        """Извлечение текста через Tesseract."""
        from PIL import Image
        import pytesseract
        import io
        
        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image, lang=self.languages)
        return text.strip()
    
    async def _extract_with_rapidocr(self, image_bytes: bytes) -> str:
        """Извлечение текста через RapidOCR."""
        import rapidocr_onnxruntime
        import numpy as np
        from PIL import Image
        import io
        
        image = Image.open(io.BytesIO(image_bytes))
        image_array = np.array(image)
        
        ocr = rapidocr_onnxruntime.RapidOCR()
        result, _ = ocr(image_array)
        
        if result:
            # result: [('text', confidence), ...]
            text = ' '.join([item[0] for item in result if item[0]])
            return text.strip()
        return ""
    
    async def classify_content_type(self, ocr_text: str) -> Dict[str, Any]:
        """
        Классификация типа контента.
        
        Context7: Для OpenRouter Vision классификация выполняется автоматически
        в методе analyze_media. Этот метод оставлен для обратной совместимости.
        
        Args:
            ocr_text: OCR текст (не используется для OpenRouter)
            
        Returns:
            Результат классификации в формате совместимом с предыдущей версией
        """
        # Context7: Для OpenRouter Vision классификация выполняется автоматически
        # Этот метод используется только для обратной совместимости
        # В реальности классификация выполняется в analyze_media()
        
        text_lower = ocr_text.lower() if ocr_text else ""
        
        # Простые эвристики для определения типа (fallback)
        is_meme = any(word in text_lower for word in [
            'мем', 'meme', 'lol', 'хаха', 'рофл', 'кринж'
        ])
        
        # Определение типа
        content_type = "other"
        if is_meme:
            content_type = "meme"
        elif len(ocr_text) > 500:
            content_type = "document"
        elif any(word in text_lower for word in ['скриншот', 'screenshot']):
            content_type = "screenshot"
        elif len(ocr_text) > 50:
            content_type = "text"
        
        return {
            "type": content_type,
            "confidence": 0.6,  # Низкая уверенность для эвристической классификации
            "tags": [],
            "is_meme": is_meme,
            "method": "openrouter_fallback"
        }
    
    async def analyze_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        tenant_id: str = "fallback",
        trace_id: str = "ocr_fallback"
    ) -> Optional[Dict[str, Any]]:
        """
        Полный анализ изображения через OpenRouter Vision API.
        
        Context7: Новый метод для полноценного Vision анализа через OpenRouter.
        Используется вместо комбинации extract_text + classify_content_type.
        
        Args:
            image_bytes: Байты изображения
            mime_type: MIME тип изображения
            tenant_id: ID tenant
            trace_id: Trace ID для корреляции
            
        Returns:
            Результат Vision анализа в формате совместимом с GigaChatVisionAdapter
        """
        if self.engine == "openrouter":
            if not self.openrouter_adapter:
                logger.warning("OpenRouter Vision adapter not available")
                return None
            try:
                result = await self.openrouter_adapter.analyze_media(
                    sha256="",
                    file_content=image_bytes,
                    mime_type=mime_type,
                    tenant_id=tenant_id,
                    trace_id=trace_id
                )
                return result
            except Exception as e:
                logger.error(
                    "OpenRouter Vision analysis failed",
                    error=str(e),
                    trace_id=trace_id
                )
                return None
        if self.engine == "paddle":
            return await self._analyze_with_paddle(
                image_bytes=image_bytes,
                mime_type=mime_type,
                trace_id=trace_id,
            )
        logger.warning("Unsupported OCR engine", engine=self.engine)
        return None

    async def _analyze_with_paddle(
        self,
        image_bytes: bytes,
        mime_type: str,
        trace_id: str,
    ) -> Optional[Dict[str, Any]]:
        client = self._ensure_http_client()
        payload = {
            "image_base64": base64.b64encode(image_bytes).decode("utf-8"),
            "return_image": False,
        }
        start_time = time.time()
        try:
            response = await client.post(
                self.paddle_endpoint,
                json=payload,
                timeout=self.paddle_timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            duration = time.time() - start_time
            ocr_requests_total.labels(engine="paddle", status="error").inc()
            ocr_duration_seconds.labels(engine="paddle").observe(duration)
            logger.error(
                "PaddleOCR request failed",
                error=str(exc),
                endpoint=self.paddle_endpoint,
                trace_id=trace_id,
            )
            return None

        data = response.json()
        duration = time.time() - start_time
        ocr_requests_total.labels(engine="paddle", status="success").inc()
        ocr_duration_seconds.labels(engine="paddle").observe(duration)

        lines = data.get("lines", []) or []
        text_lines = [
            line.get("text", "").strip()
            for line in lines
            if isinstance(line, dict) and line.get("text")
        ]
        joined_text = "\n".join(t for t in text_lines if t)
        aggregates = data.get("aggregates", {}) or {}
        line_count = aggregates.get("line_count") or len(text_lines)
        avg_confidence = aggregates.get("confidence_mean")

        classification_type = "other"
        text_len = len(joined_text)
        if line_count >= 8 or text_len > 500:
            classification_type = "document"
        elif text_len > 0:
            classification_type = "text"

        analysis_context = {
            "aggregates": aggregates,
            "language_priority": data.get("language_priority"),
            "duration_ms": data.get("duration_ms"),
        }

        analysis = {
            "provider": "paddleocr",
            "model": self.paddle_model,
            "classification": {"type": classification_type},
            "description": None,
            "labels": [],
            "is_meme": False,
            "objects": [],
            "scene": None,
            "ocr": {
                "text": joined_text,
                "engine": "paddleocr",
                "confidence": avg_confidence,
                "line_count": line_count,
            } if joined_text else None,
            "ocr_text": joined_text or None,
            "context": analysis_context,
            "tokens_used": 0,
        }

        logger.debug(
            "PaddleOCR analysis completed",
            trace_id=trace_id,
            line_count=line_count,
            text_length=text_len,
            duration_ms=int(duration * 1000),
        )

        return analysis

    def _ensure_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

