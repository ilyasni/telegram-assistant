#!/usr/bin/env python3
"""
Проверка очередей и логов на ошибки.

Context7 best practice: Комплексная диагностика Redis Streams,
DLQ, PEL для выявления проблемных сообщений.
"""

import asyncio
import os
import sys
import redis.asyncio as redis
import structlog

sys.path.append('/opt/telegram-assistant')

logger = structlog.get_logger()

# Список всех стримов из worker/event_bus.py
STREAMS = {
    'posts.parsed': 'stream:posts:parsed',
    'posts.tagged': 'stream:posts:tagged', 
    'posts.enriched': 'stream:posts:enriched',
    'posts.indexed': 'stream:posts:indexed',
    'posts.crawl': 'stream:posts:crawl',
    'posts.deleted': 'stream:posts:deleted',
    'posts.vision.uploaded': 'stream:posts:vision:uploaded',
    'posts.vision.analyzed': 'stream:posts:vision:analyzed',
    'albums.parsed': 'stream:albums:parsed',
    'album.assembled': 'stream:album:assembled',
    'posts.parsed.dlq': 'stream:posts:parsed:dlq',
    'posts.tagged.dlq': 'stream:posts:tagged:dlq',
    'posts.enriched.dlq': 'stream:posts:enriched:dlq',
    'posts.indexed.dlq': 'stream:posts:indexed:dlq',
    'posts.crawl.dlq': 'stream:posts:crawl:dlq',
    'posts.deleted.dlq': 'stream:posts:deleted:dlq',
    'posts.vision.analyzed.dlq': 'stream:posts:vision:analyzed:dlq',
    'albums.parsed.dlq': 'stream:albums:parsed:dlq',
    'album.assembled.dlq': 'stream:album:assembled:dlq',
}

# Consumer groups для каждого стрима
STREAM_GROUPS = {
    'stream:posts:parsed': ['post_persist_workers', 'tagging_workers'],
    'stream:posts:tagged': ['tag_persist_workers', 'enrich_workers', 'crawl_trigger_workers'],
    'stream:posts:enriched': ['enrichment_workers', 'indexing_workers'],
    'stream:posts:indexed': ['indexing_monitoring'],
    'stream:posts:crawl': ['crawl_workers'],
    'stream:posts:deleted': ['cleanup_workers'],
    'stream:posts:vision:uploaded': ['vision_workers'],
    'stream:posts:vision:analyzed': ['retagging_workers'],
    'stream:albums:parsed': ['album_workers'],
    'stream:album:assembled': ['album_workers'],
}

