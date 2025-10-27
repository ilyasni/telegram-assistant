"""
Channel Management API - REST endpoints для управления каналами
Поддерживает подписки, лимиты, триггеры парсинга и статистику
"""

import asyncio
import structlog
import uuid
import re
import hashlib
import json
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from models.database import get_db
from middleware.tracing import get_trace_id
from repositories.outbox import OutboxRepository, get_outbox_repository
from events.schemas.channels_v1 import ChannelSubscribedEventV1, ChannelUnsubscribedEventV1

logger = structlog.get_logger()

router = APIRouter(prefix="/channels", tags=["channels"])

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class ChannelSubscribeRequest(BaseModel):
    """Запрос на подписку на канал."""
    username: Optional[str] = Field(None, description="Username канала (@channel_name)")
    telegram_id: Optional[int] = Field(None, description="Telegram ID канала")
    title: Optional[str] = Field(None, description="Название канала")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Настройки подписки")

class ChannelResponse(BaseModel):
    """Ответ с информацией о канале."""
    id: str
    tg_channel_id: Optional[int]
    username: Optional[str]
    title: str
    is_active: bool
    last_message_at: Optional[datetime]
    created_at: datetime
    posts_count: int = 0
    subscribers_count: int = 0

class ChannelListResponse(BaseModel):
    """Ответ со списком каналов."""
    channels: List[ChannelResponse]
    total: int
    limit: int
    offset: int

class SubscriptionStatsResponse(BaseModel):
    """Статистика подписок пользователя."""
    total_channels: int
    active_channels: int
    total_posts: int
    posts_today: int
    subscription_limit: int
    can_add_more: bool

class ChannelStatsResponse(BaseModel):
    """Статистика каналов пользователя."""
    total: int
    tier: str
    max_allowed: int
    remaining: int

# Tier limits
TIER_LIMITS = {
    "free": 3,
    "pro": 20,
    "premium": 100
}

# ============================================================================
# CHANNEL SUBSCRIPTION ENDPOINTS
# ============================================================================

