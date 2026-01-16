"""
Retrieval Service - общий слой для retrieval с батчированием и shared кэшем.

Performance guardrails:
- Общий retrieval слой с батчированием и shared кэшем
- Per-domain ограничения: max_docs_per_domain, max_graph_depth
- Домены не лезут сами в Qdrant/Neo4j, а просят retrieval-layer
"""

from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

logger = structlog.get_logger(__name__)

# Импорт GraphService для интеграции с Neo4j
try:
    # GraphService находится в api/services, но мы в api/worker/services
    # Используем абсолютный импорт через добавление пути
    import sys
    import os
    # Добавляем путь к api для импорта services
    api_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
    from services.graph_service import get_graph_service
    GRAPH_SERVICE_AVAILABLE = True
except ImportError as e:
    logger.warning("GraphService not available, graph retrieval will be disabled", error=str(e))
    GRAPH_SERVICE_AVAILABLE = False
    get_graph_service = None


class RetrievalResult:
    """Результат retrieval."""
    
    def __init__(
        self,
        documents: List[Dict[str, Any]],
        graph_entities: List[Dict[str, Any]] = None,
        cache_hit: bool = False,
    ):
        self.documents = documents
        self.graph_entities = graph_entities or []
        self.cache_hit = cache_hit


class RetrievalService:
    """Общий сервис для retrieval с батчированием и shared кэшем."""
    
    def __init__(
        self,
        qdrant_client: Optional[QdrantClient] = None,
        graph_service: Optional[Any] = None,
        redis_client: Optional[Any] = None,
    ):
        """
        Инициализация RetrievalService.
        
        Args:
            qdrant_client: Qdrant клиент
            graph_service: GraphService для работы с Neo4j
            redis_client: Redis клиент для кэширования
        """
        self._qdrant_client = qdrant_client
        self._graph_service = graph_service
        self._redis_client = redis_client
        self._cache: Dict[str, Tuple[RetrievalResult, float]] = {}  # key -> (result, timestamp)
        self._cache_ttl = 3600  # 1 час
    
    def _get_cache_key(
        self,
        tenant_id: str,
        query_text: str,
        domain: str,
        limit: int,
    ) -> str:
        """Получить ключ кэша."""
        normalized = query_text.lower().strip()[:200]
        signature = hashlib.sha256(f"{tenant_id}:{domain}:{normalized}:{limit}".encode()).hexdigest()[:16]
        return f"retrieval:{tenant_id}:{domain}:{signature}"
    
    def _get_from_cache(self, key: str) -> Optional[RetrievalResult]:
        """Получить результат из кэша."""
        if key not in self._cache:
            return None
        
        result, timestamp = self._cache[key]
        age = time.time() - timestamp
        
        if age > self._cache_ttl:
            del self._cache[key]
            return None
        
        return result
    
    def _set_cache(self, key: str, result: RetrievalResult):
        """Сохранить результат в кэш."""
        # Ограничение размера кэша (LRU)
        if len(self._cache) >= 10000:
            sorted_items = sorted(self._cache.items(), key=lambda x: x[1][1])
            items_to_remove = len(sorted_items) - 10000 + 1
            for i in range(items_to_remove):
                del self._cache[sorted_items[i][0]]
        
        self._cache[key] = (result, time.time())
    
    async def retrieve_documents(
        self,
        tenant_id: str,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        domain: str = "general",
        limit: int = 50,
        channel_ids: Optional[List[str]] = None,
        max_docs_per_domain: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получить документы из Qdrant.
        
        Performance: Использует кэш и ограничения по домену.
        
        Args:
            tenant_id: ID тенанта
            query_text: Текст запроса
            query_embedding: Embedding запроса (опционально)
            domain: Домен (для ограничений)
            limit: Максимальное количество документов
            channel_ids: Фильтр по каналам
            max_docs_per_domain: Максимальное количество документов для домена
        
        Returns:
            Список документов
        """
        # Проверка кэша
        cache_key = self._get_cache_key(tenant_id, query_text, domain, limit)
        cached_result = self._get_from_cache(cache_key)
        if cached_result:
            logger.debug("retrieval.cache_hit", domain=domain, tenant_id=tenant_id)
            return cached_result.documents
        
        # Ограничение по домену
        effective_limit = min(limit, max_docs_per_domain or limit)
        
        if not self._qdrant_client:
            logger.warning("retrieval.qdrant_unavailable", domain=domain)
            return []
        
        try:
            collection_name = f"t{tenant_id}_posts"
            
            # Проверка существования коллекции
            collections = self._qdrant_client.get_collections()
            if collection_name not in [c.name for c in collections.collections]:
                logger.warning("retrieval.collection_not_found", collection=collection_name)
                return []
            
            # Подготовка фильтра
            filter_conditions = [
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(value=str(tenant_id))
                )
            ]
            
            if channel_ids:
                filter_conditions.append(
                    FieldCondition(
                        key="channel_id",
                        match=MatchValue(any=[str(cid) for cid in channel_ids])
                    )
                )
            
            search_filter = Filter(must=filter_conditions) if filter_conditions else None
            
            # Поиск в Qdrant
            if query_embedding:
                search_results = self._qdrant_client.search(
                    collection_name=collection_name,
                    query_vector=query_embedding,
                    query_filter=search_filter,
                    limit=effective_limit
                )
            else:
                # Fallback: если нет embedding, возвращаем пустой список
                logger.warning("retrieval.no_embedding", domain=domain)
                return []
            
            documents = []
            for result in search_results:
                documents.append({
                    'post_id': result.payload.get('post_id'),
                    'score': result.score,
                    'payload': result.payload
                })
            
            # Сохранение в кэш
            retrieval_result = RetrievalResult(documents=documents, cache_hit=False)
            self._set_cache(cache_key, retrieval_result)
            
            logger.debug(
                "retrieval.documents_retrieved",
                domain=domain,
                tenant_id=tenant_id,
                count=len(documents),
                limit=effective_limit
            )
            
            return documents
        
        except Exception as e:
            logger.error("retrieval.qdrant_error", error=str(e), domain=domain)
            return []
    
    async def retrieve_graph_entities(
        self,
        tenant_id: str,
        query_text: str,
        domain: str = "general",
        max_depth: int = 3,
        max_entities: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Получить сущности из Neo4j графа.
        
        Performance: Использует ограничения по домену (max_depth, max_entities).
        
        Args:
            tenant_id: ID тенанта
            query_text: Текст запроса
            domain: Домен (для ограничений)
            max_depth: Максимальная глубина графа
            max_entities: Максимальное количество сущностей
        
        Returns:
            Список сущностей графа
        """
        if not self._graph_service:
            logger.warning("retrieval.graph_unavailable", domain=domain)
            return []
        
        # Ограничение глубины по домену
        effective_depth = min(max_depth, 3)  # Максимум 3 для безопасности
        effective_limit = min(max_entities, 50)  # Максимум 50 сущностей
        
        try:
            # Интеграция с GraphService для получения сущностей
            if not self._graph_service:
                logger.debug("retrieval.graph_unavailable", domain=domain)
                return []
            
            # Проверка health check перед запросом
            if hasattr(self._graph_service, 'health_check'):
                is_healthy = await self._graph_service.health_check()
                if not is_healthy:
                    logger.warning("retrieval.graph_unhealthy", domain=domain)
                    return []
            
            # Поиск связанных постов через граф
            # GraphService.search_related_posts возвращает посты, которые содержат сущности
            graph_posts = await self._graph_service.search_related_posts(
                query=query_text,
                topic=None,
                tenant_id=tenant_id,
                limit=effective_limit,
                max_depth=effective_depth
            )
            
            # Преобразуем посты в формат сущностей графа
            entities = []
            for post in graph_posts:
                entity = {
                    "id": post.get("post_id"),
                    "type": "Post",
                    "content": post.get("content", ""),
                    "topics": post.get("topics", []),
                    "topic": post.get("topic"),
                    "relation_type": post.get("relation_type", "direct"),
                    "channel_title": post.get("channel_title"),
                }
                entities.append(entity)
            
            logger.debug(
                "retrieval.graph_retrieval",
                domain=domain,
                tenant_id=tenant_id,
                max_depth=effective_depth,
                max_entities=len(entities)
            )
            return entities
        
        except Exception as e:
            logger.error("retrieval.graph_error", error=str(e), domain=domain)
            return []
    
    async def retrieve(
        self,
        tenant_id: str,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        domain: str = "general",
        limit_docs: int = 50,
        max_docs_per_domain: Optional[int] = None,
        max_graph_depth: int = 3,
        channel_ids: Optional[List[str]] = None,
    ) -> RetrievalResult:
        """
        Получить документы и сущности графа.
        
        Performance: Батчирование и shared кэш.
        
        Args:
            tenant_id: ID тенанта
            query_text: Текст запроса
            query_embedding: Embedding запроса
            domain: Домен
            limit_docs: Максимальное количество документов
            max_docs_per_domain: Максимальное количество документов для домена
            max_graph_depth: Максимальная глубина графа
            channel_ids: Фильтр по каналам
        
        Returns:
            RetrievalResult с документами и сущностями графа
        """
        # Проверка кэша
        cache_key = self._get_cache_key(tenant_id, query_text, domain, limit_docs)
        cached_result = self._get_from_cache(cache_key)
        if cached_result:
            return cached_result
        
        # Параллельное получение документов и сущностей графа
        documents = await self.retrieve_documents(
            tenant_id=tenant_id,
            query_text=query_text,
            query_embedding=query_embedding,
            domain=domain,
            limit=limit_docs,
            channel_ids=channel_ids,
            max_docs_per_domain=max_docs_per_domain,
        )
        
        graph_entities = await self.retrieve_graph_entities(
            tenant_id=tenant_id,
            query_text=query_text,
            domain=domain,
            max_depth=max_graph_depth,
            max_entities=50,
        )
        
        result = RetrievalResult(
            documents=documents,
            graph_entities=graph_entities,
            cache_hit=False,
        )
        
        # Сохранение в кэш
        self._set_cache(cache_key, result)
        
        return result


# Singleton instance
_retrieval_service: Optional[RetrievalService] = None


def get_retrieval_service(
    qdrant_client: Optional[QdrantClient] = None,
    graph_service: Optional[Any] = None,
    redis_client: Optional[Any] = None,
) -> RetrievalService:
    """Получить экземпляр RetrievalService."""
    global _retrieval_service
    if qdrant_client or graph_service or redis_client:
        return RetrievalService(
            qdrant_client=qdrant_client,
            graph_service=graph_service,
            redis_client=redis_client,
        )
    if _retrieval_service is None:
        # Автоматическая инициализация GraphService, если доступен
        graph_svc = None
        if GRAPH_SERVICE_AVAILABLE and get_graph_service:
            try:
                graph_svc = get_graph_service()
            except Exception as e:
                logger.warning("retrieval.graph_service_init_failed", error=str(e))
        
        _retrieval_service = RetrievalService(graph_service=graph_svc)
    return _retrieval_service

