#!/usr/bin/env python3
"""
Smoke Test 1: Vision Analysis Success (Happy Path)

Цель: Убедиться, что happy path работает (XADD → анализ → analyzed → XACK)
"""

import asyncio
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

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


async def check_analyzed_stream(redis_client: redis.Redis, stream: str, post_id: str, trace_id: str, timeout: int = 30) -> dict:
    """Проверка наличия события в analyzed stream."""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # XRANGE для последних 10 записей
        messages = await redis_client.xrange(stream, count=10)
        
        for msg_id, fields in reversed(messages):  # Проверяем с конца
            # Декодирование полей
            decoded_fields = {}
            for key, value in fields.items():
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                decoded_fields[key_str] = value_str
            
            # Проверка event типа
            event_type = decoded_fields.get('event', '')
            if event_type not in ['posts.vision.analyzed', 'posts.vision.skipped']:
                continue
            
            # Парсинг data
            data_json = decoded_fields.get('data', '{}')
            if isinstance(data_json, bytes):
                data_json = data_json.decode('utf-8')
            
            try:
                data = json.loads(data_json)
                if data.get('post_id') == post_id and data.get('trace_id') == trace_id:
                    # Проверка, что это не skipped
                    skipped = decoded_fields.get('skipped', 'false')
                    if skipped != 'true':
                        return {
                            'found': True,
                            'message_id': msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id),
                            'event_type': event_type,
                            'skipped': skipped,
                            'data': data
                        }
            except json.JSONDecodeError:
                continue
        
        await asyncio.sleep(1)
    
    return {'found': False}


async def check_pending(redis_client: redis.Redis, stream: str, group: str) -> dict:
    """Проверка pending сообщений."""
    try:
        pending_info = await redis_client.xpending(stream, group)
        if isinstance(pending_info, dict):
            return {
                'pending': pending_info.get('pending', 0),
                'consumers': pending_info.get('consumers', [])
            }
        return {'pending': 0}
    except Exception as e:
        return {'pending': 0, 'error': str(e)}


async def main():
    parser = argparse.ArgumentParser(description="Smoke Test 1: Vision Analysis Success")
    parser.add_argument("--s3-key", type=str,
                       default="media/877193ef-be80-4977-aaeb-8009c3d772ee/4e/4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b..png",
                       help="S3 key for valid event")
    parser.add_argument("--sha256", type=str,
                       default="4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b",
                       help="SHA256 for media")
    parser.add_argument("--post-id", type=str, default="p-1001", help="Post ID")
    parser.add_argument("--trace-id", type=str, default="t-1001", help="Trace ID")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    
    args = parser.parse_args()
    
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    stream_in = "stream:posts:vision"
    stream_analyzed = "stream:posts:vision:analyzed"
    group = "vision_workers"
    
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        print("=== Smoke Test 1: Vision Analysis Success ===")
        print(f"Post ID: {args.post_id}")
        print(f"Trace ID: {args.trace_id}")
        print(f"S3 Key: {args.s3_key}")
        print("")
        
        # 1. Создание события
        event = VisionUploadedEventV1(
            tenant_id=args.tenant_id,
            post_id=args.post_id,
            trace_id=args.trace_id,
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
        
        # 2. Публикация события
        event_dict = event.model_dump(mode='json', exclude_none=True)
        event_json = json.dumps(event_dict, default=str)
        
        message_id = await redis_client.xadd(
            stream_in,
            {
                "event": "posts.vision.uploaded",
                "data": event_json
            }
        )
        
        print(f"✓ Event published to {stream_in}")
        print(f"  Message ID: {message_id.decode('utf-8') if isinstance(message_id, bytes) else message_id}")
        print("")
        
        # 3. Ожидание обработки
        print(f"Waiting for processing (timeout: {args.timeout}s)...")
        result = await check_analyzed_stream(redis_client, stream_analyzed, args.post_id, args.trace_id, timeout=args.timeout)
        
        if result.get('found'):
            print("✓ Event found in analyzed stream")
            print(f"  Message ID: {result['message_id']}")
            print(f"  Event Type: {result['event_type']}")
            print(f"  Skipped: {result['skipped']}")
            print("")
        else:
            print("✗ Event not found in analyzed stream (timeout)")
            print("")
        
        # 4. Проверка pending
        pending_info = await check_pending(redis_client, stream_in, group)
        pending_count = pending_info.get('pending', 0)
        
        print(f"Checking pending messages in {stream_in} (group: {group})...")
        if pending_count == 0:
            print("✓ No pending messages")
        else:
            print(f"⚠ Found {pending_count} pending messages")
            print("")
        
        # 5. Итоговый результат
        success = result.get('found') and pending_count == 0
        
        print("=== Test Result ===")
        if success:
            # Проверяем, что событие обработано (analyzed или skipped с идемпотентностью - это OK)
            is_skipped = result.get('skipped') == 'true'
            if is_skipped:
                reasons = result.get('data', {}).get('reasons', [])
                has_idempotency = any(r.get('reason') == 'idempotency' for r in reasons)
                if has_idempotency:
                    print("✓ SUCCESS: Event processed (skipped due to idempotency - expected)")
                    print("  - Event published")
                    print("  - Event processed (skipped with idempotency reason)")
                    print("  - No pending messages")
                    return 0
                else:
                    print("⚠ PARTIAL: Event processed but skipped for other reason")
                    print(f"  Reasons: {[r.get('reason') for r in reasons]}")
                    return 1
            else:
                print("✓ SUCCESS: Happy path works correctly")
                print("  - Event published")
                print("  - Event analyzed")
                print("  - No pending messages")
                return 0
        else:
            print("✗ FAILED")
            if not result.get('found'):
                print("  - Event not found in analyzed stream")
            if pending_count > 0:
                print(f"  - {pending_count} pending messages")
            return 1
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

