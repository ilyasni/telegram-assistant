"""
Скрипт для перетегирования одного поста.
Публикует событие posts.parsed для указанного post_id.
"""
import asyncio
import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from redis import Redis
import json

async def retag_post(post_id: str):
    """Перетегирует один пост, публикуя событие posts.parsed."""
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@supabase-db:5432/postgres')
    redis_url = os.getenv('REDIS_URL', 'redis://redis:6379')
    
    # Получаем данные поста из БД
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT 
            p.id::text as post_id,
            p.channel_id::text as channel_id,
            COALESCE(p.content, '') as content,
            COALESCE(p.media_urls, '[]'::jsonb)::text as urls_json,
            COALESCE(p.posted_at::text, '') as posted_at,
            COALESCE(p.telegram_post_url, '') as telegram_post_url,
            p.has_media,
            p.is_edited
        FROM posts p
        WHERE p.id = %s
        LIMIT 1
    """, (post_id,))
    
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not row:
        print(f"❌ Post {post_id} not found")
        return
    
    # Формируем событие согласно PostParsedEventV1 схеме
    urls = json.loads(row['urls_json']) if row['urls_json'] else []
    
    # Вычисляем content_hash
    import hashlib
    content_hash = hashlib.sha256(row['content'].encode('utf-8')).hexdigest() if row['content'] else ''
    
    event_data = {
        'schema_version': 'v1',
        'post_id': row['post_id'],
        'channel_id': row['channel_id'],
        'text': row['content'],  # Поле называется 'text', не 'content'
        'urls': urls,
        'posted_at': row['posted_at'],
        'telegram_post_url': row['telegram_post_url'] or '',
        'has_media': row['has_media'] or False,
        'is_edited': row['is_edited'] or False,
        'media_sha256_list': [],  # Обязательное поле
        'content_hash': content_hash  # Обязательное поле
    }
    
    # Публикуем в Redis Streams
    redis_client = Redis.from_url(redis_url, decode_responses=False)
    stream_name = 'stream:posts:parsed'
    
    message_id = redis_client.xadd(
        stream_name,
        {'data': json.dumps(event_data, ensure_ascii=False)},
        maxlen=10000
    )
    
    print(f"✅ Published posts.parsed event for post {post_id}")
    print(f"   Message ID: {message_id}")
    print(f"   Content preview: {event_data['content'][:100]}...")
    
    redis_client.close()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python retag_single_post.py <post_id>")
        sys.exit(1)
    
    post_id = sys.argv[1]
    asyncio.run(retag_post(post_id))

