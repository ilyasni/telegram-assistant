"""Users API endpoints for Telegram bot integration."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
import structlog
from sqlalchemy.orm import Session
from models.database import get_db, User, Tenant
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
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    created_at: str


class SubscriptionInfo(BaseModel):
    subscription_type: str
    subscription_expires_at: Optional[str]
    is_active: bool
    channels_limit: int
    posts_limit: int
    rag_queries_limit: int


@router.get("/{telegram_id}", response_model=UserResponse)
async def get_user_by_telegram_id(telegram_id: int, db: Session = Depends(get_db)):
    """Получить пользователя по telegram_id."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        created_at=user.created_at.isoformat()
    )


@router.post("/", response_model=UserResponse)
async def create_user(user_data: UserCreate, db: Session = Depends(get_db)):
    """Создать нового пользователя."""
    # Проверить, не существует ли уже пользователь
    existing_user = db.query(User).filter(User.telegram_id == user_data.telegram_id).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")
    
    # TODO: Валидация инвайт-кода (пока пропускаем)
    
    # Создать пользователя без хардкода tenant_id
    user = User(
        telegram_id=user_data.telegram_id,
        username=user_data.username,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        tenant_id=None
    )
    db.add(user)
    db.flush()  # получаем user.id без полного коммита

    # Context7 best practice: tenant
    tenant_id_cfg = settings.default_tenant_id
    tenant = None
    if tenant_id_cfg:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id_cfg).first()
        if not tenant:
            tenant = Tenant(id=tenant_id_cfg, name="Default Tenant")
            db.add(tenant)
            db.flush()
    else:
        # fallback: создаём отдельного tenant и привязываем пользователя
        tenant_name = f"Tenant {user.username}" if user.username else f"Tenant {user.telegram_id}"
        tenant = Tenant(name=tenant_name)
        db.add(tenant)
        db.flush()

    user.tenant_id = tenant.id
    db.commit()
    db.refresh(user)
    
    logger.info("User created", user_id=str(user.id), telegram_id=user_data.telegram_id)
    
    return UserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        created_at=user.created_at.isoformat()
    )


@router.get("/{user_id}/subscription", response_model=SubscriptionInfo)
async def get_user_subscription(user_id: UUID, db: Session = Depends(get_db)):
    """Получить информацию о подписке пользователя."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Лимиты по типу подписки
    limits = {
        "trial": {"channels": 3, "posts": 100, "rag_queries": 10},
        "basic": {"channels": 10, "posts": 1000, "rag_queries": 100},
        "premium": {"channels": 50, "posts": 10000, "rag_queries": 1000}
    }
    
    user_limits = limits.get(user.subscription_type or "trial", limits["trial"])
    
    return SubscriptionInfo(
        subscription_type=user.subscription_type or "trial",
        subscription_expires_at=user.subscription_expires_at.isoformat() if user.subscription_expires_at else None,
        is_active=user.is_active,
        channels_limit=user_limits["channels"],
        posts_limit=user_limits["posts"],
        rag_queries_limit=user_limits["rag_queries"]
    )
