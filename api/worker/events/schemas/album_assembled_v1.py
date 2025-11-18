"""
Схема события album.assembled v1
[C7-ID: EVENTS-SCHEMA-ALBUM-002]

Событие: альбом собран - все элементы прошли vision анализ
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import Field

from .base import BaseEvent


class AlbumAssembledEventV1(BaseEvent):
    """
    Событие: альбом собран - все элементы прошли vision анализ.
    
    Публикуется album_assembler_task после того, как все элементы альбома прошли vision анализ.
    Служит триггером для дальнейшей обработки (indexing, RAG, Graph).
    """
    
    # Идентификаторы
    user_id: str = Field(..., description="ID пользователя (tenant)")
    channel_id: str = Field(..., description="ID канала в системе")
    album_id: int = Field(..., description="ID альбома в media_groups (BIGINT)")
    grouped_id: int = Field(..., description="Telegram grouped_id")
    tenant_id: str = Field(..., description="ID арендатора")
    
    # Метаданные альбома
    album_kind: Optional[str] = Field(None, description="Тип альбома: photo, video, mixed")
    items_count: int = Field(..., description="Количество элементов в альбоме")
    items_analyzed: int = Field(..., description="Количество элементов, прошедших vision анализ")
    
    # Vision summary на уровне альбома
    vision_summary: Optional[str] = Field(None, description="Обобщённое описание альбома на основе vision анализа всех элементов")
    vision_labels: List[str] = Field(default_factory=list, description="Объединённые метки из всех элементов альбома")
    vision_ocr_text: Optional[str] = Field(None, description="Объединённый OCR текст из всех элементов альбома")
    vision_tags: List[str] = Field(default_factory=list, description="Объединённые теги из всех элементов альбома")
    has_meme: bool = Field(default=False, description="Содержит ли альбом мемы")
    has_text: bool = Field(default=False, description="Содержит ли альбом текст (OCR)")
    s3_key: Optional[str] = Field(None, description="S3 ключ для vision summary альбома (album/{tenant}/{album_id}_vision_summary_v1.json)")
    
    # Временные метки
    posted_at: Optional[datetime] = Field(None, description="Время публикации альбома в Telegram")
    first_analyzed_at: Optional[datetime] = Field(None, description="Время первого vision анализа элемента")
    last_analyzed_at: Optional[datetime] = Field(None, description="Время последнего vision анализа элемента")
    assembly_completed_at: datetime = Field(..., description="Время завершения сборки альбома")
    
    # Метаданные для метрик
    assembly_lag_seconds: Optional[float] = Field(None, description="Задержка сборки альбома в секундах (от первого до последнего анализа)")
    
    class Config:
        schema_extra = {
            "example": {
                "schema_version": "v1",
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "occurred_at": "2024-10-24T13:05:00Z",
                "idempotency_key": "tenant_123:channel_456:grouped_789:assembled",
                "user_id": "user_123",
                "channel_id": "channel_456",
                "album_id": 12345,
                "grouped_id": 7890123456,
                "tenant_id": "tenant_123",
                "album_kind": "photo",
                "items_count": 5,
                "items_analyzed": 5,
                "vision_summary": "Серия фотографий с конференции: выступления спикеров, демонстрации продуктов, networking",
                "vision_labels": ["conference", "speaker", "product", "networking"],
                "vision_tags": ["technology", "business", "event"],
                "has_meme": False,
                "has_text": True,
                "posted_at": "2024-10-24T12:30:00Z",
                "first_analyzed_at": "2024-10-24T13:00:00Z",
                "last_analyzed_at": "2024-10-24T13:05:00Z",
                "assembly_completed_at": "2024-10-24T13:05:00Z",
                "assembly_lag_seconds": 300.0
            }
        }

