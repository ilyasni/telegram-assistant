"""
Event schemas для channel management.
"""

from datetime import datetime, timezone
from typing import Optional, Literal
from uuid import UUID
from pydantic import BaseModel, Field

from .base import BaseEvent

class ChannelSubscribedEventV1(BaseEvent):
    """Событие: пользователь подписался на канал."""
    event_id: UUID = Field(default_factory=lambda: UUID())
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = Field(..., description="Trace ID запроса")
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    tenant_id: str = Field(..., description="ID арендатора")
    user_id: str = Field(..., description="ID пользователя")
    channel_id: str = Field(..., description="ID канала")
    channel_username: Optional[str] = Field(None, description="Username канала")
    source: Literal["api"] = Field(default="api", description="Источник события")
    version: Literal["v1"] = Field(default="v1", description="Версия схемы")

class ChannelUnsubscribedEventV1(BaseEvent):
    """Событие: пользователь отписался от канала."""
    event_id: UUID = Field(default_factory=lambda: UUID())
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = Field(..., description="Trace ID запроса")
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    tenant_id: str = Field(..., description="ID арендатора")
    user_id: str = Field(..., description="ID пользователя")
    channel_id: str = Field(..., description="ID канала")
    channel_username: Optional[str] = Field(None, description="Username канала")
    source: Literal["api"] = Field(default="api", description="Источник события")
    version: Literal["v1"] = Field(default="v1", description="Версия схемы")
