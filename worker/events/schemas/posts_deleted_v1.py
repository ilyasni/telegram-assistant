"""
Схема события posts.deleted v1
[C7-ID: EVENTS-SCHEMA-001]

Событие: пост удалён из системы (TTL или пользователь)
"""

from typing import Optional
from pydantic import Field

from .base import BaseEvent


class PostDeletedEventV1(BaseEvent):
    """
    Событие: пост удалён из системы.
    
    Публикуется cleanup task после удаления поста из всех хранилищ.
    """
    
    # Идентификаторы
    post_id: str = Field(..., description="ID поста в системе")
    tenant_id: str = Field(..., description="ID арендатора")
    channel_id: str = Field(..., description="ID канала")
    
    # Причина удаления
    reason: str = Field(..., description="Причина удаления (ttl|user|admin)")
    
    # Статус очистки из хранилищ
    qdrant_cleaned: bool = Field(default=False, description="Удален ли из Qdrant")
    neo4j_cleaned: bool = Field(default=False, description="Удален ли из Neo4j")
    postgres_cleaned: bool = Field(default=False, description="Удален ли из PostgreSQL")
    
    # Метаданные удаления
    deletion_duration_ms: int = Field(..., description="Длительность удаления в миллисекундах")
    qdrant_deletion_ms: Optional[int] = Field(None, description="Длительность удаления из Qdrant")
    neo4j_deletion_ms: Optional[int] = Field(None, description="Длительность удаления из Neo4j")
    postgres_deletion_ms: Optional[int] = Field(None, description="Длительность удаления из PostgreSQL")
    
    # Статистика очистки
    related_vectors_deleted: int = Field(default=0, description="Количество удаленных векторов")
    related_nodes_deleted: int = Field(default=0, description="Количество удаленных узлов в Neo4j")
    orphan_tags_cleaned: int = Field(default=0, description="Количество очищенных висячих тегов")
    
    # Время истечения (для TTL)
    expired_at: Optional[str] = Field(None, description="Время истечения поста (ISO 8601)")
    
    class Config:
        # Пример для документации
        schema_extra = {
            "example": {
                "schema_version": "v1",
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "occurred_at": "2024-10-24T13:04:00Z",
                "idempotency_key": "post_789:deleted:v1",
                "post_id": "post_789",
                "tenant_id": "tenant_123",
                "channel_id": "channel_456",
                "reason": "ttl",
                "qdrant_cleaned": True,
                "neo4j_cleaned": True,
                "postgres_cleaned": True,
                "deletion_duration_ms": 1200,
                "qdrant_deletion_ms": 400,
                "neo4j_deletion_ms": 300,
                "postgres_deletion_ms": 500,
                "related_vectors_deleted": 1,
                "related_nodes_deleted": 5,
                "orphan_tags_cleaned": 2,
                "expired_at": "2024-10-24T12:00:00Z"
            }
        }