async def check_stream(redis_client, stream_name: str, groups: list):
    """Проверка стрима и его consumer groups."""
    results = {
        'stream': stream_name,
        'length': 0,
        'groups': {}
    }
    
    try:
        # Проверяем длину стрима
        length = await redis_client.xlen(stream_name)
        results['length'] = length
        
        if length == 0:
            return results
        
        # Проверяем каждую группу
        for group_name in groups:
            try:
                # Проверяем pending сообщения
                pending_info = await redis_client.xpending_range(
                    stream_name,
                    group_name,
                    min='-',
                    max='+',
                    count=100
                )
                
                pending_count = len(pending_info)
                
                # Считаем старые pending (старше 5 минут)
                old_pending = 0
                if pending_count > 0:
                    current_time = await redis_client.time()
                    current_timestamp = int(current_time[0]) * 1000 + int(current_time[1] // 1000)
                    
                    for msg in pending_info:
                        idle_time = current_timestamp - msg['time_since_delivered']
                        if idle_time > 300000:  # 5 минут в миллисекундах
                            old_pending += 1
                
                results['groups'][group_name] = {
                    'pending': pending_count,
                    'old_pending': old_pending,
                    'messages': pending_info[:5] if pending_count > 0 else []
                }
                
            except redis.ResponseError as e:
                if 'NOGROUP' in str(e):
                    results['groups'][group_name] = {'error': 'GROUP_NOT_FOUND'}
                else:
                    results['groups'][group_name] = {'error': str(e)}
                    
    except redis.ResponseError as e:
        if 'no such key' in str(e).lower():
            results['error'] = 'STREAM_NOT_FOUND'
        else:
            results['error'] = str(e)
    
    return results

async def check_dlq(redis_client, dlq_name: str):
    """Проверка DLQ на наличие проблемных сообщений."""
    try:
        length = await redis_client.xlen(dlq_name)
        if length > 0:
            # Получаем последние сообщения из DLQ
            messages = await redis_client.xrevrange(dlq_name, count=5)
            return {
                'length': length,
                'messages': [
                    {
                        'id': msg_id,
                        'data': {k.decode() if isinstance(k, bytes) else k: 
                                v.decode() if isinstance(v, bytes) else v 
                                for k, v in msg_data.items()}
                    }
                    for msg_id, msg_data in messages
                ]
            }
        return {'length': 0, 'messages': []}
    except redis.ResponseError:
        return {'length': 0, 'error': 'NOT_FOUND'}

async def main():
    """Основная функция проверки."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_client = await redis.from_url(redis_url, decode_responses=True)
    
    print("=" * 80)
    print("ПРОВЕРКА ОЧЕРЕДЕЙ И ОШИБОК")
    print("=" * 80)
    print()
    
    all_issues = []
    
    try:
        # Проверяем основные стримы
        print("📊 Проверка основных стримов...\n")
        for stream_name, groups in STREAM_GROUPS.items():
            result = await check_stream(redis_client, stream_name, groups)
            
            if result.get('error'):
                if result['error'] != 'STREAM_NOT_FOUND':
                    print(f"⚠️  {stream_name}: {result['error']}")
                    all_issues.append(f"{stream_name}: {result['error']}")
                continue
            
            if result['length'] > 0 or any(g.get('pending', 0) > 0 for g in result['groups'].values()):
                print(f"📨 {stream_name}:")
                print(f"   Длина стрима: {result['length']}")
                
                for group_name, group_data in result['groups'].items():
                    if isinstance(group_data, dict) and 'error' not in group_data:
                        pending = group_data.get('pending', 0)
                        old_pending = group_data.get('old_pending', 0)
                        
                        if pending > 0:
                            status = "❌" if old_pending > 0 else "⚠️"
                            print(f"   {status} Группа {group_name}: {pending} pending ({old_pending} старых)")
                            all_issues.append(f"{stream_name}/{group_name}: {pending} pending сообщений")
                            
                            if old_pending > 0:
                                print(f"      ⚠️  Есть застрявшие сообщения!")
                print()
        
        # Проверяем DLQ
        print("\n🚨 Проверка Dead Letter Queues (DLQ)...\n")
        dlq_streams = [v for k, v in STREAMS.items() if k.endswith('.dlq')]
        
        for dlq_name in dlq_streams:
            result = await check_dlq(redis_client, dlq_name)
            
            if result.get('length', 0) > 0:
                print(f"❌ {dlq_name}: {result['length']} проблемных сообщений")
                all_issues.append(f"{dlq_name}: {result['length']} сообщений в DLQ")
                
                if result.get('messages'):
                    print(f"   Последние сообщения:")
                    for msg in result['messages'][:3]:
                        print(f"   - {msg['id']}: {msg['data']}")
                print()
        
        # Итоговый отчет
        print("\n" + "=" * 80)
        print("ИТОГОВЫЙ ОТЧЕТ")
        print("=" * 80)
        
        if all_issues:
            print(f"\n❌ Обнаружено проблем: {len(all_issues)}")
            for issue in all_issues:
                print(f"   - {issue}")
            return 1
        else:
            print("\n✅ Очереди чистые, проблемных сообщений не обнаружено")
            return 0
            
    finally:
        await redis_client.aclose()

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

