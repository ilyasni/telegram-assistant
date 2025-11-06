"""
Recommendation Service для персонализированных рекомендаций
Context7: использует граф интересов пользователя из Neo4j (не PostgreSQL)
"""

from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy.orm import Session

from services.graph_service import get_graph_service
from services.user_interest_service import get_user_interest_service
from config import settings

logger = structlog.get_logger()


# ============================================================================
# RECOMMENDATION SERVICE
# ============================================================================

class RecommendationService:
    """
    Сервис для персонализированных рекомендаций.
    
    Context7: Использует граф интересов пользователя из Neo4j (не PostgreSQL)
    """
    
    def __init__(
        self,
        graph_service: Optional[Any] = None,
        user_interest_service: Optional[Any] = None
    ):
        """
        Инициализация Recommendation Service.
        
        Args:
            graph_service: GraphService для работы с Neo4j
            user_interest_service: UserInterestService для получения интересов
        """
        self.graph_service = graph_service or get_graph_service()
        self.user_interest_service = user_interest_service or get_user_interest_service()
        
        logger.info("RecommendationService initialized")
    
    async def get_recommendations(
        self,
        user_id: UUID,
        limit: int = 10,
        days: int = 7,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Получение персонализированных рекомендаций на основе графа интересов.
        
        Context7: Использует граф интересов пользователя из Neo4j
        
        Args:
            user_id: ID пользователя
            limit: Максимальное количество рекомендаций
            days: Количество дней для фильтрации постов
            db: SQLAlchemy сессия
        
        Returns:
            Список рекомендованных постов
        """
        try:
            # Context7: Получаем интересы пользователя из графа (Neo4j)
            user_interests = await self.graph_service.get_user_interests(str(user_id))
            
            if not user_interests:
                logger.debug("No user interests found in graph", user_id=str(user_id))
                # Fallback: используем интересы из PostgreSQL
                if db:
                    interests = await self.user_interest_service.get_user_interests(user_id, limit=10, db=db)
                    if not interests:
                        return []
                    user_interests = [{'topic': i['topic'], 'weight': i['weight']} for i in interests]
                else:
                    return []
            
            # Получаем топ-3 интереса
            top_interests = sorted(user_interests, key=lambda x: x.get('weight', 0.0), reverse=True)[:3]
            
            if not top_interests:
                return []
            
            # Context7: Поиск постов через граф интересов
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            
            recommendations = []
            for interest in top_interests:
                topic = interest.get('topic')
                if not topic:
                    continue
                
                # Поиск связанных постов через граф
                related_posts = await self.graph_service.search_related_posts(
                    query=topic,
                    topic=topic,
                    limit=limit * 2,
                    max_depth=getattr(settings, 'neo4j_max_graph_depth', 2)
                )
                
                # Добавляем вес интереса к score
                for post in related_posts:
                    post['recommendation_score'] = post.get('score', 0.8) * interest.get('weight', 1.0)
                    post['interest_topic'] = topic
                    recommendations.append(post)
            
            # Дедупликация и сортировка по recommendation_score
            seen_posts = {}
            for post in recommendations:
                post_id = post.get('post_id')
                if not post_id:
                    continue
                
                if post_id not in seen_posts:
                    seen_posts[post_id] = post
                else:
                    # Обновляем score если больше
                    if post.get('recommendation_score', 0) > seen_posts[post_id].get('recommendation_score', 0):
                        seen_posts[post_id] = post
            
            # Сортировка по recommendation_score
            sorted_recommendations = sorted(
                seen_posts.values(),
                key=lambda x: x.get('recommendation_score', 0),
                reverse=True
            )
            
            logger.debug(
                "Recommendations generated",
                user_id=str(user_id),
                count=len(sorted_recommendations[:limit]),
                interests_count=len(top_interests)
            )
            
            return sorted_recommendations[:limit]
            
        except Exception as e:
            logger.error("Error generating recommendations", error=str(e), user_id=str(user_id))
            return []
    
    async def get_collaborative_recommendations(
        self,
        user_id: UUID,
        limit: int = 10,
        days: int = 7,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """
        Collaborative filtering через граф.
        
        Context7: Находит пользователей с похожими интересами и рекомендует их посты
        
        Args:
            user_id: ID пользователя
            limit: Максимальное количество рекомендаций
            days: Количество дней для фильтрации постов
            db: SQLAlchemy сессия
        
        Returns:
            Список рекомендованных постов на основе collaborative filtering
        """
        try:
            # Context7: Health check перед графовым запросом
            if not await self.graph_service.health_check():
                logger.warning("Neo4j unavailable, skipping collaborative recommendations")
                return []
            
            # Context7: Cypher запрос для collaborative filtering
            # Находим пользователей с похожими интересами
            # Используем прямой вызов Neo4j через GraphService
            # (это упрощенная версия, можно расширить через GraphService)
            
            # Получаем интересы текущего пользователя
            user_interests = await self.graph_service.get_user_interests(str(user_id))
            
            if not user_interests:
                return []
            
            # Находим похожие темы через граф
            similar_topics = []
            for interest in user_interests[:5]:  # Топ-5 интересов
                topic = interest.get('topic')
                if topic:
                    similar = await self.graph_service.find_similar_topics(topic, limit=5)
                    similar_topics.extend([s['topic'] for s in similar])
            
            # Поиск постов по похожим темам
            recommendations = []
            for topic in similar_topics[:10]:  # Ограничиваем количество тем
                posts = await self.graph_service.search_related_posts(
                    query=topic,
                    topic=topic,
                    limit=limit,
                    max_depth=getattr(settings, 'neo4j_max_graph_depth', 2)
                )
                
                for post in posts:
                    post['recommendation_score'] = 0.7  # Базовый score для collaborative
                    post['recommendation_type'] = 'collaborative'
                    recommendations.append(post)
            
            # Дедупликация
            seen_posts = {}
            for post in recommendations:
                post_id = post.get('post_id')
                if post_id and post_id not in seen_posts:
                    seen_posts[post_id] = post
            
            sorted_recommendations = sorted(
                seen_posts.values(),
                key=lambda x: x.get('recommendation_score', 0),
                reverse=True
            )
            
            logger.debug(
                "Collaborative recommendations generated",
                user_id=str(user_id),
                count=len(sorted_recommendations[:limit])
            )
            
            return sorted_recommendations[:limit]
            
        except Exception as e:
            logger.error("Error generating collaborative recommendations", error=str(e), user_id=str(user_id))
            return []


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_recommendation_service: Optional[RecommendationService] = None


def get_recommendation_service(
    graph_service: Optional[Any] = None,
    user_interest_service: Optional[Any] = None
) -> RecommendationService:
    """Получение singleton экземпляра RecommendationService."""
    global _recommendation_service
    if _recommendation_service is None:
        _recommendation_service = RecommendationService(
            graph_service=graph_service,
            user_interest_service=user_interest_service
        )
    return _recommendation_service

