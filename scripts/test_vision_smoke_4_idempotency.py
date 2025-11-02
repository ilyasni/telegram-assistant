#!/usr/bin/env python3
"""
Smoke Test 4: Vision Analysis Idempotency

Цель: Повторная публикация того же события не создаёт дублей
"""

import asyncio
import argparse
import json
import os
import sys
import time

sys.path.insert(0, "/opt/telegram-assistant")
sys.path.insert(0, "/app")

import redis.asyncio as redis

try:
    from worker.events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
except ImportError:
    try:
        from events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
    except ImportError:
        print("ERROR: Cannot import VisionUploadedEventV1")
        sys.exit(1)


async def count_analyzed_events(redis_client: redis.Redis, stream: str, post_id: str) -> int:
    """Подсчет событий для конкретного post_id в analyzed stream."""
    count = 0
    messages = await redis_client.xrange(stream, count=100)
    
    for msg_id, fields in messages:
        decoded_fields = {}
        for key, value in fields.items():
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
            value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
            decoded_fields[key_str] = value_str
        
        # Парсинг data
        data_json = decoded_fields.get('data', '{}')
        if isinstance(data_json, bytes):
            data_json = data_json.decode('utf-8')
        
        try:
            data = json.loads(data_json)
            if data.get('post_id') == post_id:
                count += 1
        except json.JSONDecodeError:
            continue
    
    return count


async def check_idempotency_key(redis_client: redis.Redis, post_id: str, sha256: str) -> dict:
    """Проверка наличия идемпотентности ключа в Redis."""
    key = f"vision:processed:{post_id}:{sha256}"
    
    exists = await redis_client.exists(key)
    ttl = -1
    if exists:
        ttl = await redis_client.ttl(key)
    
    return {
        'exists': bool(exists),
        'ttl': ttl,
        'key': key
    }


async def main():
    parser = argparse.ArgumentParser(description="Smoke Test 4: Vision Analysis Idempotency")
    parser.add_argument("--s3-key", type=str,
                       default="media/877193ef-be80-4977-aaeb-8009c3d772ee/4e/4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b..png",
                       help="S3 key for valid event")
    parser.add_argument("--sha256", type=str,
                       default="4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b",
                       help="SHA256 for media")
    parser.add_argument("--post-id", type=str, default="p-1001", help="Post ID (same as test 1)")
    parser.add_argument("--trace-id-1", type=str, default="t-1001", help="Trace ID for first event")
    parser.add_argument("--trace-id-2", type=str, default="t-1001-retry", help="Trace ID for second event")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    
    args = parser.parse_args()
    
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    stream_in = "stream:posts:vision"
    stream_analyzed = "stream:posts:vision:analyzed"
    
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        print("=== Smoke Test 4: Vision Analysis Idempotency ===")
        print(f"Post ID: {args.post_id}")
        print(f"SHA256: {args.sha256}")
        print("")
        
        # 1. Подсчет начального количества событий для post_id
        print("1. Checking initial event count...")
        initial_count = await count_analyzed_events(redis_client, stream_analyzed, args.post_id)
        print(f"   Initial events for post_id {args.post_id}: {initial_count}")
        print("")
        
        # 2. Публикация первого события (если еще не обработано)
        print("2. Publishing first event...")
        event1 = VisionUploadedEventV1(
            tenant_id=args.tenant_id,
            post_id=args.post_id,
            trace_id=args.trace_id_1,
            media_files=[
                MediaFile(
                    sha256=args.sha256,
                    s3_key=args.s3_key,
                    mime_type="image/png",
                    size_bytes=67
                )
            ],
            idempotency_key=f"{args.tenant_id}:{args.post_id}:vision_upload"
        )
        
        event_dict1 = event1.model_dump(mode='json', exclude_none=True)
        event_json1 = json.dumps(event_dict1, default=str)
        
        message_id1 = await redis_client.xadd(
            stream_in,
            {
                "event": "posts.vision.uploaded",
                "data": event_json1
            }
        )
        
        print(f"   ✓ First event published (Message ID: {message_id1.decode('utf-8') if isinstance(message_id1, bytes) else message_id1})")
        
        # Ожидание обработки первого события
        print(f"   Waiting for first event processing (timeout: {args.timeout}s)...")
        await asyncio.sleep(args.timeout)
        
        count_after_first = await count_analyzed_events(redis_client, stream_analyzed, args.post_id)
        print(f"   Events after first: {count_after_first}")
        print("")
        
        # 3. Проверка идемпотентности ключа
        print("3. Checking idempotency key...")
        idempotency_info = await check_idempotency_key(redis_client, args.post_id, args.sha256)
        print(f"   Key: {idempotency_info['key']}")
        print(f"   Exists: {idempotency_info['exists']}")
        if idempotency_info['exists']:
            ttl_hours = idempotency_info['ttl'] / 3600 if idempotency_info['ttl'] > 0 else -1
            print(f"   TTL: {idempotency_info['ttl']}s ({ttl_hours:.1f}h)")
        print("")
        
        # 4. Публикация второго события (тот же post_id + sha256)
        print("4. Publishing second event (same post_id + sha256)...")
        event2 = VisionUploadedEventV1(
            tenant_id=args.tenant_id,
            post_id=args.post_id,
            trace_id=args.trace_id_2,
            media_files=[
                MediaFile(
                    sha256=args.sha256,
                    s3_key=args.s3_key,
                    mime_type="image/png",
                    size_bytes=67
                )
            ],
            idempotency_key=f"{args.tenant_id}:{args.post_id}:vision_upload"
        )
        
        event_dict2 = event2.model_dump(mode='json', exclude_none=True)
        event_json2 = json.dumps(event_dict2, default=str)
        
        message_id2 = await redis_client.xadd(
            stream_in,
            {
                "event": "posts.vision.uploaded",
                "data": event_json2
            }
        )
        
        print(f"   ✓ Second event published (Message ID: {message_id2.decode('utf-8') if isinstance(message_id2, bytes) else message_id2})")
        
        # Ожидание обработки второго события
        print(f"   Waiting for second event processing (timeout: {args.timeout}s)...")
        await asyncio.sleep(args.timeout)
        
        count_after_second = await count_analyzed_events(redis_client, stream_analyzed, args.post_id)
        print(f"   Events after second: {count_after_second}")
        print("")
        
        # 5. Итоговый результат
        new_events = count_after_second - initial_count
        events_added = count_after_second - count_after_first
        
        print("=== Test Result ===")
        print(f"Initial events: {initial_count}")
        print(f"Events after first: {count_after_first}")
        print(f"Events after second: {count_after_second}")
        print(f"New events added by second publish: {events_added}")
        print("")
        
        if events_added == 0:
            print("✓ SUCCESS: Idempotency works correctly")
            print("  - Second event did not create duplicate analyzed event")
            if idempotency_info['exists']:
                print(f"  - Idempotency key exists: {idempotency_info['key']}")
            return 0
        elif events_added == 1:
            # Возможно, событие было skipped с idempotency reason
            print("⚠ PARTIAL: One new event found (might be skipped with idempotency reason)")
            return 1
        else:
            print(f"✗ FAILED: {events_added} new events found (expected 0)")
            return 1
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

