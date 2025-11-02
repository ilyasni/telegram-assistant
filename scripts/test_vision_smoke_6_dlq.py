#!/usr/bin/env python3
"""
Smoke Test 6: DLQ / ядовитое сообщение

Цель: Проверка лимита доставок и корректного дренажа в DLQ
"""

import asyncio
import argparse
import json
import os
import sys
import time
import subprocess

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


async def check_dlq_stream(redis_client: redis.Redis, dlq_stream: str, post_id: str = None, timeout: int = 30) -> list:
    """Проверка событий в DLQ stream."""
    messages = await redis_client.xrange(dlq_stream, count=100)
    
    dlq_events = []
    for msg_id, fields in messages:
        decoded_fields = {}
        for key, value in fields.items():
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
            value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
            decoded_fields[key_str] = value_str
        
        # Если указан post_id, фильтруем
        if post_id:
            data_json = decoded_fields.get('data', '{}')
            if isinstance(data_json, bytes):
                data_json = data_json.decode('utf-8')
            try:
                data = json.loads(data_json)
                if data.get('post_id') != post_id:
                    continue
            except json.JSONDecodeError:
                continue
        
        dlq_events.append({
            'message_id': msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id),
            'fields': decoded_fields
        })
    
    return dlq_events


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


async def read_message_to_pel(redis_client: redis.Redis, stream: str, group: str, consumer: str) -> str:
    """Чтение сообщения в PEL без ACK."""
    try:
        messages = await redis_client.xreadgroup(
            group, consumer, {stream: ">"}, count=1, block=1000
        )
        
        if messages:
            for stream_name, stream_messages in messages:
                if stream_messages:
                    message_id, fields = stream_messages[0]
                    return message_id.decode('utf-8') if isinstance(message_id, bytes) else str(message_id)
        return None
    except Exception as e:
        return None


async def check_delivery_count(redis_client: redis.Redis, message_id: str) -> int:
    """Проверка delivery_count для сообщения."""
    key = f"vision:deliveries:{message_id}"
    value = await redis_client.get(key)
    if value:
        return int(value.decode('utf-8') if isinstance(value, bytes) else value)
    return 0


