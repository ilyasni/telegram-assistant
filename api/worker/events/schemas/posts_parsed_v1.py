"""
Схема события posts.parsed v1
[C7-ID: EVENTS-SCHEMA-001]

Событие: пост распарсен из Telegram канала
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import Field

from .base import BaseEvent


class PostParsedEventV1(BaseEvent):
    """
    Событие: пост распарсен из Telegram канала.
    
    Публикуется telethon-ingest после успешного парсинга поста.
    """
    
    # Идентификаторы
    user_id: str = Field(..., description="ID пользователя (tenant)")
    channel_id: str = Field(..., description="ID канала в системе")
    post_id: str = Field(..., description="ID поста в системе")
    tenant_id: str = Field(..., description="ID арендатора")
    
    # Контент
    text: str = Field(..., description="Текст поста")
    urls: List[str] = Field(default_factory=list, description="URL из поста")
    posted_at: datetime = Field(..., description="Время публикации в Telegram")
    
    # Context7: Медиа-метаданные для связи с обработанными медиа файлами
    media_sha256_list: List[str] = Field(
        default_factory=list,
        description="Список SHA256 хешей обработанных медиа файлов (для связи с media_objects)"
    )
    
    # Метаданные для дедупликации и enrichment
    content_hash: Optional[str] = Field(None, description="SHA256 хеш контента")
    link_count: int = Field(default=0, description="Количество ссылок в посте")
    
    # Telegram метаданные
    tg_message_id: int = Field(..., description="ID сообщения в Telegram (legacy)")
    telegram_message_id: int = Field(..., description="ID сообщения в Telegram (new name)")
    tg_channel_id: int = Field(..., description="ID канала в Telegram")
    telegram_post_url: Optional[str] = Field(None, description="Прямая ссылка на пост в Telegram")
    has_media: bool = Field(default=False, description="Есть ли медиа в посте")
    is_edited: bool = Field(default=False, description="Был ли пост отредактирован")
    views_count: int = Field(default=0, description="Количество просмотров")
    forwards_count: int = Field(default=0, description="Количество пересылок")
    reactions_count: int = Field(default=0, description="Количество реакций")
    
    # Context7 P2: Данные о forwards для Graph-RAG
    forward_from_peer_id: Optional[Dict[str, Any]] = Field(
        None,
        description="Peer ID источника форварда (JSON: user_id/channel_id/chat_id)"
    )
    forward_from_chat_id: Optional[int] = Field(
        None,
        description="Chat ID источника форварда (упрощённый доступ)"
    )
    forward_from_message_id: Optional[int] = Field(
        None,
        description="Message ID исходного сообщения"
    )
    forward_date: Optional[datetime] = Field(
        None,
        description="Дата оригинального сообщения"
    )
    forward_from_name: Optional[str] = Field(
        None,
        description="Имя автора оригинального сообщения"
    )
    
    # Context7 P2: Данные о replies для Graph-RAG
    reply_to_message_id: Optional[int] = Field(
        None,
        description="Message ID поста, на который отвечают"
    )
    reply_to_chat_id: Optional[int] = Field(
        None,
        description="Chat ID канала/чата исходного поста"
    )
    thread_id: Optional[int] = Field(
        None,
        description="ID треда (для каналов с комментариями)"
    )
    
    # Context7 P2: Данные об авторе для Graph-RAG
    author_peer_id: Optional[Dict[str, Any]] = Field(
        None,
        description="Peer ID автора (JSON: user_id/channel_id/chat_id)"
    )
    author_name: Optional[str] = Field(
        None,
        description="Имя автора поста"
    )
    author_type: Optional[str] = Field(
        None,
        description="Тип автора ('user', 'channel', 'chat')"
    )
    
    class Config:
        # Пример для документации
        schema_extra = {
            "example": {
                "schema_version": "v1",
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "occurred_at": "2024-10-24T13:00:00Z",
                "idempotency_key": "tenant_123:channel_456:message_789",
                "user_id": "user_123",
                "channel_id": "channel_456", 
                "post_id": "post_789",
                "tenant_id": "tenant_123",
                "text": "Интересная новость о технологиях...",
                "urls": ["https://example.com/article"],
                "posted_at": "2024-10-24T12:30:00Z",
                "content_hash": "a1b2c3d4e5f6...",
                "link_count": 1,
                "tg_message_id": 12345,
                "telegram_message_id": 12345,
                "tg_channel_id": -1001234567890,
                "telegram_post_url": "https://t.me/example_channel/12345",
                "has_media": False,
                "is_edited": False,
                "views_count": 150,
                "forwards_count": 5,
                "reactions_count": 12
            }
        }
