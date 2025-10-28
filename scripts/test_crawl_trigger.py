#!/usr/bin/env python3
"""
Тест crawl trigger - проверка публикации в stream:posts:crawl.

Context7 best practice: Тестирование через реальные данные из Redis.
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import redis.asyncio as redis
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


async def test_crawl_trigger():
    """Тест работы crawl trigger."""
    logger.info("Testing crawl trigger...")
    
    client = redis.from_url(REDIS_URL, decode_responses=True)
    
    try:
        # Получаем последние 5 событий из stream:posts:tagged
        events = await client.xrevrange("stream:posts:tagged", count=5)
        
        logger.info("Found tagged events", count=len(events))
        
        for msg_id, fields in events:
            # Парсим payload
            data_str = fields.get('data') or fields.get('payload') or json.dumps(fields)
            if isinstance(data_str, str):
                try:
                    payload = json.loads(data_str)
                except:
                    payload = fields
            else:
                payload = fields
            
            post_id = payload.get('post_id')
            tags = payload.get('tags', [])
            urls = payload.get('urls', [])
            
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except:
                    tags = []
            
            if isinstance(urls, str):
                try:
                    urls = json.loads(urls)
                except:
                    urls = []
            
            logger.info("Event details",
                       post_id=post_id,
                       tags_count=len(tags) if tags else 0,
                       tags=tags[:5] if tags else [],
                       urls_count=len(urls) if urls else 0,
                       urls=urls[:3] if urls else [])
            
            # Проверяем: есть ли этот пост в stream:posts:crawl
            crawl_events = await client.xrevrange("stream:posts:crawl", count=100)
            crawl_post_ids = []
            for crawl_msg_id, crawl_fields in crawl_events:
                crawl_data = crawl_fields.get('data', '{}')
                if isinstance(crawl_data, str):
                    try:
                        crawl_payload = json.loads(crawl_data)
                        crawl_post_ids.append(crawl_payload.get('post_id'))
                    except:
                        pass
            
            if post_id in crawl_post_ids:
                logger.info("✅ Post found in crawl stream", post_id=post_id)
            else:
                # Определяем причину
                has_urls = bool(urls and len(urls) > 0)
                trigger_tags = ['longread', 'research', 'paper', 'release', 'law', 'deepdive', 'analysis', 'report', 'study', 'whitepaper']
                has_trigger = any(tag in trigger_tags for tag in tags) if tags else False
                
                reason = []
                if not has_urls:
                    reason.append("no_urls")
                if not has_trigger:
                    reason.append("no_trigger_tags")
                
                logger.info("❌ Post NOT in crawl stream",
                           post_id=post_id,
                           reason=", ".join(reason) if reason else "unknown",
                           has_urls=has_urls,
                           has_trigger=has_trigger)
        
        # Статистика crawl stream
        crawl_len = await client.xlen("stream:posts:crawl")
        logger.info("Crawl stream stats", length=crawl_len)
        
        await client.aclose()
        
    except Exception as e:
        logger.error("Test failed", error=str(e))
        raise


if __name__ == "__main__":
    asyncio.run(test_crawl_trigger())

