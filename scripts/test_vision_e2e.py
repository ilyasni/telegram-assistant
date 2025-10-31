#!/usr/bin/env python3
"""
E2E тест Vision + S3 пайплайна
Context7 best practice: trace_id, проверка всех этапов, диагностика
[C7-ID: TEST-VISION-E2E-001]
"""

import sys
import os
import asyncio
import json
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path

# Добавляем пути
sys.path.insert(0, str(Path(__file__).parent.parent))

async def test_vision_e2e_pipeline(post_id: str = None):
    """Полный E2E тест Vision пайплайна."""
    
    from worker.event_bus import EventPublisher, STREAMS
    from worker.events.schemas import VisionUploadedEventV1, MediaFile
    from api.services.s3_storage import S3StorageService
    from worker.services.storage_quota import StorageQuotaService
    from config import settings
    import redis.asyncio as redis
    from sqlalchemy import create_engine, text
    import structlog
    
    logger = structlog.get_logger()
    
    print("=" * 70)
    print("🧪 E2E ТЕСТ VISION + S3 ПАЙПЛАЙНА")
    print("=" * 70)
    
    # Получаем пост из БД
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        if post_id:
            query = text("""
                SELECT 
                    p.id::text as post_id,
                    p.channel_id::text as channel_id,
                    p.telegram_message_id,
                    p.media_urls,
                    (SELECT COUNT(*) FROM post_media_map WHERE post_media_map.post_id = p.id) as media_count
                FROM posts p
                WHERE p.id::text = :post_id
            """)
            result = conn.execute(query, {"post_id": post_id})
        else:
            query = text("""
                SELECT 
                    p.id::text as post_id,
                    p.channel_id::text as channel_id,
                    p.telegram_message_id,
                    p.media_urls,
                    (SELECT COUNT(*) FROM post_media_map WHERE post_media_map.post_id = p.id) as media_count
                FROM posts p
                WHERE p.media_urls IS NOT NULL 
                  AND jsonb_array_length(COALESCE(p.media_urls, '[]'::jsonb)) > 0
                ORDER BY p.created_at DESC
                LIMIT 1
            """)
            result = conn.execute(query)
        
        post = result.fetchone()
        if not post:
            print("⚠️  Пост с медиа не найден. Создаём тестовый UUID.")
            post_id = str(uuid4())
            channel_id = str(uuid4())
            media_count = 0
        else:
            post_id = post.post_id
            channel_id = post.channel_id
            media_count = post.media_count
    
    tenant_id = os.getenv("S3_DEFAULT_TENANT_ID", "877193ef-be80-4977-aaeb-8009c3d772ee")
    trace_id = f"e2e_test_{uuid4().hex[:16]}"
    
    print(f"\n📋 Параметры теста:")
    print(f"  Post ID: {post_id}")
    print(f"  Channel ID: {channel_id}")
    print(f"  Tenant ID: {tenant_id}")
    print(f"  Trace ID: {trace_id}")
    
    # Подключения
    redis_client = await redis.from_url(settings.redis_url, decode_responses=False)
    event_publisher = EventPublisher(redis_client)
    
    # Шаг 1: Проверка S3 квоты
    print(f"\n📊 Шаг 1: Проверка S3 квот...")
    try:
        s3_service = S3StorageService(
            endpoint_url=os.getenv("S3_ENDPOINT_URL", "https://s3.cloud.ru"),
            access_key_id=os.getenv("S3_ACCESS_KEY_ID", ""),
            secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY", ""),
            bucket_name=os.getenv("S3_BUCKET_NAME", "test-467940"),
            region=os.getenv("S3_REGION", "ru-central-1")
        )
        
        quota_service = StorageQuotaService(s3_service)
        
        quota_check = await quota_service.check_quota_before_upload(
            tenant_id=tenant_id,
            size_bytes=1024 * 1024,  # 1 MB
            content_type="media"
        )
        
        if not quota_check.allowed:
            print(f"  ❌ Квота превышена: {quota_check.reason}")
            await redis_client.close()
            engine.dispose()
            return
        
        print(f"  ✅ Квота доступна ({quota_check.current_usage_gb:.2f} GB / {quota_check.tenant_limit_gb:.2f} GB)")
    except Exception as e:
        print(f"  ⚠️  Ошибка проверки квоты: {e}")
    
    # Шаг 2: Создание тестового медиа события
    print(f"\n📤 Шаг 2: Создание VisionUploadedEventV1...")
    test_sha256 = hashlib.sha256(f"test_media_{post_id}_{trace_id}".encode()).hexdigest()
    
    media_file = MediaFile(
        sha256=test_sha256,
        s3_key=f"media/{tenant_id}/{test_sha256[:2]}/{test_sha256}.jpg",
        mime_type="image/jpeg",
        size_bytes=512000,  # 500 KB
        telegram_file_id=f"test_telegram_file_id_{uuid4().hex[:8]}"
    )
    
    event = VisionUploadedEventV1(
        schema_version="v1",
        trace_id=trace_id,
        idempotency_key=f"{tenant_id}:{post_id}:{media_file.sha256}",
        tenant_id=tenant_id,
        post_id=post_id,
        channel_id=channel_id,
        media_files=[media_file],
        uploaded_at=datetime.now(timezone.utc)
    )
    
    print(f"  Media SHA256: {test_sha256}")
    print(f"  S3 Key: {media_file.s3_key}")
    print(f"  Size: {media_file.size_bytes / 1024:.1f} KB")
    
    # Шаг 3: Публикация события
    print(f"\n🚀 Шаг 3: Публикация в stream:posts:vision:uploaded...")
    try:
        stream_alias = "posts.vision.uploaded"
        message_id = await event_publisher.publish_event(stream_alias, event)
        print(f"  ✅ Событие опубликовано: {message_id}")
        
        # Проверка stream
        stream_name = STREAMS.get(stream_alias, f"stream:{stream_alias.replace('.', ':')}")
        stream_length = await redis_client.xlen(stream_name)
        print(f"  Stream length: {stream_length} messages")
        
    except Exception as e:
        print(f"  ❌ Ошибка публикации: {e}")
        import traceback
        traceback.print_exc()
        await redis_client.close()
        engine.dispose()
        return
    
    # Шаг 4: Ожидание обработки
    print(f"\n⏳ Шаг 4: Ожидание обработки Vision worker (90 секунд)...")
    print(f"  Проверьте логи: docker compose logs worker | grep -i vision")
    
    for i in range(18):  # 18 * 5 = 90 секунд
        await asyncio.sleep(5)
        if i % 3 == 0:  # Каждые 15 секунд
            print(f"  ... ожидание ({i * 5} секунд)")
    
    # Шаг 5: Проверка результатов в БД
    print(f"\n🔍 Шаг 5: Проверка результатов в БД...")
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT 
                pe.vision_analyzed_at,
                pe.vision_provider,
                pe.vision_model,
                pe.vision_is_meme,
                pe.vision_classification,
                pe.vision_tokens_used,
                pe.s3_vision_keys,
                pe.s3_media_keys,
                pe.vision_analysis_reason
            FROM post_enrichment pe
            WHERE pe.post_id::text = :post_id
        """), {"post_id": post_id})
        
        enrichment = result.fetchone()
        
        if enrichment and enrichment.vision_analyzed_at:
            print(f"  ✅ Vision анализ выполнен!")
            print(f"    Analyzed At: {enrichment.vision_analyzed_at}")
            print(f"    Provider: {enrichment.vision_provider}")
            print(f"    Model: {enrichment.vision_model}")
            print(f"    Is Meme: {enrichment.vision_is_meme}")
            print(f"    Tokens Used: {enrichment.vision_tokens_used}")
            print(f"    Analysis Reason: {enrichment.vision_analysis_reason}")
            if enrichment.vision_classification:
                print(f"    Classification: {json.dumps(enrichment.vision_classification, indent=4, ensure_ascii=False)}")
            if enrichment.s3_vision_keys:
                print(f"    S3 Vision Keys: {enrichment.s3_vision_keys}")
        else:
            print(f"  ⚠️  Vision анализ ещё не выполнен")
            print(f"    Возможные причины:")
            print(f"    - Vision worker не запущен (проверьте FEATURE_VISION_ENABLED)")
            print(f"    - GigaChat credentials не настроены")
            print(f"    - Ошибка при обработке (проверьте логи worker)")
            print(f"    - Budget/quota exhausted")
    
    # Шаг 6: Проверка через API
    print(f"\n🔍 Шаг 6: Проверка через Vision API...")
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                f"http://api:8000/api/v1/vision/posts/{post_id}",
                headers={"X-Trace-ID": trace_id}
            )
            if response.status_code == 200:
                data = response.json()
                print(f"  ✅ API endpoint работает")
                print(f"    Provider: {data.get('provider')}")
                print(f"    Is Meme: {data.get('is_meme')}")
                print(f"    Media Count: {data.get('media_count')}")
            elif response.status_code == 404:
                print(f"  ⚠️  Vision анализ не найден (404)")
            else:
                print(f"  ⚠️  API ответил: {response.status_code}")
                print(f"    Response: {response.text[:200]}")
        except Exception as e:
            print(f"  ⚠️  Не удалось проверить API: {e}")
    
    # Шаг 7: Проверка Redis Stream состояние
    print(f"\n🔍 Шаг 7: Проверка Redis Streams...")
    try:
        uploaded_length = await redis_client.xlen("stream:posts:vision:uploaded")
        analyzed_length = await redis_client.xlen("stream:posts:vision:analyzed")
        
        print(f"  Uploaded events: {uploaded_length}")
        print(f"  Analyzed events: {analyzed_length}")
        
        # Проверка consumer group
        try:
            groups = await redis_client.xinfo_groups("stream:posts:vision:uploaded")
            print(f"  Consumer groups: {len(groups)}")
            for group in groups:
                print(f"    - {group.get('name', 'unknown')}: {group.get('pending', 0)} pending")
        except Exception as e:
            print(f"  ⚠️  Consumer group не создан: {e}")
            
    except Exception as e:
        print(f"  ⚠️  Ошибка проверки streams: {e}")
    
    await redis_client.close()
    engine.dispose()
    
    print(f"\n" + "=" * 70)
    print("✅ E2E ТЕСТ ЗАВЕРШЁН")
    print("=" * 70)
    print(f"\n📋 Результаты сохранены для post_id: {post_id}")
    print(f"   Trace ID: {trace_id}")
    print(f"\n💡 Следующие шаги:")
    print(f"   1. Проверьте логи: docker compose logs worker | grep '{trace_id}'")
    print(f"   2. Проверьте метрики: curl http://localhost:8001/metrics | grep vision")
    print(f"   3. Проверьте API: curl http://localhost:8000/api/v1/vision/posts/{post_id}")
    print(f"=" * 70)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="E2E test Vision pipeline")
    parser.add_argument("--post-id", type=str, help="Post ID to test (optional)")
    args = parser.parse_args()
    
    asyncio.run(test_vision_e2e_pipeline(post_id=args.post_id))

