"""Users API endpoints for Telegram bot integration."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
import uuid
import structlog
from sqlalchemy.orm import Session
from models.database import get_db, User, Tenant, Identity
from config import settings

logger = structlog.get_logger()
router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    invite_code: Optional[str] = None


class UserResponse(BaseModel):
    id: UUID
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    created_at: str


class SubscriptionInfo(BaseModel):
    subscription_type: str
    subscription_expires_at: Optional[str]
    is_active: bool
    channels_limit: int
    posts_limit: int
    rag_queries_limit: int


@router.get("/{telegram_id}", response_model=UserResponse)
async def get_user_by_telegram_id(
    telegram_id: int, 
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Получить пользователя по telegram_id.
    Context7: Если есть tenant_id в JWT - фильтруем по tenant, иначе возвращаем первый найденный.
    """
    from dependencies.auth import get_current_tenant_id_optional
    
    # Context7: Извлекаем tenant_id из JWT если доступен
    tenant_id = get_current_tenant_id_optional(request)
    
    query = db.query(User)
    
    if tenant_id:
        # Context7: Фильтруем по tenant_id для изоляции данных
        query = query.filter(User.tenant_id == uuid.UUID(tenant_id))
    
    # Ищем через identity для корректной работы с multi-tenant
    identity = db.query(Identity).filter(Identity.telegram_id == telegram_id).first()
    if identity:
        query = query.filter(User.identity_id == identity.id)
    else:
        # Fallback: если identity не найдена, ищем напрямую по telegram_id (legacy)
        query = query.filter(User.telegram_id == telegram_id)
    
    user = query.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        created_at=user.created_at.isoformat()
    )


@router.post("/", response_model=UserResponse)
async def create_user(user_data: UserCreate, db: Session = Depends(get_db)):
    """Создать новый membership (участник тенанта) для Telegram-пользователя."""
    # TODO: Валидация инвайт-кода (пока пропускаем)

    # 1) Определяем/создаём tenant
    tenant_id_cfg = settings.default_tenant_id
    if tenant_id_cfg:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id_cfg).first()
        if not tenant:
            tenant = Tenant(id=tenant_id_cfg, name="Default Tenant")
            db.add(tenant)
            db.flush()
    else:
        # fallback: создаём отдельного tenant
        tenant_name = f"Tenant {user_data.username}" if user_data.username else f"Tenant {user_data.telegram_id}"
        tenant = Tenant(name=tenant_name)
        db.add(tenant)
        db.flush()

    # 2-3) Upsert identity и membership через общую утилиту (Context7: избегаем дублирования)
    from utils.identity_membership import upsert_identity_and_membership_sync
    
    try:
        identity_id, user_id = upsert_identity_and_membership_sync(
            db=db,
            tenant_id=tenant.id,
            telegram_id=user_data.telegram_id,
            username=user_data.username,
            first_name=user_data.first_name,
            last_name=user_data.last_name,
            tier="free"
        )
        db.commit()
        
        # Получаем созданный/обновлённый user
        user = db.query(User).filter(User.id == user_id).first()
        db.refresh(user)
    except Exception as e:
        db.rollback()
        logger.error("Failed to create membership", error=str(e), telegram_id=user_data.telegram_id)
        raise HTTPException(status_code=500, detail="Failed to create membership")

    logger.info(
        "Membership created",
        user_id=str(user.id),
        tenant_id=str(tenant.id),
        identity_id=str(identity.id),
        telegram_id=user_data.telegram_id,
    )

    return UserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        created_at=user.created_at.isoformat(),
    )


@router.get("/{user_id}/subscription", response_model=SubscriptionInfo)
async def get_user_subscription(
    user_id: str, 
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Получить информацию о подписке пользователя.
    Context7: Поддерживает поиск по UUID или telegram_id с фильтрацией по tenant.
    """
    from dependencies.auth import get_current_tenant_id_optional
    
    # Context7: Извлекаем tenant_id из JWT если доступен
    tenant_id = get_current_tenant_id_optional(request)
    
    query = db.query(User)
    
    if tenant_id:
        # Context7: Фильтруем по tenant_id для изоляции данных
        query = query.filter(User.tenant_id == uuid.UUID(tenant_id))
    
    # Определяем, является ли user_id UUID или telegram_id
    try:
        # Пытаемся преобразовать в int - если получилось, это telegram_id
        telegram_id = int(user_id)
        # Ищем через identity для корректной работы с multi-tenant
        identity = db.query(Identity).filter(Identity.telegram_id == telegram_id).first()
        if identity:
            user = query.filter(User.identity_id == identity.id).first()
        else:
            # Fallback: если identity не найдена, ищем напрямую по telegram_id (legacy)
            user = query.filter(User.telegram_id == telegram_id).first()
    except ValueError:
        # Если не получилось преобразовать в int, считаем что это UUID
        user = query.filter(User.id == uuid.UUID(user_id)).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Лимиты по типу подписки
    limits = {
        "free": {"channels": 3, "posts": 100, "rag_queries": 10},
        "basic": {"channels": 10, "posts": 1000, "rag_queries": 100},
        "premium": {"channels": 50, "posts": 10000, "rag_queries": 1000}
    }
    
    user_limits = limits.get(user.tier or "free", limits["free"])
    
    return SubscriptionInfo(
        subscription_type=user.tier or "free",
        subscription_expires_at=None,  # У нас нет поля expires_at
        is_active=True,  # У нас нет поля is_active
        channels_limit=user_limits["channels"],
        posts_limit=user_limits["posts"],
        rag_queries_limit=user_limits["rag_queries"]
    )
