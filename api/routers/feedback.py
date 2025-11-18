"""Feedback API endpoints for user feedback management.

Context7: API для создания и управления feedback от пользователей с поддержкой статусов
и multi-tenant изоляции.

Endpoints:
- POST /api/feedback/ - создать feedback (для бота)
- GET /api/feedback/ - список feedback (с фильтрами, для админов)
- GET /api/feedback/{feedback_id} - получить feedback
- PATCH /api/feedback/{feedback_id} - обновить статус/заметки (только админы)
- GET /api/feedback/my - мои feedback (для пользователя)
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
import uuid
import structlog
from sqlalchemy.orm import Session
from models.database import get_db, User, UserFeedback
from dependencies.auth import get_admin_user

logger = structlog.get_logger()
router = APIRouter(prefix="/api/feedback", tags=["feedback"])


# Pydantic модели
class FeedbackCreate(BaseModel):
    """Модель для создания feedback."""
    message: str = Field(..., min_length=3, max_length=5000, description="Текст feedback")
    user_id: Optional[UUID] = Field(None, description="ID пользователя (опционально, для бота)")


class FeedbackResponse(BaseModel):
    """Модель ответа с feedback."""
    id: UUID
    user_id: UUID
    tenant_id: UUID
    message: str
    status: str
    admin_notes: Optional[str] = None
    resolved_by: Optional[UUID] = None
    created_at: str
    updated_at: str
    user_username: Optional[str] = None
    user_first_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class FeedbackUpdate(BaseModel):
    """Модель для обновления feedback (только для админов)."""
    status: Optional[str] = Field(None, description="Статус: pending, in_progress, resolved, closed")
    admin_notes: Optional[str] = Field(None, max_length=5000, description="Заметки админа")


class FeedbackListResponse(BaseModel):
    """Модель ответа со списком feedback."""
    items: List[FeedbackResponse]
    total: int
    limit: int
    offset: int


# Endpoints
@router.post("/", response_model=FeedbackResponse, status_code=201)
async def create_feedback(
    feedback_data: FeedbackCreate,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Создать новый feedback.
    
    Context7: Для запросов из бота user_id передается в теле запроса.
    Для запросов из Mini App user_id извлекается из JWT.
    """
    # Context7: Определяем user_id и tenant_id
    user_id = feedback_data.user_id
    
    # Если user_id не передан, пытаемся извлечь из JWT
    if not user_id:
        from dependencies.auth import extract_user_id_from_jwt
        user_id_str = extract_user_id_from_jwt(request)
        if user_id_str:
            try:
                user_id = uuid.UUID(user_id_str)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid user_id format in JWT")
    
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    
    # Получаем пользователя
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    tenant_id = user.tenant_id
    
    # Создаем feedback
    feedback = UserFeedback(
        user_id=user.id,
        tenant_id=tenant_id,
        message=feedback_data.message,
        status="pending"
    )
    
    try:
        db.add(feedback)
        db.commit()
        db.refresh(feedback)
        
        logger.info(
            "Feedback created",
            feedback_id=str(feedback.id),
            user_id=str(user.id),
            tenant_id=str(tenant_id),
            message_length=len(feedback_data.message)
        )
        
        return FeedbackResponse(
            id=feedback.id,
            user_id=feedback.user_id,
            tenant_id=feedback.tenant_id,
            message=feedback.message,
            status=feedback.status,
            admin_notes=feedback.admin_notes,
            resolved_by=feedback.resolved_by,
            created_at=feedback.created_at.isoformat(),
            updated_at=feedback.updated_at.isoformat(),
            user_username=user.username,
            user_first_name=user.first_name
        )
    except Exception as e:
        db.rollback()
        logger.error("Failed to create feedback", error=str(e), user_id=str(user_id))
        raise HTTPException(status_code=500, detail="Failed to create feedback")


