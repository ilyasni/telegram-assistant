"""
Base event schema.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field

class BaseEvent(BaseModel):
    """Базовый класс для всех событий."""
    schema_version: str = Field(default="v1", description="Версия схемы события")
    trace_id: str = Field(..., description="Trace ID запроса")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
