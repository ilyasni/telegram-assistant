"""
Digest API endpoints for managing user digest settings and history.
Context7: валидация topics (обязательно для включенных дайджестов)
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from uuid import UUID
from datetime import date, time, datetime
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import and_

from models.database import get_db, User, DigestSettings, DigestHistory
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
        settings_obj.channels_filter = settings_data.channels_filter
    if settings_data.max_items_per_digest is not None:
        settings_obj.max_items_per_digest = settings_data.max_items_per_digest
    
    # Финальная проверка: если enabled=True, но topics пустой - ошибка
    if settings_obj.enabled and (not settings_obj.topics or len(settings_obj.topics) == 0):
        raise HTTPException(
            status_code=400,
            detail="topics обязателен когда enabled=True. Укажите хотя бы одну тему."
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
    tenant_id = getattr(request.state, 'tenant_id', None) or str(user.tenant_id)
    
    # Получаем DigestService
    from services.digest_service import get_digest_service
    digest_service = get_digest_service()
    
    try:
        # Генерируем дайджест
        digest_content = await digest_service.generate(
            user_id=user_id,
            tenant_id=tenant_id,
            db=db
        )
        
        # Сохраняем в историю
        digest_history = DigestHistory(
            user_id=user_id,
            digest_date=date.today(),
            content=digest_content.content,
            posts_count=digest_content.posts_count,
            topics=digest_content.topics,
            status="pending"
        )
        db.add(digest_history)
        db.commit()
        db.refresh(digest_history)
        
        logger.info(
            "Digest generated",
            user_id=str(user_id),
            digest_id=str(digest_history.id),
            posts_count=digest_content.posts_count
        )
        
        return {
            "digest_id": str(digest_history.id),
            "content": digest_content.content,
            "posts_count": digest_content.posts_count,
            "topics": digest_content.topics,
            "sections": digest_content.sections
        }
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error generating digest", error=str(e), user_id=str(user_id))
        raise HTTPException(status_code=500, detail="Ошибка генерации дайджеста")

