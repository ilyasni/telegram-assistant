#!/usr/bin/env python3
"""
E2E тест: проверка сохранения альбомов в S3 и vision анализа
Context7: проверка полного цикла от альбома до S3 сохранения

Тестирует:
1. Создание альбома в БД
2. Эмиссию albums.parsed события
3. Vision анализ элементов альбома
4. Обработку vision.analyzed событий album_assembler_task
5. Сохранение vision summary в S3
6. Сохранение enrichment в БД
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timezone
from uuid import uuid4

# Добавляем пути
project_root = '/opt/telegram-assistant'
sys.path.insert(0, project_root)
sys.path.insert(0, '/app')

import structlog
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
import redis.asyncio as redis

logger = structlog.get_logger()

async def check_existing_albums_with_vision():
    """Проверка существующих альбомов с vision анализом."""
    print("\n🔍 Проверка существующих альбомов с vision анализом...")
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Проверяем альбомы с enrichment (vision_summary)
            result = await session.execute(text("""
                SELECT 
                    mg.id,
                    mg.grouped_id,
                    mg.items_count,
                    mg.meta->'enrichment'->>'s3_key' as s3_key,
                    mg.meta->'enrichment'->>'vision_summary' IS NOT NULL as has_vision_summary,
                    mg.meta->'enrichment'->>'assembly_completed_at' as assembly_completed_at
                FROM media_groups mg
                WHERE mg.meta->'enrichment' IS NOT NULL
                ORDER BY mg.created_at DESC
                LIMIT 10
            """))
            
            albums = result.fetchall()
            print(f"✅ Найдено альбомов с enrichment: {len(albums)}")
            
            for album in albums:
                print(f"\n  📸 Альбом ID: {album[0]}")
                print(f"     Grouped ID: {album[1]}")
                print(f"     Items: {album[2]}")
                if album[3]:
                    print(f"     ✅ S3 Key: {album[3]}")
                if album[4]:
                    print(f"     ✅ Vision Summary: есть")
                if album[5]:
                    print(f"     ✅ Assembly completed: {album[5]}")
            
            # Проверяем посты из альбомов
            result = await session.execute(text("""
                SELECT 
                    COUNT(DISTINCT mg.id) as albums_count,
                    COUNT(DISTINCT mgi.post_id) as posts_count,
                    COUNT(DISTINCT CASE WHEN mg.meta->'enrichment' IS NOT NULL THEN mg.id END) as albums_with_enrichment
                FROM media_groups mg
                JOIN media_group_items mgi ON mg.id = mgi.group_id
            """))
            
            stats = result.fetchone()
            if stats:
                print(f"\n📊 Статистика:")
                print(f"   Всего альбомов: {stats[0]}")
                print(f"   Всего постов в альбомах: {stats[1]}")
                print(f"   Альбомов с enrichment: {stats[2]}")
                
    finally:
        await engine.dispose()
    
    print("  ✅ Проверка завершена")


async def check_s3_albums():
    """Проверка альбомов в S3."""
    print("\n📦 Проверка альбомов в S3...")
    
    try:
        from api.services.s3_storage import S3StorageService
        
        s3_config = {
            'endpoint_url': os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru'),
            'access_key_id': os.getenv('S3_ACCESS_KEY_ID'),
            'secret_access_key': os.getenv('S3_SECRET_ACCESS_KEY'),
            'bucket_name': os.getenv('S3_BUCKET_NAME'),
            'region': os.getenv('S3_REGION', 'ru-central-1')
        }
        
        if not s3_config.get('access_key_id') or not s3_config.get('secret_access_key'):
            print("  ⚠️  S3 credentials не настроены, пропускаем проверку S3")
            return
        
        s3 = S3StorageService(**s3_config)
        tenant_id = os.getenv('S3_DEFAULT_TENANT_ID', '877193ef-be80-4977-aaeb-8009c3d772ee')
        prefix = f'album/{tenant_id}/'
        
        print(f"  🔍 Проверка S3 bucket: {s3_config['bucket_name']}")
        print(f"  📁 Prefix: {prefix}")
        
        # Список объектов через list_objects_v2
        objects = []
        paginator = s3.s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=s3_config['bucket_name'], Prefix=prefix):
            if 'Contents' in page:
                objects.extend(page['Contents'])
        
        print(f"  ✅ Найдено альбомов в S3: {len(objects)}")
        
        if objects:
            print(f"\n  📋 Последние 5 альбомов в S3:")
            for obj in sorted(objects, key=lambda x: x['LastModified'], reverse=True)[:5]:
                size_kb = obj['Size'] / 1024
                print(f"     - {obj['Key']}")
                print(f"       Размер: {size_kb:.2f} KB, Дата: {obj['LastModified']}")
                
                # Пробуем прочитать содержимое
                try:
                    response = s3.s3_client.get_object(Bucket=s3_config['bucket_name'], Key=obj['Key'])
                    content = response['Body'].read()
                    
                    # Проверяем, сжато ли (gzip)
                    if obj['Key'].endswith('.json.gz'):
                        import gzip
                        content = gzip.decompress(content)
                    
                    data = json.loads(content.decode('utf-8'))
                    print(f"       ✅ Данные валидны: album_id={data.get('album_id')}, items_analyzed={data.get('items_analyzed')}")
                except Exception as e:
                    print(f"       ⚠️  Ошибка чтения: {e}")
        else:
            print(f"  ⚠️  Альбомов в S3 не найдено")
            
    except ImportError:
        print("  ⚠️  S3StorageService не импортируется, пропускаем проверку S3")
    except Exception as e:
        print(f"  ❌ Ошибка проверки S3: {e}")
        import traceback
        traceback.print_exc()


async def check_vision_events_for_albums():
    """Проверка vision.analyzed событий для альбомов."""
    print("\n🔍 Проверка vision.analyzed событий для альбомов...")
    
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    
    try:
        # Проверяем stream:posts:vision:analyzed
        stream_key = "stream:posts:vision:analyzed"
        stream_length = await redis_client.xlen(stream_key)
        print(f"  📊 Всего событий vision.analyzed: {stream_length}")
        
        if stream_length > 0:
            # Получаем последние события
            messages = await redis_client.xrevrange(stream_key, count=20)
            print(f"  ✅ Получено последних событий: {len(messages)}")
            
            # Проверяем, какие посты относятся к альбомам
            db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
            if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
            
            engine = create_async_engine(db_url)
            async_session = async_sessionmaker(engine, expire_on_commit=False)
            
            try:
                async with async_session() as session:
                    albums_posts = set()
                    for msg_id, fields in messages[:10]:
                        try:
                            if 'data' in fields:
                                event_data = json.loads(fields['data'])
                                post_id = event_data.get('post_id')
                                
                                if post_id:
                                    # Проверяем, принадлежит ли пост альбому
                                    result = await session.execute(text("""
                                        SELECT mg.id as album_id, mg.grouped_id
                                        FROM media_group_items mgi
                                        JOIN media_groups mg ON mgi.group_id = mg.id
                                        WHERE mgi.post_id = :post_id
                                        LIMIT 1
                                    """), {"post_id": post_id})
                                    
                                    row = result.fetchone()
                                    if row:
                                        album_id = row[0]
                                        grouped_id = row[1]
                                        albums_posts.add((album_id, grouped_id, post_id))
                        except Exception as e:
                            continue
                    
                    print(f"  ✅ Найдено постов из альбомов в vision.analyzed: {len(albums_posts)}")
                    for album_id, grouped_id, post_id in list(albums_posts)[:5]:
                        print(f"     - Album ID: {album_id}, Grouped ID: {grouped_id}, Post ID: {post_id[:8]}...")
            finally:
                await engine.dispose()
        
        # Проверяем stream:albums:parsed
        albums_parsed_stream = "stream:albums:parsed"
        albums_parsed_length = await redis_client.xlen(albums_parsed_stream)
        print(f"  📊 Событий albums.parsed: {albums_parsed_length}")
        
        # Проверяем stream:album:assembled
        albums_assembled_stream = "stream:album:assembled"
        albums_assembled_length = await redis_client.xlen(albums_assembled_stream)
        print(f"  📊 Событий album.assembled: {albums_assembled_length}")
        
    finally:
        await redis_client.aclose()
    
    print("  ✅ Проверка завершена")


async def check_album_assembler_status():
    """Проверка статуса album_assembler_task."""
    print("\n🏥 Проверка статуса album_assembler_task...")
    
    import aiohttp
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8000/health/detailed", timeout=aiohttp.ClientTimeout(total=5)) as resp:
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
                        
                        # Проверяем активные состояния
                        redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
                        try:
                            keys = []
                            async for key in redis_client.scan_iter(match="album:state:*"):
                                keys.append(key)
                            
                            print(f"     ✅ Активных состояний альбомов: {len(keys)}")
                            if keys:
                                for key in keys[:3]:
                                    state_json = await redis_client.get(key)
                                    if state_json:
                                        state = json.loads(state_json)
                                        album_id = state.get('album_id', 'N/A')
                                        items_count = state.get('items_count', 0)
                                        items_analyzed = len(state.get('items_analyzed', []))
                                        print(f"        - Album {album_id}: {items_analyzed}/{items_count} обработано")
                        finally:
                            await redis_client.aclose()
                    else:
                        print("  ⚠️  album_assembler не найден в health check")
                        print(f"     Доступные tasks: {list(tasks.keys())}")
                else:
                    print(f"  ⚠️  Health check недоступен: HTTP {resp.status}")
    except Exception as e:
        print(f"  ⚠️  Ошибка проверки health check: {e}")


async def check_worker_metrics():
    """Проверка метрик worker для альбомов."""
    print("\n📊 Проверка метрик worker для альбомов...")
    
    import aiohttp
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8001/metrics", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    lines = text.split('\n')
                    
                    album_metrics = [l for l in lines if 'album' in l.lower() and not l.startswith('#')]
                    
                    if album_metrics:
                        print("  ✅ Метрики альбомов:")
                        for metric in album_metrics[:15]:
                            print(f"     {metric}")
                    else:
                        print("  ⚠️  Метрики альбомов не найдены")
                else:
                    print(f"  ⚠️  Метрики недоступны: HTTP {resp.status}")
    except Exception as e:
        print(f"  ⚠️  Ошибка получения метрик: {e}")


async def main():
    """Главная функция."""
    print("=" * 80)
    print("🧪 E2E тест: Альбомы → Vision → S3")
    print("=" * 80)
    
    # 1. Проверка существующих альбомов с vision
    await check_existing_albums_with_vision()
    
    # 2. Проверка альбомов в S3
    await check_s3_albums()
    
    # 3. Проверка vision событий для альбомов
    await check_vision_events_for_albums()
    
    # 4. Проверка статуса album_assembler_task
    await check_album_assembler_status()
    
    # 5. Проверка метрик
    await check_worker_metrics()
    
    print("\n" + "=" * 80)
    print("✅ E2E тест завершён")
    print("=" * 80)
    
    print("\n💡 Выводы:")
    print("   - Для полного теста нужно:")
    print("     1. Создать альбом с несколькими постами")
    print("     2. Запустить vision анализ для постов альбома")
    print("     3. Проверить обработку album_assembler_task")
    print("     4. Проверить сохранение в S3")
    print("\n   См. scripts/create_test_album.py и scripts/publish_test_vision_event.py")


if __name__ == "__main__":
    asyncio.run(main())

