"""
User Interest Tracking Service
Context7: гибридное хранение - PostgreSQL для запросов и аналитики, Neo4j для рекомендаций через граф
"""

import json
import time
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, desc

from models.database import User, RAGQueryHistory, Post
from services.graph_service import get_graph_service
from config import settings

logger = structlog.get_logger()


# ============================================================================
# USER INTEREST SERVICE
# ============================================================================

class UserInterestService:
    """
    Сервис для отслеживания интересов пользователей.
    
    Context7: Гибридное хранение:
    - PostgreSQL (user_interests таблица) - для быстрых запросов и аналитики
    - Neo4j (граф интересов) - для рекомендаций через графовые связи
    - Redis (кэш) - для быстрых обновлений в реальном времени
    """
    
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        graph_service: Optional[Any] = None
    ):
        """
        Инициализация User Interest Service.
        
        Args:
            redis_client: Redis клиент для кэширования
            graph_service: GraphService для работы с Neo4j
        """
        self.redis_client = redis_client
        self.graph_service = graph_service or get_graph_service()
        
        # Context7: Кэширование топ-N интересов пользователя в Redis (TTL 1 час)
        self.cache_ttl = 3600  # 1 час
        
        logger.info("UserInterestService initialized")
    
    def _get_cache_key(self, user_id: str, key_type: str = "interests") -> str:
        """Получение ключа кэша."""
        return f"user_interests:{key_type}:{user_id}"
    
    async def track_query(
        self,
        user_id: UUID,
        query_text: str,
        intent: str,
        sources: List[Dict[str, Any]],
        db: Session
    ) -> None:
        """
        Отслеживание запроса пользователя.
        
        Context7: Комбинированное обновление:
        - Real-time: быстрые обновления в Redis (TTL 1 час) с немедленной записью в PostgreSQL
        
        Args:
            user_id: ID пользователя
            query_text: Текст запроса
            intent: Определенное намерение
            sources: Найденные источники
            db: SQLAlchemy сессия
        """
        try:
            # Извлекаем темы из источников и запроса
            topics = self._extract_topics_from_query(query_text, intent, sources)
            
            # Context7: Обновление в Redis (быстрое)
            if self.redis_client:
                cache_key = self._get_cache_key(str(user_id), "pending_updates")
                pending = self.redis_client.get(cache_key)
                pending_dict = json.loads(pending) if pending else {}
                
                for topic in topics:
                    if topic not in pending_dict:
                        pending_dict[topic] = {'query_count': 0, 'view_count': 0}
                    pending_dict[topic]['query_count'] += 1
                
                self.redis_client.setex(
                    cache_key,
                    self.cache_ttl,
                    json.dumps(pending_dict)
                )
            
            # Context7: Немедленная запись в PostgreSQL
            await self._update_interests_in_postgres(
                user_id=user_id,
                topics=topics,
                query_count_delta=1,
                view_count_delta=0,
                db=db
            )
            
            logger.debug("Query tracked", user_id=str(user_id), topics_count=len(topics))
            
        except Exception as e:
            logger.error("Error tracking query", error=str(e), user_id=str(user_id))
    
    async def track_view(
        self,
        user_id: UUID,
        post_id: UUID,
        db: Session
    ) -> None:
        """
        Отслеживание просмотра поста.
        
        Context7: Комбинированное обновление:
        - Real-time: быстрые обновления в Redis с немедленной записью в PostgreSQL
        
        Args:
            user_id: ID пользователя
            post_id: ID поста
            db: SQLAlchemy сессия
        """
        try:
            # Получаем темы поста из enrichment
            from models.database import PostEnrichment
            enrichment = db.query(PostEnrichment).filter(
                PostEnrichment.post_id == post_id
            ).first()
            
            if not enrichment or not enrichment.topics:
                return
            
            topics = [t.get('name', '') if isinstance(t, dict) else str(t) for t in enrichment.topics if t]
            
            # Context7: Обновление в Redis (быстрое)
            if self.redis_client:
                cache_key = self._get_cache_key(str(user_id), "pending_updates")
                pending = self.redis_client.get(cache_key)
                pending_dict = json.loads(pending) if pending else {}
                
                for topic in topics:
                    if topic not in pending_dict:
                        pending_dict[topic] = {'query_count': 0, 'view_count': 0}
                    pending_dict[topic]['view_count'] += 1
                
                self.redis_client.setex(
                    cache_key,
                    self.cache_ttl,
                    json.dumps(pending_dict)
                )
            
            # Context7: Немедленная запись в PostgreSQL
            await self._update_interests_in_postgres(
                user_id=user_id,
                topics=topics,
                query_count_delta=0,
                view_count_delta=1,
                db=db
            )
            
            logger.debug("View tracked", user_id=str(user_id), post_id=str(post_id), topics_count=len(topics))
            
        except Exception as e:
            logger.error("Error tracking view", error=str(e), user_id=str(user_id), post_id=str(post_id))
    
    async def _update_interests_in_postgres(
        self,
        user_id: UUID,
        topics: List[str],
        query_count_delta: int,
        view_count_delta: int,
        db: Session
    ) -> None:
        """
        Обновление интересов в PostgreSQL.
        
        Context7: Использует UPSERT для идемпотентности
        """
        try:
            from models.database import UserInterest
            
            current_time = datetime.now(timezone.utc)
            
            for topic in topics:
                if not topic or not topic.strip():
                    continue
                
                # Проверяем существование записи
                interest = db.query(UserInterest).filter(
                    and_(
                        UserInterest.user_id == user_id,
                        UserInterest.topic == topic
                    )
                ).first()
                
                if interest:
                    # Обновляем существующую запись
                    interest.query_count += query_count_delta
                    interest.view_count += view_count_delta
                    interest.last_updated = current_time
                    
                    # Context7: Пересчитываем вес
                    interest.weight = self._calculate_weight(
                        interest.query_count,
                        interest.view_count,
                        current_time,
                        interest.created_at
                    )
                else:
                    # Создаем новую запись
                    interest = UserInterest(
                        user_id=user_id,
                        topic=topic,
                        query_count=query_count_delta,
                        view_count=view_count_delta,
                        weight=0.0,  # Будет пересчитан после сохранения
                        created_at=current_time,
                        last_updated=current_time
                    )
                    db.add(interest)
            
            db.commit()
            
            # Пересчитываем веса для всех интересов пользователя (нормализация)
            await self._normalize_user_weights(user_id, db)
            
        except Exception as e:
            logger.error("Error updating interests in PostgreSQL", error=str(e), user_id=str(user_id))
            db.rollback()
            raise
    
    def _calculate_weight(
        self,
        query_count: int,
        view_count: int,
        current_time: datetime,
        created_at: datetime
    ) -> float:
        """
        Context7: Расчет веса интереса.
        
        Формула: weight = (query_count * 0.4) + (view_count * 0.3) + (time_decay_factor * 0.3)
        Time decay: более свежие взаимодействия имеют больший вес (exponential decay)
        """
        # Time decay factor (exponential decay)
        days_old = (current_time - created_at).days
        time_decay = max(0.0, 1.0 - (days_old / 30.0))  # Полный decay за 30 дней
        
        # Context7: Формула веса
        weight = (query_count * 0.4) + (view_count * 0.3) + (time_decay * 0.3)
        
        return max(0.0, min(1.0, weight))  # Ограничение 0.0-1.0
    
    async def _normalize_user_weights(self, user_id: UUID, db: Session) -> None:
        """
        Нормализация весов для пользователя (сумма = 1.0).
        
        Context7: Пересчитывает веса после каждого обновления
        """
        try:
            from models.database import UserInterest
            
            interests = db.query(UserInterest).filter(
                UserInterest.user_id == user_id
            ).all()
            
            if not interests:
                return
            
            # Пересчитываем веса с учетом времени
            current_time = datetime.now(timezone.utc)
            total_weight = 0.0
            
            for interest in interests:
                interest.weight = self._calculate_weight(
                    interest.query_count,
                    interest.view_count,
                    current_time,
                    interest.created_at
                )
                total_weight += interest.weight
            
            # Нормализация
            if total_weight > 0:
                for interest in interests:
                    interest.weight = interest.weight / total_weight
            
            db.commit()
            
        except Exception as e:
            logger.error("Error normalizing user weights", error=str(e), user_id=str(user_id))
            db.rollback()
    
    def _extract_topics_from_query(
        self,
        query_text: str,
        intent: str,
        sources: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Извлечение тем из запроса и источников.
        
        Context7: Простое извлечение тем из источников
        """
        topics = set()
        
        # Добавляем темы из источников
        for source in sources:
            if 'topics' in source:
                if isinstance(source['topics'], list):
                    topics.update([t.get('name', '') if isinstance(t, dict) else str(t) for t in source['topics'] if t])
                elif isinstance(source['topics'], str):
                    topics.add(source['topics'])
        
        # Простое извлечение ключевых слов из запроса (можно улучшить через LLM)
        # Пока используем первые слова как тему
        words = query_text.lower().split()[:3]  # Первые 3 слова
        if words:
            topics.add(' '.join(words))
        
        return list(topics)
    
    async def get_user_interests(
        self,
        user_id: UUID,
        limit: int = 20,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Получение топ интересов из PostgreSQL (для аналитики).
        
        Context7: Используется для быстрых запросов и аналитики
        
        Args:
            user_id: ID пользователя
            limit: Максимальное количество результатов
            db: SQLAlchemy сессия
        
        Returns:
            Список интересов с весами
        """
        if not db:
            return []
        
        try:
            # Context7: Проверка кэша Redis
            if self.redis_client:
                cache_key = self._get_cache_key(str(user_id))
                cached = self.redis_client.get(cache_key)
                if cached:
                    return json.loads(cached)
            
            from models.database import UserInterest
            
            interests = db.query(UserInterest).filter(
                UserInterest.user_id == user_id
            ).order_by(desc(UserInterest.weight)).limit(limit).all()
            
            result = [
                {
                    'topic': interest.topic,
                    'weight': float(interest.weight),
                    'query_count': interest.query_count,
                    'view_count': interest.view_count,
                    'last_updated': interest.last_updated.isoformat() if interest.last_updated else None
                }
                for interest in interests
            ]
            
            # Context7: Кэширование результатов
            if self.redis_client:
                self.redis_client.setex(
                    cache_key,
                    self.cache_ttl,
                    json.dumps(result)
                )
            
            return result
            
        except Exception as e:
            logger.error("Error getting user interests", error=str(e), user_id=str(user_id))
            return []
    
    async def get_user_interests_graph(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Получение интересов пользователя из Neo4j (для рекомендаций).
        
        Context7: Используется для рекомендаций через графовые связи
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Список интересов из графа
        """
        try:
            return await self.graph_service.get_user_interests(user_id)
        except Exception as e:
            logger.error("Error getting user interests from graph", error=str(e), user_id=user_id)
            return []
    
    async def update_interests_from_history(
        self,
        user_id: UUID,
        days: int = 30,
        db: Optional[Session] = None
    ) -> None:
        """
        Обновление интересов из истории запросов.
        
        Context7: Анализирует rag_query_history для извлечения интересов
        
        Args:
            user_id: ID пользователя
            days: Количество дней для анализа
            db: SQLAlchemy сессия
        """
        if not db:
            return
        
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Получаем историю запросов
            queries = db.query(RAGQueryHistory).filter(
                and_(
                    RAGQueryHistory.user_id == user_id,
                    RAGQueryHistory.created_at >= cutoff_date
                )
            ).order_by(desc(RAGQueryHistory.created_at)).all()
            
            # Агрегируем темы из запросов
            topic_counts = {}
            for query in queries:
                topics = self._extract_topics_from_query(query.query_text, query.intent or '', [])
                for topic in topics:
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
            
            # Обновляем интересы
            if topic_counts:
                await self._update_interests_in_postgres(
                    user_id=user_id,
                    topics=list(topic_counts.keys()),
                    query_count_delta=0,  # Будет пересчитано
                    view_count_delta=0,
                    db=db
                )
            
            logger.info("Interests updated from history", user_id=str(user_id), topics_count=len(topic_counts))
            
        except Exception as e:
            logger.error("Error updating interests from history", error=str(e), user_id=str(user_id))


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_user_interest_service: Optional[UserInterestService] = None


def get_user_interest_service(
    redis_client: Optional[Any] = None,
    graph_service: Optional[Any] = None
) -> UserInterestService:
    """Получение singleton экземпляра UserInterestService."""
    global _user_interest_service
    if _user_interest_service is None:
        _user_interest_service = UserInterestService(
            redis_client=redis_client,
            graph_service=graph_service
        )
    return _user_interest_service

