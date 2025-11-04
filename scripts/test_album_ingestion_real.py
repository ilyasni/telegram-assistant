#!/usr/bin/env python3
"""
Тест пайплайна альбомов на реальных данных
Context7: проверка полного цикла обработки альбомов от ingestion до assembly
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

async def check_channels():
    """Проверка активных каналов в БД."""
    print("🔍 Проверка активных каналов...")
    
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy import text
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL не установлен")
        return []
    
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session = AsyncSession(engine)
    
    try:
        result = await async_session.execute(text("""
            SELECT 
                id, 
                tg_channel_id,
                username, 
                title, 
                is_active, 
                last_parsed_at,
                created_at
            FROM channels 
            WHERE is_active = true 
            ORDER BY created_at DESC 
            LIMIT 10
        """))
        
        channels = result.fetchall()
        print(f"✅ Найдено активных каналов: {len(channels)}")
        
        for channel in channels:
            print(f"  - {channel.title or channel.username or channel.telegram_channel_id}")
            print(f"    ID: {channel.id}, Username: @{channel.username}, Last parsed: {channel.last_parsed_at}")
        
        await async_session.close()
        await engine.dispose()
        
        return [{"id": str(c.id), "username": c.username, "title": c.title} for c in channels]
        
    except Exception as e:
        print(f"❌ Ошибка при проверке каналов: {e}")
        await async_session.close()
        await engine.dispose()
        return []


async def check_recent_albums():
    """Проверка недавно обработанных альбомов."""
    print("\n📦 Проверка недавно обработанных альбомов...")
    
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy import text
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL не установлен")
        return
    
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session = AsyncSession(engine)
    
    try:
        # Проверка media_groups
        result = await async_session.execute(text("""
            SELECT 
                id,
                grouped_id,
                channel_id,
                items_count,
                caption_text,
                posted_at,
                created_at,
                (meta->>'enrichment')::text as enrichment
            FROM media_groups 
            ORDER BY created_at DESC 
            LIMIT 10
        """))
        
        albums = result.fetchall()
        print(f"✅ Найдено альбомов: {len(albums)}")
        
        for album in albums:
            print(f"\n  📸 Альбом ID: {album.id}")
            print(f"     Grouped ID: {album.grouped_id}")
            print(f"     Items: {album.items_count}")
            print(f"     Caption: {album.caption_text[:50] if album.caption_text else 'нет'}...")
            print(f"     Posted at: {album.posted_at}")
            print(f"     Created at: {album.created_at}")
            if album.enrichment:
                print(f"     ✅ Enrichment присутствует")
            else:
                print(f"     ⚠️  Enrichment отсутствует")
        
        # Проверка media_group_items
        result = await async_session.execute(text("""
            SELECT 
                COUNT(*) as total_items,
                COUNT(DISTINCT group_id) as total_groups
            FROM media_group_items
        """))
        
        stats = result.fetchone()
        if stats:
            print(f"\n📊 Статистика media_group_items:")
            print(f"   Всего элементов: {stats.total_items}")
            print(f"   Всего групп: {stats.total_groups}")
        
        await async_session.close()
        await engine.dispose()
        
    except Exception as e:
        print(f"❌ Ошибка при проверке альбомов: {e}")
        import traceback
        traceback.print_exc()
        await async_session.close()
        await engine.dispose()


async def check_redis_streams():
    """Проверка Redis Streams для альбомов."""
    print("\n🔄 Проверка Redis Streams...")
    
    import redis.asyncio as redis
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    try:
        # Проверка stream:albums:parsed
        parsed_length = await redis_client.xlen("stream:albums:parsed")
        print(f"  stream:albums:parsed: {parsed_length} сообщений")
        
        if parsed_length > 0:
            # Получаем последние 5 сообщений
            messages = await redis_client.xrevrange("stream:albums:parsed", count=5)
            print(f"    Последние сообщения: {len(messages)}")
            for msg_id, fields in messages[:3]:
                print(f"      - {msg_id}: {fields}")
        
        # Проверка stream:album:assembled
        assembled_length = await redis_client.xlen("stream:album:assembled")
        print(f"  stream:album:assembled: {assembled_length} сообщений")
        
        if assembled_length > 0:
            messages = await redis_client.xrevrange("stream:album:assembled", count=5)
            print(f"    Последние сообщения: {len(messages)}")
            for msg_id, fields in messages[:3]:
                print(f"      - {msg_id}: {fields}")
        
        # Проверка состояний альбомов в Redis
        keys = await redis_client.keys("album:state:*")
        print(f"  Активных состояний альбомов: {len(keys)}")
        
        await redis_client.close()
        
    except Exception as e:
        print(f"❌ Ошибка при проверке Redis: {e}")
        import traceback
        traceback.print_exc()


async def check_worker_metrics():
    """Проверка метрик worker."""
    print("\n📊 Проверка метрик worker...")
    
    import aiohttp
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8001/metrics") as resp:
                if resp.status == 200:
                    text = await resp.text()
                    lines = text.split('\n')
                    
                    album_metrics = [l for l in lines if 'album' in l.lower() and not l.startswith('#')]
                    
                    if album_metrics:
                        print("  Метрики альбомов:")
                        for metric in album_metrics[:20]:  # Показываем первые 20
                            print(f"    {metric}")
                    else:
                        print("  ⚠️  Метрики альбомов не найдены")
                else:
                    print(f"  ❌ Ошибка получения метрик: {resp.status}")
                    
    except Exception as e:
        print(f"  ⚠️  Не удалось получить метрики: {e}")


async def check_album_assembler_health():
    """Проверка health check album_assembler_task."""
    print("\n🏥 Проверка health check album_assembler_task...")
    
    import aiohttp
    import json
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8000/health/detailed") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tasks = data.get('tasks', {})
                    album_assembler = tasks.get('album_assembler', {})
                    
                    if album_assembler:
                        print("  ✅ album_assembler найден в health check")
                        print(f"     Status: {album_assembler.get('status', 'unknown')}")
                        print(f"     Redis connected: {album_assembler.get('redis_connected', False)}")
                        print(f"     Albums in progress: {album_assembler.get('albums_in_progress', 0)}")
                        print(f"     Backlog size: {album_assembler.get('backlog_size', 0)}")
                    else:
                        print("  ⚠️  album_assembler не найден в health check")
                else:
                    print(f"  ❌ Ошибка health check: {resp.status}")
                    
    except Exception as e:
        print(f"  ⚠️  Не удалось получить health check: {e}")


async def main():
    """Главная функция."""
    print("=" * 60)
    print("🧪 Тест пайплайна альбомов на реальных данных")
    print("=" * 60)
    
    # 1. Проверка каналов
    channels = await check_channels()
    
    if not channels:
        print("\n❌ Нет активных каналов для тестирования")
        print("   Добавьте каналы в БД или убедитесь, что is_active = true")
        return
    
    # 2. Проверка недавно обработанных альбомов
    await check_recent_albums()
    
    # 3. Проверка Redis Streams
    await check_redis_streams()
    
    # 4. Проверка метрик
    await check_worker_metrics()
    
    # 5. Проверка health check
    await check_album_assembler_health()
    
    print("\n" + "=" * 60)
    print("✅ Проверка завершена")
    print("=" * 60)
    print("\n💡 Для запуска ingestion:")
    print("   1. Убедитесь, что telethon-ingest сервис запущен")
    print("   2. Проверьте логи: docker logs telethon-ingest | grep -i 'album'")
    print("   3. Проверьте worker: docker logs worker | grep -i 'album'")
    print("   4. Мониторинг метрик: curl http://localhost:8001/metrics | grep album")


if __name__ == "__main__":
    asyncio.run(main())

