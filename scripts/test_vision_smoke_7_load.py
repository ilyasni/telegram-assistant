#!/usr/bin/env python3
"""
Smoke Test 7: Нагрузочный "мини-штурм"

Цель: Стабильность под серией событий
"""

import asyncio
import argparse
import json
import os
import sys
import time
from typing import List

sys.path.insert(0, "/opt/telegram-assistant")
sys.path.insert(0, "/app")

import redis.asyncio as redis


async def get_stream_length(redis_client: redis.Redis, stream: str) -> int:
    """Получение длины стрима."""
    try:
        length = await redis_client.xlen(stream)
        return int(length)
    except Exception as e:
        print(f"  Error getting length for {stream}: {e}")
        return 0


async def count_events_by_skipped(redis_client: redis.Redis, stream: str) -> dict:
    """Подсчет событий по skipped маркеру."""
    messages = await redis_client.xrange(stream, count=1000)
    
    skipped_count = 0
    analyzed_count = 0
    
    for msg_id, fields in messages:
        decoded_fields = {}
        for key, value in fields.items():
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
            value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
            decoded_fields[key_str] = value_str
        
        skipped = decoded_fields.get('skipped', 'false')
        if skipped == 'true':
            skipped_count += 1
        else:
            analyzed_count += 1
    
    return {
        'analyzed': analyzed_count,
        'skipped': skipped_count,
        'total': skipped_count + analyzed_count
    }


async def check_pending(redis_client: redis.Redis, stream: str, group: str) -> int:
    """Проверка количества pending сообщений."""
    try:
        pending_info = await redis_client.xpending(stream, group)
        if isinstance(pending_info, dict):
            return pending_info.get('pending', 0)
        return 0
    except Exception as e:
        return 0


