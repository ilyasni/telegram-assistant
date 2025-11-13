"""
Схема события posts.enriched v1
[C7-ID: EVENTS-SCHEMA-001]

Событие: пост обогащён через Crawl4AI
"""

from typing import List, Dict, Any, Optional
from pydantic import Field

from .base import BaseEvent


class PostEnrichedEventV1(BaseEvent):
    """
    Событие: пост обогащён через Crawl4AI.
    
    Публикуется Crawl4AI сервисом после успешного обогащения.
    """
    
    # Идентификаторы
    post_id: str = Field(..., description="ID поста в системе")
    
    # Результаты обогащения
    enrichment_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Данные обогащения (извлеченный контент, метаданные)"
    )
    
    # Источники обогащения
    source_urls: List[str] = Field(
        default_factory=list,
        description="URL источники обогащения"
    )
    
    # Статистика контента
    word_count: Optional[int] = Field(None, description="Количество слов в обогащенном контенте")
    original_word_count: Optional[int] = Field(None, description="Количество слов в оригинальном тексте")
    
    # Статус обогащения
    skipped: bool = Field(default=False, description="Было ли обогащение пропущено")
    skip_reason: Optional[str] = Field(None, description="Причина пропуска обогащения")
    
    # Метаданные Crawl4AI
    crawl_duration_ms: Optional[int] = Field(None, description="Длительность crawl операции")
    crawl_success: Optional[bool] = Field(None, description="Успешность crawl операции")
    policy_applied: Optional[str] = Field(None, description="Примененная политика обогащения")
    
    # Качество обогащения
    quality_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Оценка качества обогащения (0.0-1.0)"
    )
    
    class Config:
        # Пример для документации
        schema_extra = {
            "example": {
                "schema_version": "v1",
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "occurred_at": "2024-10-24T13:02:00Z",
                "idempotency_key": "post_789:enriched:v1",
                "post_id": "post_789",
                "enrichment_data": {
                    "title": "Новости технологий",
                    "content": "Расширенный контент статьи...",
                    "author": "Tech News",
                    "published_at": "2024-10-24T10:00:00Z",
                    "summary": "Краткое изложение статьи"
                },
                "source_urls": ["https://example.com/article"],
                "word_count": 1250,
                "original_word_count": 150,
                "skipped": False,
                "skip_reason": None,
                "crawl_duration_ms": 3500,
                "crawl_success": True,
                "policy_applied": "longread_enrichment",
                "quality_score": 0.88
            }
        }
