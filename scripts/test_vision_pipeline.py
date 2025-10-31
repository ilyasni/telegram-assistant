#!/usr/bin/env python3
"""
Тестирование Vision + S3 пайплайна на реальном посте
Context7 best practice: trace_id, error handling, проверка квот
[C7-ID: TEST-VISION-PIPELINE-001]
"""

import sys
import os
import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID
from pathlib import Path

# Добавляем пути
sys.path.insert(0, str(Path(__file__).parent.parent))

async def test_vision_pipeline(post_id: str = None):
    """Тестирование полного Vision пайплайна."""
    
    # Импорты
    from worker.event_bus import EventPublisher
    from worker.events.schemas import VisionUploadedEventV1, MediaFile
    from api.services.s3_storage import S3StorageService
    from worker.services.storage_quota import StorageQuotaService
    from config import settings
    import redis.asyncio as redis
    from sqlalchemy import create_engine, text
    import hashlib
    import structlog
    
    logger = structlog.get_logger()
    
    # Подключения
    redis_client = await redis.from_url(settings.redis_url, decode_responses=False)
    event_publisher = EventPublisher(redis_client)
    
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
                    p.content,
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
                    p.content,
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
            print("❌ Пост с медиа не найден в БД")
            await redis_client.close()
            return
        
        post_id = post.post_id
        channel_id = post.channel_id
        media_urls = post.media_urls or []
        media_count = post.media_count
    
    print("=" * 70)
    print("🧪 ТЕСТИРОВАНИЕ VISION + S3 ПАЙПЛАЙНА")
    print("=" * 70)
    print(f"\n📋 Пост для тестирования:")
    print(f"  Post ID: {post_id}")
    print(f"  Channel ID: {channel_id}")
    print(f"  Telegram Message ID: {post.telegram_message_id}")
    print(f"  Media URLs: {len(media_urls)} items")
    print(f"  Media Count (DB): {media_count}")
    
    # Trace ID для корреляции
    import uuid
    trace_id = f"test_{uuid.uuid4().hex[:16]}"
    tenant_id = os.getenv("S3_DEFAULT_TENANT_ID", "877193ef-be80-4977-aaeb-8009c3d772ee")
    
    print(f"\n🔍 Trace ID: {trace_id}")
    print(f"   Tenant ID: {tenant_id}")
    
    # Проверка S3 квоты перед началом
    print(f"\n📊 Проверка S3 квот...")
    try:
        s3_service = S3StorageService(
            endpoint_url=os.getenv("S3_ENDPOINT_URL", "https://s3.cloud.ru"),
            access_key_id=os.getenv("S3_ACCESS_KEY_ID", ""),
            secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY", ""),
            bucket_name=os.getenv("S3_BUCKET_NAME", "test-467940"),
            region=os.getenv("S3_REGION", "ru-central-1")
        )
        
        quota_service = StorageQuotaService(s3_service)
        
        # Проверка квоты для тестового медиа (примерно 1 MB)
        quota_check = await quota_service.check_quota_before_upload(
            tenant_id=tenant_id,
            size_bytes=1024 * 1024,  # 1 MB
            content_type="media"
        )
        
        if not quota_check.allowed:
            print(f"⚠️  Квота превышена: {quota_check.reason}")
            print(f"   Текущее использование: {quota_check.current_usage_gb:.2f} GB")
            print(f"   Лимит: {quota_check.tenant_limit_gb:.2f} GB")
            await redis_client.close()
            return
        
        print(f"✅ Квота доступна ({quota_check.current_usage_gb:.2f} GB / {quota_check.tenant_limit_gb:.2f} GB)")
        
    except Exception as e:
        print(f"⚠️  Не удалось проверить квоту: {e}")
        print("   Продолжаем без проверки квоты...")
    
    # Для теста создаём синтетическое медиа событие
    # В реальном сценарии медиа уже загружено в S3 через MediaProcessor
    print(f"\n📤 Создание VisionUploadedEventV1...")
    
    # Генерируем тестовый SHA256 (в реальности будет из Telegram медиа)
    test_sha256 = hashlib.sha256(f"test_media_{post_id}".encode()).hexdigest()
    
    media_file = MediaFile(
        sha256=test_sha256,
        s3_key=f"media/{tenant_id}/{test_sha256[:2]}/{test_sha256}.jpg",
        mime_type="image/jpeg",
        size_bytes=512000,  # 500 KB
        telegram_file_id="test_telegram_file_id_12345"
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
    
    print(f"   Media SHA256: {media_file.sha256}")
    print(f"   S3 Key: {media_file.s3_key}")
    print(f"   Size: {media_file.size_bytes / 1024:.1f} KB")
    
    # Публикация события
    print(f"\n🚀 Публикация события в stream:posts:vision:uploaded...")
    
    try:
        message_id = await event_publisher.publish_event("posts.vision.uploaded", event)
        print(f"✅ Событие опубликовано")
        print(f"   Message ID: {message_id}")
        print(f"   Stream: stream:posts:vision:uploaded")
        
        print(f"\n⏳ Ожидание обработки Vision worker (30 секунд)...")
        await asyncio.sleep(30)
        
        # Проверка результатов
        print(f"\n🔍 Проверка результатов в БД...")
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
                    pe.s3_media_keys
                FROM post_enrichment pe
                WHERE pe.post_id::text = :post_id
            """), {"post_id": post_id})
            
            enrichment = result.fetchone()
            
            if enrichment and enrichment.vision_analyzed_at:
                print(f"✅ Vision анализ выполнен!")
                print(f"   Analyzed At: {enrichment.vision_analyzed_at}")
                print(f"   Provider: {enrichment.vision_provider}")
                print(f"   Model: {enrichment.vision_model}")
                print(f"   Is Meme: {enrichment.vision_is_meme}")
                print(f"   Tokens Used: {enrichment.vision_tokens_used}")
                print(f"   S3 Vision Keys: {enrichment.s3_vision_keys}")
                print(f"   S3 Media Keys: {enrichment.s3_media_keys}")
                
                if enrichment.vision_classification:
                    print(f"   Classification: {enrichment.vision_classification}")
            else:
                print(f"⚠️  Vision анализ ещё не выполнен")
                print(f"   Проверьте логи worker для диагностики")
        
        # Проверка через API
        print(f"\n🔍 Проверка через Vision API endpoint...")
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(
                    f"http://api:8000/api/v1/vision/posts/{post_id}",
                    headers={"X-Trace-ID": trace_id}
                )
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ API endpoint работает")
                    print(f"   Provider: {data.get('provider')}")
                    print(f"   Is Meme: {data.get('is_meme')}")
                    print(f"   Media Count: {data.get('media_count')}")
                elif response.status_code == 404:
                    print(f"⚠️  Vision анализ ещё не готов (404)")
                else:
                    print(f"⚠️  API ответил: {response.status_code}")
            except Exception as e:
                print(f"⚠️  Не удалось проверить API: {e}")
        
    except Exception as e:
        print(f"❌ Ошибка при публикации события: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await redis_client.close()
        engine.dispose()
    
    print(f"\n" + "=" * 70)
    print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 70)
    print(f"\n📋 Следующие шаги:")
    print(f"  1. Проверьте логи worker: docker compose logs worker | grep vision")
    print(f"  2. Проверьте Prometheus metrics: curl http://localhost:9090/metrics | grep vision")
    print(f"  3. Проверьте S3 bucket usage: curl http://localhost:8000/api/v1/storage/quota")
    print(f"  4. Проверьте Vision API: curl http://localhost:8000/api/v1/vision/posts/{post_id}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test Vision pipeline")
    parser.add_argument("--post-id", type=str, help="Post ID to test (optional)")
    args = parser.parse_args()
    
    asyncio.run(test_vision_pipeline(post_id=args.post_id))

