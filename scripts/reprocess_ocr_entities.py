#!/usr/bin/env python3
"""
Скрипт для переобработки старых постов с OCR для извлечения entities.

Context7: Переобрабатывает посты с новым промптом entity extraction,
обновляет entities в БД и переиндексирует в Neo4j.

Использование:
    python3 scripts/reprocess_ocr_entities.py --days 30 --limit 100
    python3 scripts/reprocess_ocr_entities.py --post-id <post_id>
"""

import asyncio
import argparse
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api', 'worker'))

import asyncpg
import structlog
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

from services.ocr_enhancement_service import OCREnhancementService
from integrations.neo4j_client import Neo4jClient
import redis.asyncio as redis

logger = structlog.get_logger()

# ============================================================================
# REPROCESSING LOGIC
# ============================================================================

async def find_posts_without_entities(
    db_pool: asyncpg.Pool,
    days: int = 30,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Найти посты с OCR без entities за последние N дней.
    
    Args:
        db_pool: Пул подключений к БД
        days: Количество дней для поиска
        limit: Максимальное количество постов
    
    Returns:
        Список постов с OCR данными
    """
    try:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        query = """
            SELECT 
                post_id,
                data->'ocr'->>'text' as ocr_text,
                data->'ocr'->>'text_enhanced' as ocr_text_enhanced,
                data->'ocr'->'entities' as entities_existing,
                jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) as entities_count,
                created_at
            FROM post_enrichment
            WHERE kind = 'vision'
              AND data->'ocr'->>'text' IS NOT NULL
              AND data->'ocr'->>'text' != ''
              AND (
                  data->'ocr'->'entities' IS NULL
                  OR jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) = 0
              )
              AND created_at >= $1
            ORDER BY created_at DESC
            LIMIT $2
        """
        
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, cutoff_date, limit)
        
        posts = []
        for row in rows:
            posts.append({
                'post_id': str(row['post_id']),
                'ocr_text': row['ocr_text'],
                'ocr_text_enhanced': row['ocr_text_enhanced'],
                'entities_existing': row['entities_existing'],
                'entities_count': row['entities_count'] or 0,
                'created_at': row['created_at']
            })
        
        logger.info(
            "Found posts without entities",
            count=len(posts),
            days=days
        )
        
        return posts
        
    except Exception as e:
        logger.error("Failed to find posts without entities", error=str(e))
        return []


async def reprocess_post_entities(
    post_id: str,
    ocr_data: Dict[str, Any],
    ocr_enhancement_service: OCREnhancementService
) -> Optional[List[Dict[str, Any]]]:
    """
    Переобработать entities для поста.
    
    Args:
        post_id: ID поста
        ocr_data: OCR данные (с text или text_enhanced)
        ocr_enhancement_service: Сервис для обработки OCR
    
    Returns:
        Список извлеченных entities или None при ошибке
    """
    try:
        # Используем text_enhanced если доступен, иначе text
        text_for_extraction = ocr_data.get('ocr_text_enhanced') or ocr_data.get('ocr_text', '')
        
        if not text_for_extraction or not text_for_extraction.strip():
            logger.warning("No OCR text for extraction", post_id=post_id)
            return []
        
        # Извлекаем entities
        entities = await ocr_enhancement_service.extract_entities(text_for_extraction)
        
        logger.info(
            "Entities extracted",
            post_id=post_id,
            entities_count=len(entities)
        )
        
        return entities
        
    except Exception as e:
        logger.error(
            "Failed to reprocess entities",
            post_id=post_id,
            error=str(e)
        )
        return None


async def update_entities_in_db(
    post_id: str,
    entities: List[Dict[str, Any]],
    db_pool: asyncpg.Pool
) -> bool:
    """
    Обновить entities в post_enrichment.
    
    Args:
        post_id: ID поста
        entities: Список извлеченных entities
        db_pool: Пул подключений к БД
    
    Returns:
        True если успешно обновлено
    """
    try:
        query = """
            UPDATE post_enrichment
            SET data = jsonb_set(
                COALESCE(data, '{}'::jsonb),
                '{ocr,entities}',
                $1::jsonb,
                true
            ),
            updated_at = NOW()
            WHERE post_id = $2::uuid
              AND kind = 'vision'
            RETURNING post_id
        """
        
        entities_json = json.dumps(entities, ensure_ascii=False)
        
        async with db_pool.acquire() as conn:
            result = await conn.fetchrow(query, entities_json, post_id)
        
        if result:
            logger.info(
                "Entities updated in DB",
                post_id=post_id,
                entities_count=len(entities)
            )
            return True
        else:
            logger.warning("Post not found for update", post_id=post_id)
            return False
            
    except Exception as e:
        logger.error(
            "Failed to update entities in DB",
            post_id=post_id,
            error=str(e)
        )
        return False


async def reindex_to_neo4j(
    post_id: str,
    entities: List[Dict[str, Any]],
    neo4j_client: Neo4jClient
) -> bool:
    """
    Переиндексировать entities в Neo4j.
    
    Args:
        post_id: ID поста
        entities: Список entities для записи
        neo4j_client: Neo4j клиент
    
    Returns:
        True если успешно переиндексировано
    """
    try:
        if not entities:
            logger.debug("No entities to reindex", post_id=post_id)
            return True
        
        # Создаем OCR entities в Neo4j
        success = await neo4j_client.create_ocr_entities(
            post_id=post_id,
            entities=entities,
            source='ocr'
        )
        
        if success:
            logger.info(
                "Entities reindexed to Neo4j",
                post_id=post_id,
                entities_count=len(entities)
            )
        else:
            logger.warning("Failed to reindex entities to Neo4j", post_id=post_id)
        
        return success
        
    except Exception as e:
        logger.error(
            "Failed to reindex to Neo4j",
            post_id=post_id,
            error=str(e)
        )
        return False


async def reprocess_posts(
    db_pool: asyncpg.Pool,
    redis_client: redis.Redis,
    neo4j_client: Neo4jClient,
    ocr_enhancement_service: OCREnhancementService,
    posts: List[Dict[str, Any]],
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Переобработать список постов.
    
    Args:
        db_pool: Пул подключений к БД
        redis_client: Redis клиент
        neo4j_client: Neo4j клиент
        ocr_enhancement_service: OCR Enhancement сервис
        posts: Список постов для обработки
        dry_run: Если True, только проверка без изменений
    
    Returns:
        Статистика обработки
    """
    stats = {
        'total': len(posts),
        'processed': 0,
        'updated_db': 0,
        'reindexed_neo4j': 0,
        'errors': 0
    }
    
    for post in posts:
        post_id = post['post_id']
        
        try:
            logger.info("Processing post", post_id=post_id)
            
            # Извлекаем entities
            entities = await reprocess_post_entities(
                post_id=post_id,
                ocr_data=post,
                ocr_enhancement_service=ocr_enhancement_service
            )
            
            if entities is None:
                stats['errors'] += 1
                continue
            
            stats['processed'] += 1
            
            if not dry_run:
                # Обновляем в БД
                if await update_entities_in_db(post_id, entities, db_pool):
                    stats['updated_db'] += 1
                
                # Переиндексируем в Neo4j
                if await reindex_to_neo4j(post_id, entities, neo4j_client):
                    stats['reindexed_neo4j'] += 1
            else:
                logger.info(
                    "DRY RUN: Would update",
                    post_id=post_id,
                    entities_count=len(entities)
                )
        
        except Exception as e:
            logger.error(
                "Failed to process post",
                post_id=post_id,
                error=str(e)
            )
            stats['errors'] += 1
    
    return stats