@router.get("/", response_model=FeedbackListResponse)
async def list_feedback(
    request: Request,
    status: Optional[str] = Query(None, description="Фильтр по статусу"),
    user_id: Optional[str] = Query(None, description="Фильтр по user_id"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Получить список feedback (только для админов).
    
    Context7: Фильтрация по tenant_id для multi-tenant изоляции.
    """
    tenant_id = admin_user.tenant_id
    
    # Валидация статуса
    valid_statuses = ['pending', 'in_progress', 'resolved', 'closed']
    if status and status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    
    # Построение запроса
    query = db.query(UserFeedback).filter(UserFeedback.tenant_id == tenant_id)
    
    if status:
        query = query.filter(UserFeedback.status == status)
    
    if user_id:
        try:
            user_uuid = uuid.UUID(user_id)
            query = query.filter(UserFeedback.user_id == user_uuid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    # Получаем общее количество
    total = query.count()
    
    # Применяем пагинацию и сортировку
    feedback_list = query.order_by(UserFeedback.created_at.desc()).offset(offset).limit(limit).all()
    
    # Формируем ответ с информацией о пользователях
    items = []
    for feedback in feedback_list:
        user = db.query(User).filter(User.id == feedback.user_id).first()
        items.append(FeedbackResponse(
            id=feedback.id,
            user_id=feedback.user_id,
            tenant_id=feedback.tenant_id,
            message=feedback.message,
            status=feedback.status,
            admin_notes=feedback.admin_notes,
            resolved_by=feedback.resolved_by,
            created_at=feedback.created_at.isoformat(),
            updated_at=feedback.updated_at.isoformat(),
            user_username=user.username if user else None,
            user_first_name=user.first_name if user else None
        ))
    
    logger.info(
        "Admin listed feedback",
        admin_id=str(admin_user.id),
        tenant_id=str(tenant_id),
        total=total,
        returned=len(items),
        status=status
    )
    
    return FeedbackListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset
    )


@router.get("/{feedback_id}", response_model=FeedbackResponse)
async def get_feedback(
    feedback_id: str,
    request: Request,
    admin_user: Optional[User] = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Получить feedback по ID.
    
    Context7: Админы могут видеть любой feedback в своем tenant.
    Пользователи могут видеть только свой feedback.
    """
    try:
        feedback_uuid = uuid.UUID(feedback_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid feedback_id format")
    
    feedback = db.query(UserFeedback).filter(UserFeedback.id == feedback_uuid).first()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    
    # Context7: Проверка прав доступа
    if admin_user:
        # Админ может видеть feedback в своем tenant
        if feedback.tenant_id != admin_user.tenant_id:
            raise HTTPException(status_code=403, detail="Access denied: different tenant")
    else:
        # Пользователь может видеть только свой feedback
        # Извлекаем user_id из JWT
        from dependencies.auth import extract_user_id_from_jwt
        user_id_str = extract_user_id_from_jwt(request)
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        try:
            user_uuid = uuid.UUID(user_id_str)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid user_id format")
        
        if feedback.user_id != user_uuid:
            raise HTTPException(status_code=403, detail="Access denied: can only view own feedback")
    
    user = db.query(User).filter(User.id == feedback.user_id).first()
    
    return FeedbackResponse(
        id=feedback.id,
        user_id=feedback.user_id,
        tenant_id=feedback.tenant_id,
        message=feedback.message,
        status=feedback.status,
        admin_notes=feedback.admin_notes,
        resolved_by=feedback.resolved_by,
        created_at=feedback.created_at.isoformat(),
        updated_at=feedback.updated_at.isoformat(),
        user_username=user.username if user else None,
        user_first_name=user.first_name if user else None
    )


@router.patch("/{feedback_id}", response_model=FeedbackResponse)
async def update_feedback(
    feedback_id: str,
    feedback_update: FeedbackUpdate,
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Обновить feedback (только для админов).
    
    Context7: Админы могут изменять статус и добавлять заметки.
    """
    try:
        feedback_uuid = uuid.UUID(feedback_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid feedback_id format")
    
    feedback = db.query(UserFeedback).filter(UserFeedback.id == feedback_uuid).first()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    
    # Context7: Проверка tenant_id
    if feedback.tenant_id != admin_user.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied: different tenant")
    
    # Валидация статуса
    valid_statuses = ['pending', 'in_progress', 'resolved', 'closed']
    if feedback_update.status and feedback_update.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    
    # Обновление полей
    if feedback_update.status is not None:
        feedback.status = feedback_update.status
        # Если статус changed to resolved, сохраняем resolved_by
        if feedback_update.status == 'resolved' and not feedback.resolved_by:
            feedback.resolved_by = admin_user.id
    
    if feedback_update.admin_notes is not None:
        feedback.admin_notes = feedback_update.admin_notes
    
    try:
        db.commit()
        db.refresh(feedback)
        
        logger.info(
            "Feedback updated",
            feedback_id=str(feedback.id),
            admin_id=str(admin_user.id),
            status=feedback.status,
            has_admin_notes=bool(feedback.admin_notes)
        )
        
        user = db.query(User).filter(User.id == feedback.user_id).first()
        
        return FeedbackResponse(
            id=feedback.id,
            user_id=feedback.user_id,
            tenant_id=feedback.tenant_id,
            message=feedback.message,
            status=feedback.status,
            admin_notes=feedback.admin_notes,
            resolved_by=feedback.resolved_by,
            created_at=feedback.created_at.isoformat(),
            updated_at=feedback.updated_at.isoformat(),
            user_username=user.username if user else None,
            user_first_name=user.first_name if user else None
        )
    except Exception as e:
        db.rollback()
        logger.error("Failed to update feedback", error=str(e), feedback_id=feedback_id)
        raise HTTPException(status_code=500, detail="Failed to update feedback")


@router.get("/my", response_model=FeedbackListResponse)
async def get_my_feedback(
    request: Request,
    status: Optional[str] = Query(None, description="Фильтр по статусу"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    db: Session = Depends(get_db)
):
    """
    Получить мои feedback (для пользователя).
    
    Context7: Пользователь видит только свой feedback, извлекая user_id из JWT.
    """
    # Извлекаем user_id из JWT
    from dependencies.auth import extract_user_id_from_jwt
    user_id_str = extract_user_id_from_jwt(request)
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        user_uuid = uuid.UUID(user_id_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Валидация статуса
    valid_statuses = ['pending', 'in_progress', 'resolved', 'closed']
    if status and status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    
    # Построение запроса
    query = db.query(UserFeedback).filter(UserFeedback.user_id == user.id)
    
    if status:
        query = query.filter(UserFeedback.status == status)
    
    # Получаем общее количество
    total = query.count()
    
    # Применяем пагинацию и сортировку
    feedback_list = query.order_by(UserFeedback.created_at.desc()).offset(offset).limit(limit).all()
    
    # Формируем ответ
    items = []
    for feedback in feedback_list:
        items.append(FeedbackResponse(
            id=feedback.id,
            user_id=feedback.user_id,
            tenant_id=feedback.tenant_id,
            message=feedback.message,
            status=feedback.status,
            admin_notes=feedback.admin_notes,
            resolved_by=feedback.resolved_by,
            created_at=feedback.created_at.isoformat(),
            updated_at=feedback.updated_at.isoformat(),
            user_username=user.username,
            user_first_name=user.first_name
        ))
    
    logger.info(
        "User listed own feedback",
        user_id=str(user.id),
        total=total,
        returned=len(items),
        status=status
    )
    
    return FeedbackListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset
    )

