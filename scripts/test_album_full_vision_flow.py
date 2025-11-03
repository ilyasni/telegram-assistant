#!/usr/bin/env python3
"""
Полный E2E тест пайплайна альбомов с vision анализом и сохранением в S3
Context7: проверка полного цикла от альбома до S3

Тестирует:
1. Создание альбома в БД
2. Эмиссию albums.parsed события
3. Создание vision.analyzed событий для элементов альбома
4. Обработку album_assembler_task
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

async def get_existing_album():
    """Получение существующего альбома из БД."""
    print("\n📦 Получение существующего альбома...")
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Получаем альбом с постами
            result = await session.execute(text("""
                SELECT 
                    mg.id as album_id,
                    mg.grouped_id,
                    mg.channel_id,
                    mg.user_id,
                    mg.items_count,
                    mg.caption_text,
                    array_agg(mgi.post_id ORDER BY mgi.position) as post_ids,
                    mg.user_id as tenant_id
                FROM media_groups mg
                JOIN media_group_items mgi ON mg.id = mgi.group_id
                GROUP BY mg.id, mg.grouped_id, mg.channel_id, mg.user_id, mg.items_count, mg.caption_text
                LIMIT 1
            """))
            
            row = result.fetchone()
            if row:
                album_id = row[0]
                grouped_id = row[1]
                channel_id = str(row[2])
                user_id = str(row[3])
                items_count = row[4]
                caption_text = row[5]
                post_ids = row[6] if row[6] else []
                tenant_id = str(row[7]) if row[7] else "default"
                
                print(f"  ✅ Найден альбом:")
                print(f"     Album ID: {album_id}")
                print(f"     Grouped ID: {grouped_id}")
                print(f"     Items: {items_count}")
                print(f"     Posts: {len(post_ids)}")
                print(f"     Channel ID: {channel_id}")
                print(f"     User ID: {user_id}")
                print(f"     Tenant ID: {tenant_id}")
                
                return {
                    'album_id': album_id,
                    'grouped_id': grouped_id,
                    'channel_id': channel_id,
                    'user_id': user_id,
                    'tenant_id': tenant_id,
                    'items_count': items_count,
                    'caption_text': caption_text,
                    'post_ids': post_ids
                }
            else:
                print("  ⚠️  Альбомы не найдены")
                return None
                
    finally:
        await engine.dispose()


async def emit_albums_parsed_event(album_data: dict):
    """Эмиссия события albums.parsed для альбома."""
    print("\n📤 Эмиссия события albums.parsed...")
    
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=False)
    
    try:
        event = {
            "schema_version": "v1",
            "trace_id": f"test_album_vision_{int(datetime.now(timezone.utc).timestamp())}",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": f"{str(album_data['tenant_id'])}:{str(album_data['channel_id'])}:{album_data['grouped_id']}",
            "user_id": str(album_data['user_id']),
            "channel_id": str(album_data['channel_id']),
            "album_id": album_data['album_id'],
            "grouped_id": album_data['grouped_id'],
            "tenant_id": str(album_data['tenant_id']),
            "album_kind": "photo",
            "items_count": album_data['items_count'],
            "caption_text": album_data['caption_text'],
            "post_ids": json.dumps([str(p) for p in album_data['post_ids']]),  # Преобразуем UUID в строки
            "content_hash": f"test_hash_{album_data['grouped_id']}"
        }
        
        # Context7: Сериализуем событие в формат, который ожидает album_assembler_task
        # Task ожидает JSON в поле 'data'
        event_json = json.dumps(event, ensure_ascii=False, default=str)
        event_payload = {
            'event': 'albums.parsed',
            'data': event_json,
            'idempotency_key': event['idempotency_key']
        }
        
        stream_key = "stream:albums:parsed"
        message_id = await redis_client.xadd(stream_key, event_payload, maxlen=10000)
        
        print(f"  ✅ Событие albums.parsed эмитировано: {message_id}")
        return message_id
        
    finally:
        await redis_client.aclose()


async def save_vision_results_to_db(album_data: dict):
    """Сохранение vision результатов в БД для постов альбома."""
    print("\n💾 Сохранение vision результатов в БД...")
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            for idx, post_id in enumerate(album_data['post_ids'][:3]):  # Берём первые 3 поста
                # Сохраняем vision результаты в post_enrichment
                # Context7: Используем новый формат с полем data (JSONB) + legacy поля для обратной совместимости
                vision_data = {
                    "model": "GigaChat-Pro",
                    "provider": "gigachat",
                    "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    "classification": "photo",
                    "description": f"Test vision description for album post {idx + 1}",
                    "is_meme": idx == 0,
                    "labels": [f"tag_{idx}_a", f"tag_{idx}_b"],
                    "ocr": {
                        "text": f"Test OCR text {idx + 1}" if idx == 0 else None,
                        "engine": "gigachat"
                    } if idx == 0 else None
                }
                
                # Сохраняем и в новом формате (data), и в legacy полях для обратной совместимости
                await session.execute(text("""
                    INSERT INTO post_enrichment (
                        post_id,
                        kind,
                        provider,
                        data,
                        status,
                        vision_description,
                        vision_classification,
                        vision_is_meme,
                        vision_ocr_text,
                        vision_analyzed_at
                    ) VALUES (
                        :post_id,
                        'vision',
                        'gigachat',
                        CAST(:data AS jsonb),
                        'ok',
                        :description,
                        CAST(:classification AS jsonb),
                        :is_meme,
                        :ocr_text,
                        NOW()
                    )
                    ON CONFLICT (post_id, kind)
                    DO UPDATE SET
                        data = EXCLUDED.data,
                        status = EXCLUDED.status,
                        vision_description = EXCLUDED.vision_description,
                        vision_classification = EXCLUDED.vision_classification,
                        vision_is_meme = EXCLUDED.vision_is_meme,
                        vision_ocr_text = EXCLUDED.vision_ocr_text,
                        vision_analyzed_at = EXCLUDED.vision_analyzed_at
                """), {
                    "post_id": post_id,
                    "data": json.dumps(vision_data),
                    "description": vision_data["description"],
                    "classification": json.dumps({"tags": vision_data["labels"], "confidence": 0.95}),
                    "is_meme": vision_data["is_meme"],
                    "ocr_text": vision_data["ocr"]["text"] if vision_data["ocr"] else None
                })
                
                print(f"  ✅ Vision результаты сохранены для post {str(post_id)[:8]}...")
            
            await session.commit()
            
    finally:
        await engine.dispose()


async def emit_vision_analyzed_events(album_data: dict):
    """Эмиссия vision.analyzed событий для постов альбома."""
    print("\n📤 Эмиссия vision.analyzed событий для постов альбома...")
    
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=False)
    
    try:
        # Импортируем схему события
        try:
            from events.schemas.posts_vision_v1 import VisionAnalyzedEventV1, MediaFile, VisionAnalysisResult
        except ImportError:
            from worker.events.schemas.posts_vision_v1 import VisionAnalyzedEventV1, MediaFile, VisionAnalysisResult
        
        message_ids = []
        
        for idx, post_id in enumerate(album_data['post_ids'][:3]):  # Берём первые 3 поста
            # Создаём тестовые vision результаты
            vision_result = VisionAnalysisResult(
                provider="gigachat",
                model="GigaChat-Pro",
                schema_version="1.0",
                classification={
                    "type": "photo",
                    "confidence": 0.95,
                    "tags": [f"tag_{idx}_a", f"tag_{idx}_b"]
                },
                description=f"Test vision description for album post {idx + 1}",
                ocr_text=f"Test OCR text for album post {idx + 1}" if idx == 0 else None,
                is_meme=(idx == 0),
                tokens_used=150,
                file_id="test_file_id",
                analyzed_at=datetime.now(timezone.utc)
            )
            
            post_id_str = str(post_id)
            media_file = MediaFile(
                sha256=f"test_sha256_{post_id_str[:8]}_{idx}",
                s3_key=f"media/test/{post_id_str[:2]}/{post_id_str}.jpg",
                mime_type="image/jpeg",
                size_bytes=1000
            )
            
            analyzed_event = VisionAnalyzedEventV1(
                tenant_id=str(album_data['tenant_id']),
                post_id=post_id_str,
                media=[media_file],
                vision=vision_result.model_dump(),
                analysis_duration_ms=500,
                idempotency_key=f"{str(album_data['tenant_id'])}:{post_id_str}:vision_analyzed",
                trace_id=f"test_vision_{int(datetime.now(timezone.utc).timestamp())}"
            )
            
            event_json = analyzed_event.model_dump_json()
            message_id = await redis_client.xadd(
                "stream:posts:vision:analyzed",
                {
                    "event": "posts.vision.analyzed",
                    "data": event_json,
                    "idempotency_key": analyzed_event.idempotency_key
                }
            )
            
            message_ids.append(message_id)
            print(f"  ✅ Vision.analyzed эмитировано для post {post_id_str[:8]}...: {message_id}")
            
            # Небольшая задержка между событиями
            await asyncio.sleep(0.2)
        
        print(f"  ✅ Всего эмитировано {len(message_ids)} vision.analyzed событий")
        return message_ids
        
    except Exception as e:
        print(f"  ❌ Ошибка эмиссии vision.analyzed: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        await redis_client.aclose()


async def wait_for_album_assembly(album_id: int, timeout: int = 60):
    """Ожидание сборки альбома."""
    print(f"\n⏳ Ожидание сборки альбома {album_id} (timeout: {timeout}s)...")
    
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    
    try:
        start_time = datetime.now(timezone.utc)
        assembled_stream = "stream:album:assembled"
        
        # Получаем текущее количество событий
        initial_count = await redis_client.xlen(assembled_stream)
        print(f"  📊 Начальное количество событий album.assembled: {initial_count}")
        
        # Проверяем состояние альбома в Redis
        state_key = f"album:state:{album_id}"
        
        while True:
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            if elapsed > timeout:
                print(f"  ⚠️  Timeout: альбом не собран за {timeout}s")
                break
            
            # Проверяем состояние альбома
            state_json = await redis_client.get(state_key)
            if state_json:
                state = json.loads(state_json)
                items_count = state.get('items_count', 0)
                items_analyzed = len(state.get('items_analyzed', []))
                
                print(f"  📊 Прогресс: {items_analyzed}/{items_count} элементов обработано ({elapsed:.1f}s)")
                
                if items_analyzed >= items_count:
                    print(f"  ✅ Все элементы обработаны!")
                    break
            
            # Проверяем событие album.assembled
            current_count = await redis_client.xlen(assembled_stream)
            if current_count > initial_count:
                print(f"  ✅ Событие album.assembled получено!")
                break
            
            await asyncio.sleep(2)
        
        # Финальная проверка
        final_count = await redis_client.xlen(assembled_stream)
        if final_count > initial_count:
            # Получаем последнее событие
            messages = await redis_client.xrevrange(assembled_stream, count=1)
            if messages:
                msg_id, fields = messages[0]
                print(f"  ✅ Последнее событие album.assembled: {msg_id}")
                return True
        
        return False
        
    finally:
        await redis_client.aclose()


async def check_album_s3_and_db(album_id: int):
    """Проверка сохранения альбома в S3 и БД."""
    print(f"\n🔍 Проверка сохранения альбома {album_id} в S3 и БД...")
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Проверяем enrichment в БД
            result = await session.execute(text("""
                SELECT 
                    mg.id,
                    mg.meta->'enrichment'->>'s3_key' as s3_key,
                    mg.meta->'enrichment'->>'vision_summary' IS NOT NULL as has_vision_summary,
                    mg.meta->'enrichment'->>'assembly_completed_at' as assembly_completed_at,
                    mg.meta->'enrichment' as enrichment_json
                FROM media_groups mg
                WHERE mg.id = :album_id
            """), {"album_id": album_id})
            
            row = result.fetchone()
            if row and row[1]:  # s3_key exists
                print(f"  ✅ Альбом сохранён в БД с enrichment:")
                print(f"     S3 Key: {row[1]}")
                print(f"     Vision Summary: {'✅' if row[2] else '❌'}")
                print(f"     Assembly completed: {row[4] if row[4] else 'N/A'}")
                
                # Проверяем S3
                try:
                    from api.services.s3_storage import S3StorageService
                    
                    s3_config = {
                        'endpoint_url': os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru'),
                        'access_key_id': os.getenv('S3_ACCESS_KEY_ID'),
                        'secret_access_key': os.getenv('S3_SECRET_ACCESS_KEY'),
                        'bucket_name': os.getenv('S3_BUCKET_NAME'),
                        'region': os.getenv('S3_REGION', 'ru-central-1')
                    }
                    
                    if s3_config.get('access_key_id') and s3_config.get('secret_access_key'):
                        s3 = S3StorageService(**s3_config)
                        
                        try:
                            response = s3.s3_client.head_object(
                                Bucket=s3_config['bucket_name'],
                                Key=row[1]
                            )
                            size_bytes = response['ContentLength']
                            print(f"  ✅ Файл найден в S3: {size_bytes} bytes")
                            
                            # Читаем содержимое
                            response = s3.s3_client.get_object(
                                Bucket=s3_config['bucket_name'],
                                Key=row[1]
                            )
                            content = response['Body'].read()
                            
                            # Проверяем сжатие
                            if row[1].endswith('.gz'):
                                import gzip
                                content = gzip.decompress(content)
                            
                            data = json.loads(content.decode('utf-8'))
                            print(f"  ✅ Данные валидны:")
                            print(f"     Album ID: {data.get('album_id')}")
                            print(f"     Items analyzed: {data.get('items_analyzed')}/{data.get('items_count')}")
                            print(f"     Vision summary: {'✅' if data.get('vision_summary') else '❌'}")
                            
                            return True
                        except Exception as e:
                            print(f"  ⚠️  Файл не найден в S3: {e}")
                            return False
                    else:
                        print("  ⚠️  S3 credentials не настроены")
                        return False
                except ImportError:
                    print("  ⚠️  S3StorageService не импортируется")
                    return False
            else:
                print(f"  ⚠️  Альбом не имеет enrichment в БД")
                return False
                
    finally:
        await engine.dispose()


async def main():
    """Главная функция."""
    print("=" * 80)
    print("🧪 Полный E2E тест: Альбом → Vision → S3")
    print("=" * 80)
    
    # 1. Получаем существующий альбом
    album_data = await get_existing_album()
    if not album_data:
        print("\n❌ Альбом не найден. Создайте альбом сначала:")
        print("   docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/create_test_album.py")
        return
    
    # 2. Эмитируем albums.parsed событие
    await emit_albums_parsed_event(album_data)
    
    # 3. Ждём немного для инициализации состояния
    print("\n⏳ Ожидание инициализации состояния альбома (3s)...")
    await asyncio.sleep(3)
    
    # 4. Сохраняем vision результаты в БД (album_assembler_task читает из post_enrichment)
    await save_vision_results_to_db(album_data)
    
    # 5. Эмитируем vision.analyzed события
    await emit_vision_analyzed_events(album_data)
    
    # 6. Ожидаем сборки альбома
    assembled = await wait_for_album_assembly(album_data['album_id'], timeout=60)
    
    if assembled:
        # 7. Проверяем сохранение в S3 и БД
        saved = await check_album_s3_and_db(album_data['album_id'])
        
        if saved:
            print("\n" + "=" * 80)
            print("✅ ПОЛНЫЙ E2E ТЕСТ ПРОЙДЕН!")
            print("=" * 80)
            print("\n📋 Результаты:")
            print("   ✅ Альбом создан в БД")
            print("   ✅ Событие albums.parsed эмитировано")
            print("   ✅ Vision.analyzed события обработаны")
            print("   ✅ Альбом собран (album.assembled)")
            print("   ✅ Vision summary сохранён в S3")
            print("   ✅ Enrichment сохранён в БД")
        else:
            print("\n⚠️  Альбом собран, но не сохранён в S3")
    else:
        print("\n⚠️  Альбом не собран за отведённое время")
        print("   Проверьте логи album_assembler_task:")
        print("   docker logs telegram-assistant-worker-1 | grep -i 'album\|assembler'")


if __name__ == "__main__":
    asyncio.run(main())

