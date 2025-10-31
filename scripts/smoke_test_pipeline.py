#!/usr/bin/env python3
"""
Смоук-тест для проверки стабилизированного пайплайна.

Context7 best practice: проверка event-driven архитектуры, идемпотентности,
и сквозного потока данных от telethon-ingest до БД.
"""

import asyncio
import os
import sys
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

import redis.asyncio as redis
import asyncpg
import structlog

logger = structlog.get_logger()

# Конфигурация
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
STREAM_NAME = "posts.parsed"
CONSUMER_GROUP = "post_persist_workers"

async def generate_test_event() -> Dict[str, Any]:
    """Генерация тестового события post.parsed."""
    now = datetime.now(timezone.utc)
    post_id = str(uuid.uuid4())
    
    return {
        'post_id': post_id,
        'channel_id': '12345',
        'telegram_message_id': 999999,
        'content': 'Test post for smoke test',
        'media_urls': json.dumps([]),
        'created_at': now.isoformat(),
        'is_processed': False,
        'posted_at': now.isoformat(),
        'url': '',
        'has_media': False,
        'views_count': 0,
        'forwards_count': 0,
        'reactions_count': 0,
        'replies_count': 0,
        'is_pinned': False,
        'is_edited': False,
        'edited_at': '',
        'post_author': '',
        'reply_to_message_id': None,
        'reply_to_chat_id': None,
        'via_bot_id': None,
        'via_business_bot_id': None,
        'is_silent': False,
        'is_legacy': False,
        'noforwards': False,
        'invert_media': False,
        'tg_channel_id': 12345,
        'content_hash': 'test_hash_123',
        'urls': json.dumps([]),
        'link_count': 0,
        'tenant_id': 'test-tenant',
        'user_id': 'test-user'
    }

async def publish_test_event(redis_client: redis.Redis, event_data: Dict[str, Any]) -> str:
    """Публикация тестового события в Redis Stream."""
    try:
        # Публикация в stream:posts:parsed
        message_id = await redis_client.xadd("stream:posts:parsed", event_data)
        logger.info("Test event published", message_id=message_id, post_id=event_data['post_id'])
        return message_id
    except Exception as e:
        logger.error("Failed to publish test event", error=str(e))
        raise

async def check_database_post(db_pool: asyncpg.Pool, post_id: str) -> bool:
    """Проверка наличия поста в БД."""
    try:
        async with db_pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT 1 FROM posts WHERE id = $1",
                post_id
            )
            return result is not None
    except Exception as e:
        logger.error("Failed to check database post", error=str(e))
        return False

async def check_consumer_group_lag(redis_client: redis.Redis) -> Dict[str, int]:
    """Проверка лага consumer group."""
    try:
        # Получение информации о consumer group
        info = await redis_client.xinfo_groups("stream:posts:parsed")
        group_info = next((g for g in info if g['name'] == CONSUMER_GROUP), None)
        
        if not group_info:
            return {'pending': 0, 'consumers': 0}
        
        # Получение pending сообщений
        pending = await redis_client.xpending("stream:posts:parsed", CONSUMER_GROUP)
        
        return {
            'pending': pending['pending'],
            'consumers': group_info['consumers']
        }
    except Exception as e:
        logger.error("Failed to check consumer group lag", error=str(e))
        return {'pending': 0, 'consumers': 0}

async def test_idempotency(redis_client: redis.Redis, db_pool: asyncpg.Pool, event_data: Dict[str, Any]):
    """Тест идемпотентности: повторная отправка не должна создавать дубли."""
    logger.info("Testing idempotency...")
    
    # Первая отправка
    message_id_1 = await publish_test_event(redis_client, event_data)
    await asyncio.sleep(2)  # Ждём обработки
    
    # Проверка наличия в БД
    exists_1 = await check_database_post(db_pool, event_data['post_id'])
    logger.info("First send result", exists=exists_1)
    
    # Вторая отправка (должна быть проигнорирована)
    message_id_2 = await publish_test_event(redis_client, event_data)
    await asyncio.sleep(2)  # Ждём обработки
    
    # Проверка, что дублей нет
    exists_2 = await check_database_post(db_pool, event_data['post_id'])
    logger.info("Second send result", exists=exists_2)
    
    # Проверка, что в БД только одна запись
    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM posts WHERE id = $1",
            event_data['post_id']
        )
    
    logger.info("Idempotency test result", 
               first_exists=exists_1, 
               second_exists=exists_2, 
               total_count=count)
    
    return exists_1 and exists_2 and count == 1

async def cleanup_test_data(db_pool: asyncpg.Pool, post_id: str):
    """Очистка тестовых данных."""
    try:
        async with db_pool.acquire() as conn:
            # Удаление из posts
            await conn.execute("DELETE FROM posts WHERE id = $1", post_id)
            
            # Удаление из post_enrichment
            await conn.execute("DELETE FROM post_enrichment WHERE post_id = $1", post_id)
        
        logger.info("Test data cleaned up", post_id=post_id)
    except Exception as e:
        logger.error("Failed to cleanup test data", error=str(e))

async def main():
    """Главная функция смоук-теста."""
    logger.info("Starting smoke test for stabilized pipeline")
    
    # Подключение к Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    
    # Подключение к БД
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    
    try:
        # 1. Проверка consumer group
        logger.info("Step 1: Checking consumer group")
        lag_info = await check_consumer_group_lag(redis_client)
        logger.info("Consumer group status", **lag_info)
        
        # 2. Генерация тестового события
        logger.info("Step 2: Generating test event")
        event_data = await generate_test_event()
        logger.info("Test event generated", post_id=event_data['post_id'])
        
        # 3. Публикация события
        logger.info("Step 3: Publishing test event")
        message_id = await publish_test_event(redis_client, event_data)
        
        # 4. Ожидание обработки
        logger.info("Step 4: Waiting for processing")
        await asyncio.sleep(5)
        
        # 5. Проверка БД
        logger.info("Step 5: Checking database")
        exists = await check_database_post(db_pool, event_data['post_id'])
        logger.info("Database check result", exists=exists)
        
        # 6. Тест идемпотентности
        logger.info("Step 6: Testing idempotency")
        idempotent = await test_idempotency(redis_client, db_pool, event_data)
        
        # 7. Финальная проверка лага
        logger.info("Step 7: Final lag check")
        final_lag = await check_consumer_group_lag(redis_client)
        logger.info("Final lag status", **final_lag)
        
        # Результаты
        success = exists and idempotent and final_lag['pending'] == 0
        
        if success:
            logger.info("✅ Smoke test PASSED", 
                       post_exists=exists,
                       idempotent=idempotent,
                       no_pending=final_lag['pending'] == 0)
        else:
            logger.error("❌ Smoke test FAILED",
                        post_exists=exists,
                        idempotent=idempotent,
                        no_pending=final_lag['pending'] == 0)
        
        # Очистка
        await cleanup_test_data(db_pool, event_data['post_id'])
        
        return success
        
    except Exception as e:
        logger.error("Smoke test failed with error", error=str(e))
        return False
    finally:
        await redis_client.close()
        await db_pool.close()

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
