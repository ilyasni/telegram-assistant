#!/usr/bin/env python3
"""
Smoke Test 2: Vision Analysis Skipped (S3 Missing)

Цель: Путь без доступного S3 → событие skipped в stream:posts:vision:analyzed с маркером skipped="true"
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


async def check_skipped_event(redis_client: redis.Redis, stream: str, post_id: str, trace_id: str, timeout: int = 30) -> dict:
    """Проверка наличия skipped события в analyzed stream."""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        messages = await redis_client.xrange(stream, count=10)
        
        for msg_id, fields in reversed(messages):
            decoded_fields = {}
            for key, value in fields.items():
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                decoded_fields[key_str] = value_str
            
            # Проверка skipped маркера
            skipped = decoded_fields.get('skipped', 'false')
            event_type = decoded_fields.get('event', '')
            
            if skipped != 'true' or event_type != 'posts.vision.skipped':
                continue
            
            # Парсинг data
            data_json = decoded_fields.get('data', '{}')
            if isinstance(data_json, bytes):
                data_json = data_json.decode('utf-8')
            
            try:
                data = json.loads(data_json)
                if data.get('post_id') == post_id and data.get('trace_id') == trace_id:
                    return {
                        'found': True,
                        'message_id': msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id),
                        'event_type': event_type,
                        'skipped': skipped,
                        'data': data,
                        'reasons': data.get('reasons', [])
                    }
            except json.JSONDecodeError:
                continue
        
        await asyncio.sleep(1)
    
    return {'found': False}


async def main():
    parser = argparse.ArgumentParser(description="Smoke Test 2: Vision Analysis Skipped (S3 Missing)")
    parser.add_argument("--s3-key", type=str, default="no/such/key.jpg", help="Missing S3 key")
    parser.add_argument("--post-id", type=str, default="p-1002", help="Post ID")
    parser.add_argument("--trace-id", type=str, default="t-1002", help="Trace ID")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    
    args = parser.parse_args()
    
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    stream_in = "stream:posts:vision"
    stream_analyzed = "stream:posts:vision:analyzed"
    
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        print("=== Smoke Test 2: Vision Analysis Skipped (S3 Missing) ===")
        print(f"Post ID: {args.post_id}")
        print(f"Trace ID: {args.trace_id}")
        print(f"S3 Key (missing): {args.s3_key}")
        print("")
        
        # 1. Создание события с отсутствующим S3 ключом
        import hashlib
        sha256 = hashlib.sha256(f"missing_{args.s3_key}".encode()).hexdigest()
        
        event = VisionUploadedEventV1(
            tenant_id=args.tenant_id,
            post_id=args.post_id,
            trace_id=args.trace_id,
            media_files=[
                MediaFile(
                    sha256=sha256,
                    s3_key=args.s3_key,
                    mime_type="image/jpeg",
                    size_bytes=0
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
        result = await check_skipped_event(redis_client, stream_analyzed, args.post_id, args.trace_id, timeout=args.timeout)
        
        if result.get('found'):
            print("✓ Skipped event found in analyzed stream")
            print(f"  Message ID: {result['message_id']}")
            print(f"  Event Type: {result['event_type']}")
            print(f"  Skipped: {result['skipped']}")
            
            reasons = result.get('reasons', [])
            if reasons:
                print(f"  Reasons ({len(reasons)}):")
                for reason in reasons:
                    reason_str = reason.get('reason', 'unknown')
                    media_id = reason.get('media_id', 'unknown')
                    print(f"    - Media {media_id}: {reason_str}")
            print("")
        else:
            print("✗ Skipped event not found in analyzed stream (timeout)")
            print("")
        
        # 4. Итоговый результат
        success = result.get('found')
        
        if success:
            # Проверка причины
            reasons = result.get('reasons', [])
            has_s3_missing = any(r.get('reason') == 's3_missing' for r in reasons)
            
            print("=== Test Result ===")
            if has_s3_missing:
                print("✓ SUCCESS: Skipped event emitted with s3_missing reason")
                return 0
            else:
                print("⚠ PARTIAL: Skipped event found, but reason is not 's3_missing'")
                print(f"  Actual reasons: {[r.get('reason') for r in reasons]}")
                return 1
        else:
            print("=== Test Result ===")
            print("✗ FAILED: Skipped event not found")
            return 1
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

