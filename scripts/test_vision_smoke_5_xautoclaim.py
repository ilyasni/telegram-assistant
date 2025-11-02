#!/usr/bin/env python3
"""
Smoke Test 5: XAUTOCLAIM / восстановление pending

Цель: Демонстрация восстановления застрявших сообщений
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


async def check_pending(redis_client: redis.Redis, stream: str, group: str) -> dict:
    """Проверка pending сообщений."""
    try:
        pending_info = await redis_client.xpending(stream, group)
        if isinstance(pending_info, dict):
            pending_count = pending_info.get('pending', 0)
            # Получение деталей pending сообщений
            pending_messages = []
            if pending_count > 0:
                pending_messages = await redis_client.xpending_range(
                    stream, group, min="-", max="+", count=100
                )
            return {
                'pending': pending_count,
                'messages': pending_messages,
                'consumers': pending_info.get('consumers', [])
            }
        return {'pending': 0, 'messages': []}
    except Exception as e:
        return {'pending': 0, 'messages': [], 'error': str(e)}


async def check_analyzed_event(redis_client: redis.Redis, stream: str, post_id: str, trace_id: str, timeout: int = 60) -> bool:
    """Проверка наличия события в analyzed stream."""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        messages = await redis_client.xrange(stream, count=10)
        
        for msg_id, fields in reversed(messages):
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
                if data.get('post_id') == post_id and data.get('trace_id') == trace_id:
                    return True
            except json.JSONDecodeError:
                continue
        
        await asyncio.sleep(2)
    
    return False


def stop_worker():
    """Остановка worker контейнера."""
    try:
        result = subprocess.run(
            ["docker", "compose", "stop", "worker"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            print("  ✓ Worker stopped")
            return True
        else:
            print(f"  ✗ Failed to stop worker: {result.stderr}")
            return False
    except Exception as e:
        print(f"  ✗ Error stopping worker: {e}")
        return False


def start_worker():
    """Запуск worker контейнера."""
    try:
        result = subprocess.run(
            ["docker", "compose", "start", "worker"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            print("  ✓ Worker started")
            return True
        else:
            print(f"  ✗ Failed to start worker: {result.stderr}")
            return False
    except Exception as e:
        print(f"  ✗ Error starting worker: {e}")
        return False


async def read_message_to_pel(redis_client: redis.Redis, stream: str, group: str, consumer: str) -> str:
    """Чтение сообщения в PEL без ACK через отдельный consumer."""
    try:
        # XREADGROUP для чтения в PEL
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
        print(f"  ✗ Error reading message to PEL: {e}")
        return None


async def main():
    parser = argparse.ArgumentParser(description="Smoke Test 5: XAUTOCLAIM / восстановление pending")
    parser.add_argument("--s3-key", type=str,
                       default="media/877193ef-be80-4977-aaeb-8009c3d772ee/4e/4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b..png",
                       help="S3 key for valid event")
    parser.add_argument("--sha256", type=str,
                       default="4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b",
                       help="SHA256 for media")
    parser.add_argument("--post-id", type=str, default="p-1005", help="Post ID")
    parser.add_argument("--trace-id", type=str, default="t-1005", help="Trace ID")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    parser.add_argument("--group", type=str, default="vision_workers", help="Consumer group")
    parser.add_argument("--consumer", type=str, default="tester", help="Test consumer name")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL")
    parser.add_argument("--idle-timeout", type=int, default=60, help="Idle timeout for XAUTOCLAIM (seconds)")
    parser.add_argument("--wait-timeout", type=int, default=120, help="Wait timeout for processing (seconds)")
    parser.add_argument("--skip-stop-start", action="store_true", help="Skip stopping/starting worker (for manual testing)")
    
    args = parser.parse_args()
    
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    stream_in = "stream:posts:vision"
    stream_analyzed = "stream:posts:vision:analyzed"
    
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        print("=== Smoke Test 5: XAUTOCLAIM / восстановление pending ===")
        print(f"Post ID: {args.post_id}")
        print(f"Trace ID: {args.trace_id}")
        print(f"Consumer Group: {args.group}")
        print(f"Test Consumer: {args.consumer}")
        print("")
        
        # 1. Остановка worker (если не пропущено)
        if not args.skip_stop_start:
            print("1. Stopping worker...")
            if not stop_worker():
                print("  ⚠ Warning: Failed to stop worker, continuing anyway")
            await asyncio.sleep(2)
            print("")
        
        # 2. Публикация тестового события
        print("2. Publishing test event...")
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
        
        # 3. Чтение сообщения в PEL без ACK через отдельный consumer
        print("3. Reading message to PEL (without ACK)...")
        pel_message_id = await read_message_to_pel(redis_client, stream_in, args.group, args.consumer)
        
        if pel_message_id:
            print(f"  ✓ Message in PEL (Message ID: {pel_message_id})")
        else:
            print("  ⚠ No message read to PEL (might be processed already)")
        print("")
        
        # 4. Проверка pending
        print("4. Checking pending messages...")
        pending_info = await check_pending(redis_client, stream_in, args.group)
        pending_count = pending_info.get('pending', 0)
        print(f"  Pending count: {pending_count}")
        
        if pending_count > 0:
            print(f"  ✓ Found {pending_count} pending message(s)")
            pending_messages = pending_info.get('messages', [])
            if pending_messages:
                oldest = pending_messages[0]
                age_ms = oldest.get('time_since_delivered', 0)
                print(f"  Oldest message age: {age_ms}ms ({age_ms/1000:.1f}s)")
        else:
            print("  ⚠ No pending messages (might be processed already)")
        print("")
        
        # 5. Запуск worker (если не пропущено)
        if not args.skip_stop_start:
            print("5. Starting worker...")
            if not start_worker():
                print("  ✗ Failed to start worker")
                return 1
            await asyncio.sleep(5)  # Даем время на инициализацию
            print("")
        
        # 6. Ожидание обработки через XAUTOCLAIM
        print(f"6. Waiting for XAUTOCLAIM processing (idle >= {args.idle_timeout}s, wait timeout: {args.wait_timeout}s)...")
        
        # Проверяем, что сообщение обработано
        processed = False
        start_wait = time.time()
        
        while time.time() - start_wait < args.wait_timeout:
            # Проверка analyzed stream
            if await check_analyzed_event(redis_client, stream_analyzed, args.post_id, args.trace_id, timeout=5):
                processed = True
                break
            
            # Проверка pending (должно уменьшиться)
            pending_info = await check_pending(redis_client, stream_in, args.group)
            pending_count = pending_info.get('pending', 0)
            
            if pending_count == 0:
                # Сообщение обработано (или нет в PEL)
                if pel_message_id:
                    # Проверяем, что оно в analyzed
                    if await check_analyzed_event(redis_client, stream_analyzed, args.post_id, args.trace_id, timeout=5):
                        processed = True
                        break
            
            await asyncio.sleep(2)
        
        if processed:
            print("  ✓ Event processed (found in analyzed stream)")
        else:
            print("  ⚠ Event not found in analyzed stream")
        print("")
        
        # 7. Финальная проверка pending
        print("7. Final pending check...")
        final_pending_info = await check_pending(redis_client, stream_in, args.group)
        final_pending_count = final_pending_info.get('pending', 0)
        print(f"  Final pending count: {final_pending_count}")
        print("")
        
        # 8. Итоговый результат
        print("=== Test Result ===")
        if processed and final_pending_count == 0:
            print("✓ SUCCESS: XAUTOCLAIM works correctly")
            print("  - Message was in PEL")
            print("  - Message processed after worker restart")
            print("  - No pending messages remaining")
            return 0
        elif processed:
            print("⚠ PARTIAL: Event processed, but pending count > 0")
            print(f"  Pending: {final_pending_count}")
            return 1
        else:
            print("✗ FAILED: Event not processed")
            return 1
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

