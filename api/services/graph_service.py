"""
Graph Service для работы с Neo4j из API сервиса
Context7 best practice: переиспользование логики из worker/integrations/neo4j_client.py
"""

import asyncio
import os
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timezone

import structlog
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

from config import settings

logger = structlog.get_logger()


# ============================================================================
# GRAPH SERVICE
# ============================================================================

class GraphService:
    """
    Graph Service для работы с Neo4j из API сервиса.
    
    Context7: Переиспользование логики из worker/integrations/neo4j_client.py
    - Connection pooling
    - Health checks
    - Async-loop provider
    - Параметризованные Cypher запросы
    """
    
    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ):
        """
        Инициализация Graph Service.
        
        Args:
            uri: Neo4j URI (по умолчанию из env)
            username: Neo4j username (по умолчанию из env)
            password: Neo4j password (по умолчанию из env)
        """
        # Context7: Источник правды — env, с возможностью явной передачи аргументов
        env_uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL")
        self.uri = uri or env_uri or "neo4j://neo4j:7687"
        self.username = username or os.getenv("NEO4J_USER", "neo4j")
        # Context7: Исправлен дефолтный пароль (должен совпадать с docker-compose.yml)
        self.password = password or os.getenv("NEO4J_PASSWORD", "neo4j123")
        
        # Context7: Async-loop provider (как в worker/integrations/neo4j_client.py)
        self._driver = None
        self._current_loop = None
        
        logger.info("GraphService initialized", uri=self.uri, username=self.username)
    
    async def connect(self):
        """Подключение к Neo4j с проверкой event loop."""
        try:
            # Context7: Проверка event loop
            current_loop = asyncio.get_running_loop()
            if self._current_loop is not None and self._current_loop != current_loop:
                logger.warning("Event loop changed, reconnecting Neo4j driver")
                await self.close()
            
            if self._driver is None or self._current_loop != current_loop:
                # Создание нового драйвера в текущем event loop
                self._driver = AsyncGraphDatabase.driver(
                    self.uri,
                    auth=(self.username, self.password)
                )
                self._current_loop = current_loop
                
                # Проверка подключения
                await self._ping()
                
                logger.info("GraphService connected successfully")
            
        except Exception as e:
            logger.error("Failed to connect to Neo4j", error=str(e))
            raise
    
    async def _ping(self):
        """Health-пинг перед операциями."""
        try:
            async with self._driver.session() as session:
                result = await session.run("RETURN 1 as ping")
                record = await result.single()
                if record and record["ping"] == 1:
                    logger.debug("Neo4j ping successful")
                else:
                    raise Exception("Neo4j ping failed")
        except Exception as e:
            logger.error("Neo4j ping failed", error=str(e))
            raise
    
    async def search_related_posts(
        self,
        query: str,
        topic: Optional[str] = None,
        limit: int = 10,
        max_depth: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Поиск связанных постов через граф.
        
        Context7: Использует параметризованные Cypher запросы (никогда f-strings)
        
        Args:
            query: Текст запроса (для поиска по темам)
            topic: Тема для фильтрации (опционально)
            limit: Максимальное количество результатов
            max_depth: Максимальная глубина обхода графа (2-3 для производительности)
        
        Returns:
            Список связанных постов с метаданными
        """
        try:
            if not self._driver:
                await self.connect()
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Context7: Параметризованный Cypher запрос
                # Важно: Neo4j не поддерживает параметры в MATCH patterns для переменной глубины
                # Используем фиксированную глубину *1..2
                if topic:
                    cypher_query = """
                    MATCH (t:Topic {name: $topic})
                    MATCH (t)<-[:HAS_TOPIC]-(p:Post)
                    OPTIONAL MATCH path = (t)-[:RELATED_TO*1..2]-(related_t:Topic)
                    WHERE related_t IS NOT NULL
                    OPTIONAL MATCH (related_t)<-[:HAS_TOPIC]-(related_p:Post)
                    WITH DISTINCT p, related_p, t.name AS topic_name
                    RETURN p.post_id AS post_id,
                           p.content AS content,
                           topic_name,
                           'direct' AS relation_type
                    ORDER BY p.posted_at DESC
                    LIMIT $limit
                    """
                    result = await session.run(
                        cypher_query,
                        topic=topic,
                        limit=limit
                    )
                else:
                    # Поиск по всем темам, связанным с запросом
                    cypher_query = """
                    MATCH (t:Topic)
                    WHERE toLower(t.name) CONTAINS toLower($query)
                    MATCH (t)<-[:HAS_TOPIC]-(p:Post)
                    OPTIONAL MATCH (p)-[:IN_CHANNEL]->(c:Channel)
                    RETURN p.post_id AS post_id,
                           p.content AS content,
                           collect(DISTINCT t.name) AS topics,
                           c.title AS channel_title
                    ORDER BY p.posted_at DESC
                    LIMIT $limit
                    """
                    result = await session.run(
                        cypher_query,
                        query=query,
                        limit=limit
                    )
                
                posts = []
                async for record in result:
                    posts.append({
                        'post_id': record.get('post_id'),
                        'content': record.get('content', ''),
                        'topic': record.get('topic_name') or (record.get('topics', [])[0] if record.get('topics') else None),
                        'topics': record.get('topics', []),
                        'channel_title': record.get('channel_title'),
                        'relation_type': record.get('relation_type', 'direct')
                    })
                
                logger.debug("Found related posts", count=len(posts), topic=topic)
                return posts
                
        except Exception as e:
            logger.error("Error searching related posts", error=str(e), query=query[:50])
            return []
    
    async def get_user_interests(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Получение интересов пользователя из графа (Neo4j).
        
        Context7: Для рекомендаций используется граф, а не PostgreSQL
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Список интересов с весами
        """
        try:
            if not self._driver:
                await self.connect()
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Context7: Параметризованный Cypher запрос
                query = """
                MATCH (u:User {user_id: $user_id})-[r:INTERESTED_IN]->(t:Topic)
                RETURN t.name AS topic,
                       r.weight AS weight,
                       r.last_updated AS last_updated
                ORDER BY r.weight DESC
                LIMIT 20
                """
                
                result = await session.run(query, user_id=user_id)
                
                interests = []
                async for record in result:
                    interests.append({
                        'topic': record.get('topic'),
                        'weight': record.get('weight', 0.0),
                        'last_updated': record.get('last_updated')
                    })
                
                logger.debug("Retrieved user interests from graph", user_id=user_id, count=len(interests))
                return interests
                
        except Exception as e:
            logger.error("Error getting user interests from graph", error=str(e), user_id=user_id)
            return []
    
    async def update_user_interest(
        self,
        user_id: str,
        topic: str,
        weight: float
    ) -> bool:
        """
        Обновление интереса пользователя в графе.
        
        Context7: MERGE с параметрами для upsert
        
        Args:
            user_id: ID пользователя
            topic: Название темы
            weight: Вес интереса (0.0-1.0)
        
        Returns:
            True если успешно
        """
        try:
            if not self._driver:
                await self.connect()
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Context7: MERGE с параметрами (никогда f-strings)
                query = """
                MERGE (u:User {user_id: $user_id})
                MERGE (t:Topic {name: $topic})
                MERGE (u)-[r:INTERESTED_IN]->(t)
                SET r.weight = $weight,
                    r.last_updated = datetime()
                RETURN r.weight AS weight
                """
                
                result = await session.run(
                    query,
                    user_id=user_id,
                    topic=topic,
                    weight=weight
                )
                
                record = await result.single()
                if record:
                    logger.debug("User interest updated in graph", user_id=user_id, topic=topic, weight=weight)
                    return True
                else:
                    logger.warning("Failed to update user interest", user_id=user_id, topic=topic)
                    return False
                    
        except Exception as e:
            logger.error("Error updating user interest in graph", error=str(e), user_id=user_id, topic=topic)
            return False
    
    async def find_similar_topics(self, topic: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Поиск похожих тем через граф.
        
        Context7: Использует связи RELATED_TO для поиска похожих тем
        
        Args:
            topic: Тема для поиска
            limit: Максимальное количество результатов
        
        Returns:
            Список похожих тем с similarity scores
        """
        try:
            if not self._driver:
                await self.connect()
            
            await self._ping()
            
            async with self._driver.session() as session:
                # Context7: Параметризованный Cypher запрос
                query = """
                MATCH (t:Topic {name: $topic})-[r:RELATED_TO]-(similar:Topic)
                RETURN similar.name AS topic,
                       r.similarity AS similarity
                ORDER BY r.similarity DESC
                LIMIT $limit
                """
                
                result = await session.run(query, topic=topic, limit=limit)
                
                similar_topics = []
                async for record in result:
                    similar_topics.append({
                        'topic': record.get('topic'),
                        'similarity': record.get('similarity', 0.0)
                    })
                
                logger.debug("Found similar topics", topic=topic, count=len(similar_topics))
                return similar_topics
                
        except Exception as e:
            logger.error("Error finding similar topics", error=str(e), topic=topic)
            return []
    
    async def health_check(self) -> bool:
        """Health check для Neo4j."""
        try:
            if not self._driver:
                await self.connect()
            
            await self._ping()
            return True
            
        except Exception as e:
            logger.error("Neo4j health check failed", error=str(e))
            return False
    
    async def close(self):
        """Закрытие подключения к Neo4j."""
        try:
            if self._driver:
                await self._driver.close()
                self._driver = None
                self._current_loop = None
                logger.info("GraphService connection closed")
        except Exception as e:
            logger.error("Error closing Neo4j connection", error=str(e))


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_graph_service: Optional[GraphService] = None


def get_graph_service(
    uri: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> GraphService:
    """Получение singleton экземпляра GraphService."""
    global _graph_service
    if _graph_service is None:
        _graph_service = GraphService(uri=uri, username=username, password=password)
    return _graph_service

