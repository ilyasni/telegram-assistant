"""
Themes API endpoints для доступа к темам и каналам из TGStat.
Context7: RESTful API с валидацией, пагинацией и фильтрацией.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
from uuid import UUID
import structlog
import uuid

from models.database import get_db
from api.services.tgstat_service import get_tgstat_service
from middleware.tracing import get_trace_id

logger = structlog.get_logger()
router = APIRouter(prefix="/themes", tags=["themes"])

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class ThemeResponse(BaseModel):
    """Ответ с данными темы."""
    id: str
    slug: str
    name: str
    description: Optional[str] = None
    channels_count: int
    indexed_at: Optional[str] = None
    created_at: str


class ChannelResponse(BaseModel):
    """Ответ с данными канала."""
    id: str
    channel_username: str
    title: str
    subscribers: int
    er: Optional[float] = None
    url: str
    rank_in_theme: int
    indexed_at: str


class ThemesListResponse(BaseModel):
    """Ответ со списком тем."""
    themes: List[ThemeResponse]
    total: int
    limit: int
    offset: int


class ChannelsListResponse(BaseModel):
    """Ответ со списком каналов."""
    channels: List[ChannelResponse]
    total: int
    limit: int
    offset: int
    theme_slug: str


class UserThemeResponse(BaseModel):
    """Ответ с информацией о подключенной подборке."""
    theme_id: str
    theme_slug: str
    theme_name: str
    subscribed_at: str
    is_active: bool


class UserThemesListResponse(BaseModel):
    """Ответ со списком подключенных подборок."""
    themes: List[UserThemeResponse]
    total: int


# ============================================================================
# THEMES ENDPOINTS
# ============================================================================

@router.get("", response_model=ThemesListResponse)
async def get_themes(
    limit: int = Query(100, ge=1, le=500, description="Максимальное количество тем"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    search: Optional[str] = Query(None, description="Поиск по названию или описанию темы"),
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение списка всех тем.
    
    Context7: Поддерживает пагинацию и поиск по названию/описанию.
    
    Args:
        limit: Максимальное количество тем (1-500)
        offset: Смещение для пагинации
        search: Поисковый запрос
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Список тем с метаданными пагинации
    """
    logger.info(
        "Getting themes list",
        limit=limit,
        offset=offset,
        search=search,
        trace_id=trace_id
    )
    
    try:
        service = get_tgstat_service()
        result = service.get_all_themes(
            db=db,
            limit=limit,
            offset=offset,
            search=search
        )
        
        return ThemesListResponse(
            themes=[ThemeResponse(**theme) for theme in result['themes']],
            total=result['total'],
            limit=result['limit'],
            offset=result['offset']
        )
        
    except Exception as e:
        logger.error(
            "Error getting themes",
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{theme_slug}", response_model=ThemeResponse)
async def get_theme(
    theme_slug: str,
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение темы по slug.
    
    Context7: Валидация slug и безопасный параметризованный запрос.
    
    Args:
        theme_slug: Slug темы
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Данные темы
    """
    logger.info(
        "Getting theme by slug",
        theme_slug=theme_slug,
        trace_id=trace_id
    )
    
    # Context7: Валидация slug (только буквы, цифры, дефисы, подчёркивания)
    if not theme_slug or not theme_slug.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid theme_slug")
    
    try:
        service = get_tgstat_service()
        theme = service.get_theme_by_slug(db=db, slug=theme_slug)
        
        if not theme:
            raise HTTPException(status_code=404, detail="Theme not found")
        
        return ThemeResponse(**theme)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting theme",
            theme_slug=theme_slug,
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# CHANNELS ENDPOINTS
# ============================================================================

@router.get("/{theme_slug}/channels", response_model=ChannelsListResponse)
async def get_theme_channels(
    theme_slug: str,
    limit: int = Query(30, ge=1, le=100, description="Максимальное количество каналов"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    min_subscribers: Optional[int] = Query(None, ge=0, description="Минимальное количество подписчиков"),
    min_er: Optional[float] = Query(None, ge=0.0, le=100.0, description="Минимальный Engagement Rate (%)"),
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение каналов темы.
    
    Context7: Поддерживает фильтрацию по подписчикам и ER, пагинацию.
    
    Args:
        theme_slug: Slug темы
        limit: Максимальное количество каналов (1-100)
        offset: Смещение для пагинации
        min_subscribers: Минимальное количество подписчиков
        min_er: Минимальный Engagement Rate в процентах
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Список каналов с метаданными
    """
    logger.info(
        "Getting theme channels",
        theme_slug=theme_slug,
        limit=limit,
        offset=offset,
        min_subscribers=min_subscribers,
        min_er=min_er,
        trace_id=trace_id
    )
    
    # Context7: Валидация slug
    if not theme_slug or not theme_slug.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid theme_slug")
    
    try:
        service = get_tgstat_service()
        result = service.get_theme_channels(
            db=db,
            theme_slug=theme_slug,
            limit=limit,
            offset=offset,
            min_subscribers=min_subscribers,
            min_er=min_er
        )
        
        return ChannelsListResponse(
            channels=[ChannelResponse(**channel) for channel in result['channels']],
            total=result['total'],
            limit=result['limit'],
            offset=result['offset'],
            theme_slug=result['theme_slug']
        )
        
    except Exception as e:
        logger.error(
            "Error getting theme channels",
            theme_slug=theme_slug,
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{theme_slug}/recommendations", response_model=List[ChannelResponse])
async def get_recommended_channels(
    theme_slug: str,
    limit: int = Query(10, ge=1, le=50, description="Максимальное количество рекомендаций"),
    min_subscribers: Optional[int] = Query(None, ge=0, description="Минимальное количество подписчиков"),
    min_er: Optional[float] = Query(None, ge=0.0, le=100.0, description="Минимальный Engagement Rate (%)"),
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение рекомендованных каналов темы.
    
    Context7: Возвращает топ каналов темы для рекомендаций пользователям.
    
    Args:
        theme_slug: Slug темы
        limit: Максимальное количество рекомендаций (1-50)
        min_subscribers: Минимальное количество подписчиков
        min_er: Минимальный Engagement Rate в процентах
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Список рекомендованных каналов
    """
    logger.info(
        "Getting recommended channels",
        theme_slug=theme_slug,
        limit=limit,
        min_subscribers=min_subscribers,
        min_er=min_er,
        trace_id=trace_id
    )
    
    # Context7: Валидация slug
    if not theme_slug or not theme_slug.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid theme_slug")
    
    try:
        service = get_tgstat_service()
        channels = service.get_recommended_channels(
            db=db,
            theme_slug=theme_slug,
            limit=limit,
            min_subscribers=min_subscribers,
            min_er=min_er
        )
        
        return [ChannelResponse(**channel) for channel in channels]
        
    except Exception as e:
        logger.error(
            "Error getting recommended channels",
            theme_slug=theme_slug,
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# THEME SUBSCRIPTION ENDPOINTS
# ============================================================================

@router.post("/{theme_slug}/subscribe/{user_id}", status_code=201)
async def subscribe_to_theme(
    theme_slug: str,
    user_id: str,
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Подключение подборки для пользователя.
    
    Context7: Транзакционно подключает все каналы из подборки с защитой от гонок.
    Использует UPSERT для идемпотентности. Не перезаписывает ручные каналы.
    
    Args:
        theme_slug: Slug подборки
        user_id: ID пользователя (UUID или telegram_id)
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Информация о подключенной подборке
    """
    logger.info(
        "Subscribing user to theme",
        theme_slug=theme_slug,
        user_id=user_id,
        trace_id=trace_id
    )
    
    # Валидация slug
    if not theme_slug or not theme_slug.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid theme_slug")
    
    try:
        # Начать транзакцию
        db.begin()
        
        # 1. Получить тему по slug
        service = get_tgstat_service()
        theme = service.get_theme_by_slug(db=db, slug=theme_slug)
        
        if not theme:
            db.rollback()
            raise HTTPException(status_code=404, detail="Theme not found")
        
        theme_id = UUID(theme['id'])
        
        # 2. Определить user_uuid (UUID или telegram_id)
        try:
            telegram_id = int(user_id)
            user_result = db.execute(
                text("SELECT id, tenant_id FROM users WHERE telegram_id = :telegram_id"),
                {"telegram_id": telegram_id}
            )
            user_row = user_result.fetchone()
            if not user_row:
                db.rollback()
                raise HTTPException(status_code=404, detail="User not found")
            user_uuid = user_row.id
            tenant_id = user_row.tenant_id
        except ValueError:
            # UUID
            user_uuid = UUID(user_id)
            user_result = db.execute(
                text("SELECT tenant_id FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            )
            user_row = user_result.fetchone()
            if not user_row:
                db.rollback()
                raise HTTPException(status_code=404, detail="User not found")
            tenant_id = user_row.tenant_id
        
        # 3. Получить все каналы подборки
        theme_channels_result = service.get_theme_channels(
            db=db,
            theme_slug=theme_slug,
            limit=100,  # Максимум каналов в подборке
            offset=0
        )
        
        if not theme_channels_result or not theme_channels_result.get('channels'):
            db.rollback()
            raise HTTPException(status_code=404, detail="Theme has no channels")
        
        # 4. Для каждого канала: найти или создать Channel, затем UPSERT в user_channel
        channels_added = 0
        channels_skipped = 0
        
        for channel_data in theme_channels_result['channels']:
            channel_username = channel_data['channel_username'].lstrip('@')
            channel_title = channel_data['title']
            
            # Найти или создать Channel
            channel_result = db.execute(
                text("SELECT id FROM channels WHERE LTRIM(username, '@') = :username"),
                {"username": channel_username}
            )
            channel_row = channel_result.fetchone()
            
            if not channel_row:
                # Создать новый канал
                channel_id = uuid.uuid4()
                db.execute(
                    text("""
                        INSERT INTO channels (id, username, title, is_active, created_at)
                        VALUES (:id, :username, :title, true, NOW())
                    """),
                    {
                        "id": channel_id,
                        "username": channel_username,
                        "title": channel_title
                    }
                )
            else:
                channel_id = channel_row.id
            
            # UPSERT в user_channel с защитой от гонок
            # Проверяем, нет ли уже manual подписки
            existing_manual_result = db.execute(
                text("""
                    SELECT user_id, channel_id
                    FROM user_channel
                    WHERE user_id = :user_id 
                      AND channel_id = :channel_id 
                      AND source = 'manual'
                      AND is_active = true
                """),
                {
                    "user_id": user_uuid,
                    "channel_id": channel_id
                }
            )
            
            if existing_manual_result.fetchone():
                # Канал уже подключен вручную, пропускаем
                logger.debug(
                    "Channel already subscribed manually, skipping",
                    channel_username=channel_username,
                    user_id=str(user_uuid)
                )
                channels_skipped += 1
            else:
                # Проверяем, есть ли уже theme подписка для этой подборки
                existing_theme_result = db.execute(
                    text("""
                        SELECT user_id, channel_id, is_active
                        FROM user_channel
                        WHERE user_id = :user_id 
                          AND channel_id = :channel_id 
                          AND source = 'theme'
                          AND theme_id = :theme_id
                    """),
                    {
                        "user_id": user_uuid,
                        "channel_id": channel_id,
                        "theme_id": theme_id
                    }
                )
                existing_theme_row = existing_theme_result.fetchone()
                
                if existing_theme_row:
                    # Обновляем существующую подписку
                    db.execute(
                        text("""
                            UPDATE user_channel
                            SET is_active = true,
                                updated_at = NOW(),
                                subscribed_at = CASE 
                                    WHEN is_active = false THEN NOW() 
                                    ELSE subscribed_at 
                                END
                            WHERE user_id = :user_id 
                              AND channel_id = :channel_id 
                              AND source = 'theme'
                              AND theme_id = :theme_id
                        """),
                        {
                            "user_id": user_uuid,
                            "channel_id": channel_id,
                            "theme_id": theme_id
                        }
                    )
                    channels_added += 1
                else:
                    # Создаем новую подписку
                    try:
                        db.execute(
                            text("""
                                INSERT INTO user_channel (user_id, channel_id, source, theme_id, is_active, subscribed_at, updated_at)
                                VALUES (:user_id, :channel_id, 'theme', :theme_id, true, NOW(), NOW())
                            """),
                            {
                                "user_id": user_uuid,
                                "channel_id": channel_id,
                                "theme_id": theme_id
                            }
                        )
                        channels_added += 1
                    except Exception as e:
                        # Если конфликт (например, race condition), пропускаем
                        logger.debug(
                            "Channel subscription conflict, skipping",
                            channel_username=channel_username,
                            user_id=str(user_uuid),
                            error=str(e)
                        )
                        channels_skipped += 1
        
        # 5. UPSERT в user_theme (идемпотентно)
        db.execute(
            text("""
                INSERT INTO user_theme (user_id, theme_id, is_active, subscribed_at)
                VALUES (:user_id, :theme_id, true, NOW())
                ON CONFLICT (user_id, theme_id)
                DO UPDATE SET is_active = true
            """),
            {
                "user_id": user_uuid,
                "theme_id": theme_id
            }
        )
        
        # Коммит транзакции
        db.commit()
        
        logger.info(
            "User subscribed to theme",
            theme_slug=theme_slug,
            user_id=str(user_uuid),
            channels_added=channels_added,
            channels_skipped=channels_skipped,
            trace_id=trace_id
        )
        
        return {
            "status": "subscribed",
            "theme_id": str(theme_id),
            "theme_slug": theme_slug,
            "theme_name": theme['name'],
            "channels_added": channels_added,
            "channels_skipped": channels_skipped
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            "Error subscribing to theme",
            theme_slug=theme_slug,
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{theme_slug}/unsubscribe/{user_id}")
async def unsubscribe_from_theme(
    theme_slug: str,
    user_id: str,
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Отключение подборки для пользователя.
    
    Context7: Транзакционно отключает только каналы с source='theme' и theme_id=theme.id.
    Не затрагивает ручные каналы (source='manual').
    
    Args:
        theme_slug: Slug подборки
        user_id: ID пользователя (UUID или telegram_id)
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Информация об отключенной подборке
    """
    logger.info(
        "Unsubscribing user from theme",
        theme_slug=theme_slug,
        user_id=user_id,
        trace_id=trace_id
    )
    
    # Валидация slug
    if not theme_slug or not theme_slug.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid theme_slug")
    
    try:
        # Начать транзакцию
        db.begin()
        
        # 1. Получить тему по slug
        service = get_tgstat_service()
        theme = service.get_theme_by_slug(db=db, slug=theme_slug)
        
        if not theme:
            db.rollback()
            raise HTTPException(status_code=404, detail="Theme not found")
        
        theme_id = UUID(theme['id'])
        
        # 2. Определить user_uuid
        try:
            telegram_id = int(user_id)
            user_result = db.execute(
                text("SELECT id FROM users WHERE telegram_id = :telegram_id"),
                {"telegram_id": telegram_id}
            )
            user_row = user_result.fetchone()
            if not user_row:
                db.rollback()
                raise HTTPException(status_code=404, detail="User not found")
            user_uuid = user_row.id
        except ValueError:
            user_uuid = UUID(user_id)
            user_result = db.execute(
                text("SELECT id FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            )
            if not user_result.fetchone():
                db.rollback()
                raise HTTPException(status_code=404, detail="User not found")
        
        # 3. Найти запись в user_theme и пометить is_active=false
        user_theme_result = db.execute(
            text("""
                UPDATE user_theme
                SET is_active = false
                WHERE user_id = :user_id AND theme_id = :theme_id
                RETURNING user_id, theme_id
            """),
            {
                "user_id": user_uuid,
                "theme_id": theme_id
            }
        )
        
        if not user_theme_result.fetchone():
            db.rollback()
            raise HTTPException(status_code=404, detail="Theme subscription not found")
        
        # 4. Для всех каналов подборки: отключить только source='theme' и theme_id=theme.id
        channels_deactivated = db.execute(
            text("""
                UPDATE user_channel
                SET is_active = false, updated_at = NOW()
                WHERE user_id = :user_id
                  AND channel_id IN (
                      SELECT DISTINCT c.id
                      FROM channels c
                      JOIN theme_channels tc ON LTRIM(c.username, '@') = tc.channel_username
                      WHERE tc.theme_id = :theme_id
                  )
                  AND source = 'theme'
                  AND theme_id = :theme_id
            """),
            {
                "user_id": user_uuid,
                "theme_id": theme_id
            }
        ).rowcount
        
        # Коммит транзакции
        db.commit()
        
        logger.info(
            "User unsubscribed from theme",
            theme_slug=theme_slug,
            user_id=str(user_uuid),
            channels_deactivated=channels_deactivated,
            trace_id=trace_id
        )
        
        return {
            "status": "unsubscribed",
            "theme_id": str(theme_id),
            "theme_slug": theme_slug,
            "channels_deactivated": channels_deactivated
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            "Error unsubscribing from theme",
            theme_slug=theme_slug,
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/users/{user_id}/subscribed", response_model=UserThemesListResponse)
async def get_user_subscribed_themes(
    user_id: str,
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Получение списка подключенных подборок пользователя.
    
    Context7: Возвращает все активные подборки пользователя.
    
    Args:
        user_id: ID пользователя (UUID или telegram_id)
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Список подключенных подборок
    """
    logger.info(
        "Getting user subscribed themes",
        user_id=user_id,
        trace_id=trace_id
    )
    
    try:
        # Определить user_uuid
        try:
            telegram_id = int(user_id)
            user_result = db.execute(
                text("SELECT id FROM users WHERE telegram_id = :telegram_id"),
                {"telegram_id": telegram_id}
            )
            user_row = user_result.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail="User not found")
            user_uuid = user_row.id
        except ValueError:
            user_uuid = UUID(user_id)
            user_result = db.execute(
                text("SELECT id FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            )
            if not user_result.fetchone():
                raise HTTPException(status_code=404, detail="User not found")
        
        # Получить подключенные подборки
        themes_result = db.execute(
            text("""
                SELECT 
                    ut.theme_id,
                    ut.subscribed_at,
                    ut.is_active,
                    t.slug as theme_slug,
                    t.name as theme_name
                FROM user_theme ut
                JOIN themes t ON ut.theme_id = t.id
                WHERE ut.user_id = :user_id AND ut.is_active = true
                ORDER BY ut.subscribed_at DESC
            """),
            {"user_id": user_uuid}
        )
        
        themes = []
        for row in themes_result.fetchall():
            themes.append(UserThemeResponse(
                theme_id=str(row.theme_id),
                theme_slug=row.theme_slug,
                theme_name=row.theme_name,
                subscribed_at=row.subscribed_at.isoformat(),
                is_active=row.is_active
            ))
        
        return UserThemesListResponse(
            themes=themes,
            total=len(themes)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting user subscribed themes",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sync/{theme_id}", status_code=202)
async def sync_theme_subscriptions_endpoint(
    theme_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id)
):
    """
    Синхронизация подписок пользователей при изменении подборки.
    
    Context7: Идемпотентная синхронизация по принципу "desired state → reconcile".
    Вызывается автоматически при изменении theme_channels администратором.
    
    Args:
        theme_id: ID подборки (UUID)
        background_tasks: FastAPI BackgroundTasks для асинхронной обработки
        db: SQLAlchemy сессия
        trace_id: ID трейса для логирования
    
    Returns:
        Статус запуска синхронизации
    """
    logger.info(
        "Triggering theme subscriptions sync",
        theme_id=theme_id,
        trace_id=trace_id
    )
    
    try:
        # Валидация theme_id
        try:
            theme_uuid = UUID(theme_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid theme_id format")
        
        # Проверяем существование темы
        theme_result = db.execute(
            text("SELECT id FROM themes WHERE id = :theme_id"),
            {"theme_id": theme_uuid}
        )
        if not theme_result.fetchone():
            raise HTTPException(status_code=404, detail="Theme not found")
        
        # Запускаем синхронизацию в фоне
        from api.services.theme_sync_service import get_theme_sync_service
        sync_service = get_theme_sync_service()
        
        # Context7: Создаем новую сессию для фоновой задачи
        from models.database import SessionLocal
        def sync_task():
            db_session = SessionLocal()
            try:
                sync_service.sync_theme_subscriptions(
                    theme_id=theme_uuid,
                    db=db_session,
                    batch_size=100
                )
            finally:
                db_session.close()
        
        background_tasks.add_task(sync_task)
        
        logger.info(
            "Theme subscriptions sync triggered",
            theme_id=theme_id,
            trace_id=trace_id
        )
        
        return {
            "status": "sync_triggered",
            "theme_id": theme_id,
            "message": "Синхронизация запущена в фоне"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error triggering theme sync",
            theme_id=theme_id,
            error=str(e),
            error_type=type(e).__name__,
            trace_id=trace_id,
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error")
