#!/usr/bin/env python3
"""
Создание Redis consumer groups для стабилизации пайплайна.

Context7 best practice: создание consumer groups с MKSTREAM для обеспечения
отказоустойчивости и горизонтального масштабирования.
"""

import asyncio
import os
import sys
import redis.asyncio as redis
import structlog

logger = structlog.get_logger()

# Конфигурация consumer groups
CONSUMER_GROUPS = {
    'stream:posts:parsed': 'post_persist_workers',
    'stream:posts:tagged': 'tag_persist_workers', 
    'stream:posts:enriched': 'enrichment_workers',
    'stream:posts:indexed': 'indexing_workers',
    'stream:posts:crawl': 'crawl_workers',
    'stream:posts:deleted': 'cleanup_workers',
    # Context7: Унифицированное именование - используем stream:posts:vision (без :uploaded суффикса)
    'stream:posts:vision': 'vision_workers',
    'stream:posts:vision:analyzed': 'retagging_workers',  # Context7: RetaggingTask использует этот стрим
}

async def create_consumer_groups():
    """Создание всех необходимых consumer groups."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    
    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
        
        for stream, group in CONSUMER_GROUPS.items():
            try:
                # XGROUP CREATE с MKSTREAM
                await redis_client.xgroup_create(stream, group, id='$', mkstream=True)
                logger.info("Consumer group created", stream=stream, group=group)
            except Exception as e:
                error_str = str(e)
                # Context7: Обработка разных типов ошибок Redis
                if "BUSYGROUP" in error_str or "Consumer Group name already exists" in error_str:
                    logger.info("Consumer group already exists", stream=stream, group=group)
                elif "no such key" in error_str.lower() or "NOGROUP" in error_str:
                    # Stream не существует, но mkstream должен создать - это странно
                    logger.warning("Stream may not exist yet", stream=stream, error=error_str)
                else:
                    logger.error("Failed to create consumer group", stream=stream, group=group, error=error_str)
                    raise
        
        logger.info("All consumer groups created successfully")
        
    except Exception as e:
        logger.error("Failed to create consumer groups", error=str(e))
        raise
    finally:
        await redis_client.aclose()  # Context7: используем aclose() вместо deprecated close()

async def check_consumer_groups():
    """Проверка существования consumer groups."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    
    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
        
        for stream, group in CONSUMER_GROUPS.items():
            try:
                # Проверка существования группы
                info = await redis_client.xinfo_groups(stream)
                group_exists = any(g['name'] == group for g in info)
                
                if group_exists:
                    logger.info("Consumer group exists", stream=stream, group=group)
                else:
                    logger.warning("Consumer group missing", stream=stream, group=group)
                    
            except Exception as e:
                error_str = str(e)
                if "no such key" in error_str.lower() or "NOGROUP" in error_str:
                    logger.warning("Stream does not exist or group not found", stream=stream, group=group)
                else:
                    logger.error("Failed to check consumer group", stream=stream, group=group, error=error_str)
        
    except Exception as e:
        logger.error("Failed to check consumer groups", error=str(e))
        raise
    finally:
        await redis_client.aclose()  # Context7: используем aclose() вместо deprecated close()

async def main():
    """Главная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage Redis consumer groups")
    parser.add_argument("--check", action="store_true", help="Check existing consumer groups")
    parser.add_argument("--create", action="store_true", help="Create consumer groups")
    
    args = parser.parse_args()
    
    if args.check:
        await check_consumer_groups()
    elif args.create:
        await create_consumer_groups()
    else:
        # По умолчанию создаём группы
        await create_consumer_groups()

if __name__ == "__main__":
    asyncio.run(main())
