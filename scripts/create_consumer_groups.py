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
    'stream:posts:vision:uploaded': 'vision_workers',
    'stream:posts:vision:analyzed': 'vision_analysis_workers',
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
            except redis.exceptions.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    logger.info("Consumer group already exists", stream=stream, group=group)
                else:
                    logger.error("Failed to create consumer group", stream=stream, group=group, error=str(e))
                    raise
            except Exception as e:
                logger.error("Failed to create consumer group", stream=stream, group=group, error=str(e))
                raise
        
        logger.info("All consumer groups created successfully")
        
    except Exception as e:
        logger.error("Failed to create consumer groups", error=str(e))
        raise
    finally:
        await redis_client.close()

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
                    
            except redis.exceptions.ResponseError as e:
                if "no such key" in str(e).lower():
                    logger.warning("Stream does not exist", stream=stream)
                else:
                    logger.error("Failed to check consumer group", stream=stream, group=group, error=str(e))
            except Exception as e:
                logger.error("Failed to check consumer group", stream=stream, group=group, error=str(e))
        
    except Exception as e:
        logger.error("Failed to check consumer groups", error=str(e))
        raise
    finally:
        await redis_client.close()

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
