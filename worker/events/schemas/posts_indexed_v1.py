"""
Схема события posts.indexed v1
[C7-ID: EVENTS-SCHEMA-001]

Событие: пост проиндексирован в Qdrant и Neo4j
"""

from typing import Optional
from pydantic import Field

from .base import BaseEvent


class PostIndexedEventV1(BaseEvent):
    """
    Событие: пост проиндексирован в Qdrant и Neo4j.
    
    Публикуется worker после успешной индексации в векторной БД и графе.
    """
    
    # Идентификаторы
    post_id: str = Field(..., description="ID поста в системе")
    
    # Qdrant метаданные
    vector_id: str = Field(..., description="ID вектора в Qdrant")
    embedding_provider: str = Field(..., description="Провайдер эмбеддингов (gigachat|openrouter)")
    embedding_dim: int = Field(..., description="Размерность эмбеддинга")
    qdrant_collection: str = Field(..., description="Название коллекции в Qdrant")
    
    # Neo4j метаданные
    neo4j_nodes_created: int = Field(default=0, description="Количество созданных узлов в Neo4j")
    neo4j_relationships_created: int = Field(default=0, description="Количество созданных связей в Neo4j")
    
    # Статистика индексации
    indexing_duration_ms: int = Field(..., description="Длительность индексации в миллисекундах")
    embedding_generation_ms: int = Field(..., description="Длительность генерации эмбеддинга")
    qdrant_indexing_ms: int = Field(..., description="Длительность индексации в Qdrant")
    neo4j_indexing_ms: int = Field(..., description="Длительность индексации в Neo4j")
    
    # Качество индексации
    embedding_quality_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Оценка качества эмбеддинга"
    )
    
    class Config:
        # Пример для документации
        schema_extra = {
            "example": {
                "schema_version": "v1",
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "occurred_at": "2024-10-24T13:03:00Z",
                "idempotency_key": "post_789:indexed:v1",
                "post_id": "post_789",
                "vector_id": "vector_12345",
                "embedding_provider": "gigachat",
                "embedding_dim": 1536,
                "qdrant_collection": "user_123_posts",
                "neo4j_nodes_created": 5,
                "neo4j_relationships_created": 8,
                "indexing_duration_ms": 2500,
                "embedding_generation_ms": 1200,
                "qdrant_indexing_ms": 800,
                "neo4j_indexing_ms": 500,
                "embedding_quality_score": 0.92
            }
        }
