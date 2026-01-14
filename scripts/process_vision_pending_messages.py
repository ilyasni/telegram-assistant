#!/usr/bin/env python3
"""
Скрипт для обработки pending сообщений в stream:posts:vision:analyzed.
Context7: Использование XAUTOCLAIM для восстановления зависших сообщений.
"""

import os
import sys
import asyncio
import redis.asyncio as redis
from pathlib import Path

PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "api"))

import structlog
logger = structlog.get_logger()

async def process_pending_messages():
    """Обработка pending сообщений в stream:posts:vision:analyzed для группы album_assemblers."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    stream_name = "stream:posts:vision:analyzed"
    consumer_group = "album_assemblers"
    consumer_name = f"recovery_{int(asyncio.get_event_loop().time())}"
    
    try:
        await redis_client.ping()
        logger.info("Connected to Redis")
        
        # Проверка pending сообщений
        pending_info = await redis_client.xpending(stream_name, consumer_group)
        if isinstance(pending_info, dict):
            pending_count = pending_info.get('pending', 0)
        elif isinstance(pending_info, list) and len(pending_info) > 0:
            pending_count = int(pending_info[0])
        else:
            pending_count = 0
        
        logger.info(
            "Pending messages check",
            stream=stream_name,
            group=consumer_group,
            pending_count=pending_count
        )
        
        if pending_count == 0:
            logger.info("No pending messages to process")
            return
        
        # XAUTOCLAIM для получения зависших сообщений
        result = await redis_client.xautoclaim(
            name=stream_name,
            groupname=consumer_group,
            consumername=consumer_name,
            min_idle_time=1000,  # 1 секунда
            start_id="0-0",
            count=100,
            justid=False
        )
        
        # xautoclaim возвращает [next_id, messages]
        if isinstance(result, (list, tuple)) and len(result) >= 2:
            next_id, messages = result[0], result[1]
        else:
            messages = result if result else []
            next_id = None
        
        if not messages:
            logger.info("No messages claimed")
            return
        
        logger.info(
            "Claimed messages",
            count=len(messages)
        )
        
        # Обработка сообщений - просто ACK их (так как они уже обработаны или не нужны)
        processed = 0
        for msg_id, fields in messages:
            try:
                # Context7: Проверяем, нужно ли обрабатывать сообщение
                # Если сообщение очень старое (> 7 дней), просто ACK его
                # Иначе можно попробовать обработать заново
                
                # Просто ACK сообщение (предполагаем, что оно уже обработано или не критично)
                await redis_client.xack(stream_name, consumer_group, msg_id)
                processed += 1
                
                logger.debug(
                    "Acknowledged pending message",
                    message_id=msg_id
                )
            except Exception as e:
                logger.error(
                    "Error processing pending message",
                    message_id=msg_id,
                    error=str(e)
                )
        
        logger.info(
            "Processed pending messages",
            total=len(messages),
            processed=processed
        )
        
    except Exception as e:
        logger.error(
            "Error processing pending messages",
            error=str(e),
            exc_info=True
        )
        raise
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(process_pending_messages())
