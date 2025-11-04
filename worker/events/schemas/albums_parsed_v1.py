"""
Схема события albums.parsed v1
[C7-ID: EVENTS-SCHEMA-ALBUM-001]

Событие: альбом распарсен и сохранён в БД
"""

from datetime import datetime
from typing import List, Optional
from pydantic import Field

from .base import BaseEvent


class AlbumParsedEventV1(BaseEvent):
    """
    Событие: альбом распарсен из Telegram канала и сохранён в БД.
    
    Публикуется telethon-ingest после успешного сохранения альбома через save_media_group().
    Служит триггером для дальнейшей обработки (vision, indexing, assembly).
    """
    
    # Идентификаторы
    user_id: str = Field(..., description="ID пользователя (tenant)")
    channel_id: str = Field(..., description="ID канала в системе")
    album_id: int = Field(..., description="ID альбома в media_groups (BIGINT)")
    grouped_id: int = Field(..., description="Telegram grouped_id (уникальный для канала)")
    tenant_id: str = Field(..., description="ID арендатора")
    
    # Метаданные альбома
    album_kind: Optional[str] = Field(None, description="Тип альбома: photo, video, mixed")
    items_count: int = Field(..., description="Количество элементов в альбоме")
    caption_text: Optional[str] = Field(None, description="Текст альбома из первого сообщения")
    posted_at: Optional[datetime] = Field(None, description="Время публикации альбома в Telegram")
    
    # Ссылки на элементы
    post_ids: List[str] = Field(default_factory=list, description="Список post_id элементов альбома (в порядке)")
    cover_media_id: Optional[str] = Field(None, description="UUID media_object для обложки альбома")
    
    # Метаданные для дедупликации
    content_hash: Optional[str] = Field(None, description="SHA256 хеш контента альбома")
    
    class Config:
        schema_extra = {
            "example": {
                "schema_version": "v1",
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "occurred_at": "2024-10-24T13:00:00Z",
                "idempotency_key": "tenant_123:channel_456:grouped_789",
                "user_id": "user_123",
                "channel_id": "channel_456",
                "album_id": 12345,
                "grouped_id": 7890123456,
                "tenant_id": "tenant_123",
                "album_kind": "photo",
                "items_count": 5,
                "caption_text": "Серия фотографий с конференции",
                "posted_at": "2024-10-24T12:30:00Z",
                "post_ids": ["post_1", "post_2", "post_3", "post_4", "post_5"],
                "cover_media_id": "550e8400-e29b-41d4-a716-446655440001",
                "content_hash": "a1b2c3d4e5f6..."
            }
        }

