#!/usr/bin/env python3
"""
Smoke Test 3: Vision Analysis Partial Success (Album / 2 Media)

Цель: Одно ОК + одно пропущено → analyzed с результатами для успешного медиа
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


async def check_analyzed_event(redis_client: redis.Redis, stream: str, post_id: str, trace_id: str, timeout: int = 60) -> dict:
    """Проверка наличия analyzed события (не skipped)."""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        messages = await redis_client.xrange(stream, count=10)
        
        for msg_id, fields in reversed(messages):
            decoded_fields = {}
            for key, value in fields.items():
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                decoded_fields[key_str] = value_str
            
            event_type = decoded_fields.get('event', '')
            skipped = decoded_fields.get('skipped', 'false')
            
            # Ищем analyzed (не skipped)
            if event_type != 'posts.vision.analyzed' or skipped == 'true':
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
                        'enrichments': data.get('enrichments', [])
                    }
            except json.JSONDecodeError:
                continue
        
        await asyncio.sleep(2)  # Больше интервал для частичного успеха
    
    return {'found': False}


async def main():
    parser = argparse.ArgumentParser(description="Smoke Test 3: Vision Analysis Partial Success")
    parser.add_argument("--valid-s3-key", type=str,
                       default="media/877193ef-be80-4977-aaeb-8009c3d772ee/4e/4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b..png",
                       help="Valid S3 key")
    parser.add_argument("--valid-sha256", type=str,
                       default="4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b",
                       help="SHA256 for valid media")
    parser.add_argument("--missing-s3-key", type=str, default="missing/media.jpg", help="Missing S3 key")
    parser.add_argument("--post-id", type=str, default="p-1003", help="Post ID")
    parser.add_argument("--trace-id", type=str, default="t-1003", help="Trace ID")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")
    
    args = parser.parse_args()
    
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    stream_in = "stream:posts:vision"
    stream_analyzed = "stream:posts:vision:analyzed"
    
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        print("=== Smoke Test 3: Vision Analysis Partial Success ===")
        print(f"Post ID: {args.post_id}")
        print(f"Trace ID: {args.trace_id}")
        print(f"Valid S3 Key: {args.valid_s3_key}")
        print(f"Missing S3 Key: {args.missing_s3_key}")
        print("")
        
        # 1. Создание события с 2 медиа
        import hashlib
        missing_sha256 = hashlib.sha256(f"missing_{args.missing_s3_key}".encode()).hexdigest()
        
        event = VisionUploadedEventV1(
            tenant_id=args.tenant_id,
            post_id=args.post_id,
            trace_id=args.trace_id,
            media_files=[
                MediaFile(
                    sha256=args.valid_sha256,
                    s3_key=args.valid_s3_key,
                    mime_type="image/png",
                    size_bytes=67
                ),
                MediaFile(
                    sha256=missing_sha256,
                    s3_key=args.missing_s3_key,
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
        print(f"  Media files: 2 (1 valid, 1 missing)")
        print("")
        
        # 3. Ожидание обработки
        print(f"Waiting for processing (timeout: {args.timeout}s)...")
        result = await check_analyzed_event(redis_client, stream_analyzed, args.post_id, args.trace_id, timeout=args.timeout)
        
        if result.get('found'):
            print("✓ Analyzed event found (not skipped)")
            print(f"  Message ID: {result['message_id']}")
            print(f"  Event Type: {result['event_type']}")
            
            enrichments = result.get('enrichments', [])
            print(f"  Enrichments: {len(enrichments)}")
            
            for i, enrichment in enumerate(enrichments):
                sha256 = enrichment.get('sha256', 'unknown')
                has_analysis = 'analysis' in enrichment
                print(f"    - Media {i+1} ({sha256[:16]}...): {'✓ analyzed' if has_analysis else '✗ skipped'}")
            print("")
        else:
            print("✗ Analyzed event not found (timeout)")
            print("")
        
        # 4. Итоговый результат
        success = result.get('found')
        
        if success:
            enrichments = result.get('enrichments', [])
            analyzed_count = sum(1 for e in enrichments if 'analysis' in e)
            skipped_count = len(enrichments) - analyzed_count
            
            print("=== Test Result ===")
            if analyzed_count >= 1 and skipped_count >= 1:
                print(f"✓ SUCCESS: Partial success detected ({analyzed_count} analyzed, {skipped_count} skipped)")
                return 0
            elif analyzed_count >= 1:
                print(f"⚠ PARTIAL: Only {analyzed_count} analyzed, expected at least 1 skipped")
                return 1
            else:
                print("✗ FAILED: No enrichments found")
                return 1
        else:
            print("=== Test Result ===")
            print("✗ FAILED: Analyzed event not found")
            return 1
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

