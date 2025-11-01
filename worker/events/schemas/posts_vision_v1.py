"""
Vision Analysis Event Schema V1
Context7 best practice: версионирование событий, trace propagation
"""

from datetime import datetime
from typing import List, Literal, Optional, Dict, Any, Annotated
from pydantic import BaseModel, Field, field_validator, StringConstraints

from .base import BaseEvent

# Type alias для constrained string
ConstrainedStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class MediaFile(BaseModel):
    """Медиа файл для Vision анализа."""
    sha256: str = Field(..., description="SHA256 хеш файла")
    s3_key: str = Field(..., description="S3 ключ файла")
    mime_type: str = Field(..., description="MIME тип")
    size_bytes: int = Field(..., description="Размер в байтах", ge=0)


class VisionEnrichment(BaseModel):
    """
    Строгая Pydantic-схема для результатов Vision анализа.
    
    Context7 best practice: валидация структуры данных для гарантированного формата
    и использования в downstream-задачах (эмбеддинги, Qdrant, Neo4j).
    """
    classification: Literal["photo", "meme", "document", "screenshot", "infographic", "other"] = Field(
        ...,
        description="Тип контента изображения"
    )
    description: Annotated[str, StringConstraints(min_length=5)] = Field(
        ...,
        description="Краткое текстовое описание содержимого (caption)"
    )
    is_meme: bool = Field(
        default=False,
        description="Является ли изображение мемом"
    )
    labels: List[ConstrainedStr] = Field(
        default_factory=list,
        max_length=20,
        description="Классы/атрибуты изображения (максимум 20)"
    )
    objects: List[str] = Field(
        default_factory=list,
        max_length=10,
        description="Объекты на изображении (максимум 10)"
    )
    scene: Optional[str] = Field(
        default=None,
        description="Описание сцены/окружения"
    )
    ocr: Optional[Dict[str, Any]] = Field(
        default=None,
        description="OCR данные: {text, engine, confidence}"
    )
    nsfw_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Оценка NSFW контента (0.0-1.0)"
    )
    aesthetic_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Эстетическая оценка изображения (0.0-1.0)"
    )
    dominant_colors: List[str] = Field(
        default_factory=list,
        max_length=5,
        description="Доминирующие цвета (hex или css-названия, максимум 5)"
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Дополнительный контекст: emotions, themes, relationships"
    )
    s3_keys: Dict[str, str] = Field(
        default_factory=dict,
        description="S3 ключи: {image, thumb, ...}"
    )
    
    @field_validator('labels', mode='before')
    @classmethod
    def normalize_labels(cls, v):
        """Нормализация labels: lowercasing и усечение."""
        if not isinstance(v, list):
            return []
        normalized = [str(label).strip().lower() for label in v if label and str(label).strip()]
        # Усечение до max_length
        return normalized[:20]
    
    @field_validator('objects', mode='before')
    @classmethod
    def normalize_objects(cls, v):
        """Нормализация objects: усечение до max_length."""
        if not isinstance(v, list):
            return []
        normalized = [str(obj).strip() for obj in v if obj and str(obj).strip()]
        return normalized[:10]
    
    @field_validator('description', mode='before')
    @classmethod
    def validate_description(cls, v):
        """Валидация description: минимальная длина."""
        if not v or len(str(v).strip()) < 5:
            raise ValueError("Description must be at least 5 characters long")
        return str(v).strip()


class VisionAnalysisResult(BaseModel):
    """Результаты Vision анализа (legacy схема для обратной совместимости)."""
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
    
    @classmethod
    def from_vision_enrichment(cls, enrichment: VisionEnrichment, provider: str, model: str, 
                               tokens_used: int, file_id: Optional[str], analyzed_at: datetime):
        """Создание VisionAnalysisResult из VisionEnrichment для обратной совместимости."""
        return cls(
            provider=provider,
            model=model,
            schema_version="1.0",
            classification={
                "type": enrichment.classification,
                "confidence": 1.0,
                "tags": enrichment.labels
            },
            description=enrichment.description,
            ocr_text=enrichment.ocr.get("text") if enrichment.ocr else None,
            is_meme=enrichment.is_meme,
            context=enrichment.context,
            tokens_used=tokens_used,
            file_id=file_id,
            analyzed_at=analyzed_at
        )


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

