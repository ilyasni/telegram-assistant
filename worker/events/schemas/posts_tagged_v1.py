"""
Схема события posts.tagged v1
[C7-ID: EVENTS-SCHEMA-001]

Событие: пост протегирован через AI провайдер
"""

from typing import List, Dict, Any, Optional
import hashlib
import json
from pydantic import Field

from .base import BaseEvent


class PostTaggedEventV1(BaseEvent):
    """
    Событие: пост протегирован через AI провайдер.
    
    Публикуется worker после успешного тегирования поста.
    """
    
    # Идентификаторы
    post_id: str = Field(..., description="ID поста в системе")
    
    # Результаты тегирования (упрощённая схема)
    tags: List[str] = Field(
        default_factory=list,
        description="Список тегов как строк"
    )
    tags_hash: str = Field(..., description="SHA256 хеш тегов для дедупликации")
    
    # Метаданные AI провайдера
    provider: str = Field(default="gigachat", description="AI провайдер (gigachat|openrouter|local)")
    latency_ms: Optional[int] = Field(None, description="Латентность тегирования в миллисекундах")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные метаданные")
    
    @staticmethod
    def compute_hash(tags: List[str]) -> str:
        """Вычисление хеша тегов для дедупликации."""
        # Нормализуем порядок для стабильного хеша
        norm = json.dumps(sorted(set([t for t in tags if t])), ensure_ascii=False)
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()
    
    class Config:
        # Пример для документации
        schema_extra = {
            "example": {
                "schema_version": "v1",
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "occurred_at": "2024-10-24T13:01:00Z",
                "idempotency_key": "post_789:tagged:v1",
                "post_id": "post_789",
                "tags": ["технологии", "искусственный интеллект", "машинное обучение"],
                "tags_hash": "a1b2c3d4e5f6789...",
                "provider": "gigachat",
                "latency_ms": 1250,
                "metadata": {"model": "GigaChat:latest", "language": "ru"}
            }
        }
