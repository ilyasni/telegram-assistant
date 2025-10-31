"""
Vision Analysis Event Schema V1
Context7 best practice: версионирование событий, trace propagation
"""

from datetime import datetime
from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field

from .base import BaseEvent


class MediaFile(BaseModel):
    """Медиа файл для Vision анализа."""
    sha256: str = Field(..., description="SHA256 хеш файла")
    s3_key: str = Field(..., description="S3 ключ файла")
    mime_type: str = Field(..., description="MIME тип")
    size_bytes: int = Field(..., description="Размер в байтах", ge=0)


class VisionAnalysisResult(BaseModel):
    """Результаты Vision анализа."""
    provider: str = Field(..., description="Провайдер (gigachat | ocr_fallback)")
    model: str = Field(..., description="Модель (GigaChat-Pro, etc)")
    schema_version: str = Field(default="1.0", description="Версия схемы результатов")
    
    classification: Dict[str, Any] = Field(
        ...,
        description="Классификация контента: {type, confidence, tags}"
    )
    description: Optional[str] = Field(None, description="Текстовое описание")
    ocr_text: Optional[str] = Field(None, description="Извлечённый текст (OCR)")
    is_meme: bool = Field(default=False, description="Мем или нет")
    context: Optional[Dict[str, Any]] = Field(None, description="Контекст: objects, emotions, themes")
    
    tokens_used: int = Field(..., description="Использовано токенов", ge=0)
    file_id: Optional[str] = Field(None, description="GigaChat file_id для кэша")
    analyzed_at: datetime = Field(..., description="Время анализа")


class VisionAnalyzedEventV1(BaseEvent):
    """
    Событие: Vision анализ завершён.
    
    Публикуется Vision Worker после успешного анализа медиа.
    """
    
    # Event metadata (в BaseEvent)
    event_type: Literal["posts.vision.analyzed"] = "posts.vision.analyzed"
    schema_version: Literal["1.0"] = "1.0"
    schema_ref: str = Field(default="posts_vision_analyzed_v1.json")
    producer: str = Field(default="vision-worker")
    
    # Контекст
    tenant_id: str = Field(..., description="ID tenant")
    post_id: str = Field(..., description="ID поста")
    
    # Медиа
    media: List[MediaFile] = Field(..., description="Анализируемые медиа файлы")
    
    # Vision результаты
    vision: VisionAnalysisResult = Field(..., description="Результаты Vision анализа")
    
    # Метрики
    analysis_duration_ms: int = Field(..., description="Длительность анализа в мс", ge=0)
    
    # Dedupe key для идемпотентности
    @staticmethod
    def build_dedupe_key(tenant_id: str, post_id: str, sha256: str) -> str:
        """Построение dedupe key."""
        return f"{tenant_id}:{post_id}:{sha256}"


class VisionUploadedEventV1(BaseEvent):
    """
    Событие: Медиа загружено и готово для Vision анализа.
    
    Публикуется Telethon Ingestion после загрузки медиа в S3.
    """
    
    event_type: Literal["posts.vision.uploaded"] = "posts.vision.uploaded"
    schema_version: Literal["1.0"] = "1.0"
    producer: str = Field(default="telethon-ingest")
    
    tenant_id: str = Field(..., description="ID tenant")
    post_id: str = Field(..., description="ID поста")
    
    media_files: List[MediaFile] = Field(..., description="Загруженные медиа файлы")
    
    # Для фильтрации: только подходящие для GigaChat Vision
    requires_vision: bool = Field(
        default=True,
        description="Требуется ли Vision анализ (проверка размера/формата)"
    )

