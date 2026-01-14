#!/usr/bin/env python3
"""
Скрипт для безопасной очистки старых ошибок из DLQ.

Context7 best practice: Очистка старых ошибок с фильтрацией по дате и типу.
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

import redis.asyncio as redis
import structlog

sys.path.append('/opt/telegram-assistant')

logger = structlog.get_logger()

# DLQ стримы из event_bus.py
DLQ_STREAMS = [
    'stream:posts:parsed:dlq',
    'stream:posts:tagged:dlq',
    'stream:posts:enriched:dlq',
    'stream:posts:indexed:dlq',
    'stream:posts:crawl:dlq',
    'stream:posts:deleted:dlq',
    'stream:posts:vision:analyzed:dlq',
    'stream:albums:parsed:dlq',
    'stream:album:assembled:dlq',
]


async def parse_message_data(message_data: List) -> Optional[Dict[str, Any]]:
    """Парсинг сообщения из Redis Stream."""
    if not message_data or len(message_data) < 2:
        return None
    
    # Формат: [field1, value1, field2, value2, ...]
    data = {}
    for i in range(0, len(message_data), 2):
        key = message_data[i]
        value = message_data[i + 1] if i + 1 < len(message_data) else None
        
        if isinstance(key, bytes):
            key = key.decode('utf-8')
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        
        data[key] = value
    
    # Попытка парсинга JSON из поля 'data'
    if 'data' in data:
        try:
            json_data = json.loads(data['data'])
            data.update(json_data)
        except (json.JSONDecodeError, TypeError):
            pass
    
    return data


async def analyze_dlq_stream(
    redis_client: redis.Redis,
    stream_name: str,
    min_age_days: int = 60
) -> Dict[str, Any]:
    """Анализ DLQ стрима."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    cutoff_timestamp = cutoff_date.isoformat()
    
    length = await redis_client.xlen(stream_name)
    
    if length == 0:
        return {
            'stream': stream_name,
            'total': 0,
            'old_count': 0,
            'old_ids': [],
            'errors': {}
        }
    
    # Читаем все сообщения (для анализа)
    messages = await redis_client.xrange(stream_name, min='-', max='+', count=10000)
    
    old_ids = []
    error_types = {}
    old_count = 0
    
    for msg_id, msg_data in messages:
        data = await parse_message_data(msg_data)
        
        if not data:
            continue
        
        # Определяем timestamp сообщения
        msg_timestamp = None
        
        # Пробуем разные поля для timestamp
        for field in ['dlq_timestamp', 'occurred_at', 'timestamp', 'created_at']:
            if field in data:
                try:
                    msg_timestamp = datetime.fromisoformat(data[field].replace('Z', '+00:00'))
                    break
                except (ValueError, AttributeError, TypeError):
                    continue
        
        # Если timestamp не найден, пропускаем (не удаляем неизвестные)
        if not msg_timestamp:
            continue
        
        # Проверяем возраст
        if msg_timestamp < cutoff_date:
            old_ids.append(msg_id)
            old_count += 1
            
            # Собираем статистику по типам ошибок
            error_type = data.get('error_type', 'unknown')
            dlq_reason = data.get('dlq_reason', 'unknown')
            key = f"{error_type}:{dlq_reason}"
            error_types[key] = error_types.get(key, 0) + 1
    
    return {
        'stream': stream_name,
        'total': length,
        'old_count': old_count,
        'old_ids': old_ids,
        'errors': error_types,
        'cutoff_date': cutoff_timestamp
    }


async def cleanup_dlq_stream(
    redis_client: redis.Redis,
    stream_name: str,
    message_ids: List[bytes],
    dry_run: bool = True
) -> int:
    """Очистка сообщений из DLQ стрима."""
    if not message_ids:
        return 0
    
    if dry_run:
        logger.info("DRY-RUN: Would delete messages", 
                   stream=stream_name, 
                   count=len(message_ids))
        return len(message_ids)
    
    # Удаляем сообщения по ID
    deleted_count = 0
    for msg_id in message_ids:
        try:
            await redis_client.xdel(stream_name, msg_id)
            deleted_count += 1
        except Exception as e:
            logger.warning("Failed to delete message", 
                         stream=stream_name, 
                         msg_id=msg_id,
                         error=str(e))
    
    return deleted_count


async def main():
    """Основная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Очистка старых ошибок из DLQ')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Режим проверки без удаления')
    parser.add_argument('--min-age-days', type=int, default=60,
                       help='Минимальный возраст ошибок для удаления (дни, по умолчанию 60)')
    parser.add_argument('--stream', type=str, 
                       help='Обработать только указанный стрим (по умолчанию все)')
    parser.add_argument('--redis-url', type=str,
                       default=os.getenv('REDIS_URL', 'redis://redis:6379'),
                       help='URL Redis')
    
    args = parser.parse_args()
    
    redis_client = redis.from_url(args.redis_url, decode_responses=False)
    
    try:
        await redis_client.ping()
        logger.info("Connected to Redis")
    except Exception as e:
        logger.error("Failed to connect to Redis", error=str(e))
        sys.exit(1)
    
    streams_to_process = [args.stream] if args.stream else DLQ_STREAMS
    
    print("=" * 60)
    print("АНАЛИЗ И ОЧИСТКА DLQ")
    print("=" * 60)
    print(f"Режим: {'DRY-RUN (без удаления)' if args.dry_run else 'РЕАЛЬНОЕ УДАЛЕНИЕ'}")
    print(f"Минимальный возраст: {args.min_age_days} дней")
    print(f"Обрабатываем стримов: {len(streams_to_process)}")
    print("=" * 60)
    print()
    
    total_old = 0
    total_deleted = 0
    
    for stream_name in streams_to_process:
        print(f"\n📊 Анализ: {stream_name}")
        
        try:
            analysis = await analyze_dlq_stream(
                redis_client, 
                stream_name, 
                min_age_days=args.min_age_days
            )
            
            print(f"  Всего сообщений: {analysis['total']}")
            print(f"  Старых (> {args.min_age_days} дней): {analysis['old_count']}")
            
            if analysis['errors']:
                print(f"  Типы ошибок:")
                for error_key, count in sorted(analysis['errors'].items(), key=lambda x: x[1], reverse=True)[:5]:
                    print(f"    - {error_key}: {count}")
            
            if analysis['old_count'] > 0:
                total_old += analysis['old_count']
                
                if not args.dry_run:
                    deleted = await cleanup_dlq_stream(
                        redis_client,
                        stream_name,
                        analysis['old_ids'],
                        dry_run=False
                    )
                    total_deleted += deleted
                    print(f"  ✅ Удалено: {deleted}")
                else:
                    print(f"  🔍 Будет удалено: {analysis['old_count']} (DRY-RUN)")
            else:
                print(f"  ✅ Нет старых ошибок для удаления")
                
        except Exception as e:
            logger.error("Failed to process stream", 
                        stream=stream_name, 
                        error=str(e), 
                        exc_info=True)
            print(f"  ❌ Ошибка: {e}")
    
    print()
    print("=" * 60)
    print("ИТОГИ")
    print("=" * 60)
    print(f"Найдено старых ошибок: {total_old}")
    if not args.dry_run:
        print(f"Удалено: {total_deleted}")
    else:
        print(f"Будет удалено: {total_old} (DRY-RUN)")
    print("=" * 60)
    
    await redis_client.close()


if __name__ == '__main__':
    asyncio.run(main())
