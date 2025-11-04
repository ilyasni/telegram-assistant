#!/usr/bin/env python3
"""
Запуск ingestion для проверки пайплайна альбомов
Context7: триггер парсинга каналов для тестирования
"""

import os
import sys
import asyncio
from pathlib import Path

# Добавляем пути для импорта
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "telethon-ingest"))

async def trigger_parsing():
    """Запуск парсинга через API или напрямую."""
    
    print("🚀 Запуск ingestion для тестирования пайплайна альбомов")
    print("=" * 60)
    
    # Вариант 1: Проверка, работает ли scheduler
    print("\n1️⃣ Проверка статуса telethon-ingest...")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8011/health/details") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"   ✅ telethon-ingest работает")
                    print(f"   Status: {data.get('status', 'unknown')}")
                    scheduler = data.get('scheduler', {})
                    print(f"   Scheduler status: {scheduler.get('status', 'unknown')}")
                    print(f"   Last tick: {scheduler.get('last_tick_ts', 'unknown')}")
                else:
                    print(f"   ⚠️  Health check вернул {resp.status}")
    except Exception as e:
        print(f"   ⚠️  Не удалось проверить health: {e}")
    
    # Вариант 2: Проверка логов на наличие активности парсинга
    print("\n2️⃣ Проверка логов ingestion...")
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "logs", "telegram-assistant-telethon-ingest-1", "--tail", "100"],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = result.stdout.split('\n')
        
        # Ищем признаки парсинга
        parsing_lines = [l for l in lines if 'parsing' in l.lower() or 'channel' in l.lower()][-10:]
        if parsing_lines:
            print("   Последние записи о парсинге:")
            for line in parsing_lines:
                print(f"     {line[:100]}")
        else:
            print("   ⚠️  Активности парсинга не найдено в логах")
    except Exception as e:
        print(f"   ⚠️  Не удалось проверить логи: {e}")
    
    # Вариант 3: Проверка Redis на наличие активности
    print("\n3️⃣ Проверка Redis Streams...")
    try:
        import redis.asyncio as redis
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        redis_client = redis.from_url(redis_url, decode_responses=True)
        
        # Проверка стримов постов
        posts_parsed = await redis_client.xlen("stream:posts:parsed")
        print(f"   stream:posts:parsed: {posts_parsed} сообщений")
        
        albums_parsed = await redis_client.xlen("stream:albums:parsed")
        print(f"   stream:albums:parsed: {albums_parsed} сообщений")
        
        album_assembled = await redis_client.xlen("stream:album:assembled")
        print(f"   stream:album:assembled: {album_assembled} сообщений")
        
        await redis_client.close()
    except Exception as e:
        print(f"   ⚠️  Ошибка проверки Redis: {e}")
    
    print("\n" + "=" * 60)
    print("💡 Рекомендации:")
    print("   1. Scheduler автоматически запускается в telethon-ingest")
    print("   2. Проверьте логи: docker logs telegram-assistant-telethon-ingest-1 | grep -i parsing")
    print("   3. Если парсинг не запускается, проверьте активные каналы в БД")
    print("   4. Для ручного запуска используйте: docker exec telegram-assistant-telethon-ingest-1 python -m scripts.manual_parse_channel <username>")


if __name__ == "__main__":
    asyncio.run(trigger_parsing())

