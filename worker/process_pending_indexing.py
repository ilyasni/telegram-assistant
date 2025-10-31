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


async def get_pending_posts(db_url: str, limit: Optional[int] = None, include_failed: bool = True) -> List[str]:
    """
    Получение post_id постов со статусом pending или failed с retryable ошибками.
    
    Context7: [C7-ID: retry-failed-001] - автоматический ретрай failed постов с retryable ошибками
    Supabase best practice: параметризованный SQL запрос.
    """
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    
    # Context7: Ищем pending посты И failed посты с retryable ошибками
    query = """
        SELECT p.id, is_.embedding_status, is_.graph_status, is_.error_message
        FROM posts p
        INNER JOIN indexing_status is_ ON p.id = is_.post_id
        WHERE (
            is_.embedding_status = 'pending' OR is_.graph_status = 'pending'
        )
    """
    
    if include_failed:
        # Context7: Добавляем failed посты с retryable ошибками
        query += """
            OR (
                (is_.embedding_status = 'failed' OR is_.graph_status = 'failed')
                AND is_.error_message IS NOT NULL
                AND (
                    is_.error_message ILIKE '%retryable_network%'
                    OR is_.error_message ILIKE '%retryable_rate_limit%'
                    OR is_.error_message ILIKE '%retryable_server_error%'
                    OR is_.error_message ILIKE '%connection refused%'
                    OR is_.error_message ILIKE '%connection error%'
                    OR is_.error_message ILIKE '%timeout%'
                    OR is_.error_message ILIKE '%max retries exceeded%'
                )
            )
        """
    
    query += """
        ORDER BY 
            CASE 
                WHEN is_.embedding_status = 'pending' OR is_.graph_status = 'pending' THEN 1
                ELSE 2
            END,
            p.created_at DESC
    """
    
    if limit:
        query += f" LIMIT {limit}"
    
    cursor.execute(query)
    rows = cursor.fetchall()
    
    post_ids = [row[0] for row in rows]
    
    # Логирование статистики
    pending_count = sum(1 for r in rows if r[1] == 'pending' or r[2] == 'pending')
    failed_count = len(rows) - pending_count
    
    logger.info("Found posts to retry",
                total=len(post_ids),
                pending=pending_count,
                failed_retryable=failed_count)
    
    cursor.close()
    conn.close()
    
    return post_ids


async def process_pending_posts(limit: Optional[int] = None, include_failed: bool = True):
    """
    Обработка pending постов и failed постов с retryable ошибками через IndexingTask._process_single_message.
    
    Context7: [C7-ID: retry-failed-002] - автоматический ретрай failed постов
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
    
    # Получение постов со статусом pending и failed с retryable ошибками
    logger.info("Fetching posts to process from database...", include_failed=include_failed)
    post_ids = await get_pending_posts(db_url, limit=limit, include_failed=include_failed)
    
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
        # QdrantClient не имеет метода close(), соединение управляется через httpx
        # if hasattr(task, 'qdrant_client') and task.qdrant_client:
        #     await task.qdrant_client.close()
        if hasattr(task, 'neo4j_client') and task.neo4j_client:
            await task.neo4j_client.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Process pending and failed (retryable) posts from indexing_status',
        epilog='Context7: [C7-ID: retry-failed-002] - автоматический ретрай failed постов с retryable ошибками'
    )
    parser.add_argument('--limit', type=int, default=None, 
                       help='Limit number of posts to process (default: all)')
    parser.add_argument('--skip-failed', action='store_true',
                       help='Skip failed posts, process only pending posts')
    
    args = parser.parse_args()
    
    asyncio.run(process_pending_posts(limit=args.limit, include_failed=not args.skip_failed))