@router.post("/users/{user_id}/subscribe", response_model=None, status_code=201)
def subscribe_to_channel(
    user_id: str,
    request: ChannelSubscribeRequest,
    req: Request,
    db: Session = Depends(get_db),
    outbox: OutboxRepository = Depends(get_outbox_repository)
):
    """Подписка на канал с триггером парсинга."""
    try:
        trace_id = get_trace_id(req)
        
        # Получение пользователя по telegram_id
        user_result = db.execute(
            text("SELECT id, tenant_id, tier FROM users WHERE telegram_id = :telegram_id"),
            {"telegram_id": int(user_id)}
        )
        user_row = user_result.fetchone()
        if not user_row:
            raise HTTPException(
                status_code=404, 
                detail={"error": "user_not_found", "trace_id": trace_id}
            )
        
        # Получение данных пользователя
        user_uuid = user_row.id
        tenant_id = user_row.tenant_id
        user_tier = user_row.tier or "free"
        
        # Проверка tier limits
        subscription_check = _check_subscription_limits(user_uuid, db)
        if not subscription_check['can_add_more']:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "tier_limit_exceeded",
                    "max": subscription_check['subscription_limit'],
                    "current": subscription_check['total_channels'],
                    "trace_id": trace_id
                }
            )
        
        # Валидация username
        if request.username and not re.match(r'^@?[a-zA-Z0-9_]{5,32}$', request.username):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_channel", "trace_id": trace_id}
            )
        
        # Получение или создание канала
        channel = _get_or_create_channel(
            tenant_id=tenant_id,
            username=request.username,
            telegram_id=request.telegram_id,
            title=request.title,
            db=db
        )
        
        if not channel:
            raise HTTPException(
                status_code=400, 
                detail={"error": "channel_creation_failed", "trace_id": trace_id}
            )
        
        # Создание подписки пользователя (идемпотентно)
        try:
            subscription_id = _create_user_subscription(
                user_id=user_uuid,
                channel_id=channel['id'],
                settings=request.settings,
                db=db
            )
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail={"error": "already_subscribed", "trace_id": trace_id}
            )
        
        # Генерация idempotency_key
        idempotency_key = hashlib.sha256(
            f"{tenant_id}:{user_uuid}:{channel['id']}".encode()
        ).hexdigest()
        
        # Публикация события в outbox
        event = ChannelSubscribedEventV1(
            event_id=uuid.uuid4(),
            occurred_at=datetime.now(timezone.utc),
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            tenant_id=str(tenant_id),
            user_id=str(user_uuid),
            channel_id=channel['id'],
            channel_username=request.username,
            source="api"
        )
        
        outbox.enqueue(
            event_type="channels.subscribed.v1",
            payload=event.model_dump(mode='json'),
            aggregate_id=channel['id'],
            idempotency_key=idempotency_key
        )
        
        logger.info("User subscribed to channel",
                   user_id=user_id,
                   channel_id=channel['id'],
                   trace_id=trace_id)
        
        return {
            "request_id": trace_id,
            "subscription_id": subscription_id,
            "queued": True
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Subscription failed", error=str(e), trace_id=trace_id)
        raise HTTPException(
            status_code=500, 
            detail={"error": "internal_error", "trace_id": trace_id}
        )

@router.get("/users/{user_id}/list", response_model=ChannelListResponse)
def list_user_channels(
    user_id: str,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Список подписанных каналов пользователя."""
    try:
        # Определяем, является ли user_id UUID или telegram_id
        try:
            # Пытаемся преобразовать в int - если получилось, это telegram_id
            telegram_id = int(user_id)
            user_result = db.execute(
                text("SELECT id FROM users WHERE telegram_id = :telegram_id"),
                {"telegram_id": telegram_id}
            )
            user_row = user_result.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail="User not found")
            user_uuid = str(user_row.id)
        except ValueError:
            # Если не получилось преобразовать в int, считаем что это UUID
            user_uuid = user_id
        
        # Получение каналов пользователя
        channels_result = db.execute(
            text("""
                SELECT 
                    c.id, c.tg_channel_id, c.username, c.title, c.is_active,
                    c.last_message_at, c.created_at,
                    COUNT(p.id) as posts_count,
                    COUNT(uc.user_id) as subscribers_count
                FROM channels c
                JOIN user_channel uc ON c.id = uc.channel_id
                LEFT JOIN posts p ON c.id = p.channel_id
                WHERE uc.user_id = :user_id AND uc.is_active = true
                GROUP BY c.id, c.tg_channel_id, c.username, c.title, c.is_active, c.last_message_at, c.created_at
                ORDER BY c.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"user_id": user_uuid, "limit": limit, "offset": offset}
        )
        
        channels = []
        for row in channels_result.fetchall():
            channel_data = dict(row._mapping)
            channel_data['id'] = str(channel_data['id'])
            # Переименовываем tg_channel_id в tg_channel_id для модели
            if 'tg_channel_id' in channel_data:
                channel_data['tg_channel_id'] = channel_data['tg_channel_id']
            channels.append(ChannelResponse(**channel_data))
        
        # Подсчёт общего количества
        total_result = db.execute(
            text("""
                SELECT COUNT(*) as total
                FROM user_channel uc
                WHERE uc.user_id = :user_id AND uc.is_active = true
            """),
            {"user_id": user_uuid}
        )
        total = total_result.fetchone().total
        
        return ChannelListResponse(
            channels=channels,
            total=total,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        logger.error(f"Failed to list channels: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/users/{user_id}/unsubscribe/{channel_id}")
def unsubscribe_from_channel(
    user_id: str,
    channel_id: str,
    db: Session = Depends(get_db)
):
    """Отписка от канала (soft delete)."""
    try:
        # Проверка существования подписки
        subscription_result = db.execute(
            text("""
                SELECT id FROM user_channel 
                WHERE user_id = :user_id AND channel_id = :channel_id AND is_active = true
            """),
            {"user_id": user_id, "channel_id": channel_id}
        )
        
        if not subscription_result.fetchone():
            raise HTTPException(status_code=404, detail="Subscription not found")
        
        # Soft delete подписки
        db.execute(
            text("""
                UPDATE user_channel 
                SET is_active = false, updated_at = NOW()
                WHERE user_id = :user_id AND channel_id = :channel_id
            """),
            {"user_id": user_id, "channel_id": channel_id}
        )
        
        db.commit()
        
        logger.info(f"User {user_id} unsubscribed from channel {channel_id}")
        return {"status": "unsubscribed", "channel_id": channel_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unsubscription failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/users/{user_id}/stats", response_model=ChannelStatsResponse)
def get_user_channel_stats(
    user_id: str,
    db: Session = Depends(get_db)
):
    """Статистика каналов пользователя."""
    try:
        # Получение пользователя по telegram_id и tier
        user_result = db.execute(
            text("SELECT id, tier FROM users WHERE telegram_id = :telegram_id"),
            {"telegram_id": int(user_id)}
        )
        user_row = user_result.fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")
        
        user_uuid = str(user_row.id)
        user_tier = user_row.tier or "free"
        max_allowed = TIER_LIMITS.get(user_tier, 3)
        
        # Подсчет текущих каналов
        count_result = db.execute(
            text("SELECT COUNT(*) FROM user_channel WHERE user_id = :user_id AND is_active = true"),
            {"user_id": user_uuid}
        )
        total = count_result.scalar() or 0
        
        return ChannelStatsResponse(
            total=total,
            tier=user_tier,
            max_allowed=max_allowed,
            remaining=max(0, max_allowed - total)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get channel stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ============================================================================
# CHANNEL MANAGEMENT ENDPOINTS
# ============================================================================

@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel_info(
    channel_id: str,
    db: Session = Depends(get_db)
):
    """Получение информации о канале."""
    try:
        channel_info = _get_channel_info(channel_id, db)
        if not channel_info:
            raise HTTPException(status_code=404, detail="Channel not found")
        
        return channel_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get channel info: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/{channel_id}/trigger-parsing")
async def trigger_channel_parsing(
    channel_id: str,
    user_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Ручной триггер парсинга канала."""
    try:
        # Получение tenant_id пользователя
        tenant_result = db.execute(
            text("SELECT tenant_id FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        )
        tenant_row = tenant_result.fetchone()
        if not tenant_row:
            raise HTTPException(status_code=404, detail="User not found")
        
        tenant_id = tenant_row.tenant_id
        
        # Проверка подписки пользователя на канал
        subscription_result = db.execute(
            text("""
                SELECT 1 FROM user_channel 
                WHERE user_id = :user_id AND channel_id = :channel_id AND is_active = true
            """),
            {"user_id": user_id, "channel_id": channel_id}
        )
        
        if not subscription_result.fetchone():
            raise HTTPException(status_code=403, detail="User not subscribed to this channel")
        
        # Триггер парсинга в фоне
        background_tasks.add_task(
            _trigger_channel_parsing,
            user_id=user_id,
            channel_id=channel_id,
            tenant_id=tenant_id
        )
        
        return {"status": "parsing_triggered", "channel_id": channel_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to trigger parsing: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _check_subscription_limits(user_id: str, db: Session) -> Dict[str, Any]:
    """Проверка лимитов подписки пользователя."""
    # Получение текущего количества подписок
    current_result = db.execute(
        text("""
            SELECT COUNT(*) as total_channels
            FROM user_channel 
            WHERE user_id = :user_id AND is_active = true
        """),
        {"user_id": user_id}
    )
    total_channels = current_result.fetchone().total_channels
    
    # Получение лимита подписки
    limit_result = db.execute(
        text("""
            SELECT 
                CASE 
                    WHEN tier = 'free' THEN 3
                    WHEN tier = 'basic' THEN 10
                    WHEN tier = 'premium' THEN 50
                    WHEN tier = 'enterprise' THEN 200
                    ELSE 3
                END as subscription_limit
            FROM users 
            WHERE id = :user_id
        """),
        {"user_id": user_id}
    )
    subscription_limit = limit_result.fetchone().subscription_limit
    
    return {
        'total_channels': total_channels,
        'subscription_limit': subscription_limit,
        'can_add_more': total_channels < subscription_limit
    }

def _get_or_create_channel(
    tenant_id: str,
    username: Optional[str],
    telegram_id: Optional[int],
    title: Optional[str],
    db: Session
) -> Optional[Dict[str, Any]]:
    """Получение или создание канала."""
    try:
        # Поиск существующего канала
        if telegram_id:
            existing_result = db.execute(
                text("SELECT id FROM channels WHERE tg_channel_id = :telegram_id"),
                {"telegram_id": telegram_id}
            )
            existing_row = existing_result.fetchone()
            if existing_row:
                return {"id": str(existing_row.id)}
        
        if username:
            existing_result = db.execute(
                text("SELECT id FROM channels WHERE username = :username"),
                {"username": username}
            )
            existing_row = existing_result.fetchone()
            if existing_row:
                return {"id": str(existing_row.id)}
        
        # Создание нового канала
        channel_id = str(uuid.uuid4())
        
        db.execute(
            text("""
                INSERT INTO channels (id, tg_channel_id, username, title, is_active, created_at)
                VALUES (:id, :telegram_id, :username, :title, true, NOW())
            """),
            {
                "id": channel_id,
                "telegram_id": telegram_id,
                "username": username,
                "title": title or username or f"Channel {telegram_id}"
            }
        )
        
        db.commit()
        return {"id": channel_id}
        
    except Exception as e:
        logger.error(f"Failed to get or create channel: {e}")
        db.rollback()
        return None

def _create_user_subscription(
    user_id: str,
    channel_id: str,
    settings: Dict[str, Any],
    db: Session
) -> str:
    """Создание подписки пользователя на канал."""
    try:
        # Проверка существующей подписки
        existing_result = db.execute(
            text("""
                SELECT user_id, channel_id FROM user_channel 
                WHERE user_id = :user_id AND channel_id = :channel_id
            """),
            {"user_id": user_id, "channel_id": channel_id}
        )
        
        existing_row = existing_result.fetchone()
        if existing_row:
            # Активация существующей подписки
            db.execute(
                text("""
                    UPDATE user_channel 
                    SET is_active = true, settings = :settings
                    WHERE user_id = :user_id AND channel_id = :channel_id
                """),
                {"user_id": user_id, "channel_id": channel_id, "settings": json.dumps(settings)}
            )
            subscription_id = f"{user_id}:{channel_id}"
        else:
            # Создание новой подписки
            db.execute(
                text("""
                    INSERT INTO user_channel (user_id, channel_id, is_active, settings, subscribed_at)
                    VALUES (:user_id, :channel_id, true, :settings, NOW())
                """),
                {
                    "user_id": user_id, 
                    "channel_id": channel_id, 
                    "settings": json.dumps(settings)
                }
            )
            subscription_id = f"{user_id}:{channel_id}"
        
        db.commit()
        return subscription_id
        
    except Exception as e:
        logger.error(f"Failed to create subscription: {e}")
        db.rollback()
        raise

def _get_channel_info(channel_id: str, db: Session) -> Optional[ChannelResponse]:
    """Получение информации о канале."""
    try:
        result = db.execute(
            text("""
                SELECT 
                    c.id, c.telegram_id, c.username, c.title, c.is_active,
                    c.last_message_at, c.created_at,
                    COUNT(p.id) as posts_count,
                    COUNT(uc.user_id) as subscribers_count
                FROM channels c
                LEFT JOIN posts p ON c.id = p.channel_id
                LEFT JOIN user_channel uc ON c.id = uc.channel_id AND uc.is_active = true
                WHERE c.id = :channel_id
                GROUP BY c.id, c.telegram_id, c.username, c.title, c.is_active, c.last_message_at, c.created_at
            """),
            {"channel_id": channel_id}
        )
        
        row = result.fetchone()
        if row:
            channel_data = dict(row._mapping)
            channel_data['id'] = str(channel_data['id'])
            return ChannelResponse(**channel_data)
        
        return None
        
    except Exception as e:
        logger.error(f"Failed to get channel info: {e}")
        return None

async def _trigger_channel_parsing(user_id: str, channel_id: str, tenant_id: str):
    """Триггер парсинга канала (фоновая задача)."""
    try:
        # Здесь будет интеграция с telethon-ingest сервисом
        # Пока просто логируем
        logger.info(f"Triggering parsing for channel {channel_id} by user {user_id}")
        
        # TODO: Реализовать вызов telethon-ingest API или прямой вызов парсера
        
    except Exception as e:
        logger.error(f"Failed to trigger channel parsing: {e}")