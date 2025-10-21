"""Роутер для работы с постами."""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from typing import List, Optional
import structlog
from config import settings

router = APIRouter(prefix="/posts", tags=["posts"])
logger = structlog.get_logger()

# Создание сессии БД
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class PostResponse(BaseModel):
    """Модель ответа поста."""
    id: str
    tenant_id: str
    channel_id: str
    telegram_message_id: int
    content: Optional[str]
    media_urls: List[str]
    created_at: str
    is_processed: bool


def get_db():
    """Получение сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=List[PostResponse])
async def get_posts(
    tenant_id: str,
    channel_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db = Depends(get_db)
):
    """Получение списка постов."""
    try:
        query = """
            SELECT p.id, p.tenant_id, p.channel_id, p.telegram_message_id, 
                   p.content, p.media_urls, p.created_at, p.is_processed
            FROM posts p
            WHERE p.tenant_id = :tenant_id
        """
        params = {"tenant_id": tenant_id}
        
        if channel_id:
            query += " AND p.channel_id = :channel_id"
            params["channel_id"] = channel_id
        
        query += " ORDER BY p.created_at DESC LIMIT :limit OFFSET :offset"
        params.update({"limit": limit, "offset": offset})
        
        result = db.execute(text(query), params)
        
        posts = []
        for row in result:
            posts.append(PostResponse(
                id=str(row.id),
                tenant_id=str(row.tenant_id),
                channel_id=str(row.channel_id),
                telegram_message_id=row.telegram_message_id,
                content=row.content,
                media_urls=row.media_urls or [],
                created_at=row.created_at.isoformat(),
                is_processed=row.is_processed
            ))
        
        return posts
        
    except Exception as e:
        logger.error("Failed to get posts", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: str,
    tenant_id: str,
    db = Depends(get_db)
):
    """Получение поста по ID."""
    try:
        result = db.execute(text("""
            SELECT id, tenant_id, channel_id, telegram_message_id, 
                   content, media_urls, created_at, is_processed
            FROM posts 
            WHERE id = :post_id AND tenant_id = :tenant_id
        """), {
            "post_id": post_id,
            "tenant_id": tenant_id
        })
        
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        
        return PostResponse(
            id=str(row.id),
            tenant_id=str(row.tenant_id),
            channel_id=str(row.channel_id),
            telegram_message_id=row.telegram_message_id,
            content=row.content,
            media_urls=row.media_urls or [],
            created_at=row.created_at.isoformat(),
            is_processed=row.is_processed
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get post", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")
