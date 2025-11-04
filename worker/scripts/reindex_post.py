#!/usr/bin/env python3
"""
Скрипт для переиндексации конкретного поста.

Использование:
    docker compose exec worker python worker/scripts/reindex_post.py <post_id>
"""

import asyncio
import os
import sys
import structlog
from typing import Dict, Any

# Добавляем пути для импортов
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tasks.indexing_task import IndexingTask
from event_bus import RedisStreamsClient, EventPublisher
from integrations.qdrant_client import QdrantClient
from integrations.neo4j_client import Neo4jClient

logger = structlog.get_logger()


async def reindex_post(post_id: str):
    """Переиндексация конкретного поста."""
    # Параметры подключения
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    neo4j_url = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
    
    logger.info("Starting reindexing", post_id=post_id)
    
    # Создание и инициализация IndexingTask
    task = IndexingTask(redis_url, qdrant_url, neo4j_url)
    
    try:
        # Инициализация компонентов
        task.redis_client = RedisStreamsClient(redis_url)
        await task.redis_client.connect()
        
        task.qdrant_client = QdrantClient(qdrant_url)
        await task.qdrant_client.connect()
        
        from config import settings
        task.neo4j_client = Neo4jClient(
            uri=neo4j_url,
            username=os.getenv("NEO4J_USER", settings.neo4j_username),
            password=os.getenv("NEO4J_PASSWORD", settings.neo4j_password)
        )
        await task.neo4j_client.connect()
        
        # Инициализация EmbeddingService
        from ai_providers.gigachain_adapter import create_gigachain_adapter
        from ai_providers.embedding_service import create_embedding_service
        ai_adapter = await create_gigachain_adapter()
        task.embedding_service = await create_embedding_service(ai_adapter)
        
        # Инициализация Publisher
        task.publisher = EventPublisher(task.redis_client)
        
        # Формируем сообщение в формате, ожидаемом _process_single_message
        message = {
            'payload': {
                'post_id': post_id
            }
        }
        
        # Обработка поста
        await task._process_single_message(message)
        
        logger.info("Reindexing completed successfully", post_id=post_id)
        
    except Exception as e:
        logger.error("Reindexing failed", 
                   post_id=post_id,
                   error=str(e),
                   error_type=type(e).__name__)
        raise
    finally:
        # Закрытие соединений
        if hasattr(task, 'redis_client') and task.redis_client:
            await task.redis_client.disconnect()
        if hasattr(task, 'neo4j_client') and task.neo4j_client:
            await task.neo4j_client.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python reindex_post.py <post_id>", file=sys.stderr)
        sys.exit(1)
    
    post_id = sys.argv[1]
    asyncio.run(reindex_post(post_id))

