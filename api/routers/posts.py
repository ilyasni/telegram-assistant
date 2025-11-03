"""Роутер для работы с постами."""

from fastapi import APIRouter, HTTPException, Depends
from fastapi import Request
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
    telegram_post_url: Optional[str]


def get_db():
    """Получение сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=List[PostResponse])
async def get_posts(
    request: Request,
    channel_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db = Depends(get_db)
):
    """Получение списка постов с изоляцией по tenant_id (Context7)."""
    from dependencies.auth import get_current_tenant_id_optional
    
    try:
        # Context7: Извлекаем tenant_id из JWT
        tenant_id = get_current_tenant_id_optional(request)
        
        # Если tenant_id есть - фильтруем по tenant через JOIN с channels
        # Если нет - возвращаем только посты с tenant_id=NULL (legacy режим)
        query = """
            SELECT p.id, p.channel_id, p.telegram_message_id, 
                   p.content, p.media_urls, p.created_at, p.is_processed, p.telegram_post_url,
                   c.tenant_id
            FROM posts p
            JOIN channels c ON p.channel_id = c.id
        """
        params = {}
        conditions = []
        
        # Context7: Фильтрация по tenant_id если доступен
        if tenant_id:
            conditions.append("c.tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id
        
        if channel_id:
            conditions.append("p.channel_id = :channel_id")
            params["channel_id"] = channel_id
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY p.created_at DESC LIMIT :limit OFFSET :offset"
        params.update({"limit": limit, "offset": offset})
        
        result = db.execute(text(query), params)
        
        posts = []
        for row in result:
            # Используем tenant_id из результата запроса (через JOIN)
            row_tenant_id = str(row.tenant_id) if row.tenant_id else tenant_id or ""
            posts.append(PostResponse(
                id=str(row.id),
                tenant_id=row_tenant_id,
                channel_id=str(row.channel_id),
                telegram_message_id=row.telegram_message_id,
                content=row.content,
                media_urls=row.media_urls or [],
                created_at=row.created_at.isoformat(),
                is_processed=row.is_processed,
                telegram_post_url=row.telegram_post_url
            ))
        
        return posts
        
    except Exception as e:
        logger.error("Failed to get posts", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: str,
    request: Request,
    db = Depends(get_db)
):
    """Получение поста по ID с проверкой tenant_id (Context7)."""
    from dependencies.auth import get_current_tenant_id_optional
    
    try:
        # Context7: Извлекаем tenant_id из JWT для проверки доступа
        tenant_id = get_current_tenant_id_optional(request)
        
        # Context7: JOIN с channels для проверки tenant_id
        query = """
            SELECT p.id, p.channel_id, p.telegram_message_id, 
                   p.content, p.media_urls, p.created_at, p.is_processed, p.telegram_post_url,
                   c.tenant_id
            FROM posts p
            JOIN channels c ON p.channel_id = c.id
            WHERE p.id = :post_id
        """
        params = {"post_id": post_id}
        
        # Context7: Фильтрация по tenant_id если доступен
        if tenant_id:
            query += " AND c.tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id
        
        result = db.execute(text(query), params)
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        
        return PostResponse(
            id=str(row.id),
            tenant_id=str(row.tenant_id) if row.tenant_id else tenant_id or "",
            channel_id=str(row.channel_id),
            telegram_message_id=row.telegram_message_id,
            content=row.content,
            media_urls=row.media_urls or [],
            created_at=row.created_at.isoformat(),
            is_processed=row.is_processed,
            telegram_post_url=row.telegram_post_url
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get post", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")
