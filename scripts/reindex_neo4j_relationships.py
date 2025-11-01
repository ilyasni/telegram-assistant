#!/usr/bin/env python3
"""
Ретроактивная индексация связей TAGGED_AS и HAS_VISION в Neo4j.

Context7 best practice: Batch processing с progress tracking и idempotency.

Запуск:
    python scripts/reindex_neo4j_relationships.py [--dry-run] [--batch-size N] [--offset N]
"""

import argparse
import asyncio
import json
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


async def reindex_tags_to_neo4j(
    batch_size: int = 100,
    offset: int = 0,
    dry_run: bool = False
):
    """
    Ретроактивная индексация тегов в Neo4j.
    
    Context7 best practice: Batch processing с progress tracking и idempotency.
    
    Args:
        batch_size: Размер батча для обработки
        offset: Смещение для начала обработки
        dry_run: Если True, только считаем статистику без изменений
    """
    # Context7: asyncpg pool для БД
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
    total_created = 0
    total_failed = 0
    current_offset = offset
    
    try:
        async with pool.acquire() as conn:
            # Получаем общее количество enrichments для прогресса
            total_count = await conn.fetchval("""
                SELECT COUNT(*) FROM post_enrichment pe
                WHERE pe.kind = 'tags' AND pe.status = 'ok'
                AND jsonb_typeof(pe.data) = 'object'
                AND pe.data ? 'tags'
                AND jsonb_array_length(pe.data->'tags') > 0
            """)
            
            logger.info("Total enrichments to process", total=total_count)
        
        # Batch processing
        while True:
            async with pool.acquire() as conn:
                # Context7: Batch query с проверкой на непустые теги
                rows = await conn.fetch("""
                    SELECT 
                        pe.post_id, 
                        pe.data->'tags' as tags_jsonb,
                        p.channel_id
                    FROM post_enrichment pe
                    LEFT JOIN posts p ON p.id = pe.post_id
                    WHERE pe.kind = 'tags' AND pe.status = 'ok'
                    AND jsonb_typeof(pe.data) = 'object'
                    AND pe.data ? 'tags'
                    AND jsonb_array_length(pe.data->'tags') > 0
                    ORDER BY pe.post_id
                    LIMIT $1 OFFSET $2
                """, batch_size, current_offset)
            
            if not rows:
                logger.info("No more enrichments to process")
                break
            
            logger.info("Processing batch",
                       offset=current_offset,
                       batch_size=len(rows))
            
            batch_created = 0
            batch_failed = 0
            
            for row in rows:
                try:
                    post_id = str(row['post_id'])
                    channel_id = str(row['channel_id']) if row['channel_id'] else None
                    tags_jsonb = row['tags_jsonb']
                    
                    # Проверка типа тегов
                    if not tags_jsonb:
                        logger.debug("Skipping post with null tags", post_id=post_id)
                        total_processed += 1
                        continue
                    
                    # Декодирование JSONB
                    if isinstance(tags_jsonb, (list, dict)):
                        tags = tags_jsonb
                    else:
                        tags = json.loads(tags_jsonb)
                    
                    if not isinstance(tags, list) or not tags:
                        logger.debug("Skipping post with invalid tags",
                                   post_id=post_id,
                                   tags_type=type(tags).__name__)
                        total_processed += 1
                        continue
                    
                    logger.debug("Processing post",
                               post_id=post_id,
                               tags_count=len(tags),
                               tags_sample=tags[:3])
                    
                    if dry_run:
                        logger.info("DRY RUN: Would create relationships",
                                  post_id=post_id,
                                  tags_count=len(tags))
                        batch_created += 1
                        total_processed += 1
                        continue
                    
                    # Context7: Проверка существования Post node в Neo4j
                    post_exists = await check_post_exists_in_neo4j(neo4j_client, post_id)
                    
                    if not post_exists:
                        logger.warning("Post node not found in Neo4j, skipping",
                                     post_id=post_id,
                                     channel_id=channel_id)
                        total_processed += 1
                        continue
                    
                    # Context7: Создание связей
                    tags_dicts = [
                        {'name': tag, 'category': 'general', 'confidence': 1.0}
                        for tag in tags if isinstance(tag, str) and tag.strip()
                    ]
                    
                    if not tags_dicts:
                        logger.debug("No valid tags to create", post_id=post_id)
                        total_processed += 1
                        continue
                    
                    success = await neo4j_client.create_tag_relationships(post_id, tags_dicts)
                    
                    if success:
                        batch_created += 1
                        total_created += 1
                        logger.info("Relationships created",
                                  post_id=post_id,
                                  tags_count=len(tags_dicts))
                    else:
                        batch_failed += 1
                        total_failed += 1
                        logger.error("Failed to create relationships", post_id=post_id)
                    
                    total_processed += 1
                
                except Exception as e:
                    batch_failed += 1
                    total_failed += 1
                    logger.error("Error processing post",
                               post_id=row.get('post_id'),
                               error=str(e),
                               exc_info=True)
                    total_processed += 1
            
            logger.info("Batch completed",
                       offset=current_offset,
                       created=batch_created,
                       failed=batch_failed,
                       total_processed=total_processed,
                       total_created=total_created,
                       total_failed=total_failed)
            
            current_offset += len(rows)
            
            # Небольшая пауза между батчами
            await asyncio.sleep(0.5)
    
    finally:
        await pool.close()
        if not dry_run:
            await neo4j_client.close()
    
    logger.info("Reindex completed",
               total_processed=total_processed,
               total_created=total_created,
               total_failed=total_failed)
    
    return {
        'processed': total_processed,
        'created': total_created,
        'failed': total_failed
    }


# Добавляем метод проверки существования Post node в Neo4jClient
async def check_post_exists_in_neo4j(neo4j_client: Neo4jClient, post_id: str) -> bool:
    """Проверка существования Post node в Neo4j."""
    try:
        async with neo4j_client._driver.session() as session:
            result = await session.run(
                "MATCH (p:Post {post_id: $post_id}) RETURN p.post_id as post_id",
                post_id=post_id
            )
            record = await result.single()
            return record is not None
    except Exception as e:
        logger.error("Error checking post existence", post_id=post_id, error=str(e))
        return False


def parse_args():
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(description="Ретроактивная индексация Neo4j relationships")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только статистика без изменений"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Размер батча (по умолчанию 100)"
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Начальное смещение (по умолчанию 0)"
    )
    return parser.parse_args()


async def main():
    """Главная функция."""
    args = parse_args()
    
    try:
        results = await reindex_tags_to_neo4j(
            batch_size=args.batch_size,
            offset=args.offset,
            dry_run=args.dry_run
        )
        
        print("\n" + "="*80)
        print("REINDEX RESULTS")
        print("="*80)
        print(f"Processed: {results['processed']}")
        print(f"Created: {results['created']}")
        print(f"Failed: {results['failed']}")
        if args.dry_run:
            print("\n(Dry run mode - no changes made)")
        print("="*80)
        
        sys.exit(0 if results['failed'] == 0 else 1)
    
    except Exception as e:
        logger.error("Reindex failed", error=str(e), exc_info=True)
        print(f"❌ Reindex failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

