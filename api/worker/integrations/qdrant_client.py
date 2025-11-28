"""
Qdrant Client с поддержкой sweeper job для очистки expired векторов
[C7-ID: WORKER-QDRANT-SWEEP-001]

Поддерживает per-user коллекции и периодическую очистку по expires_at
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import structlog
from qdrant_client import QdrantClient as QdrantSDK
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

logger = structlog.get_logger()

# ============================================================================
# QDRANT CLIENT
# ============================================================================

class QdrantClient:
    """
    Qdrant клиент с поддержкой sweeper job.
    
    Поддерживает:
    - Per-user коллекции
    - Sweeper job для очистки expired векторов
    - Сквозной TTL в payload
    - Метрики и мониторинг
    """
    
    def __init__(self, url: str = "http://localhost:6333"):
        self.url = url
        self.client: Optional[QdrantSDK] = None
        self._collections_cache: Dict[str, bool] = {}
        
        logger.info("QdrantClient initialized", url=url)
    
    async def connect(self):
        """Подключение к Qdrant."""
        try:
            self.client = QdrantSDK(url=self.url)
            
            # Проверка подключения
            await self._ping()
            
            logger.info("QdrantClient connected successfully")
            
        except Exception as e:
            logger.error("Failed to connect to Qdrant", error=str(e))
            raise
    
    async def _ping(self):
        """Проверка подключения к Qdrant."""
        try:
            # Простой запрос для проверки подключения
            collections = self.client.get_collections()
            logger.debug("Qdrant ping successful", collections_count=len(collections.collections))
        except Exception as e:
            logger.error("Qdrant ping failed", error=str(e))
            raise
    
    async def ensure_collection(self, collection_name: str, vector_size: int = None):
        """
        Создание коллекции если не существует.
        
        Context7: Размерность берется из переданного параметра или из настроек
        - EmbeddingsGigaR: 2560 измерений
        - Embeddings (Giga-Embeddings-instruct): 2048 измерений
        Если не указана, используется значение из EMBEDDING_DIMENSION или 2560 по умолчанию
        """
        import os
        if vector_size is None:
            vector_size = int(os.getenv("EMBEDDING_DIMENSION", os.getenv("EMBED_DIM", "2560")))
        try:
            if collection_name in self._collections_cache:
                return
            
            # Проверка существования коллекции
            try:
                collection_info = self.client.get_collection(collection_name)
                self._collections_cache[collection_name] = True
                logger.debug("Collection already exists", collection=collection_name)
                return
            except UnexpectedResponse:
                # Коллекция не существует, создаем
                pass
            
            # Создание коллекции
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE
                )
            )
            
            self._collections_cache[collection_name] = True
            logger.info("Collection created", 
                       collection=collection_name,
                       vector_size=vector_size)
            
        except Exception as e:
            logger.error("Error ensuring collection", 
                        collection=collection_name,
                        error=str(e))
            raise
    
    async def upsert_vector(
        self, 
        collection_name: str, 
        vector_id: str, 
        vector: List[float], 
        payload: Dict[str, Any]
    ) -> str:
        """Добавление/обновление вектора в коллекции."""
        try:
            # Обеспечение существования коллекции
            await self.ensure_collection(collection_name, len(vector))
            
            # Upsert вектора
            self.client.upsert(
                collection_name=collection_name,
                points=[
                    models.PointStruct(
                        id=vector_id,
                        vector=vector,
                        payload=payload
                    )
                ]
            )
            
            logger.debug("Vector upserted successfully",
                        collection=collection_name,
                        vector_id=vector_id)
            
            return vector_id
            
        except Exception as e:
            logger.error("Error upserting vector",
                        collection=collection_name,
                        vector_id=vector_id,
                        error=str(e))
            raise
    
    async def delete_vector(self, collection_name: str, vector_id: str) -> bool:
        """Удаление вектора из коллекции."""
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=models.PointIdsList(points=[vector_id])
            )
            
            logger.debug("Vector deleted successfully",
                        collection=collection_name,
                        vector_id=vector_id)
            
            return True
            
        except Exception as e:
            logger.error("Error deleting vector",
                        collection=collection_name,
                        vector_id=vector_id,
                        error=str(e))
            return False
    
    async def retrieve_vectors(
        self,
        collection_name: str,
        vector_ids: List[str],
        with_vectors: bool = True,
        with_payload: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Получение векторов из Qdrant по их ID.
        
        Context7 best practice: используем retrieve для batch получения точек по ID.
        Это эффективнее, чем scroll с фильтром для получения конкретных точек.
        
        Args:
            collection_name: Название коллекции
            vector_ids: Список ID векторов для получения
            with_vectors: Включать ли векторы в результат
            with_payload: Включать ли payload в результат
        
        Returns:
            Список словарей с полями:
            - id: ID точки
            - vector: Вектор (если with_vectors=True)
            - payload: Payload (если with_payload=True)
        """
        if not vector_ids:
            return []
        
        try:
            # Context7: Используем retrieve для batch получения точек
            # Qdrant SDK поддерживает до 100 точек за запрос
            batch_size = 100
            all_points = []
            
            for i in range(0, len(vector_ids), batch_size):
                batch_ids = vector_ids[i:i + batch_size]
                
                retrieved_points = self.client.retrieve(
                    collection_name=collection_name,
                    ids=batch_ids,
                    with_vectors=with_vectors,
                    with_payload=with_payload
                )
                
                for point in retrieved_points:
                    all_points.append({
                        'id': str(point.id),
                        'vector': point.vector if with_vectors else None,
                        'payload': point.payload if with_payload else None
                    })
            
            logger.debug("Vectors retrieved successfully",
                        collection=collection_name,
                        requested_count=len(vector_ids),
                        retrieved_count=len(all_points))
            
            return all_points
            
        except Exception as e:
            logger.error("Error retrieving vectors",
                        collection=collection_name,
                        vector_ids_count=len(vector_ids),
                        error=str(e))
            return []
    
    async def search_vectors(
        self, 
        collection_name: str, 
        query_vector: List[float], 
        limit: int = 10,
        filter_conditions: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Поиск векторов в коллекции.
        
        Context7: Обязательная фильтрация по tenant_id для multi-tenant изоляции.
        """
        try:
            # Подготовка фильтра
            must_conditions = []
            
            # Context7: Обязательная фильтрация по tenant_id
            if tenant_id:
                must_conditions.append(
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=str(tenant_id))
                    )
                )
            
            # Добавляем дополнительные фильтры
            if filter_conditions:
                for key, value in filter_conditions.items():
                    if isinstance(value, str):
                        must_conditions.append(
                            models.FieldCondition(
                                key=key,
                                match=models.MatchValue(value=value)
                            )
                        )
                    elif isinstance(value, (int, float)):
                        # Context7: Поддержка числовых значений (album_id, channel_id и т.д.)
                        must_conditions.append(
                            models.FieldCondition(
                                key=key,
                                match=models.MatchValue(value=value)
                            )
                        )
                    elif isinstance(value, list):
                        # Context7: Поддержка списков значений (например, tags)
                        must_conditions.append(
                            models.FieldCondition(
                                key=key,
                                match=models.MatchAny(any=value)
                            )
                        )
                    elif isinstance(value, dict) and 'range' in value:
                        must_conditions.append(
                            models.FieldCondition(
                                key=key,
                                range=models.Range(**value['range'])
                            )
                        )
                    elif isinstance(value, bool):
                        # Context7: Поддержка boolean значений (например, vision.is_meme)
                        must_conditions.append(
                            models.FieldCondition(
                                key=key,
                                match=models.MatchValue(value=value)
                            )
                        )
            
            # Создаём фильтр только если есть условия
            search_filter = models.Filter(must=must_conditions) if must_conditions else None
            
            # Поиск
            search_results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                query_filter=search_filter,
                limit=limit
            )
            
            # Форматирование результатов
            results = []
            for result in search_results:
                results.append({
                    'id': result.id,
                    'score': result.score,
                    'payload': result.payload
                })
            
            logger.debug("Search completed",
                        collection=collection_name,
                        results_count=len(results))
            
            return results
            
        except Exception as e:
            logger.error("Error searching vectors",
                        collection=collection_name,
                        error=str(e))
            return []
    
    async def sweep_expired_vectors(self, collection_name: str) -> int:
        """
        [C7-ID: WORKER-QDRANT-SWEEP-001] - Sweeper job для очистки expired векторов.
        
        Удаляет векторы где expires_at < now().
        """
        try:
            current_time = datetime.now(timezone.utc).isoformat()
            
            # Поиск expired векторов
            expired_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="expires_at",
                        range=models.Range(lt=current_time)
                    )
                ]
            )
            
            # Scroll для получения всех expired векторов
            expired_points = []
            offset = None
            
            while True:
                scroll_result = self.client.scroll(
                    collection_name=collection_name,
                    scroll_filter=expired_filter,
                    limit=100,
                    offset=offset
                )
                
                if not scroll_result[0]:  # Нет больше точек
                    break
                
                expired_points.extend([point.id for point in scroll_result[0]])
                offset = scroll_result[1]
                
                # Ограничение на количество точек за раз
                if len(expired_points) >= 1000:
                    break
            
            if not expired_points:
                logger.debug("No expired vectors found", collection=collection_name)
                return 0
            
            # Удаление expired векторов
            self.client.delete(
                collection_name=collection_name,
                points_selector=models.PointIdsList(points=expired_points)
            )
            
            logger.info("Expired vectors deleted",
                       collection=collection_name,
                       deleted_count=len(expired_points))
            
            return len(expired_points)
            
        except Exception as e:
            logger.error("Error sweeping expired vectors",
                        collection=collection_name,
                        error=str(e))
            return 0
    
    async def sweep_all_collections(self) -> Dict[str, int]:
        """Sweep всех коллекций пользователей."""
        try:
            # Получение списка коллекций
            collections = self.client.get_collections()
            # Context7: Поддержка нового формата t{tenant_id}_posts и старого user_{tenant_id}_posts
            user_collections = [
                col.name for col in collections.collections 
                if (col.name.startswith('t') and col.name.endswith('_posts')) or
                   (col.name.startswith('user_') and col.name.endswith('_posts'))
            ]
            
            sweep_results = {}
            total_deleted = 0
            
            for collection_name in user_collections:
                deleted_count = await self.sweep_expired_vectors(collection_name)
                sweep_results[collection_name] = deleted_count
                total_deleted += deleted_count
            
            logger.info("Sweep completed for all collections",
                       total_collections=len(user_collections),
                       total_deleted=total_deleted)
            
            return sweep_results
            
        except Exception as e:
            logger.error("Error sweeping all collections", error=str(e))
            return {}
    
    async def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """Получение статистики коллекции."""
        try:
            collection_info = self.client.get_collection(collection_name)
            
            return {
                'name': collection_name,
                'vectors_count': collection_info.vectors_count,
                'indexed_vectors_count': collection_info.indexed_vectors_count,
                'points_count': collection_info.points_count,
                'segments_count': collection_info.segments_count,
                'status': collection_info.status
            }
            
        except Exception as e:
            logger.error("Error getting collection stats",
                        collection=collection_name,
                        error=str(e))
            return {}
    
    async def get_all_collections_stats(self) -> Dict[str, Dict[str, Any]]:
        """Получение статистики всех коллекций."""
        try:
            collections = self.client.get_collections()
            stats = {}
            
            for collection in collections.collections:
                collection_name = collection.name
                # Context7: Поддержка нового формата t{tenant_id}_posts и старого user_{tenant_id}_posts
                if (collection_name.startswith('t') and collection_name.endswith('_posts')) or \
                   (collection_name.startswith('user_') and collection_name.endswith('_posts')):
                    stats[collection_name] = await self.get_collection_stats(collection_name)
            
            return stats
            
        except Exception as e:
            logger.error("Error getting all collections stats", error=str(e))
            return {}
    
    async def health_check(self) -> bool:
        """Health check для Qdrant."""
        try:
            await self._ping()
            return True
        except Exception as e:
            logger.error("Qdrant health check failed", error=str(e))
            return False
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение общей статистики Qdrant."""
        try:
            collections = self.client.get_collections()
            # Context7: Поддержка нового формата t{tenant_id}_posts и старого user_{tenant_id}_posts
            user_collections = [
                col.name for col in collections.collections 
                if (col.name.startswith('t') and col.name.endswith('_posts')) or
                   (col.name.startswith('user_') and col.name.endswith('_posts'))
            ]
            
            total_vectors = 0
            for collection_name in user_collections:
                stats = await self.get_collection_stats(collection_name)
                total_vectors += stats.get('vectors_count', 0)
            
            return {
                'connected': True,
                'collections_count': len(user_collections),
                'total_vectors': total_vectors,
                'collections': user_collections
            }
            
        except Exception as e:
            logger.error("Error getting Qdrant stats", error=str(e))
            return {
                'connected': False,
                'error': str(e)
            }
