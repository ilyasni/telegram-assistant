#!/usr/bin/env python3
"""
Скрипт для перетегирования постов с некорректными тегами.
Удаляет старые теги и генерирует новые через строгий промпт.
"""

import asyncio
import json
import logging
import os
import sys
from typing import List, Dict, Any
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as redis
from worker.ai_providers.gigachain_adapter import create_gigachain_adapter
from worker.events.schemas.posts_tagged_v1 import PostTaggedEventV1

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_posts_with_bad_tags(db_pool: asyncpg.Pool) -> List[Dict[str, Any]]:
    """Получить посты с некорректными тегами."""
    async with db_pool.acquire() as conn:
        query = """
        SELECT 
            p.id as post_id,
            p.content,
            p.channel_id,
            pe.tags as old_tags,
            pe.enrichment_provider
        FROM posts p
        JOIN post_enrichment pe ON p.id = pe.post_id
        WHERE pe.kind = 'tags'
          AND (pe.tags::text LIKE '%финансоваяаналитика%' 
               OR pe.tags::text LIKE '%инвестиции%' 
               OR pe.tags::text LIKE '%экономика%')
        ORDER BY pe.updated_at DESC
        LIMIT 100;
        """
        rows = await conn.fetch(query)
        return [dict(row) for row in rows]

async def retag_post(adapter, post_data: Dict[str, Any]) -> Dict[str, Any]:
    """Перетегировать один пост."""
    try:
        # Генерация новых тегов
        results = await adapter.generate_tags_batch([post_data['content']])
        if not results or not results[0].tags:
            return None
        
        # Извлечение тегов
        new_tags = [tag.name for tag in results[0].tags if tag.name]
        
        return {
            'post_id': post_data['post_id'],
            'old_tags': post_data['old_tags'],
            'new_tags': new_tags,
            'provider': results[0].provider,
            'latency_ms': results[0].processing_time_ms
        }
    except Exception as e:
        logger.error(f"Failed to retag post {post_data['post_id']}: {e}")
        return None

async def save_new_tags(db_pool: asyncpg.Pool, retag_result: Dict[str, Any]):
    """Сохранить новые теги в БД."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Обновляем теги
            await conn.execute("""
                UPDATE post_enrichment 
                SET 
                    tags = $1::text[],
                    enrichment_provider = $2,
                    enrichment_latency_ms = $3,
                    metadata = metadata || $4::jsonb,
                    updated_at = NOW()
                WHERE post_id = $5 AND kind = 'tags'
            """, 
                retag_result['new_tags'],
                retag_result['provider'],
                retag_result['latency_ms'],
                json.dumps({
                    'retagged_at': datetime.now(timezone.utc).isoformat(),
                    'old_tags': retag_result['old_tags']
                }, ensure_ascii=False),
                retag_result['post_id']
            )

async def publish_tagged_event(redis_client: redis.Redis, retag_result: Dict[str, Any]):
    """Опубликовать событие posts.tagged."""
    try:
        # Создаём событие
        event = PostTaggedEventV1(
            idempotency_key=f"{retag_result['post_id']}:tagged:v1",
            post_id=retag_result['post_id'],
            tags=retag_result['new_tags'],
            tags_hash=PostTaggedEventV1.compute_hash(retag_result['new_tags']),
            provider=retag_result['provider'],
            latency_ms=retag_result['latency_ms'],
            metadata={
                'retagged': True,
                'old_tags': retag_result['old_tags']
            }
        )
        
        # Публикуем в Redis Stream
        await redis_client.xadd(
            'stream:posts:tagged',
            event.dict(),
            maxlen=10000
        )
        
        logger.info(f"Published tagged event for post {retag_result['post_id']}")
        
    except Exception as e:
        logger.error(f"Failed to publish event for post {retag_result['post_id']}: {e}")

async def main():
    """Основная функция."""
    # Конфигурация
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    print("🚀 Starting post retagging...")
    print(f"Database URL: {db_url}")
    print(f"Redis URL: {redis_url}")
    
    # Создание соединений
    db_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=10)
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    try:
        # Создание AI адаптера
        print("🤖 Creating AI adapter...")
        adapter = await create_gigachain_adapter()
        
        # Получение постов с плохими тегами
        print("📊 Fetching posts with bad tags...")
        posts = await get_posts_with_bad_tags(db_pool)
        print(f"Found {len(posts)} posts with bad tags")
        
        if not posts:
            print("✅ No posts with bad tags found")
            return
        
        # Перетегирование
        print("🔄 Retagging posts...")
        retagged_count = 0
        
        for i, post_data in enumerate(posts, 1):
            print(f"Processing {i}/{len(posts)}: {post_data['post_id']}")
            
            # Перетегирование
            retag_result = await retag_post(adapter, post_data)
            if not retag_result:
                print(f"❌ Failed to retag post {post_data['post_id']}")
                continue
            
            # Сохранение новых тегов
            await save_new_tags(db_pool, retag_result)
            
            # Публикация события
            await publish_tagged_event(redis_client, retag_result)
            
            retagged_count += 1
            print(f"✅ Retagged post {post_data['post_id']}: {retag_result['old_tags']} -> {retag_result['new_tags']}")
            
            # Небольшая пауза между запросами
            await asyncio.sleep(0.5)
        
        print(f"✅ Successfully retagged {retagged_count} posts")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise
    finally:
        await adapter.close()
        await redis_client.close()
        await db_pool.close()

if __name__ == "__main__":
    asyncio.run(main())
