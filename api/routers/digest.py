"""
Digest API endpoints for managing user digest settings and history.
Context7: валидация topics (обязательно для включенных дайджестов)
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from uuid import UUID
from datetime import date, time, datetime, timezone
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import and_

from models.database import get_db, User, DigestSettings, DigestHistory, UserChannel, Channel
from api.services.enrichment_trigger_service import upsert_triggers_from_digest
from config import settings

logger = structlog.get_logger()
router = APIRouter(prefix="/digest", tags=["digest"])

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class DigestSettingsRequest(BaseModel):
    """Запрос на обновление настроек дайджеста."""
    enabled: Optional[bool] = None
    schedule_time: Optional[str] = None  # Формат: "HH:MM"
    schedule_tz: Optional[str] = None
    frequency: Optional[str] = Field(None, pattern="^(daily|weekly|monthly)$")
    topics: Optional[List[str]] = Field(None, min_length=1)  # Обязательно минимум 1 тема для enabled=True
    channels_filter: Optional[List[str]] = None  # Список channel_id или null
    max_items_per_digest: Optional[int] = Field(None, ge=1, le=50)
    
    @field_validator("schedule_time")
    @classmethod
    def validate_schedule_time(cls, v: Optional[str]) -> Optional[str]:
        """Валидация формата времени."""
        if v is None:
            return None
        try:
            time.fromisoformat(v)
            return v
        except ValueError:
            raise ValueError("schedule_time должен быть в формате HH:MM (например, 09:00)")
    
    @field_validator("topics")
    @classmethod
    def validate_topics(cls, v: Optional[List[str]], info) -> Optional[List[str]]:
        """Валидация topics: если enabled=True, topics обязателен."""
        if v is None:
            return None
        if len(v) == 0:
            raise ValueError("topics не может быть пустым списком")
        return v
    
    def validate_enabled_with_topics(self):
        """Дополнительная валидация: если enabled=True, topics обязателен."""
        if self.enabled is True and (self.topics is None or len(self.topics) == 0):
            raise ValueError("topics обязателен когда enabled=True")


class DigestSettingsResponse(BaseModel):
    """Ответ с настройками дайджеста."""
    user_id: UUID
    enabled: bool
    schedule_time: str
    schedule_tz: str
    frequency: str
    topics: List[str]
    channels_filter: Optional[List[str]]
    max_items_per_digest: int
    created_at: datetime
    updated_at: datetime


class DigestHistoryResponse(BaseModel):
    """Ответ с историей дайджестов."""
    id: UUID
    user_id: UUID
    digest_date: date
    content: str
    posts_count: int
    topics: List[str]
    sent_at: Optional[datetime]
    status: str
    created_at: datetime


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("/settings/{user_id}", response_model=DigestSettingsResponse)
async def get_digest_settings(user_id: UUID, db: Session = Depends(get_db)):
    """Получить настройки дайджеста пользователя."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    settings_obj = db.query(DigestSettings).filter(DigestSettings.user_id == user_id).first()
    
    if not settings_obj:
        # Создаём настройки по умолчанию
        settings_obj = DigestSettings(
            user_id=user_id,
            enabled=False,
            schedule_time=time(9, 0),
            schedule_tz="Europe/Moscow",
            frequency="daily",
            topics=[],
            channels_filter=None,
            max_items_per_digest=10
        )
        db.add(settings_obj)
        db.commit()
        db.refresh(settings_obj)
    
    return DigestSettingsResponse(
        user_id=settings_obj.user_id,
        enabled=settings_obj.enabled,
        schedule_time=settings_obj.schedule_time.strftime("%H:%M"),
        schedule_tz=settings_obj.schedule_tz,
        frequency=settings_obj.frequency,
        topics=settings_obj.topics,
        channels_filter=settings_obj.channels_filter,
        max_items_per_digest=settings_obj.max_items_per_digest,
        created_at=settings_obj.created_at,
        updated_at=settings_obj.updated_at
    )