async def main():
    """Главная функция скрипта."""
    parser = argparse.ArgumentParser(description='Переобработка OCR entities для старых постов')
    parser.add_argument('--days', type=int, default=30, help='Количество дней для поиска (по умолчанию: 30)')
    parser.add_argument('--limit', type=int, default=100, help='Максимальное количество постов (по умолчанию: 100)')
    parser.add_argument('--post-id', type=str, help='Обработать конкретный пост по ID')
    parser.add_argument('--dry-run', action='store_true', help='Только проверка без изменений')
    
    args = parser.parse_args()
    
    print("="*70)
    print("🔄 Переобработка OCR entities для старых постов")
    print("="*70)
    print(f"   Days: {args.days}")
    print(f"   Limit: {args.limit}")
    print(f"   Dry run: {args.dry_run}")
    if args.post_id:
        print(f"   Post ID: {args.post_id}")
    print("="*70)
    
    # Инициализация подключений
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:54322/postgres")
    if db_url.startswith("postgresql://"):
        # Конвертируем в asyncpg DSN
        dsn = db_url.replace("postgresql://", "postgresql://")
    else:
        dsn = db_url
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    
    neo4j_uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL") or "neo4j://neo4j:7687"
    neo4j_username = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "neo4j123")
    
    try:
        # Создание пулов подключений
        db_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=5)
        redis_client = redis.from_url(redis_url, decode_responses=True)
        neo4j_client = Neo4jClient(uri=neo4j_uri, username=neo4j_username, password=neo4j_password)
        await neo4j_client.connect()
        
        # Инициализация OCR Enhancement Service
        ocr_enhancement_service = OCREnhancementService(
            redis_client=redis_client,
            enabled=True,
            entity_extraction_enabled=True
        )
        
        # Поиск постов для обработки
        if args.post_id:
            # Обработка конкретного поста
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT 
                        post_id,
                        data->'ocr'->>'text' as ocr_text,
                        data->'ocr'->>'text_enhanced' as ocr_text_enhanced,
                        data->'ocr'->'entities' as entities_existing
                    FROM post_enrichment
                    WHERE post_id = $1::uuid AND kind = 'vision'
                """, args.post_id)
            
            if not row:
                print(f"❌ Пост {args.post_id} не найден")
                return
            
            posts = [{
                'post_id': str(row['post_id']),
                'ocr_text': row['ocr_text'],
                'ocr_text_enhanced': row['ocr_text_enhanced'],
                'entities_existing': row['entities_existing'],
                'entities_count': 0
            }]
        else:
            # Поиск постов без entities
            posts = await find_posts_without_entities(db_pool, days=args.days, limit=args.limit)
        
        if not posts:
            print("✅ Постов для обработки не найдено")
            return
        
        print(f"\n📊 Найдено постов для обработки: {len(posts)}")
        
        # Переобработка
        stats = await reprocess_posts(
            db_pool=db_pool,
            redis_client=redis_client,
            neo4j_client=neo4j_client,
            ocr_enhancement_service=ocr_enhancement_service,
            posts=posts,
            dry_run=args.dry_run
        )
        
        # Вывод статистики
        print("\n" + "="*70)
        print("📈 Статистика обработки:")
        print("="*70)
        print(f"   Всего постов: {stats['total']}")
        print(f"   Обработано: {stats['processed']}")
        print(f"   Обновлено в БД: {stats['updated_db']}")
        print(f"   Переиндексировано в Neo4j: {stats['reindexed_neo4j']}")
        print(f"   Ошибок: {stats['errors']}")
        print("="*70)
        
    except Exception as e:
        logger.error("Script failed", error=str(e))
        import traceback
        traceback.print_exc()
    finally:
        # Закрытие подключений
        if 'db_pool' in locals():
            await db_pool.close()
        if 'redis_client' in locals():
            await redis_client.close()
        if 'neo4j_client' in locals():
            await neo4j_client.close()


if __name__ == "__main__":
    asyncio.run(main())

