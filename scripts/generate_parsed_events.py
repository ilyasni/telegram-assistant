#!/usr/bin/env python3
"""
Скрипт для генерации событий posts.parsed для существующих постов в БД.
Это запускает пайплайн тегирования и обогащения для всех необработанных постов.
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime
from typing import List, Dict, Any

import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Добавляем путь к проекту
sys.path.append('/opt/telegram-assistant')

from worker.events.schemas.posts_parsed_v1 import PostParsedEventV1
from worker.event_bus import EventPublisher

logger = None

async def get_unprocessed_posts(db_session: AsyncSession) -> List[Dict[str, Any]]:
    """Получение необработанных постов из БД."""
    query = text("""
        SELECT 
            p.id as post_id,
            p.channel_id,
            p.telegram_message_id,
            p.content,
            p.media_urls,
            p.posted_at,
            p.telegram_post_url,
            p.has_media,
            p.is_edited,
            p.post_author,
            p.reply_to_message_id,
            p.reply_to_chat_id,
            p.via_bot_id,
            p.via_business_bot_id,
            p.is_silent,
            p.is_legacy,
            p.noforwards,
            p.invert_media,
            c.telegram_channel_id,
            c.tenant_id
        FROM posts p
        JOIN channels c ON p.channel_id = c.id
        WHERE p.is_processed = false
        ORDER BY p.created_at ASC
        LIMIT 100
    """)
    
    result = await db_session.execute(query)
    posts = []
    
    for row in result:
        # Извлечение URL из media_urls
        media_urls = row.media_urls if row.media_urls else []
        urls = []
        if isinstance(media_urls, list):
            urls = [str(url) for url in media_urls if url]
        
        # Создание content_hash
        content = row.content or ""
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        
        # Создание idempotency_key
        idempotency_key = f"{row.post_id}:{content_hash}"
        
        post_data = {
            'post_id': str(row.post_id),
            'channel_id': str(row.channel_id),
            'telegram_message_id': row.telegram_message_id,
            'content': content,
            'urls': urls,
            'posted_at': row.posted_at,
            'content_hash': content_hash,
            'idempotency_key': idempotency_key,
            'telegram_post_url': row.telegram_post_url,
            'has_media': row.has_media,
            'is_edited': row.is_edited,
            'post_author': row.post_author,
            'reply_to_message_id': row.reply_to_message_id,
            'reply_to_chat_id': row.reply_to_chat_id,
            'via_bot_id': row.via_bot_id,
            'via_business_bot_id': row.via_business_bot_id,
            'is_silent': row.is_silent,
            'is_legacy': row.is_legacy,
            'noforwards': row.noforwards,
            'invert_media': row.invert_media,
            'telegram_channel_id': row.telegram_channel_id,
            'tenant_id': str(row.tenant_id),
            'user_id': str(row.tenant_id)  # Используем tenant_id как user_id
        }
        posts.append(post_data)
    
    return posts

async def create_parsed_event(post_data: Dict[str, Any]) -> PostParsedEventV1:
    """Создание события PostParsedEventV1 из данных поста."""
    return PostParsedEventV1(
        idempotency_key=post_data['idempotency_key'],
        user_id=post_data['user_id'],
        channel_id=post_data['channel_id'],
        post_id=post_data['post_id'],
        tenant_id=post_data['tenant_id'],
        text=post_data['content'],
        urls=post_data['urls'],
        posted_at=post_data['posted_at'],
        content_hash=post_data['content_hash'],
        link_count=len(post_data['urls']),
        tg_message_id=post_data['telegram_message_id'],
        telegram_message_id=post_data['telegram_message_id'],
        tg_channel_id=post_data['telegram_channel_id'],
        telegram_post_url=post_data['telegram_post_url'],
        has_media=post_data['has_media'],
        is_edited=post_data['is_edited']
    )

async def publish_parsed_events(redis_client: redis.Redis, events: List[PostParsedEventV1]):
    """Публикация событий posts.parsed в Redis Streams."""
    publisher = EventPublisher(redis_client)
    
    for event in events:
        try:
            await publisher.publish_event('posts.parsed', event.dict())
            print(f"✅ Published event for post {event.post_id}")
        except Exception as e:
            print(f"❌ Failed to publish event for post {event.post_id}: {e}")

async def main():
    """Основная функция."""
    global logger
    
    # Конфигурация
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    print("🚀 Starting posts.parsed events generation...")
    print(f"Database URL: {db_url}")
    print(f"Redis URL: {redis_url}")
    
    # Создание соединений
    engine = create_async_engine(db_url)
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    try:
        async with AsyncSession(engine) as db_session:
            # Получение необработанных постов
            print("📊 Fetching unprocessed posts...")
            posts = await get_unprocessed_posts(db_session)
            print(f"Found {len(posts)} unprocessed posts")
            
            if not posts:
                print("✅ No unprocessed posts found")
                return
            
            # Создание событий
            print("🔄 Creating parsed events...")
            events = []
            for post_data in posts:
                try:
                    event = await create_parsed_event(post_data)
                    events.append(event)
                except Exception as e:
                    print(f"❌ Failed to create event for post {post_data['post_id']}: {e}")
            
            print(f"Created {len(events)} events")
            
            # Публикация событий
            print("📤 Publishing events to Redis...")
            await publish_parsed_events(redis_client, events)
            
            print(f"✅ Successfully published {len(events)} events")
            print("🎯 Worker should now start processing these events...")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        raise
    finally:
        await redis_client.close()
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
