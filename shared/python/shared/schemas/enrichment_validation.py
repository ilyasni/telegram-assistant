"""
Pydantic модели для валидации данных обогащения.

Context7 best practice: валидация данных перед сохранением в БД/Qdrant/Neo4j.
Используется для предотвращения ошибок и обеспечения консистентности данных.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator
import structlog

logger = structlog.get_logger()


class OCRData(BaseModel):
    """OCR данные из Vision анализа."""
    text: str = Field(..., description="Извлечённый текст")
    engine: Optional[str] = Field(None, description="OCR engine (gigachat, tesseract)")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence score")
    
    @field_validator('text')
    @classmethod
    def validate_text(cls, v: str) -> str:
        """Валидация OCR текста."""
        if not v or not v.strip():
            raise ValueError("OCR text cannot be empty")
        if len(v) > 100000:  # Лимит 100KB
            logger.warning("OCR text too long, truncating", original_length=len(v))
            return v[:100000]
        return v.strip()


class VisionEnrichmentData(BaseModel):
    """Валидация Vision enrichment данных."""
    model: str = Field(..., description="Модель (GigaChat-Pro, etc)")
    model_version: Optional[str] = Field(None, description="Версия модели")
    provider: str = Field(..., description="Провайдер (gigachat, ocr_fallback)")
    analyzed_at: str = Field(..., description="ISO timestamp анализа")
    classification: str = Field(..., description="Тип классификации")
    description: str = Field(..., min_length=1, description="Описание изображения")
    is_meme: bool = Field(default=False, description="Является ли мемом")
    labels: List[str] = Field(default_factory=list, max_length=20, description="Метки")
    objects: List[str] = Field(default_factory=list, max_length=10, description="Объекты")
    scene: Optional[str] = Field(None, description="Описание сцены")
    ocr: Optional[OCRData] = Field(None, description="OCR данные")
    nsfw_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="NSFW score")
    aesthetic_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Aesthetic score")
    dominant_colors: List[str] = Field(default_factory=list, max_length=5, description="Цвета")
    context: Dict[str, Any] = Field(default_factory=dict, description="Контекст")
    s3_keys: Dict[str, str] = Field(default_factory=dict, description="S3 ключи")
    file_id: Optional[str] = Field(None, description="GigaChat file_id")
    tokens_used: int = Field(default=0, ge=0, description="Использовано токенов")
    cost_microunits: int = Field(default=0, ge=0, description="Стоимость в microunits")
    analysis_reason: str = Field(default="new", description="Причина анализа")
    s3_keys_list: List[Dict[str, Any]] = Field(default_factory=list, description="S3 keys list")
    
    @field_validator('analyzed_at')
    @classmethod
    def validate_analyzed_at(cls, v: str) -> str:
        """Валидация ISO timestamp."""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"Invalid ISO timestamp: {v}")
        return v
    
    @field_validator('classification')
    @classmethod
    def validate_classification(cls, v: str) -> str:
        """Валидация типа классификации."""
        valid_types = {'photo', 'meme', 'document', 'screenshot', 'infographic', 'other'}
        if v not in valid_types:
            logger.warning("Invalid classification type, using 'other'", classification=v)
            return 'other'
        return v
    
    @field_validator('dominant_colors')
    @classmethod
    def validate_colors(cls, v: List[str]) -> List[str]:
        """Валидация hex цветов."""
        import re
        hex_pattern = re.compile(r'^#[0-9A-Fa-f]{6}$')
        validated = []
        for color in v[:5]:  # Максимум 5 цветов
            if hex_pattern.match(color):
                validated.append(color.upper())
            else:
                logger.warning("Invalid hex color format", color=color)
        return validated
    
    @field_validator('ocr', mode='before')
    @classmethod
    def validate_ocr(cls, v: Any) -> Optional[OCRData]:
        """
        Context7: Валидация OCR данных перед созданием OCRData объекта.
        Конвертирует пустые OCR объекты в None для корректной обработки.
        """
        if v is None:
            return None
        
        # Если это словарь, проверяем наличие валидного текста
        if isinstance(v, dict):
            ocr_text = v.get("text")
            # Если текст пустой или отсутствует, возвращаем None
            if not ocr_text or not str(ocr_text).strip():
                logger.debug("OCR text is empty, converting to None", ocr_dict=v)
                return None
            # Если текст валидный, создаем OCRData объект
            try:
                return OCRData(**v)
            except Exception as e:
                logger.warning("Failed to create OCRData, converting to None", error=str(e), ocr_dict=v)
                return None
        
        # Если это уже OCRData объект, возвращаем как есть
        if isinstance(v, OCRData):
            return v
        
        # Для других типов пытаемся создать OCRData
        try:
            return OCRData(**v) if isinstance(v, dict) else None
        except Exception:
            logger.warning("Invalid OCR format, converting to None", ocr_value=v)
            return None


class CrawlEnrichmentData(BaseModel):
    """Валидация Crawl enrichment данных."""
    url: str = Field(..., description="URL источника")
    url_hash: str = Field(..., description="SHA256 hash нормализованного URL")
    content_sha256: Optional[str] = Field(None, description="SHA256 hash контента")
    markdown: Optional[str] = Field(None, description="Markdown контент")
    html: Optional[str] = Field(None, description="HTML контент")
    word_count: int = Field(default=0, ge=0, description="Количество слов")
    s3_keys: Dict[str, str] = Field(default_factory=dict, description="S3 ключи")
    crawled_at: Optional[str] = Field(None, description="ISO timestamp crawl")
    
    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Валидация URL."""
        from urllib.parse import urlparse
        parsed = urlparse(v)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid URL: {v}")
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        return v
    
    @field_validator('crawled_at')
    @classmethod
    def validate_crawled_at(cls, v: Optional[str]) -> Optional[str]:
        """Валидация ISO timestamp."""
        if v is None:
            return None
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"Invalid ISO timestamp: {v}")
        return v


