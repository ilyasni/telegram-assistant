"""Роутер для работы с каналами."""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from typing import List, Optional
import json
import structlog
from config import settings

router = APIRouter(prefix="/channels", tags=["channels"])
logger = structlog.get_logger()

# Создание сессии БД
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class ChannelCreate(BaseModel):
    """Модель для создания канала."""
    telegram_id: int
    username: Optional[str] = None
    title: str
    settings: dict = {}


class ChannelResponse(BaseModel):
    """Модель ответа канала."""
    id: str
    tenant_id: str
    telegram_id: int
    username: Optional[str]
    title: str
    is_active: bool
    last_message_at: Optional[str]
    created_at: str
    settings: dict


def get_db():
    """Получение сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=List[ChannelResponse])
async def get_channels(
    tenant_id: str,
    limit: int = 100,
    offset: int = 0,
    db = Depends(get_db)
):
    """Получение списка каналов."""
    try:
        result = db.execute(text("""
            SELECT id, tenant_id, telegram_id, username, title, is_active, 
                   last_message_at, created_at, settings
            FROM channels 
            WHERE tenant_id = :tenant_id
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """), {
            "tenant_id": tenant_id,
            "limit": limit,
            "offset": offset
        })
        
        channels = []
        for row in result:
            channels.append(ChannelResponse(
                id=str(row.id),
                tenant_id=str(row.tenant_id),
                telegram_id=row.telegram_id,
                username=row.username,
                title=row.title,
                is_active=row.is_active,
                last_message_at=row.last_message_at.isoformat() if row.last_message_at else None,
                created_at=row.created_at.isoformat(),
                settings=row.settings or {}
            ))
        
        return channels
        
    except Exception as e:
        logger.error("Failed to get channels", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/", response_model=ChannelResponse)
async def create_channel(
    channel: ChannelCreate,
    tenant_id: str,
    db = Depends(get_db)
):
    """Создание нового канала."""
    try:
        result = db.execute(text("""
            INSERT INTO channels (tenant_id, telegram_id, username, title, settings)
            VALUES (:tenant_id, :telegram_id, :username, :title, :settings)
            RETURNING id, tenant_id, telegram_id, username, title, is_active, 
                      last_message_at, created_at, settings
        """), {
            "tenant_id": tenant_id,
            "telegram_id": channel.telegram_id,
            "username": channel.username,
            "title": channel.title,
            "settings": json.dumps(channel.settings) if channel.settings else None
        })
        
        row = result.fetchone()
        db.commit()
        
        return ChannelResponse(
            id=str(row.id),
            tenant_id=str(row.tenant_id),
            telegram_id=row.telegram_id,
            username=row.username,
            title=row.title,
            is_active=row.is_active,
            last_message_at=row.last_message_at.isoformat() if row.last_message_at else None,
            created_at=row.created_at.isoformat(),
            settings=row.settings or {}
        )
        
    except Exception as e:
        logger.error("Failed to create channel", error=str(e))
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(
    channel_id: str,
    tenant_id: str,
    db = Depends(get_db)
):
    """Получение канала по ID."""
    try:
        result = db.execute(text("""
            SELECT id, tenant_id, telegram_id, username, title, is_active, 
                   last_message_at, created_at, settings
            FROM channels 
            WHERE id = :channel_id AND tenant_id = :tenant_id
        """), {
            "channel_id": channel_id,
            "tenant_id": tenant_id
        })
        
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Channel not found")
        
        return ChannelResponse(
            id=str(row.id),
            tenant_id=str(row.tenant_id),
            telegram_id=row.telegram_id,
            username=row.username,
            title=row.title,
            is_active=row.is_active,
            last_message_at=row.last_message_at.isoformat() if row.last_message_at else None,
            created_at=row.created_at.isoformat(),
            settings=row.settings or {}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get channel", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")
