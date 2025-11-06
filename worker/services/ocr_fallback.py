"""
OCR Fallback Service для случаев quota exhausted
Context7 best practice: использование OpenRouter Vision API как fallback для Vision анализа
с моделью qwen/qwen2.5-vl-32b-instruct:free
"""

import logging
from typing import Dict, Any, Optional

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
        engine: str = "openrouter",
        languages: str = "rus+eng",
        openrouter_adapter: Optional[Any] = None
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
        
        # Context7: Инициализация OpenRouter Vision Adapter
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
            engine=engine,
            openrouter_available=bool(self.openrouter_adapter)
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
                # Context7: Используем OpenRouter Vision для анализа
                # Получаем полный анализ и извлекаем OCR текст
                result = await self.openrouter_adapter.analyze_media(
                    sha256="",  # Не используется для fallback
                    file_content=image_bytes,
                    mime_type="image/jpeg",  # Определяется автоматически
                    tenant_id="fallback",
                    trace_id="ocr_fallback"
                )
                
                # Извлекаем OCR текст из результата
                ocr_data = result.get("ocr")
                if ocr_data and isinstance(ocr_data, dict):
                    text = ocr_data.get("text", "")
                else:
                    text = ""
                
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
            else:
                logger.warning(
                    "OpenRouter Vision adapter not available",
                    engine=self.engine,
                    openrouter_available=bool(self.openrouter_adapter)
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
        if not self.openrouter_adapter:
            logger.warning("OpenRouter Vision adapter not available")
            return None
        
        try:
            result = await self.openrouter_adapter.analyze_media(
                sha256="",  # Не используется для fallback
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