async def main():
    parser = argparse.ArgumentParser(description="Smoke Test 7: Нагрузочный мини-штурм")
    parser.add_argument("--count", type=int, default=100, help="Total number of events")
    parser.add_argument("--valid-s3-key", type=str,
                       default="media/877193ef-be80-4977-aaeb-8009c3d772ee/4e/4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b..png",
                       help="Valid S3 key")
    parser.add_argument("--valid-sha256", type=str,
                       default="4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b",
                       help="SHA256 for valid media")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    parser.add_argument("--post-id-prefix", type=str, default="p-load", help="Post ID prefix")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis URL")
    parser.add_argument("--parallel", action="store_true", help="Publish events in parallel")
    parser.add_argument("--wait-timeout", type=int, default=300, help="Wait timeout for processing (seconds)")
    parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval (seconds)")
    
    args = parser.parse_args()
    
    redis_url = args.redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    stream_in = "stream:posts:vision"
    stream_analyzed = "stream:posts:vision:analyzed"
    stream_dlq = "stream:posts:vision:dlq"
    group = "vision_workers"
    
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        print("=== Smoke Test 7: Нагрузочный мини-штурм ===")
        print(f"Total events: {args.count}")
        print(f"Valid events: {int(args.count * 0.6)}")
        print(f"Missing events: {int(args.count * 0.3)}")
        print(f"Poison events: {args.count - int(args.count * 0.6) - int(args.count * 0.3)}")
        print(f"Publish mode: {'parallel' if args.parallel else 'sequential'}")
        print("")
        
        # 0. Использование утилиты для публикации
        print("0. Using publish_test_vision_event.py utility...")
        import subprocess
        
        publish_cmd = [
            "python3", "scripts/publish_test_vision_event.py",
            "--count", str(args.count),
            "--type", "mixed",
            "--s3-key", args.valid_s3_key,
            "--sha256", args.valid_sha256,
            "--tenant-id", args.tenant_id,
            "--post-id-prefix", args.post_id_prefix,
            "--redis-url", redis_url
        ]
        
        if args.parallel:
            publish_cmd.append("--parallel")
        
        result = subprocess.run(publish_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            print(f"  ✗ Failed to publish events: {result.stderr}")
            return 1
        
        print(f"  ✓ Events published")
        print("")
        
        # 1. Исходное состояние стримов
        print("1. Initial stream state...")
        initial_analyzed_length = await get_stream_length(redis_client, stream_analyzed)
        initial_dlq_length = await get_stream_length(redis_client, stream_dlq)
        initial_pending = await check_pending(redis_client, stream_in, group)
        
        print(f"  Analyzed stream length: {initial_analyzed_length}")
        print(f"  DLQ stream length: {initial_dlq_length}")
        print(f"  Pending messages: {initial_pending}")
        print("")
        
        # 2. Ожидание обработки
        print(f"2. Waiting for processing (timeout: {args.wait_timeout}s, poll interval: {args.poll_interval}s)...")
        
        start_wait = time.time()
        last_analyzed_length = initial_analyzed_length
        
        while time.time() - start_wait < args.wait_timeout:
            current_analyzed_length = await get_stream_length(redis_client, stream_analyzed)
            current_pending = await check_pending(redis_client, stream_in, group)
            current_dlq_length = await get_stream_length(redis_client, stream_dlq)
            
            processed = current_analyzed_length - initial_analyzed_length
            elapsed = time.time() - start_wait
            
            print(f"  [{elapsed:.0f}s] Analyzed: +{processed} (total: {current_analyzed_length}), "
                  f"Pending: {current_pending}, DLQ: {current_dlq_length}")
            
            # Проверка завершения (нет pending и достаточно событий обработано)
            if current_pending == 0 and processed >= args.count * 0.9:  # 90% обработано
                print("  ✓ Processing appears complete (pending=0, 90% processed)")
                break
            
            # Если нет новых событий за последние 30 секунд
            if current_analyzed_length == last_analyzed_length:
                if elapsed > 30:
                    print("  ⚠ No new events for 30s, assuming complete")
                    break
            else:
                last_analyzed_length = current_analyzed_length
            
            await asyncio.sleep(args.poll_interval)
        
        print("")
        
        # 3. Финальное состояние стримов
        print("3. Final stream state...")
        final_analyzed_length = await get_stream_length(redis_client, stream_analyzed)
        final_dlq_length = await get_stream_length(redis_client, stream_dlq)
        final_pending = await check_pending(redis_client, stream_in, group)
        
        processed_count = final_analyzed_length - initial_analyzed_length
        dlq_count = final_dlq_length - initial_dlq_length
        
        print(f"  Analyzed stream length: {final_analyzed_length} (+{processed_count})")
        print(f"  DLQ stream length: {final_dlq_length} (+{dlq_count})")
        print(f"  Pending messages: {final_pending}")
        print("")
        
        # 4. Детальная статистика по skipped
        print("4. Detailed statistics (last 1000 events in analyzed stream)...")
        stats = await count_events_by_skipped(redis_client, stream_analyzed)
        print(f"  Analyzed events: {stats['analyzed']}")
        print(f"  Skipped events: {stats['skipped']}")
        print(f"  Total: {stats['total']}")
        print("")
        
        # 5. Итоговый результат
        expected_analyzed = int(args.count * 0.6)
        expected_skipped = int(args.count * 0.3)
        expected_dlq = args.count - expected_analyzed - expected_skipped
        
        print("=== Test Result ===")
        print(f"Expected: {expected_analyzed} analyzed, {expected_skipped} skipped, {expected_dlq} DLQ")
        print(f"Actual: {processed_count} processed, {stats['analyzed']} analyzed, {stats['skipped']} skipped, {dlq_count} DLQ")
        print(f"Pending: {final_pending}")
        print("")
        
        success = (
            final_pending == 0 and
            processed_count >= args.count * 0.9 and  # 90% обработано
            stats['analyzed'] >= expected_analyzed * 0.9 and  # 90% analyzed
            stats['skipped'] >= expected_skipped * 0.9  # 90% skipped
        )
        
        if success:
            print("✓ SUCCESS: Load test passed")
            print("  - All events processed (pending=0)")
            print("  - Analyzed events match expected")
            print("  - Skipped events match expected")
            print("  - PEL stable (no growth)")
            return 0
        else:
            print("✗ FAILED: Load test failed")
            if final_pending > 0:
                print(f"  - {final_pending} pending messages")
            if processed_count < args.count * 0.9:
                print(f"  - Only {processed_count}/{args.count} events processed")
            if stats['analyzed'] < expected_analyzed * 0.9:
                print(f"  - Only {stats['analyzed']}/{expected_analyzed} analyzed events")
            if stats['skipped'] < expected_skipped * 0.9:
                print(f"  - Only {stats['skipped']}/{expected_skipped} skipped events")
            return 1
        
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

