#!/usr/bin/env python3
"""
Скрипт для прямой обработки старых постов со статусом pending.

Context7 best practice: batch processing с проверкой идемпотентности.
Supabase best practice: параметризованные SQL запросы.

Использование:
    python3 -m scripts.process_pending_posts [--limit N] [--dry-run]
"""

import asyncio
import os
import sys
import argparse
import structlog
import psycopg2
from typing import List, Dict, Any, Optional
from datetime import datetime

# Добавляем путь к корню проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_bus import RedisStreamsClient, EventPublisher, PostEnrichedEvent
from integrations.qdrant_client import QdrantClient
from integrations.neo4j_client import Neo4jClient
from ai_providers.gigachain_adapter import create_gigachain_adapter
from ai_providers.embedding_service import create_embedding_service

logger = structlog.get_logger()


async def get_pending_posts(
    db_url: str, 
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Получение постов со статусом pending из БД.
    
    Supabase best practice: параметризованный SQL запрос.
    """
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    
    query = """
        SELECT 
            p.id,
            p.channel_id,
            p.content,
            p.telegram_message_id,
            p.created_at,
            is_.embedding_status,
            is_.graph_status
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
    
    posts = []
    for row in rows:
        posts.append({
            'id': row[0],
            'channel_id': row[1],
            'content': row[2],
            'telegram_message_id': row[3],
            'created_at': row[4],
            'embedding_status': row[5],
            'graph_status': row[6]
        })
    
    cursor.close()
    conn.close()
    
    return posts


async def publish_enriched_event(
    publisher: EventPublisher,
    post_data: Dict[str, Any]
) -> bool:
    """Публикация события posts.enriched для поста.
    
    Context7 best practice: использование унифицированного payload.
    """
    try:
        # Создаем событие PostEnrichedEvent
        event = PostEnrichedEvent(
            post_id=post_data['id'],
            channel_id=post_data['channel_id'],
            text=post_data['content'] or '',
            telegram_post_url=None,  # Можно вычислить из channel_id и telegram_message_id
            posted_at=post_data['created_at'].isoformat() if post_data['created_at'] else None,
            enrichment_data={
                'kind': 'enrichment',
                'source': 'backlog_processor',
                'version': 'v1',
                'tags': [],
                'entities': [],
                'urls': []
            }
        )
        
        await publisher.publish("posts.enriched", event)
        return True
        
    except Exception as e:
        logger.error(f"Failed to publish event for post {post_data['id']}", 
                    error=str(e), 
                    post_id=post_data['id'])
        return False


async def process_posts_directly(
    posts: List[Dict[str, Any]],
    redis_url: str,
    qdrant_url: str,
    neo4j_url: str,
    dry_run: bool = False
) -> Dict[str, int]:
    """Прямая обработка постов через IndexingTask логику.
    
    Context7 best practice: повторное использование кода из IndexingTask.
    """
    from tasks.indexing_task import IndexingTask
    
    stats = {
        'processed': 0,
        'failed': 0,
        'skipped': 0
    }
    
    # Инициализация компонентов
    if not dry_run:
        redis_client = RedisStreamsClient(redis_url)
        await redis_client.connect()
        
        qdrant_client = QdrantClient(qdrant_url)
        await qdrant_client.connect()
        
        from config import settings
        neo4j_client = Neo4jClient(
            uri=neo4j_url,
            username=os.getenv("NEO4J_USER", settings.neo4j_username),
            password=os.getenv("NEO4J_PASSWORD", settings.neo4j_password)
        )
        await neo4j_client.connect()
        
        ai_adapter = await create_gigachain_adapter()
        embedding_service = await create_embedding_service(ai_adapter)
    else:
        redis_client = None
        qdrant_client = None
        neo4j_client = None
        embedding_service = None
    
    try:
        for post_data in posts:
            post_id = post_data['id']
            
            # Проверка идемпотентности
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
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
                stats['skipped'] += 1
                continue
            
            if dry_run:
                logger.info(f"[DRY RUN] Would process post", post_id=post_id, content=post_data['content'][:100])
                stats['processed'] += 1
                continue
            
            try:
                # Создаем временный экземпляр IndexingTask для обработки
                # Используем метод _process_single_message напрямую
                task = IndexingTask(redis_url, qdrant_url, neo4j_url)
                await task.start()
                
                # Формируем сообщение в формате, ожидаемом _process_single_message
                message = {
                    'payload': {
                        'post_id': post_id,
                        'channel_id': post_data['channel_id'],
                        'text': post_data['content'],
                        'telegram_message_id': post_data['telegram_message_id'],
                        'created_at': post_data['created_at'].isoformat() if post_data['created_at'] else None
                    }
                }
                
                await task._process_single_message(message)
                stats['processed'] += 1
                
                if stats['processed'] % 10 == 0:
                    logger.info(f"Progress: {stats['processed']} processed, {stats['failed']} failed")
                    
            except Exception as e:
                logger.error(f"Failed to process post {post_id}", 
                           error=str(e), 
                           post_id=post_id)
                stats['failed'] += 1
                continue
    finally:
        if not dry_run:
            if redis_client:
                await redis_client.disconnect()
            if qdrant_client:
                await qdrant_client.close()
            if neo4j_client:
                await neo4j_client.close()
    
    return stats


async def main():
    parser = argparse.ArgumentParser(description='Process pending posts from indexing_status')
    parser.add_argument('--limit', type=int, default=None, 
                       help='Limit number of posts to process (default: all)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be processed without actually processing')
    parser.add_argument('--method', choices=['event', 'direct'], default='direct',
                       help='Processing method: event (publish to stream) or direct (process immediately)')
    
    args = parser.parse_args()
    
    # Параметры подключения
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    neo4j_url = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
    
    logger.info("Starting pending posts processing", 
               limit=args.limit, 
               dry_run=args.dry_run, 
               method=args.method)
    
    # Получение постов со статусом pending
    posts = await get_pending_posts(db_url, limit=args.limit)
    
    if not posts:
        logger.info("No pending posts found")
        return
    
    logger.info(f"Found {len(posts)} pending posts to process")
    
    if args.method == 'event':
        # Метод 1: Публикация событий в stream
        redis_client = RedisStreamsClient(redis_url)
        await redis_client.connect()
        publisher = EventPublisher(redis_client)
        
        published = 0
        failed = 0
        
        for post_data in posts:
            if not args.dry_run:
                success = await publish_enriched_event(publisher, post_data)
                if success:
                    published += 1
                else:
                    failed += 1
            else:
                logger.info(f"[DRY RUN] Would publish event for post", post_id=post_data['id'])
                published += 1
            
            if (published + failed) % 50 == 0:
                logger.info(f"Progress: {published} published, {failed} failed")
        
        await redis_client.disconnect()
        
        logger.info(f"Completed: {published} events published, {failed} failed")
        
    else:
        # Метод 2: Прямая обработка
        stats = await process_posts_directly(
            posts, 
            redis_url, 
            qdrant_url, 
            neo4j_url,
            dry_run=args.dry_run
        )
        
        logger.info(f"Completed: {stats['processed']} processed, {stats['failed']} failed, {stats['skipped']} skipped")


if __name__ == '__main__':
    asyncio.run(main())

