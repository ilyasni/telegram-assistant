#!/usr/bin/env python3
"""
Миграция channel_title в Neo4j для существующих узлов Post.

Context7 best practice: Batch processing с progress tracking и idempotency.

Запуск:
    python scripts/migrate_neo4j_channel_title.py [--dry-run] [--batch-size N] [--offset N]
"""

import argparse
import asyncio
import os
import sys
from typing import Dict, List, Optional, Any

import asyncpg
import structlog

# Добавляем пути для импорта
# В контейнере worker все модули в /app/
sys.path.insert(0, '/app')

from integrations.neo4j_client import Neo4jClient

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Конфигурация
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j123")


async def migrate_channel_titles(
    batch_size: int = 100,
    offset: int = 0,
    dry_run: bool = False
):
    """
    Миграция channel_title из PostgreSQL в Neo4j.
    
    Context7 best practice: Batch processing с progress tracking и idempotency.
    
    Args:
        batch_size: Размер батча для обработки
        offset: Смещение для начала обработки
        dry_run: Если True, только считаем статистику без изменений
    """
    # Context7: asyncpg pool для БД
    print(f"🚀 Starting migration: batch_size={batch_size}, offset={offset}, dry_run={dry_run}")
    logger.info("Creating database pool", batch_size=batch_size, offset=offset, dry_run=dry_run)
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60
    )
    
    # Context7: Neo4j async driver
    neo4j_client = Neo4jClient(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    if not dry_run:
        await neo4j_client.connect()
    
    total_processed = 0
    total_updated = 0
    total_failed = 0
    total_skipped = 0
    current_offset = offset
    
    try:
        async with pool.acquire() as conn:
            # Получаем общее количество постов с каналами для прогресса
            total_count = await conn.fetchval("""
                SELECT COUNT(DISTINCT p.id)
                FROM posts p
                JOIN channels c ON p.channel_id = c.id
                WHERE c.title IS NOT NULL AND c.title != ''
            """)
            
            print(f"📊 Total posts to process: {total_count}")
            logger.info("Total posts to process", total=total_count)
        
        # Batch processing
        while True:
            async with pool.acquire() as conn:
                # Получаем батч постов с channel_title
                rows = await conn.fetch("""
                    SELECT DISTINCT
                        p.id::text as post_id,
                        c.title as channel_title,
                        p.id as post_id_for_order
                    FROM posts p
                    JOIN channels c ON p.channel_id = c.id
                    WHERE c.title IS NOT NULL AND c.title != ''
                    ORDER BY post_id_for_order
                    LIMIT $1 OFFSET $2
                """, batch_size, current_offset)
                
                if not rows:
                    logger.info("No more posts to process", offset=current_offset)
                    break
                
                logger.info(
                    "Processing batch",
                    batch_size=len(rows),
                    offset=current_offset,
                    total_processed=total_processed
                )
                
                # Обрабатываем каждый пост
                for row in rows:
                    post_id = row['post_id']
                    channel_title = row['channel_title']
                    
                    try:
                        if dry_run:
                            logger.debug(
                                "Would update post",
                                post_id=post_id,
                                channel_title=channel_title
                            )
                            total_updated += 1
                        else:
                            # Обновляем узел в Neo4j
                            async with neo4j_client._driver.session() as session:
                                result = await session.run(
                                    """
                                    MATCH (p:Post {post_id: $post_id})
                                    WHERE p.channel_title IS NULL OR p.channel_title = ''
                                    SET p.channel_title = $channel_title
                                    RETURN p.post_id as post_id
                                    """,
                                    post_id=post_id,
                                    channel_title=channel_title
                                )
                                
                                records = await result.data()
                                if records and len(records) > 0:
                                    total_updated += 1
                                    logger.debug(
                                        "Updated post channel_title",
                                        post_id=post_id,
                                        channel_title=channel_title
                                    )
                                else:
                                    total_skipped += 1
                                    logger.debug(
                                        "Post not found or already has channel_title",
                                        post_id=post_id
                                    )
                        
                        total_processed += 1
                        
                    except Exception as e:
                        total_failed += 1
                        logger.error(
                            "Failed to update post",
                            post_id=post_id,
                            error=str(e),
                            error_type=type(e).__name__
                        )
                
                current_offset += len(rows)
                
                # Прогресс
                if total_count > 0:
                    progress = (total_processed / total_count) * 100
                    logger.info(
                        "Progress",
                        processed=total_processed,
                        total=total_count,
                        progress=f"{progress:.2f}%",
                        updated=total_updated,
                        skipped=total_skipped,
                        failed=total_failed
                    )
                
                # Если батч меньше batch_size, значит это последний батч
                if len(rows) < batch_size:
                    break
        
        print(f"\n✅ Migration completed:")
        print(f"   Processed: {total_processed}")
        print(f"   Updated: {total_updated}")
        print(f"   Skipped: {total_skipped}")
        print(f"   Failed: {total_failed}")
        print(f"   Dry run: {dry_run}")
        logger.info(
            "Migration completed",
            total_processed=total_processed,
            total_updated=total_updated,
            total_skipped=total_skipped,
            total_failed=total_failed,
            dry_run=dry_run
        )
        
    except Exception as e:
        logger.error("Migration failed", error=str(e), error_type=type(e).__name__)
        raise
    finally:
        await pool.close()
        if not dry_run:
            await neo4j_client.close()


async def main():
    """Главная функция."""
    parser = argparse.ArgumentParser(
        description="Миграция channel_title в Neo4j для существующих узлов Post"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать статистику без изменений"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Размер батча для обработки (по умолчанию: 100)"
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Смещение для начала обработки (по умолчанию: 0)"
    )
    
    args = parser.parse_args()
    
    logger.info(
        "Starting migration",
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        offset=args.offset
    )
    
    await migrate_channel_titles(
        batch_size=args.batch_size,
        offset=args.offset,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    asyncio.run(main())

