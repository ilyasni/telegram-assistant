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
    tenant_id: Optional[str] = Field(None, description="ID арендатора для персонализации")
    user_id: Optional[str] = Field(None, description="ID пользователя, для которого обработан пост")
    channel_id: Optional[str] = Field(None, description="ID канала")
    
    # Результаты тегирования (упрощённая схема)
    tags: List[str] = Field(
        default_factory=list,
        description="Список тегов как строк"
    )
    tags_hash: str = Field(..., description="SHA256 хеш тегов для дедупликации")
    topics: List[str] = Field(
        default_factory=list,
        description="Активные темы пользователя на момент тегирования"
    )
    
    # Метаданные AI провайдера
    provider: str = Field(default="gigachat", description="AI провайдер (gigachat|openrouter|local)")
    latency_ms: Optional[int] = Field(None, description="Латентность тегирования в миллисекундах")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные метаданные")
    
    # Context7: Версионирование и анти-петля для retagging
    trigger: Optional[str] = Field(
        default="initial",
        description="Триггер тегирования: initial (начальное), vision_retag (ретеггинг после Vision), manual (ручное)"
    )
    vision_version: Optional[str] = Field(
        default=None,
        description="Версия Vision анализа, которая использовалась при тегировании (например, 'vision@2025-01-29#p3')"
    )
    
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
                "tenant_id": "tenant_123",
                "user_id": "user_123",
                "channel_id": "channel_456",
                "tags": ["технологии", "искусственный интеллект", "машинное обучение"],
                "tags_hash": "a1b2c3d4e5f6789...",
                "topics": ["ai", "машинное обучение"],
                "provider": "gigachat",
                "latency_ms": 1250,
                "metadata": {"model": "GigaChat:latest", "language": "ru"}
            }
        }
