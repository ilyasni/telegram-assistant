#!/usr/bin/env python3
"""
Скрипт для прямой обработки старых постов со статусом pending через IndexingTask.

Context7 best practice: повторное использование кода из IndexingTask.
Supabase best practice: параметризованные SQL запросы.

Использование:
    docker compose exec worker python3 process_pending_indexing.py [--limit N]
"""

import asyncio
import os
import sys
import structlog
import psycopg2
from typing import List, Dict, Any, Optional

logger = structlog.get_logger()


async def get_pending_posts(db_url: str, limit: Optional[int] = None) -> List[str]:
    """Получение post_id постов со статусом pending.
    
    Supabase best practice: параметризованный SQL запрос.
    """
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    
    query = """
        SELECT p.id
        FROM posts p
        INNER JOIN indexing_status is_ ON p.id = is_.post_id
        WHERE is_.embedding_status = 'pending' 
           OR is_.graph_status = 'pending'
        ORDER BY p.created_at DESC
    """
    
    if limit:
        query += f" LIMIT {limit}"
    
    cursor.execute(query)
    rows = cursor.fetchall()
    
    post_ids = [row[0] for row in rows]
    
    cursor.close()
    conn.close()
    
    return post_ids


async def process_pending_posts(limit: Optional[int] = None):
    """Обработка pending постов через IndexingTask._process_single_message.
    
    Context7 best practice: повторное использование кода из IndexingTask.
    """
    from tasks.indexing_task import IndexingTask
    from event_bus import RedisStreamsClient, EventPublisher
    from integrations.qdrant_client import QdrantClient
    from integrations.neo4j_client import Neo4jClient
    
    # Параметры подключения
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    neo4j_url = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
    
    # Получение постов со статусом pending
    logger.info("Fetching pending posts from database...")
    post_ids = await get_pending_posts(db_url, limit=limit)
    
    if not post_ids:
        logger.info("No pending posts found")
        return
    
    logger.info(f"Found {len(post_ids)} pending posts to process")
    
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
        
        # Обработка каждого поста
        processed = 0
        failed = 0
        skipped = 0
        
        for post_id in post_ids:
            try:
                # Проверка идемпотентности
                conn = psycopg2.connect(db_url)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT embedding_status, graph_status FROM indexing_status WHERE post_id = %s",
                    (post_id,)
                )
                row = cursor.fetchone()
                cursor.close()
                conn.close()
                
                if row and row[0] in ('completed', 'processing') and row[1] in ('completed', 'processing'):
                    logger.debug(f"Skipping already processed post", post_id=post_id)
                    skipped += 1
                    continue
                
                # Формируем сообщение в формате, ожидаемом _process_single_message
                # Используем формат, который приходит из posts.enriched stream
                message = {
                    'payload': {
                        'post_id': post_id
                    }
                }
                
                # Обработка поста
                await task._process_single_message(message)
                processed += 1
                
                if processed % 10 == 0:
                    logger.info(f"Progress: {processed} processed, {failed} failed, {skipped} skipped")
                    
            except Exception as e:
                logger.error(f"Failed to process post {post_id}", 
                           error=str(e), 
                           post_id=post_id,
                           error_type=type(e).__name__)
                failed += 1
                continue
        
        logger.info(f"Completed: {processed} processed, {failed} failed, {skipped} skipped")
        
    finally:
        # Закрытие соединений
        if hasattr(task, 'redis_client') and task.redis_client:
            await task.redis_client.disconnect()
        if hasattr(task, 'qdrant_client') and task.qdrant_client:
            await task.qdrant_client.close()
        if hasattr(task, 'neo4j_client') and task.neo4j_client:
            await task.neo4j_client.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Process pending posts from indexing_status')
    parser.add_argument('--limit', type=int, default=None, 
                       help='Limit number of posts to process (default: all)')
    
    args = parser.parse_args()
    
    asyncio.run(process_pending_posts(limit=args.limit))

