#!/usr/bin/env python3
"""
Утилита для публикации тестовых Vision событий в Redis Streams.

Context7: Использование Pydantic моделей для валидации событий, 
поддержка параллельной публикации для нагрузочных тестов.
"""

import asyncio
import argparse
import hashlib
import json
import os
import sys
from typing import List, Dict, Any
from datetime import datetime, timezone

# Добавляем путь к shared модулям
sys.path.insert(0, "/opt/telegram-assistant")
sys.path.insert(0, "/app")

import redis.asyncio as redis

try:
    from worker.events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
except ImportError:
    try:
        from events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
    except ImportError:
        print("ERROR: Cannot import VisionUploadedEventV1. Make sure you run this in the worker container.")
        sys.exit(1)


def generate_test_sha256(prefix: str = "test") -> str:
    """Генерация тестового SHA256."""
    return hashlib.sha256(f"{prefix}{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()


def extract_sha256_from_s3_key(s3_key: str) -> str:
    """Извлечение SHA256 из S3 ключа (последний компонент перед расширением)."""
    # Формат: media/{uuid}/{hex2}/{sha256}..{ext}
    parts = s3_key.split("/")
    if len(parts) >= 3:
        filename = parts[-1]
        # Убираем расширение и ".."
        if ".." in filename:
            sha256_part = filename.split("..")[0]
            if len(sha256_part) == 64:  # SHA256 = 64 hex chars
                return sha256_part
    return generate_test_sha256(s3_key)


async def create_valid_event(
    post_id: str,
    trace_id: str,
    tenant_id: str,
    s3_key: str,
    sha256: str = None,
    mime_type: str = "image/png"
) -> VisionUploadedEventV1:
    """Создание валидного Vision события."""
    if sha256 is None:
        sha256 = extract_sha256_from_s3_key(s3_key)
    
    return VisionUploadedEventV1(
        tenant_id=tenant_id,
        post_id=post_id,
        trace_id=trace_id,
        media_files=[
            MediaFile(
                sha256=sha256,
                s3_key=s3_key,
                mime_type=mime_type,
                size_bytes=67  # Тестовое значение
            )
        ],
        idempotency_key=f"{tenant_id}:{post_id}:vision_upload"
    )


async def create_missing_event(
    post_id: str,
    trace_id: str,
    tenant_id: str,
    s3_key: str = "no/such/key.jpg"
) -> VisionUploadedEventV1:
    """Создание события с отсутствующим S3 ключом."""
    sha256 = generate_test_sha256(f"missing_{s3_key}")
    
    return VisionUploadedEventV1(
        tenant_id=tenant_id,
        post_id=post_id,
        trace_id=trace_id,
        media_files=[
            MediaFile(
                sha256=sha256,
                s3_key=s3_key,
                mime_type="image/jpeg",
                size_bytes=0
            )
        ],
        idempotency_key=f"{tenant_id}:{post_id}:vision_upload"
    )


async def create_poison_event(
    post_id: str,
    trace_id: str,
    tenant_id: str,
    s3_key: str = None
) -> VisionUploadedEventV1:
    """Создание 'poison' события с некорректным payload (для DLQ тестов)."""
    if s3_key is None:
        s3_key = f"poison/{post_id}/test.jpg"
    
    sha256 = generate_test_sha256(f"poison_{post_id}")
    
    # Используем валидную структуру, но с заведомо проблемным ключом
    # (poison-паттерн будет проверяться через mock-флаг или некорректные данные)
    return VisionUploadedEventV1(
        tenant_id=tenant_id,
        post_id=post_id,
        trace_id=trace_id,
        media_files=[
            MediaFile(
                sha256=sha256,
                s3_key=s3_key,
                mime_type="image/jpeg",
                size_bytes=0
            )
        ],
        idempotency_key=f"{tenant_id}:{post_id}:vision_upload:poison"
    )


async def publish_event(redis_client: redis.Redis, event: VisionUploadedEventV1, stream: str = "stream:posts:vision") -> str:
    """Публикация события в Redis Stream."""
    event_dict = event.model_dump(mode='json', exclude_none=True)
    event_json = json.dumps(event_dict, default=str)
    
    message_id = await redis_client.xadd(
        stream,
        {
            "event": "posts.vision.uploaded",
            "data": event_json
        }
    )
    
    return message_id


async def publish_events_batch(
    redis_client: redis.Redis,
    events: List[VisionUploadedEventV1],
    stream: str = "stream:posts:vision",
    parallel: bool = False
) -> List[str]:
    """Публикация батча событий."""
    if parallel:
        tasks = [publish_event(redis_client, event, stream) for event in events]
        message_ids = await asyncio.gather(*tasks)
    else:
        message_ids = []
        for event in events:
            msg_id = await publish_event(redis_client, event, stream)
            message_ids.append(msg_id)
            await asyncio.sleep(0.1)  # Небольшая задержка для последовательной публикации
    
    return message_ids


async def generate_mixed_events(
    count: int,
    tenant_id: str,
    post_id_prefix: str,
    valid_s3_key: str,
    valid_sha256: str = None
) -> List[VisionUploadedEventV1]:
    """Генерация смешанных событий: 60% valid, 30% missing, 10% poison."""
    events = []
    valid_count = int(count * 0.6)
    missing_count = int(count * 0.3)
    poison_count = count - valid_count - missing_count
    
    counter = 0
    
    # Valid события
    for i in range(valid_count):
        counter += 1
        post_id = f"{post_id_prefix}-{counter:04d}"
        trace_id = f"t-test-{counter:04d}"
        event = await create_valid_event(
            post_id=post_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            s3_key=valid_s3_key,
            sha256=valid_sha256
        )
        events.append(event)
    
    # Missing события
    for i in range(missing_count):
        counter += 1
        post_id = f"{post_id_prefix}-{counter:04d}"
        trace_id = f"t-test-{counter:04d}"
        event = await create_missing_event(
            post_id=post_id,
            trace_id=trace_id,
            tenant_id=tenant_id
        )
        events.append(event)
    
    # Poison события
    for i in range(poison_count):
        counter += 1
        post_id = f"{post_id_prefix}-{counter:04d}"
        trace_id = f"t-test-{counter:04d}"
        event = await create_poison_event(
            post_id=post_id,
            trace_id=trace_id,
            tenant_id=tenant_id
        )
        events.append(event)
    
    return events


async def main():
    parser = argparse.ArgumentParser(description="Publish test Vision events to Redis Streams")
    parser.add_argument("--count", type=int, default=1, help="Number of events (default: 1)")
    parser.add_argument("--type", choices=["valid", "missing", "poison", "mixed"], default="valid",
                       help="Event type (default: valid)")
    parser.add_argument("--s3-key", type=str, 
                       default="media/877193ef-be80-4977-aaeb-8009c3d772ee/4e/4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b..png",
                       help="S3 key for valid events")
    parser.add_argument("--sha256", type=str, default=None,
                       help="SHA256 for media (default: extract from s3-key or generate)")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID (default: default)")
    parser.add_argument("--post-id-prefix", type=str, default="p-test", help="Post ID prefix (default: p-test)")
    parser.add_argument("--parallel", action="store_true", help="Publish events in parallel")
    parser.add_argument("--redis-url", type=str, default=None,
                       help="Redis URL (default: from ENV or redis://redis:6379/0)")
    parser.add_argument("--stream", type=str, default="stream:posts:vision", help="Redis stream name")
    
    args = parser.parse_args()
    
    # Redis URL
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    
    # Подключение к Redis
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        # Генерация событий
        events = []
        
        if args.type == "valid":
            for i in range(args.count):
                post_id = f"{args.post_id_prefix}-{i+1:04d}"
                trace_id = f"t-test-{i+1:04d}"
                event = await create_valid_event(
                    post_id=post_id,
                    trace_id=trace_id,
                    tenant_id=args.tenant_id,
                    s3_key=args.s3_key,
                    sha256=args.sha256
                )
                events.append(event)
        
        elif args.type == "missing":
            for i in range(args.count):
                post_id = f"{args.post_id_prefix}-{i+1:04d}"
                trace_id = f"t-test-{i+1:04d}"
                event = await create_missing_event(
                    post_id=post_id,
                    trace_id=trace_id,
                    tenant_id=args.tenant_id
                )
                events.append(event)
        
        elif args.type == "poison":
            for i in range(args.count):
                post_id = f"{args.post_id_prefix}-{i+1:04d}"
                trace_id = f"t-test-{i+1:04d}"
                event = await create_poison_event(
                    post_id=post_id,
                    trace_id=trace_id,
                    tenant_id=args.tenant_id
                )
                events.append(event)
        
        elif args.type == "mixed":
            events = await generate_mixed_events(
                count=args.count,
                tenant_id=args.tenant_id,
                post_id_prefix=args.post_id_prefix,
                valid_s3_key=args.s3_key,
                valid_sha256=args.sha256
            )
        
        # Публикация
        print(f"Publishing {len(events)} events ({args.type}) to {args.stream}...")
        message_ids = await publish_events_batch(
            redis_client,
            events,
            stream=args.stream,
            parallel=args.parallel
        )
        
        print(f"✓ Published {len(message_ids)} events")
        print(f"  First message_id: {message_ids[0] if message_ids else 'N/A'}")
        print(f"  Last message_id: {message_ids[-1] if message_ids else 'N/A'}")
        
        if args.type == "mixed":
            print(f"  Breakdown: {int(len(events) * 0.6)} valid, {int(len(events) * 0.3)} missing, {len(events) - int(len(events) * 0.6) - int(len(events) * 0.3)} poison")
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

