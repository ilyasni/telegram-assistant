"""
Event schemas для работы с Telegram-группами и дайджестами.
"""

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import Field, conlist

from .base import BaseEvent


WindowSize = Literal[4, 6, 12, 24]
DeliveryChannel = Literal["telegram", "email", "webhook"]
DigestFormat = Literal["markdown", "json"]


class GroupLinkedEventV1(BaseEvent):
    """Событие: пользователь подключил группу для мониторинга."""

    event_id: UUID = Field(default_factory=uuid4, description="ID события")
    tenant_id: UUID = Field(..., description="ID арендатора")
    user_id: UUID = Field(..., description="ID пользователя-инициатора")
    group_id: UUID = Field(..., description="ID группы в Postgres")
    tg_chat_id: int = Field(..., description="Telegram chat id (может быть отрицательным)")
    title: str = Field(..., description="Название группы")
    username: Optional[str] = Field(None, description="Username, если доступен")
    trace_id: str = Field(..., description="Trace ID запроса")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    source: Literal["api", "miniapp"] = Field(..., description="Источник события")
    version: Literal["v1"] = Field(default="v1", description="Версия схемы")


class GroupConversationWindowReadyEventV1(BaseEvent):
    """Событие: собранное окно обсуждений готово для дальнейшей обработки."""

    event_id: UUID = Field(default_factory=uuid4, description="ID события")
    tenant_id: UUID = Field(..., description="ID арендатора")
    group_id: UUID = Field(..., description="ID группы")
    window_id: UUID = Field(..., description="ID окна обсуждения")
    window_size_hours: WindowSize = Field(..., description="Размер окна в часах")
    window_start: datetime = Field(..., description="Начало окна")
    window_end: datetime = Field(..., description="Конец окна")
    message_count: int = Field(..., description="Количество сообщений в окне")
    participant_ids: set[UUID] = Field(..., min_items=1, description="Участники окна")
    indicators: dict = Field(..., description="Индикаторы настроений (conflict/collab/stress/enthusiasm)")
    trace_id: str = Field(..., description="Trace ID пайплайна")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    source: Literal["ingest", "worker"] = Field(default="worker", description="Источник события")
    version: Literal["v1"] = Field(default="v1", description="Версия схемы")


class GroupDigestRequestedEventV1(BaseEvent):
    """Событие: пользователь запросил дайджест по группе."""

    event_id: UUID = Field(default_factory=uuid4, description="ID события")
    tenant_id: UUID = Field(..., description="ID арендатора")
    user_id: UUID = Field(..., description="ID пользователя-инициатора")
    group_id: UUID = Field(..., description="ID группы")
    window_size_hours: WindowSize = Field(..., description="Окно (4/6/12/24 часа)")
    delivery_channel: DeliveryChannel = Field(default="telegram", description="Канал доставки")
    delivery_address: Optional[str] = Field(None, description="Адрес доставки для email/webhook")
    format: DigestFormat = Field(default="markdown", description="Формат дайджеста")
    include_sections: conlist(str, min_items=1) = Field(
        default_factory=lambda: ["topics", "metrics", "participants"],
        description="Секции, которые нужно включить",
    )
    trace_id: str = Field(..., description="Trace ID запроса")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    source: Literal["bot", "api", "miniapp"] = Field(default="bot", description="Источник события")
    version: Literal["v1"] = Field(default="v1", description="Версия схемы")


class GroupDigestGeneratedEventV1(BaseEvent):
    """Событие: дайджест сформирован мультиагентной системой."""

    event_id: UUID = Field(default_factory=uuid4, description="ID события")
    tenant_id: UUID = Field(..., description="ID арендатора")
    digest_id: UUID = Field(..., description="ID дайджеста")
    window_id: UUID = Field(..., description="ID окна, для которого сформирован дайджест")
    window_size_hours: WindowSize = Field(..., description="Размер окна")
    summary: str = Field(..., description="Резюме обсуждения")
    topics: list[dict] = Field(..., description="Список тем (topic, priority, message_count, highlights)")
    participants: list[dict] = Field(..., description="Активные участники и их роли")
    metrics: dict = Field(..., description="Метрики эмоций/индикаторов")
    evaluation_scores: dict = Field(..., description="Результаты авто-оценки качества")
    attachments: Optional[list[dict]] = Field(None, description="Дополнительные вложения (например, ссылки на отчёты)")
    trace_id: str = Field(..., description="Trace ID пайплайна")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    source: Literal["worker"] = Field(default="worker", description="Источник события")
    version: Literal["v1"] = Field(default="v1", description="Версия схемы")


class GroupDigestDeliveredEventV1(BaseEvent):
    """Событие: дайджест доставлен пользователю."""

    event_id: UUID = Field(default_factory=uuid4, description="ID события")
    tenant_id: UUID = Field(..., description="ID арендатора")
    digest_id: UUID = Field(..., description="ID дайджеста")
    delivery_channel: DeliveryChannel = Field(..., description="Канал доставки")
    delivery_address: Optional[str] = Field(None, description="Адрес доставки")
    delivered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    delivery_status: Literal["sent", "failed"] = Field(..., description="Статус доставки")
    failure_reason: Optional[str] = Field(None, description="Причина отказа (если есть)")
    evaluation_scores: Optional[dict] = Field(None, description="Финальные оценки качества (если перегенерация)")
    trace_id: str = Field(..., description="Trace ID пайплайна")
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    source: Literal["worker", "bot"] = Field(default="worker", description="Источник события")
    version: Literal["v1"] = Field(default="v1", description="Версия схемы")

