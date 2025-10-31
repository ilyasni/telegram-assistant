"""
DLQ Event Schema V1
Context7 best practice: dead letter queue для failed events
"""

from datetime import datetime
from typing import Literal, Optional, Dict, Any
from pydantic import Field

from .base import BaseEvent


class DLQEventV1(BaseEvent):
    """
    Событие в Dead Letter Queue.
    
    Содержит информацию о failed event и причине ошибки.
    """
    
    event_type: Literal["dlq.message"] = "dlq.message"
    schema_version: Literal["1.0"] = "1.0"
    
    # Оригинальное событие
    base_event_type: str = Field(..., description="Тип оригинального события")
    payload_snippet: Dict[str, Any] = Field(..., description="Первые 1KB payload")
    
    # Ошибка
    error_code: str = Field(
        ...,
        description="Код ошибки: retryable_network | non_retryable_validation | quota_exceeded"
    )
    error_details: str = Field(..., description="Детали ошибки (макс 500 символов)")
    error_stack_trace: Optional[str] = Field(None, description="Stack trace (макс 1KB)")
    
    # Retry logic
    retry_count: int = Field(..., description="Количество retry попыток", ge=0)
    first_seen_at: datetime = Field(..., description="Время первого появления")
    last_seen_at: datetime = Field(..., description="Время последнего обновления")
    next_retry_at: Optional[datetime] = Field(None, description="Время следующей попытки retry")
    
    # Tenant context
    tenant_id: Optional[str] = Field(None, description="ID tenant (если известен)")

