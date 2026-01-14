"""
TGStat Service для работы с темами и каналами из TGStat.
Context7: Использует данные из таблиц themes и theme_channels для рекомендаций каналов.
"""

from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import text, and_, or_, desc, func
import structlog
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# Context7: Prometheus метрики для мониторинга использования TGStat данных
tgstat_themes_requests_total = Counter(
    'tgstat_themes_requests_total',
    'Total number of requests to TGStat themes API',
    ['operation', 'status']
)

tgstat_channels_requests_total = Counter(
    'tgstat_channels_requests_total',
    'Total number of requests to TGStat channels API',
    ['operation', 'status']
)

tgstat_service_duration_seconds = Histogram(
    'tgstat_service_duration_seconds',
    'Duration of TGStat service operations',
    ['operation']
)


class TGStatService:
    """
    Сервис для работы с темами и каналами из TGStat.
    
    Context7: Предоставляет бизнес-логику для доступа к данным TGStat,
    рекомендаций каналов по темам и фильтрации.
    """
    
    def __init__(self):
        """Инициализация TGStat Service."""
        logger.info("TGStatService initialized")
    
    def get_all_themes(
        self,
        db: Session,
        limit: Optional[int] = None,
        offset: int = 0,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Получение списка всех тем.
        
        Context7: Поддерживает пагинацию и поиск по названию темы.
        
        Args:
            db: SQLAlchemy сессия
            limit: Максимальное количество тем
            offset: Смещение для пагинации
            search: Поисковый запрос (поиск в названии и описании)
        
        Returns:
            Словарь с темами и метаданными пагинации
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Context7: Базовый запрос с использованием параметризованных запросов
            # Context7: Фильтруем только подборки с количеством каналов > 1
            query = text("""
                SELECT 
                    id,
                    slug,
                    name,
                    description,
                    channels_count,
                    indexed_at,
                    created_at
                FROM themes
                WHERE channels_count > 1
            """)
            
            params = {}
            
            # Context7: Добавление поиска (безопасный параметризованный запрос)
            if search:
                query = text("""
                    SELECT 
                        id,
                        slug,
                        name,
                        description,
                        channels_count,
                        indexed_at,
                        created_at
                    FROM themes
                    WHERE channels_count > 1
                      AND (name ILIKE :search OR description ILIKE :search)
                """)
                params['search'] = f'%{search}%'
            
            # Context7: Получение общего количества для пагинации
            count_query = text("""
                SELECT COUNT(*) as total
                FROM themes
                WHERE channels_count > 1
            """)
            
            if search:
                count_query = text("""
                    SELECT COUNT(*) as total
                    FROM themes
                    WHERE channels_count > 1
                      AND (name ILIKE :search OR description ILIKE :search)
                """)
            
            total_result = db.execute(count_query, params)
            total = total_result.scalar() or 0
            
            # Context7: Добавление сортировки и лимитов
            # Context7: Используем параметризованный запрос для безопасности
            if search:
                query = text("""
                    SELECT 
                        id,
                        slug,
                        name,
                        description,
                        channels_count,
                        indexed_at,
                        created_at
                    FROM themes
                    WHERE channels_count > 1
                      AND (name ILIKE :search OR description ILIKE :search)
                    ORDER BY name ASC
                    LIMIT :limit OFFSET :offset
                """)
            else:
                query = text("""
                    SELECT 
                        id,
                        slug,
                        name,
                        description,
                        channels_count,
                        indexed_at,
                        created_at
                    FROM themes
                    WHERE channels_count > 1
                    ORDER BY name ASC
                    LIMIT :limit OFFSET :offset
                """)
            
            params['limit'] = limit or 100
            params['offset'] = offset
            
            result = db.execute(query, params)
            themes = []
            
            for row in result:
                themes.append({
                    'id': str(row.id),
                    'slug': row.slug,
                    'name': row.name,
                    'description': row.description,
                    'channels_count': row.channels_count,
                    'indexed_at': row.indexed_at.isoformat() if row.indexed_at else None,
                    'created_at': row.created_at.isoformat() if row.created_at else None
                })
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            tgstat_service_duration_seconds.labels(operation='get_all_themes').observe(duration)
            tgstat_themes_requests_total.labels(operation='list', status='success').inc()
            
            return {
                'themes': themes,
                'total': total,
                'limit': params['limit'],
                'offset': offset
            }
            
        except Exception as e:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            tgstat_service_duration_seconds.labels(operation='get_all_themes').observe(duration)
            tgstat_themes_requests_total.labels(operation='list', status='error').inc()
            logger.error(
                "Error getting themes",
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            raise
    
    def get_theme_by_slug(
        self,
        db: Session,
        slug: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получение темы по slug.
        
        Context7: Безопасный параметризованный запрос для предотвращения SQL injection.
        
        Args:
            db: SQLAlchemy сессия
            slug: Slug темы
        
        Returns:
            Словарь с данными темы или None
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            query = text("""
                SELECT 
                    id,
                    slug,
                    name,
                    description,
                    channels_count,
                    indexed_at,
                    created_at
                FROM themes
                WHERE slug = :slug
            """)
            
            result = db.execute(query, {'slug': slug})
            row = result.first()
            
            if not row:
                tgstat_themes_requests_total.labels(operation='get_by_slug', status='not_found').inc()
                return None
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            tgstat_service_duration_seconds.labels(operation='get_theme_by_slug').observe(duration)
            tgstat_themes_requests_total.labels(operation='get_by_slug', status='success').inc()
            
            return {
                'id': str(row.id),
                'slug': row.slug,
                'name': row.name,
                'description': row.description,
                'channels_count': row.channels_count,
                'indexed_at': row.indexed_at.isoformat() if row.indexed_at else None,
                'created_at': row.created_at.isoformat() if row.created_at else None
            }
            
        except Exception as e:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            tgstat_service_duration_seconds.labels(operation='get_theme_by_slug').observe(duration)
            tgstat_themes_requests_total.labels(operation='get_by_slug', status='error').inc()
            logger.error(
                "Error getting theme by slug",
                slug=slug,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            raise
    
    def get_theme_channels(
        self,
        db: Session,
        theme_slug: str,
        limit: Optional[int] = None,
        offset: int = 0,
        min_subscribers: Optional[int] = None,
        min_er: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Получение каналов темы.
        
        Context7: Поддерживает фильтрацию по количеству подписчиков и ER,
        пагинацию и сортировку по рангу.
        
        Args:
            db: SQLAlchemy сессия
            theme_slug: Slug темы
            limit: Максимальное количество каналов
            offset: Смещение для пагинации
            min_subscribers: Минимальное количество подписчиков
            min_er: Минимальный Engagement Rate
        
        Returns:
            Словарь с каналами и метаданными
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Context7: Получение ID темы
            theme = self.get_theme_by_slug(db, theme_slug)
            if not theme:
                tgstat_channels_requests_total.labels(operation='list', status='not_found').inc()
                return {
                    'channels': [],
                    'total': 0,
                    'limit': limit or 30,
                    'offset': offset,
                    'theme_slug': theme_slug
                }
            
            theme_id = theme['id']
            
            # Context7: Построение параметризованного запроса с фильтрами
            # Context7: Используем условную логику для построения безопасных запросов
            params = {'theme_id': theme_id}
            
            # Context7: Определение условий WHERE на основе фильтров
            has_subscribers_filter = min_subscribers is not None
            has_er_filter = min_er is not None
            
            # Context7: Построение безопасного запроса в зависимости от фильтров
            if has_subscribers_filter and has_er_filter:
                params['min_subscribers'] = min_subscribers
                params['min_er'] = min_er
                where_clause = "theme_id = :theme_id AND subscribers >= :min_subscribers AND (er IS NULL OR er >= :min_er)"
            elif has_subscribers_filter:
                params['min_subscribers'] = min_subscribers
                where_clause = "theme_id = :theme_id AND subscribers >= :min_subscribers"
            elif has_er_filter:
                params['min_er'] = min_er
                where_clause = "theme_id = :theme_id AND (er IS NULL OR er >= :min_er)"
            else:
                where_clause = "theme_id = :theme_id"
            
            # Context7: Подсчёт общего количества (безопасный параметризованный запрос)
            count_query = text(f"""
                SELECT COUNT(*) as total
                FROM theme_channels
                WHERE {where_clause}
            """)
            
            total_result = db.execute(count_query, params)
            total = total_result.scalar() or 0
            
            # Context7: Получение каналов с сортировкой по рангу (безопасный параметризованный запрос)
            query = text(f"""
                SELECT 
                    id,
                    channel_username,
                    title,
                    subscribers,
                    er,
                    url,
                    rank_in_theme,
                    indexed_at
                FROM theme_channels
                WHERE {where_clause}
                ORDER BY rank_in_theme ASC
                LIMIT :limit OFFSET :offset
            """)
            
            params['limit'] = limit or 30
            params['offset'] = offset
            
            result = db.execute(query, params)
            channels = []
            
            for row in result:
                channels.append({
                    'id': str(row.id),
                    'channel_username': row.channel_username,
                    'title': row.title,
                    'subscribers': row.subscribers,
                    'er': row.er,
                    'url': row.url,
                    'rank_in_theme': row.rank_in_theme,
                    'indexed_at': row.indexed_at.isoformat() if row.indexed_at else None
                })
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            tgstat_service_duration_seconds.labels(operation='get_theme_channels').observe(duration)
            tgstat_channels_requests_total.labels(operation='list', status='success').inc()
            
            return {
                'channels': channels,
                'total': total,
                'limit': params['limit'],
                'offset': offset,
                'theme_slug': theme_slug
            }
            
        except Exception as e:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            tgstat_service_duration_seconds.labels(operation='get_theme_channels').observe(duration)
            tgstat_channels_requests_total.labels(operation='list', status='error').inc()
            logger.error(
                "Error getting theme channels",
                theme_slug=theme_slug,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            raise
    
    def get_recommended_channels(
        self,
        db: Session,
        theme_slug: str,
        limit: int = 10,
        min_subscribers: Optional[int] = None,
        min_er: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Получение рекомендованных каналов темы.
        
        Context7: Возвращает топ каналов темы с фильтрацией.
        Используется для рекомендаций пользователям.
        
        Args:
            db: SQLAlchemy сессия
            theme_slug: Slug темы
            limit: Максимальное количество каналов
            min_subscribers: Минимальное количество подписчиков
            min_er: Минимальный Engagement Rate
        
        Returns:
            Список рекомендованных каналов
        """
        result = self.get_theme_channels(
            db=db,
            theme_slug=theme_slug,
            limit=limit,
            offset=0,
            min_subscribers=min_subscribers,
            min_er=min_er
        )
        
        return result.get('channels', [])


# Context7: Singleton экземпляр сервиса
_tgstat_service: Optional[TGStatService] = None


def get_tgstat_service() -> TGStatService:
    """
    Получение экземпляра TGStat Service.
    
    Context7: Singleton pattern для переиспользования экземпляра.
    
    Returns:
        Экземпляр TGStatService
    """
    global _tgstat_service
    if _tgstat_service is None:
        _tgstat_service = TGStatService()
    return _tgstat_service
