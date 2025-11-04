"""Задачи для создания embeddings."""

import asyncio
import structlog
from typing import List, Dict, Any
import requests
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, PayloadSchemaType
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime

from config import settings

logger = structlog.get_logger()


class EmbeddingService:
    """Сервис для создания и индексации embeddings."""
    
    def __init__(self):
        self.qdrant_client = None
        self.db_connection = None
        
    async def initialize(self):
        """Инициализация сервиса."""
        try:
            print("EmbeddingService: Starting initialization...", flush=True)
            
            # Пока что не инициализируем Qdrant и БД - просто логируем
            print("EmbeddingService: Skipping Qdrant and DB initialization for now", flush=True)
            logger.info("EmbeddingService initialized (simplified)")
            print("EmbeddingService: Initialization complete", flush=True)
            
        except Exception as e:
            logger.error("Failed to initialize embedding service", error=str(e))
            print(f"Failed to initialize embedding service: {e}", flush=True)
            raise
    
    async def process_post_embeddings(self, post_id: str, tenant_id: str):
        """Обработка embeddings для поста."""
        try:
            # Получение данных поста
            post_data = await self._get_post_data(post_id, tenant_id)
            if not post_data:
                logger.warning("Post not found", post_id=post_id)
                return
            
            # Создание embeddings
            embeddings = await self._create_embeddings(post_data['content'])
            
            # Сохранение в Qdrant
            await self._save_to_qdrant(post_id, tenant_id, post_data, embeddings)
            
            # Обновление статуса в БД
            await self._update_embedding_status(post_id, 'completed')
            
            logger.info("Post embeddings processed", 
                       post_id=post_id, 
                       embedding_dim=len(embeddings))
            
        except Exception as e:
            logger.error("Failed to process post embeddings", 
                        post_id=post_id, 
                        error=str(e))
            await self._update_embedding_status(post_id, 'failed', str(e))
    
    async def _get_post_data(self, post_id: str, tenant_id: str) -> Dict[str, Any]:
        """Получение данных поста из БД."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                # Context7: tenant_id получаем из channels, так как posts больше не содержит tenant_id
                cursor.execute("""
                    SELECT p.id, c.tenant_id, p.channel_id, p.telegram_message_id,
                           p.content, p.media_urls, p.created_at,
                           c.username as channel_username, c.title as channel_title
                    FROM posts p
                    JOIN channels c ON p.channel_id = c.id
                    WHERE p.id = %s AND c.tenant_id = %s
                """, (post_id, tenant_id))
                
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
                
        except Exception as e:
            logger.error("Failed to get post data", error=str(e))
            return None
    
    async def _create_embeddings(self, content: str) -> List[float]:
        """Создание embeddings для текста."""
        if not content or not content.strip():
            # Пустой контент - создаём нулевой вектор
            return [0.0] * settings.EMBED_DIM
        
        try:
            # Используем простой хеш-эмбеддинг для демонстрации
            # В реальном проекте здесь будет вызов внешнего API (OpenAI, GigaChat, etc.)
            import hashlib
            
            # Создаём детерминированный вектор на основе хеша текста
            text_hash = hashlib.md5(content.encode()).hexdigest()
            
            # Преобразуем хеш в вектор нужной размерности
            embeddings = []
            for i in range(0, len(text_hash), 2):
                hex_pair = text_hash[i:i+2]
                embeddings.append(int(hex_pair, 16) / 255.0 - 0.5)  # нормализация в [-0.5, 0.5]
            
            # Дополняем до нужной размерности
            while len(embeddings) < settings.EMBED_DIM:
                embeddings.append(0.0)
            
            # Обрезаем до нужной размерности
            embeddings = embeddings[:settings.EMBED_DIM]
            
            logger.debug("Created hash-based embeddings", 
                        content_length=len(content),
                        embedding_dim=len(embeddings))
            
            return embeddings
            
        except Exception as e:
            logger.error("Failed to create embeddings", error=str(e))
            # Возвращаем нулевой вектор в случае ошибки
            return [0.0] * settings.EMBED_DIM
    
    async def _save_to_qdrant(self, post_id: str, tenant_id: str, post_data: Dict[str, Any], embeddings: List[float]):
        """Сохранение embeddings в Qdrant."""
        try:
            # Context7: Per-tenant коллекция: t{tenant_id}_posts
            collection_name = f"t{tenant_id}_posts"
            
            # Проверка существования коллекции
            collections = self.qdrant_client.get_collections()
            if collection_name not in [c.name for c in collections.collections]:
                # Создание коллекции
                self.qdrant_client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=settings.EMBED_DIM,
                        distance=Distance.COSINE
                    )
                )
                logger.info("Created collection", collection=collection_name)
            
            # Подготовка метаданных
            payload = {
                "post_id": post_id,
                "tenant_id": tenant_id,
                "channel_id": str(post_data['channel_id']),
                "telegram_message_id": post_data['telegram_message_id'],
                "content": post_data['content'],
                "channel_username": post_data['channel_username'],
                "channel_title": post_data['channel_title'],
                "created_at": post_data['created_at'].isoformat(),
                "media_urls": post_data['media_urls'] or []
            }
            
            # Создание точки
            point = PointStruct(
                id=post_id,
                vector=embeddings,
                payload=payload
            )
            
            # Индексы по payload (для быстрого фильтра по tenant/channel)
            try:
                self.qdrant_client.create_payload_index(
                    collection_name=collection_name,
                    field_name="tenant_id",
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass
            try:
                self.qdrant_client.create_payload_index(
                    collection_name=collection_name,
                    field_name="channel_id",
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass

            # Сохранение в Qdrant
            self.qdrant_client.upsert(
                collection_name=collection_name,
                points=[point]
            )
            
            logger.info("Saved to Qdrant", 
                       collection=collection_name, 
                       post_id=post_id)
            
        except Exception as e:
            logger.error("Failed to save to Qdrant", error=str(e))
            raise
    
    async def _update_embedding_status(self, post_id: str, status: str, error_message: str = None):
        """Обновление статуса обработки embeddings."""
        try:
            with self.db_connection.cursor() as cursor:
                if status == 'completed':
                    cursor.execute("""
                        UPDATE indexing_status 
                        SET embedding_status = %s, processing_completed_at = %s
                        WHERE post_id = %s
                    """, (status, datetime.utcnow(), post_id))
                else:
                    cursor.execute("""
                        UPDATE indexing_status 
                        SET embedding_status = %s, error_message = %s, retry_count = retry_count + 1
                        WHERE post_id = %s
                    """, (status, error_message, post_id))
                
                self.db_connection.commit()
                
        except Exception as e:
            logger.error("Failed to update embedding status", error=str(e))
    
    async def close(self):
        """Закрытие соединений."""
        if self.db_connection:
            self.db_connection.close()
        logger.info("Embedding service closed")