def _normalize_channels_filter(
    raw_channels: Optional[List[str]],
    user_id: UUID,
    db: Session
) -> Optional[List[str]]:
    """Нормализует список каналов: принимает UUID или Telegram ID и возвращает UUID."""
    if raw_channels is None:
        return None
    if len(raw_channels) == 0:
        return None

    # Загружаем каналы пользователя с tg_channel_id
    user_channels = (
        db.query(UserChannel.channel_id, Channel.tg_channel_id)
        .join(Channel, Channel.id == UserChannel.channel_id)
        .filter(UserChannel.user_id == user_id, UserChannel.is_active == True)
        .all()
    )

    if not user_channels:
        return None

    # Строим отображение "alias" -> UUID
    channel_alias_map = {}
    for channel_id, tg_channel_id in user_channels:
        uuid_value = str(channel_id)
        channel_alias_map[uuid_value] = uuid_value

        if tg_channel_id is not None:
            tg_str = str(tg_channel_id)
            channel_alias_map[tg_str] = uuid_value

            stripped = tg_str.lstrip("-")
            if stripped:
                channel_alias_map[stripped] = uuid_value

            if tg_str.startswith("-100"):
                suffix = tg_str[4:]
                if suffix:
                    channel_alias_map[suffix] = uuid_value
                    # Также учитываем вариант с добавленным -100 для положительного ввода
                    channel_alias_map[f"-100{suffix}"] = uuid_value
            else:
                # Пользователь мог передать значение без служебного префикса
                base = stripped or tg_str
                channel_alias_map[f"-100{base}"] = uuid_value

    normalized: List[str] = []
    seen: set[str] = set()
    invalid_entries: List[str] = []

    for raw_value in raw_channels:
        if raw_value is None:
            continue

        candidate = str(raw_value).strip()
        if not candidate:
            continue

        mapped_uuid: Optional[str] = None

        # Пробуем UUID напрямую
        try:
            uuid_candidate = str(UUID(candidate))
            mapped_uuid = channel_alias_map.get(uuid_candidate)
        except ValueError:
            mapped_uuid = None

        # Если UUID не найден, проверяем alias
        if mapped_uuid is None:
            mapped_uuid = channel_alias_map.get(candidate)

        # Дополнительные попытки для числовых значений (например, "139883458")
        if mapped_uuid is None:
            digits = candidate.lstrip("+-")
            if digits.isdigit():
                positive_form = digits
                negative_form = f"-{digits}"
                prefixed_form = f"-100{digits}"

                mapped_uuid = (
                    channel_alias_map.get(positive_form)
                    or channel_alias_map.get(negative_form)
                    or channel_alias_map.get(prefixed_form)
                )

        if mapped_uuid is None:
            invalid_entries.append(candidate)
            continue

        if mapped_uuid not in seen:
            seen.add(mapped_uuid)
            normalized.append(mapped_uuid)

    if invalid_entries:
        invalid_display = ", ".join(invalid_entries[:5])
        raise HTTPException(
            status_code=400,
            detail=f"channels_filter содержит неизвестные каналы: {invalid_display}"
        )

    return normalized or None


