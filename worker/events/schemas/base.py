"""
Базовый класс для всех событий
[C7-ID: EVENTS-SCHEMA-001]

Содержит общие поля: schema_version, trace_id, occurred_at, idempotency_key
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """
    Базовый класс для всех событий в системе.
    
    Содержит обязательные поля для трассировки, версионирования и идемпотентности.
    """
    
    # Версия схемы события для эволюции без breaking changes
    schema_version: str = Field(
        default="v1", 
        description="Версия схемы события для совместимости"
    )
    
    # Уникальный идентификатор трассировки (OpenTelemetry compatible)
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Идентификатор трассировки для корреляции логов"
    )
    
    # Время возникновения события (UTC ISO 8601)
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Время возникновения события в UTC"
    )
    
    # Ключ идемпотентности для предотвращения дублирования
    idempotency_key: str = Field(
        ...,
        description="Уникальный ключ для идемпотентности обработки"
    )
    
    class Config:
        # Использовать enum values для сериализации
        use_enum_values = True
        # Валидировать присвоение
        validate_assignment = True
        # Позволить популяцию по имени поля
        allow_population_by_field_name = True
        # JSON encoders для datetime
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
