"""HTTP API endpoints для TGStat Parser.
Context7: RESTful API с валидацией входных данных и health checks.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from sqlalchemy import text
import structlog
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from scheduler.tasks import sync_service
from database.models import ThemeRepository, Theme, ThemeChannel
from config import settings

logger = structlog.get_logger()

# Context7: Создание FastAPI приложения
# Context7: Примечание: lifespan будет установлен в main.py
# Временно создаём app без lifespan, он будет пересоздан в main.py с lifespan
app = FastAPI(
    title="TGStat Parser API",
    description="API для парсинга и синхронизации тем и каналов из TGStat",
    version="1.0.0"
)


# Context7: Pydantic модели для валидации запросов/ответов
class SyncResponse(BaseModel):
    """Ответ на запрос синхронизации."""
    success: bool
    themes_count: Optional[int] = None
    channels_count: Optional[int] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None


class ThemeResponse(BaseModel):
    """Ответ с данными темы."""
    id: str
    slug: str
    name: str
    description: Optional[str]
    channels_count: int
    indexed_at: Optional[str]
    created_at: str


class ChannelResponse(BaseModel):
    """Ответ с данными канала."""
    id: str
    channel_username: str
    title: str
    subscribers: int
    er: Optional[float]
    url: str
    rank_in_theme: int
    indexed_at: str


class HealthResponse(BaseModel):
    """Ответ health check."""
    status: str
    service: str = "tgstat-parser"


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint.
    
    Context7: Простая проверка доступности сервиса.
    """
    return HealthResponse(status="healthy")


@app.get("/health/details")
async def health_check_details():
    """Детальный health check.
    
    Context7: Расширенная информация о состоянии сервиса.
    """
    try:
        # Context7: Проверка подключения к БД
        with ThemeRepository() as repo:
            repo.session.execute(text("SELECT 1"))
        
        db_status = "ok"
    except Exception as e:
        logger.warning("Database health check failed", error=str(e))
        db_status = "error"
    
    return {
        "status": "healthy" if db_status == "ok" else "degraded",
        "service": "tgstat-parser",
        "database": db_status,
        "version": "1.0.0"
    }


@app.post("/sync", response_model=SyncResponse)
async def sync_all_themes():
    """Ручной запуск синхронизации всех тем.
    
    Context7: Запускает синхронизацию (может занять время, но возвращает результат).
    """
    logger.info("Manual sync requested")
    
    try:
        # Context7: Запуск синхронизации
        # Context7: Примечание: для длительных операций можно использовать BackgroundTasks
        result = sync_service.sync_all_themes()
        
        return SyncResponse(**result)
        
    except Exception as e:
        logger.error(
            "Sync error",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sync/{theme_slug}", response_model=Dict[str, Any])
async def sync_theme(theme_slug: str):
    """Синхронизация конкретной темы.
    
    Context7: Валидация theme_slug и синхронизация одной темы.
    
    Args:
        theme_slug: Slug темы для синхронизации
    """
    logger.info("Manual theme sync requested", theme_slug=theme_slug)
    
    # Context7: Валидация slug (только буквы, цифры, дефисы, подчёркивания)
    if not theme_slug or not theme_slug.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid theme_slug")
    
    try:
        channels_count = sync_service.sync_theme_channels(theme_slug)
        
        return {
            "success": True,
            "theme_slug": theme_slug,
            "channels_count": channels_count
        }
        
    except Exception as e:
        logger.error(
            "Theme sync error",
            theme_slug=theme_slug,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/themes", response_model=List[ThemeResponse])
async def get_themes():
    """Получение списка всех тем.
    
    Context7: Возвращает все темы из БД.
    """
    try:
        with ThemeRepository() as repo:
            themes = repo.session.query(Theme).order_by(Theme.name).all()
            
            return [
                ThemeResponse(
                    id=str(theme.id),
                    slug=theme.slug,
                    name=theme.name,
                    description=theme.description,
                    channels_count=theme.channels_count,
                    indexed_at=theme.indexed_at.isoformat() if theme.indexed_at else None,
                    created_at=theme.created_at.isoformat()
                )
                for theme in themes
            ]
            
    except Exception as e:
        logger.error(
            "Error getting themes",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/themes/{theme_slug}/channels", response_model=List[ChannelResponse])
async def get_theme_channels(theme_slug: str):
    """Получение каналов конкретной темы.
    
    Context7: Возвращает топ-30 каналов темы, отсортированных по рангу.
    
    Args:
        theme_slug: Slug темы
    """
    try:
        with ThemeRepository() as repo:
            theme = repo.get_theme_by_slug(theme_slug)
            
            if not theme:
                raise HTTPException(status_code=404, detail="Theme not found")
            
            channels = repo.session.query(ThemeChannel).filter(
                ThemeChannel.theme_id == theme.id
            ).order_by(ThemeChannel.rank_in_theme).all()
            
            return [
                ChannelResponse(
                    id=str(channel.id),
                    channel_username=channel.channel_username,
                    title=channel.title,
                    subscribers=channel.subscribers,
                    er=channel.er,
                    url=channel.url,
                    rank_in_theme=channel.rank_in_theme,
                    indexed_at=channel.indexed_at.isoformat()
                )
                for channel in channels
            ]
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting theme channels",
            theme_slug=theme_slug,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint.
    
    Context7: Экспорт метрик для мониторинга.
    """
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
