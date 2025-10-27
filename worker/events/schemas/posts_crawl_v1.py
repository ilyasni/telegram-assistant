"""
Post Crawl Event Schema V1
[C7-ID: EVENT-SCHEMA-CRAWL-001]
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class PostCrawlRequestV1(BaseModel):
    """Запрос на crawling поста."""
    post_id: str = Field(..., description="UUID поста")
    urls: List[str] = Field(..., description="URL для crawling")
    tags: List[str] = Field(default_factory=list, description="Теги поста")
    trigger_reason: str = Field(..., description="Причина триггера (trigger_tag/manual)")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PostCrawlResultV1(BaseModel):
    """Результат crawling поста."""
    post_id: str
    kind: str = Field(..., description="Тип обогащения: crawl/ocr/vision")
    crawl_md: Optional[str] = Field(None, description="Markdown контент")
    ocr_text: Optional[str] = Field(None, description="OCR текст")
    vision_labels: Optional[List[str]] = Field(None, description="Vision метки")
    enrichment_provider: str = Field(default="crawl4ai")
    enrichment_latency_ms: int
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
