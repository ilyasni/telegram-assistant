"""Задачи для создания embeddings."""

import asyncio
import structlog
from typing import List, Dict, Any, Optional
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

            if not self.qdrant_client:
                self.qdrant_client = QdrantClient(url=settings.qdrant_url, timeout=10.0)
                logger.info("EmbeddingService connected to Qdrant", url=settings.qdrant_url)

            if not self.db_connection:
                self.db_connection = psycopg2.connect(settings.database_url)
                self.db_connection.autocommit = False
                logger.info("EmbeddingService connected to Postgres")

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

    async def process_group_message_embeddings(self, group_message_id: str, tenant_id: str):
        """Обработка embeddings для сообщения группы."""
        try:
            group_data = await self._get_group_message_data(group_message_id, tenant_id)
            if not group_data:
                logger.warning(
                    "Group message not found",
                    group_message_id=group_message_id,
                    tenant_id=tenant_id,
                )
                return

            embeddings = await self._create_embeddings(group_data['content'])
            await self._save_group_to_qdrant(group_message_id, tenant_id, group_data, embeddings)
            await self._update_group_embedding_status(group_message_id, embeddings)

            logger.info(
                "Group message embeddings processed",
                group_message_id=group_message_id,
                tenant_id=tenant_id,
                embedding_dim=len(embeddings),
            )
        except Exception as e:
            logger.error(
                "Failed to process group message embeddings",
                group_message_id=group_message_id,
                tenant_id=tenant_id,
                error=str(e),
            )
            await self._update_group_embedding_status(group_message_id, None, error=str(e))
    
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
    
    async def _get_group_message_data(self, group_message_id: str, tenant_id: str) -> Dict[str, Any]:
        """Получение данных сообщения группы."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        gm.id,
                        gm.group_id,
                        gm.tenant_id,
                        gm.tg_message_id,
                        gm.content,
                        gm.media_urls,
                        gm.posted_at,
                        gm.sender_tg_id,
                        gm.sender_username,
                        g.title AS group_title,
                        g.username AS group_username,
                        g.tg_chat_id,
                        g.settings
                    FROM group_messages gm
                    JOIN groups g ON gm.group_id = g.id
                    WHERE gm.id = %s AND gm.tenant_id = %s
                    """,
                    (group_message_id, tenant_id),
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except Exception as e:
            logger.error("Failed to get group message data", error=str(e))
            return None

    async def _save_group_to_qdrant(
        self,
        group_message_id: str,
        tenant_id: str,
        group_data: Dict[str, Any],
        embeddings: List[float],
    ):
        """Сохранение embeddings сообщения группы в Qdrant."""
        try:
            collection_name = f"t{tenant_id}_groups"

            collections = self.qdrant_client.get_collections()
            if collection_name not in [c.name for c in collections.collections]:
                self.qdrant_client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=settings.EMBED_DIM,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created group collection", collection=collection_name)

            payload = {
                "group_message_id": group_message_id,
                "tenant_id": tenant_id,
                "group_id": str(group_data["group_id"]),
                "tg_message_id": group_data["tg_message_id"],
                "content": group_data["content"],
                "sender_tg_id": group_data.get("sender_tg_id"),
                "sender_username": group_data.get("sender_username"),
                "posted_at": group_data.get("posted_at"),
                "group_title": group_data.get("group_title"),
                "group_username": group_data.get("group_username"),
                "tg_chat_id": group_data.get("tg_chat_id"),
                "media_urls": group_data.get("media_urls") or [],
            }

            point = PointStruct(
                id=group_message_id,
                vector=embeddings,
                payload=payload,
            )

            for field_name in ("tenant_id", "group_id", "sender_tg_id"):
                try:
                    self.qdrant_client.create_payload_index(
                        collection_name=collection_name,
                        field_name=field_name,
                        field_schema=PayloadSchemaType.KEYWORD,
                    )
                except Exception:
                    pass

            self.qdrant_client.upsert(
                collection_name=collection_name,
                points=[point],
            )
        except Exception as e:
            logger.error("Failed to save group embeddings to Qdrant", error=str(e))
            raise

    async def _update_group_embedding_status(self, group_message_id: str, embeddings: Optional[List[float]], error: str = None):
        """Обновление статуса обработки embeddings для группового сообщения."""
        try:
            with self.db_connection.cursor() as cursor:
                if embeddings is not None:
                    cursor.execute(
                        """
                        UPDATE group_message_analytics
                        SET embeddings = %s::jsonb,
                            analysed_at = %s,
                            metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                        WHERE message_id = %s
                        """,
                        (
                            json.dumps(embeddings),
                            datetime.utcnow(),
                            json.dumps({"embedding_status": "completed"}),
                            group_message_id,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE group_message_analytics
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                        WHERE message_id = %s
                        """,
                        (
                            json.dumps({"embedding_status": "failed", "error": error}),
                            group_message_id,
                        ),
                    )

                self.db_connection.commit()
        except Exception as e:
            logger.error("Failed to update group embedding status", error=str(e))
    
    async def close(self):
        """Закрытие соединений."""
        if self.db_connection:
            self.db_connection.close()
        logger.info("Embedding service closed")