@router.put("/settings/{user_id}", response_model=DigestSettingsResponse)
async def update_digest_settings(
    user_id: UUID,
    settings_data: DigestSettingsRequest,
    db: Session = Depends(get_db)
):
    """
    Обновить настройки дайджеста пользователя.
    
    Context7: Валидация - если enabled=True, topics обязателен.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Проверяем валидацию enabled + topics
    if settings_data.enabled is True:
        # Если enabled устанавливается в True, проверяем что topics есть
        if settings_data.topics is None or len(settings_data.topics) == 0:
            # Проверяем существующие настройки
            existing = db.query(DigestSettings).filter(DigestSettings.user_id == user_id).first()
            if not existing or not existing.topics or len(existing.topics) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="topics обязателен когда enabled=True. Укажите хотя бы одну тему."
                )
    
    # Получаем или создаём настройки
    settings_obj = db.query(DigestSettings).filter(DigestSettings.user_id == user_id).first()
    
    if not settings_obj:
        settings_obj = DigestSettings(user_id=user_id)
        db.add(settings_obj)
    
    # Обновляем поля
    if settings_data.enabled is not None:
        settings_obj.enabled = settings_data.enabled
    if settings_data.schedule_time is not None:
        settings_obj.schedule_time = time.fromisoformat(settings_data.schedule_time)
    if settings_data.schedule_tz is not None:
        settings_obj.schedule_tz = settings_data.schedule_tz
    if settings_data.frequency is not None:
        settings_obj.frequency = settings_data.frequency
    if settings_data.topics is not None:
        settings_obj.topics = settings_data.topics
    if settings_data.channels_filter is not None:
        settings_obj.channels_filter = _normalize_channels_filter(
            settings_data.channels_filter,
            user_id,
            db
        )
    if settings_data.max_items_per_digest is not None:
        settings_obj.max_items_per_digest = settings_data.max_items_per_digest
    
    # Финальная проверка: если enabled=True, но topics пустой - ошибка
    if settings_obj.enabled and (not settings_obj.topics or len(settings_obj.topics) == 0):
        raise HTTPException(
            status_code=400,
            detail="topics обязателен когда enabled=True. Укажите хотя бы одну тему."
        )

    if settings_obj.topics:
        updated_triggers = upsert_triggers_from_digest(db, user, settings_obj.topics)
        logger.info(
            "Crawl triggers updated from digest settings",
            user_id=str(user_id),
            triggers_count=len(updated_triggers.triggers or []),
        )
    
    db.commit()
    db.refresh(settings_obj)
    
    logger.info(
        "Digest settings updated",
        user_id=str(user_id),
        enabled=settings_obj.enabled,
        topics_count=len(settings_obj.topics) if settings_obj.topics else 0
    )
    
    return DigestSettingsResponse(
        user_id=settings_obj.user_id,
        enabled=settings_obj.enabled,
        schedule_time=settings_obj.schedule_time.strftime("%H:%M"),
        schedule_tz=settings_obj.schedule_tz,
        frequency=settings_obj.frequency,
        topics=settings_obj.topics,
        channels_filter=settings_obj.channels_filter,
        max_items_per_digest=settings_obj.max_items_per_digest,
        created_at=settings_obj.created_at,
        updated_at=settings_obj.updated_at
    )


@router.get("/history/{user_id}", response_model=List[DigestHistoryResponse])
async def get_digest_history(
    user_id: UUID,
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Получить историю дайджестов пользователя."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    history = db.query(DigestHistory).filter(
        DigestHistory.user_id == user_id
    ).order_by(
        DigestHistory.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    return [
        DigestHistoryResponse(
            id=item.id,
            user_id=item.user_id,
            digest_date=item.digest_date,
            content=item.content,
            posts_count=item.posts_count,
            topics=item.topics,
            sent_at=item.sent_at,
            status=item.status,
            created_at=item.created_at
        )
        for item in history
    ]


@router.post("/generate/{user_id}")
async def generate_digest_now(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    """
    Сгенерировать дайджест для пользователя немедленно.
    
    Context7: Проверяет наличие topics в настройках перед генерацией.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем tenant_id
    tenant_id = getattr(request.state, 'tenant_id', None)
    if not tenant_id and user.tenant_id:
        tenant_id = str(user.tenant_id)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Не задан tenant_id для пользователя")
    
    # Получаем настройки, чтобы убедиться в наличии тем
    digest_settings = db.query(DigestSettings).filter(DigestSettings.user_id == user_id).first()
    if not digest_settings or not digest_settings.topics:
        raise HTTPException(status_code=400, detail="Нет тем для генерации дайджеста. Добавьте topics в настройках.")

    try:
        from tasks.scheduler_tasks import generate_digest_for_user

        requested_by = getattr(request.state, "user_id", None)
        if not requested_by and request.client:
            requested_by = request.client.host

        history = await generate_digest_for_user(
            user_id=str(user_id),
            tenant_id=tenant_id,
            topics=digest_settings.topics,
            db=db,
            trigger="manual",
            requested_by=str(requested_by) if requested_by else None
        )

        if not history:
            raise HTTPException(status_code=400, detail="Не удалось поставить дайджест в очередь")

        logger.info(
            "Digest generation scheduled",
            user_id=str(user_id),
            digest_id=str(history.id),
            status=history.status
        )

        response_payload = {
            "digest_id": str(history.id),
            "status": history.status,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "posts_count": history.posts_count,
            "topics": history.topics,
            "content": history.content if history.content else None,
            "sent_at": history.sent_at.isoformat() if history.sent_at else None,
        }

        return response_payload

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error scheduling digest generation", error=str(e), user_id=str(user_id))
        raise HTTPException(status_code=500, detail="Ошибка постановки дайджеста в очередь")

