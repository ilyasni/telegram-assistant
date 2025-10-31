"""
OCR Fallback Service для случаев quota exhausted
Context7 best practice: локальный OCR как fallback для Vision API
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
    OCR Fallback Service для извлечения текста из изображений.
    
    Engines:
    - tesseract: Tesseract OCR (требует установки)
    - rapidocr: RapidOCR (Python-based)
    """
    
    def __init__(self, engine: str = "tesseract", languages: str = "rus+eng"):
        self.engine = engine.lower()
        self.languages = languages
        
        # Lazy инициализация engines
        self._tesseract_available = False
        self._rapidocr_available = False
        
        logger.info(
            "OCRFallbackService initialized",
            engine=engine,
            languages=languages
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
        Извлечение текста из изображения через OCR.
        
        Args:
            image_bytes: Байты изображения
            
        Returns:
            Извлечённый текст
        """
        import time
        start_time = time.time()
        
        try:
            if self.engine == "tesseract" and self._check_tesseract():
                text = await self._extract_with_tesseract(image_bytes)
            elif self.engine == "rapidocr" and self._check_rapidocr():
                text = await self._extract_with_rapidocr(image_bytes)
            else:
                logger.warning(
                    "Requested OCR engine not available",
                    engine=self.engine,
                    tesseract_available=self._tesseract_available,
                    rapidocr_available=self._rapidocr_available
                )
                return ""
            
            duration = time.time() - start_time
            ocr_duration_seconds.labels(engine=self.engine).observe(duration)
            ocr_requests_total.labels(engine=self.engine, status="success").inc()
            
            logger.debug(
                "OCR text extracted",
                engine=self.engine,
                text_length=len(text),
                duration_ms=int(duration * 1000)
            )
            
            return text
            
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
        Zero-shot классификация типа контента по OCR тексту.
        
        Упрощённая эвристика, в production нужна модель или LLM.
        """
        text_lower = ocr_text.lower()
        
        # Простые эвристики для определения типа
        is_meme = any(word in text_lower for word in [
            'мем', 'meme', 'lol', 'хаха', 'рофл', 'кринж'
        ])
        
        # Определение типа
        content_type = "text"
        if is_meme:
            content_type = "meme"
        elif len(ocr_text) > 500:
            content_type = "document"
        elif any(word in text_lower for word in ['скриншот', 'screenshot']):
            content_type = "screenshot"
        
        return {
            "type": content_type,
            "confidence": 0.6,  # Низкая уверенность для OCR-only классификации
            "tags": [],
            "is_meme": is_meme,
            "method": "ocr_fallback"
        }

