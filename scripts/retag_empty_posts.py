#!/usr/bin/env python3
"""
Скрипт для перетегирования постов с пустыми тегами.
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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_posts_with_empty_tags(db_pool: asyncpg.Pool) -> List[Dict[str, Any]]:
    """Получить посты с пустыми тегами."""
    async with db_pool.acquire() as conn:
        query = """
        SELECT 
            p.id as post_id,
            p.content,
            p.channel_id
        FROM posts p
        JOIN post_enrichment pe ON p.id = pe.post_id
        WHERE pe.kind = 'tags'
          AND pe.tags = '{}'::text[]
        ORDER BY p.created_at DESC
        LIMIT 10;
        """
        rows = await conn.fetch(query)
        return [dict(row) for row in rows]

async def generate_tags_for_post(content: str) -> List[str]:
    """Генерация тегов для поста (упрощённая версия)."""
    # Простая логика генерации тегов - извлекаем ключевые слова
    words = content.lower().split()
    
    # Фильтруем стоп-слова и короткие слова
    stop_words = {'и', 'в', 'на', 'с', 'для', 'от', 'до', 'по', 'за', 'о', 'об', 'что', 'как', 'где', 'когда', 'кто', 'почему'}
    
    # Извлекаем значимые слова (длиннее 3 символов)
    keywords = [word for word in words if len(word) > 3 and word not in stop_words]
    
    # Возвращаем первые 5 уникальных слов
    return list(set(keywords))[:5]

async def save_new_tags(db_pool: asyncpg.Pool, post_id: str, tags: List[str]):
    """Сохранить новые теги в БД."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Обновляем теги
            await conn.execute("""
                UPDATE post_enrichment 
                SET 
                    tags = $1::text[],
                    enrichment_provider = 'manual_retag',
                    updated_at = NOW()
                WHERE post_id = $2 AND kind = 'tags'
            """, 
                tags,
                post_id
            )

async def main():
    """Основная функция."""
    # Конфигурация
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    
    print("🚀 Starting post retagging...")
    print(f"Database URL: {db_url}")
    
    # Создание соединения
    db_pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    
    try:
        # Получение постов с пустыми тегами
        print("📊 Fetching posts with empty tags...")
        posts = await get_posts_with_empty_tags(db_pool)
        print(f"Found {len(posts)} posts with empty tags")
        
        if not posts:
            print("✅ No posts with empty tags found")
            return
        
        # Перетегирование
        print("🔄 Retagging posts...")
        retagged_count = 0
        
        for i, post_data in enumerate(posts, 1):
            print(f"Processing {i}/{len(posts)}: {post_data['post_id']}")
            
            # Генерация тегов
            tags = await generate_tags_for_post(post_data['content'])
            if not tags:
                print(f"❌ No tags generated for post {post_data['post_id']}")
                continue
            
            # Сохранение новых тегов
            await save_new_tags(db_pool, post_data['post_id'], tags)
            
            retagged_count += 1
            print(f"✅ Retagged post {post_data['post_id']}: {tags}")
            
            # Небольшая пауза между запросами
            await asyncio.sleep(0.1)
        
        print(f"✅ Successfully retagged {retagged_count} posts")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise
    finally:
        await db_pool.close()

if __name__ == "__main__":
    asyncio.run(main())