class QdrantPayload(BaseModel):
    """Валидация Qdrant payload."""
    post_id: str = Field(..., description="Post ID")
    tenant_id: str = Field(..., description="Tenant ID")
    channel_id: Optional[str] = Field(None, description="Channel ID")
    tags: List[str] = Field(default_factory=list, description="Теги")
    vision: Optional[Dict[str, Any]] = Field(None, description="Vision данные")
    crawl: Optional[Dict[str, Any]] = Field(None, description="Crawl данные")
    album_id: Optional[str] = Field(None, description="Album ID")
    posted_at: Optional[int] = Field(None, description="Posted timestamp")
    expires_at: Optional[int] = Field(None, description="Expires timestamp")
    has_media: bool = Field(default=False, description="Has media")
    content_length: int = Field(default=0, ge=0, description="Content length")
    
    @field_validator('post_id', 'tenant_id')
    @classmethod
    def validate_uuid_format(cls, v: str) -> str:
        """Валидация UUID формата (опционально)."""
        # Не строгая валидация - может быть не UUID в dev режиме
        if not v or len(v) < 1:
            raise ValueError(f"Invalid ID: {v}")
        return v
    
    @model_validator(mode='after')
    def validate_payload_size(self) -> 'QdrantPayload':
        """Валидация размера payload (должен быть < 64KB)."""
        import json
        payload_json = json.dumps(self.model_dump(), ensure_ascii=False)
        payload_size = len(payload_json.encode('utf-8'))
        
        if payload_size > 64000:  # 64KB лимит Qdrant
            logger.warning(
                "Qdrant payload too large, truncating",
                original_size=payload_size,
                post_id=self.post_id
            )
            # Усекаем большие поля
            if self.vision and len(str(self.vision)) > 10000:
                self.vision = {"truncated": True}
            if self.crawl and len(str(self.crawl)) > 10000:
                self.crawl = {"truncated": True}
        
        return self


class Neo4jPostNode(BaseModel):
    """Валидация Neo4j Post узла."""
    post_id: str = Field(..., description="Post ID")
    tenant_id: str = Field(..., description="Tenant ID")
    channel_id: Optional[str] = Field(None, description="Channel ID")
    content: Optional[str] = Field(None, description="Content")
    posted_at: Optional[str] = Field(None, description="Posted timestamp")
    expires_at: Optional[str] = Field(None, description="Expires timestamp")
    indexed_at: Optional[str] = Field(None, description="Indexed timestamp")
    enrichment_data: Optional[Dict[str, Any]] = Field(None, description="Enrichment data")
    
    @field_validator('post_id', 'tenant_id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Валидация ID."""
        if not v or len(v) < 1:
            raise ValueError(f"Invalid ID: {v}")
        return v
    
    @field_validator('posted_at', 'expires_at', 'indexed_at')
    @classmethod
    def validate_timestamp(cls, v: Optional[str]) -> Optional[str]:
        """Валидация ISO timestamp."""
        if v is None:
            return None
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"Invalid ISO timestamp: {v}")
        return v


def validate_vision_enrichment(data: Dict[str, Any]) -> VisionEnrichmentData:
    """
    Валидация Vision enrichment данных.
    
    Context7: Валидация перед сохранением в БД.
    
    Args:
        data: Словарь с Vision данными
        
    Returns:
        Валидированный VisionEnrichmentData
        
    Raises:
        ValidationError: Если данные невалидны
    """
    try:
        return VisionEnrichmentData(**data)
    except Exception as e:
        logger.error("Vision enrichment validation failed", error=str(e), data_keys=list(data.keys()))
        raise


def validate_crawl_enrichment(data: Dict[str, Any]) -> CrawlEnrichmentData:
    """
    Валидация Crawl enrichment данных.
    
    Context7: Валидация перед сохранением в БД.
    
    Args:
        data: Словарь с Crawl данными
        
    Returns:
        Валидированный CrawlEnrichmentData
        
    Raises:
        ValidationError: Если данные невалидны
    """
    try:
        return CrawlEnrichmentData(**data)
    except Exception as e:
        logger.error("Crawl enrichment validation failed", error=str(e), data_keys=list(data.keys()))
        raise


def validate_qdrant_payload(payload: Dict[str, Any]) -> QdrantPayload:
    """
    Валидация Qdrant payload.
    
    Context7: Валидация перед индексацией в Qdrant.
    
    Args:
        payload: Словарь с payload данными
        
    Returns:
        Валидированный QdrantPayload
        
    Raises:
        ValidationError: Если данные невалидны
    """
    try:
        return QdrantPayload(**payload)
    except Exception as e:
        logger.error("Qdrant payload validation failed", error=str(e), payload_keys=list(payload.keys()))
        raise


def validate_neo4j_post_node(node_data: Dict[str, Any]) -> Neo4jPostNode:
    """
    Валидация Neo4j Post узла.
    
    Context7: Валидация перед созданием узла в Neo4j.
    
    Args:
        node_data: Словарь с данными узла
        
    Returns:
        Валидированный Neo4jPostNode
        
    Raises:
        ValidationError: Если данные невалидны
    """
    try:
        return Neo4jPostNode(**node_data)
    except Exception as e:
        logger.error("Neo4j post node validation failed", error=str(e), node_keys=list(node_data.keys()))
        raise