async def main():
    parser = argparse.ArgumentParser(description="Smoke Test 6: DLQ / ядовитое сообщение")
    parser.add_argument("--s3-key", type=str, default="no/such/key.jpg", help="Missing S3 key (for poison)")
    parser.add_argument("--post-id", type=str, default="p-1006", help="Post ID")
    parser.add_argument("--trace-id", type=str, default="t-1006", help="Trace ID")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    parser.add_argument("--group", type=str, default="vision_workers", help="Consumer group")
    parser.add_argument("--consumer", type=str, default="tester", help="Test consumer name")
    parser.add_argument("--max-deliveries", type=int, default=1, help="MAX_DELIVERIES for test")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL")
    parser.add_argument("--wait-timeout", type=int, default=120, help="Wait timeout for processing (seconds)")
    parser.add_argument("--skip-stop-start", action="store_true", help="Skip stopping/starting worker")
    
    args = parser.parse_args()
    
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    stream_in = "stream:posts:vision"
    stream_dlq = "stream:posts:vision:dlq"
    
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        print("=== Smoke Test 6: DLQ / ядовитое сообщение ===")
        print(f"Post ID: {args.post_id}")
        print(f"Trace ID: {args.trace_id}")
        print(f"MAX_DELIVERIES: {args.max_deliveries}")
        print("")
        
        # Важно: Для этого теста требуется установить VISION_MAX_DELIVERIES=1 в worker
        print("⚠ NOTE: This test requires VISION_MAX_DELIVERIES=1 in worker environment")
        print("   You may need to restart worker with this env var set")
        print("")
        
        # 1. Проверка начального состояния DLQ
        print("1. Checking initial DLQ state...")
        initial_dlq = await check_dlq_stream(redis_client, stream_dlq, args.post_id)
        initial_dlq_count = len(initial_dlq)
        print(f"  Initial DLQ events for post_id: {initial_dlq_count}")
        print("")
        
        # 2. Остановка worker (если не пропущено)
        if not args.skip_stop_start:
            print("2. Stopping worker...")
            try:
                subprocess.run(["docker", "compose", "stop", "worker"], capture_output=True, timeout=30)
                print("  ✓ Worker stopped")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"  ⚠ Warning: {e}")
            print("")
        
        # 3. Публикация тестового события (с заведомо проблемным ключом)
        print("3. Publishing test event (poison)...")
        import hashlib
        sha256 = hashlib.sha256(f"poison_{args.post_id}".encode()).hexdigest()
        
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
            idempotency_key=f"{args.tenant_id}:{args.post_id}:vision_upload:poison"
        )
        
        event_dict = event.model_dump(mode='json', exclude_none=True)
        event_json = json.dumps(event_dict, default=str)
        
        message_id = await redis_client.xadd(
            stream_in,
            {
                "event": "posts.vision.uploaded",
                "data": event_json
            }
        )
        
        msg_id_str = message_id.decode('utf-8') if isinstance(message_id, bytes) else str(message_id)
        print(f"  ✓ Event published (Message ID: {msg_id_str})")
        print("")
        
        # 4. Чтение сообщения в PEL несколько раз (для превышения MAX_DELIVERIES)
        print(f"4. Reading message to PEL {args.max_deliveries + 1} times (to exceed MAX_DELIVERIES)...")
        
        for attempt in range(args.max_deliveries + 1):
            pel_message_id = await read_message_to_pel(redis_client, stream_in, args.group, args.consumer)
            if pel_message_id:
                delivery_count = await check_delivery_count(redis_client, pel_message_id)
                print(f"  Attempt {attempt + 1}: Message in PEL, delivery_count={delivery_count}")
                
                # Симулируем обработку (не ACK, чтобы оставить в PEL)
                if attempt < args.max_deliveries:
                    await asyncio.sleep(1)
            else:
                print(f"  Attempt {attempt + 1}: No message in PEL")
        
        print("")
        
        # 5. Запуск worker (если не пропущено)
        if not args.skip_stop_start:
            print("5. Starting worker...")
            try:
                subprocess.run(["docker", "compose", "start", "worker"], capture_output=True, timeout=30)
                print("  ✓ Worker started")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"  ⚠ Warning: {e}")
            print("")
        
        # 6. Ожидание обработки и проверка DLQ
        print(f"6. Waiting for DLQ processing (timeout: {args.wait_timeout}s)...")
        
        dlq_found = False
        start_wait = time.time()
        
        while time.time() - start_wait < args.wait_timeout:
            dlq_events = await check_dlq_stream(redis_client, stream_dlq, args.post_id)
            if len(dlq_events) > initial_dlq_count:
                dlq_found = True
                break
            await asyncio.sleep(2)
        
        if dlq_found:
            print("  ✓ Event found in DLQ")
            new_dlq_events = dlq_events[initial_dlq_count:]
            for dlq_event in new_dlq_events:
                fields = dlq_event['fields']
                error = fields.get('error', 'N/A')
                delivery_count = fields.get('delivery_count', 'N/A')
                max_deliveries = fields.get('max_deliveries', 'N/A')
                print(f"    Message ID: {dlq_event['message_id']}")
                print(f"    Error: {error[:100]}")
                print(f"    Delivery Count: {delivery_count}")
                print(f"    Max Deliveries: {max_deliveries}")
        else:
            print("  ⚠ Event not found in DLQ")
        print("")
        
        # 7. Проверка pending (должно быть 0 после XACK)
        print("7. Checking pending messages (should be 0 after XACK)...")
        pending_info = await check_pending(redis_client, stream_in, args.group)
        pending_count = pending_info.get('pending', 0)
        print(f"  Pending count: {pending_count}")
        print("")
        
        # 8. Итоговый результат
        print("=== Test Result ===")
        if dlq_found and pending_count == 0:
            print("✓ SUCCESS: DLQ works correctly")
            print("  - Event exceeded MAX_DELIVERIES")
            print("  - Event sent to DLQ")
            print("  - Original message XACK'ed (no pending)")
            return 0
        elif dlq_found:
            print("⚠ PARTIAL: Event in DLQ, but pending count > 0")
            print(f"  Pending: {pending_count}")
            return 1
        else:
            print("✗ FAILED: Event not found in DLQ")
            print("  Check VISION_MAX_DELIVERIES environment variable in worker")
            return 1
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

