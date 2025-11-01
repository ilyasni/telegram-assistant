#!/usr/bin/env python3
"""
Context7: Функциональное тестирование Vision + S3 Pipeline на реальных данных.

Использование:
    python scripts/test_vision_pipeline.py --check-status
    python scripts/test_vision_pipeline.py --trigger-vision --post-id <uuid>
    python scripts/test_vision_pipeline.py --full-test
"""

import asyncio
import os
import sys
import argparse
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

# Context7: Настройка путей для cross-service импортов
# Определяем окружение: worker контейнер, api контейнер, или dev (host)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Worker контейнер: /app
if os.path.exists('/app'):
    if '/app' not in sys.path:
        sys.path.insert(0, '/app')
    # Для доступа к api: /opt/telegram-assistant/api
    api_mount = '/opt/telegram-assistant/api'
    if api_mount not in sys.path and os.path.exists(api_mount):
        sys.path.insert(0, api_mount)

# Dev окружение: project_root
elif project_root and os.path.exists(project_root):
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    worker_root = os.path.join(project_root, 'worker')
    api_root = os.path.join(project_root, 'api')
    if worker_root not in sys.path and os.path.exists(worker_root):
        sys.path.insert(0, worker_root)
    if api_root not in sys.path and os.path.exists(api_root):
        sys.path.insert(0, api_root)

# Context7: Импорты делаем опциональными - только когда нужны
# S3StorageService и StorageQuotaService импортируются внутри функций
# VisionUploadedEventV1 и MediaFile нужны всегда
try:
    # Worker контейнер: импорты из /app/events
    from events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
except ImportError:
    try:
        # Worker dev: импорты из worker.events
        from worker.events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
    except ImportError:
        # Fallback
        from events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile

import asyncpg
import redis.asyncio as redis
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session


def get_db_connection_string() -> str:
    """Context7: Получение строки подключения к БД (используем DATABASE_URL как worker)."""
    # Context7: Worker использует DATABASE_URL
    database_url = os.getenv("DATABASE_URL")
    
    if database_url:
        # Убираем +asyncpg для asyncpg (если есть)
        return database_url.replace("postgresql+asyncpg://", "postgresql://")
    
    # Fallback: собираем из отдельных переменных
    db_host = os.getenv("DB_HOST", os.getenv("POSTGRES_HOST", "supabase-db"))
    db_port = os.getenv("DB_PORT", os.getenv("POSTGRES_PORT", "5432"))
    db_user = os.getenv("POSTGRES_USER", "postgres")
    db_password = os.getenv("POSTGRES_PASSWORD", "") or os.getenv("DB_PASSWORD", "")
    db_name = os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "postgres"))
    
    if not db_password:
        raise ValueError("DATABASE_URL или POSTGRES_PASSWORD не установлены. Проверьте переменные окружения.")
    
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


def get_redis_url() -> str:
    """Context7: Получение URL Redis."""
    return os.getenv("REDIS_URL", "redis://redis:6379")


async def check_database_status() -> Dict[str, Any]:
    """
    Context7: Проверка состояния БД - посты с медиа, media_objects, vision анализ.
    
    Returns:
        Dict с статистикой БД
    """
    print("📊 Проверка состояния БД...")
    
    conn = await asyncpg.connect(get_db_connection_string())
    
    try:
        # Общая статистика
        total_posts = await conn.fetchval("SELECT COUNT(*) FROM posts")
        posts_with_media = await conn.fetchval(
            "SELECT COUNT(*) FROM posts WHERE has_media = true"
        )
        media_objects = await conn.fetchval("SELECT COUNT(*) FROM media_objects")
        post_media_links = await conn.fetchval("SELECT COUNT(*) FROM post_media_map")
        vision_analyzed = await conn.fetchval(
            "SELECT COUNT(*) FROM post_enrichment WHERE vision_analyzed_at IS NOT NULL"
        )
        
        # Посты с медиа без vision анализа
        posts_without_vision = await conn.fetchval("""
            SELECT COUNT(*)
            FROM posts p
            LEFT JOIN post_enrichment pe ON p.id = pe.post_id
            WHERE p.has_media = true
            AND (pe.vision_analyzed_at IS NULL OR pe.post_id IS NULL)
            LIMIT 100
        """)
        
        # Примеры постов с медиа
        sample_posts = await conn.fetch("""
            SELECT p.id, p.channel_id, p.telegram_message_id, p.has_media, p.created_at,
                   (SELECT COUNT(*) FROM post_media pm WHERE pm.post_id = p.id) as media_count
            FROM posts p
            WHERE p.has_media = true
            ORDER BY p.created_at DESC
            LIMIT 5
        """)
        
        result = {
            "total_posts": total_posts,
            "posts_with_media": posts_with_media,
            "media_objects": media_objects,
            "post_media_links": post_media_links,
            "vision_analyzed": vision_analyzed,
            "posts_without_vision": posts_without_vision,
            "sample_posts": [
                {
                    "id": str(row["id"]),
                    "channel_id": str(row["channel_id"]),
                    "telegram_message_id": row["telegram_message_id"],
                    "has_media": row["has_media"],
                    "media_count": row["media_count"] or 0,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None
                }
                for row in sample_posts
            ]
        }
        
        print(f"✅ Всего постов: {total_posts}")
        print(f"✅ Постов с медиа: {posts_with_media}")
        print(f"✅ Media objects в БД: {media_objects}")
        print(f"✅ Post-media links: {post_media_links}")
        print(f"✅ Vision analyzed: {vision_analyzed}")
        print(f"⚠️  Постов с медиа без vision анализа: {posts_without_vision}")
        
        return result
        
    finally:
        await conn.close()


async def check_storage_quota() -> Dict[str, Any]:
    """
    Context7: Проверка Storage Quota (опционально, требует S3StorageService).
    
    Returns:
        Dict с информацией о квоте
    """
    print("\n💾 Проверка Storage Quota...")
    
    # Context7: Импортируем только когда нужно (опционально)
    try:
        # Вариант 1: прямой импорт через api (когда api доступен через sys.path)
        from api.services.s3_storage import S3StorageService
    except ImportError:
        # Вариант 2: импорт из /app/services (API контейнер с volume mount)
        app_services_path = '/app/services'
        if app_services_path not in sys.path and os.path.exists(app_services_path):
            sys.path.insert(0, '/app')
        try:
            from services.s3_storage import S3StorageService
        except ImportError:
            # Вариант 3: через /opt/telegram-assistant/api (dev volume mount)
            api_path = '/opt/telegram-assistant/api'
            if api_path not in sys.path and os.path.exists(api_path):
                sys.path.insert(0, api_path)
            try:
                from api.services.s3_storage import S3StorageService
            except ImportError:
                raise ImportError("S3StorageService не найден. Проверьте монтирование api в worker контейнер.")
    
    try:
        # Worker контейнер: импорты из /app/services
        from services.storage_quota import StorageQuotaService
    except ImportError:
        try:
            # Worker dev: импорты из worker.services
            from worker.services.storage_quota import StorageQuotaService
        except ImportError:
            # Fallback
            worker_path = '/opt/telegram-assistant/worker'
            if worker_path not in sys.path and os.path.exists(worker_path):
                sys.path.insert(0, worker_path)
            from services.storage_quota import StorageQuotaService
    
    try:
        s3_service = S3StorageService()
        quota_service = StorageQuotaService(s3_service)
        
        status = quota_service.get_quota_status()
        
        result = {
            "used_gb": status.used_gb,
            "limit_gb": status.limit_gb,
            "usage_percent": status.usage_percent,
            "emergency_threshold_gb": status.emergency_threshold_gb,
            "is_critical": status.usage_percent >= 93.0,
            "is_warning": status.usage_percent >= 85.0
        }
        
        print(f"✅ Storage usage: {result['used_gb']:.2f} GB / {result['limit_gb']:.2f} GB ({result['usage_percent']:.1f}%)")
        
        if result['is_critical']:
            print("⚠️  КРИТИЧНО: Использование > 93%")
        elif result['is_warning']:
            print("⚠️  Предупреждение: Использование > 85%")
        
        return result
        
    except ImportError as e:
        print(f"⚠️  S3StorageService недоступен (требуется монтирование api): {e}")
        print("   Пропускаем проверку Storage Quota")
        return {"error": "S3StorageService недоступен", "skipped": True}
    except Exception as e:
        print(f"❌ Ошибка проверки quota: {e}")
        return {"error": str(e)}


async def check_redis_streams() -> Dict[str, Any]:
    """
    Context7: Проверка Redis Streams для Vision событий.
    
    Returns:
        Dict с информацией о стримах
    """
    print("\n📨 Проверка Redis Streams...")
    
    try:
        redis_url = get_redis_url()
        redis_client = redis.from_url(redis_url, decode_responses=True)
        
        # Проверка стрима posts:vision
        stream_name = "posts:vision"
        stream_key = f"stream:{stream_name}"
        
        try:
            stream_info = await redis_client.xinfo_stream(stream_key)
            length = stream_info.get("length", 0)
            
            # Получаем последние события
            last_events = await redis_client.xrevrange(stream_key, count=5)
            
            result = {
                "stream_exists": True,
                "length": length,
                "last_events_count": len(last_events),
                "sample_events": [
                    {
                        "event_id": event_id,
                        "data": {k: v for k, v in data.items() if k != "trace_id"}
                    }
                    for event_id, data in last_events[:2]
                ]
            }
            
            print(f"✅ Stream {stream_name}: {length} событий")
            
        except Exception as e:
            if "no such key" in str(e).lower():
                result = {"stream_exists": False, "length": 0}
                print(f"⚠️  Stream {stream_name} не найден (ожидаемо для новых систем)")
            else:
                raise
        
        await redis_client.close()
        return result
        
    except Exception as e:
        print(f"❌ Ошибка проверки Redis: {e}")
        return {"error": str(e)}


async def trigger_vision_event_for_post(post_id: UUID) -> Dict[str, Any]:
    """
    Context7: Эмиссия VisionUploadedEvent для существующего поста с медиа.
    
    Args:
        post_id: UUID поста
        
    Returns:
        Dict с результатом операции
    """
    print(f"\n🚀 Эмиссия VisionUploadedEvent для поста {post_id}...")
    
    conn = await asyncpg.connect(get_db_connection_string())
    redis_url = get_redis_url()
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        # Получаем информацию о посте
        post_data = await conn.fetchrow("""
            SELECT p.id, p.channel_id, p.telegram_message_id, p.has_media,
                   (SELECT COUNT(*) FROM post_media pm WHERE pm.post_id = p.id) as media_count
            FROM posts p
            WHERE p.id = $1
        """, post_id)
        
        if not post_data:
            return {"error": f"Пост {post_id} не найден"}
        
        if not post_data["has_media"] or (post_data["media_count"] or 0) == 0:
            return {"error": f"Пост {post_id} не содержит медиа (has_media={post_data['has_media']}, count={post_data['media_count']})"}
        
        # Получаем media objects для поста (сначала пробуем через post_media_map, потом через post_media)
        media_objects = await conn.fetch("""
            SELECT mo.file_sha256, mo.mime, mo.size_bytes, mo.s3_key, pmm.position, pmm.role
            FROM post_media_map pmm
            JOIN media_objects mo ON pmm.file_sha256 = mo.file_sha256
            WHERE pmm.post_id = $1
            ORDER BY pmm.position
        """, post_id)
        
        # Если нет в post_media_map, пробуем через post_media (legacy)
        if not media_objects:
            post_media_records = await conn.fetch("""
                SELECT pm.id, pm.media_type, pm.sha256, pm.file_size_bytes, pm.media_url
                FROM post_media pm
                WHERE pm.post_id = $1
                ORDER BY pm.id
            """, post_id)
            
            if not post_media_records:
                return {
                    "error": f"Media objects для поста {post_id} не найдены",
                    "hint": "Пост имеет has_media=true, но нет записей в post_media_map или post_media. Медиа может быть только в Telegram."
                }
            
            # Если есть только post_media без media_objects - медиа ещё не загружено в S3
            return {
                "error": f"Media для поста {post_id} есть в post_media, но не загружено в S3",
                "hint": "Медиа нужно загрузить через MediaProcessor. Используй telethon-ingest для обработки поста.",
                "post_media_count": len(post_media_records)
            }
        
        # Формируем MediaFile объекты
        media_files = [
            MediaFile(
                sha256=row["file_sha256"],
                s3_key=row["s3_key"],
                mime_type=row["mime"],
                size_bytes=row["size_bytes"]
            )
            for row in media_objects
        ]
        
        # Создаём событие
        event = VisionUploadedEventV1(
            post_id=str(post_id),
            tenant_id=str(post_data["tenant_id"]) if post_data["tenant_id"] else os.getenv("S3_DEFAULT_TENANT_ID", ""),
            media_files=media_files,
            trace_id=f"test-{datetime.utcnow().isoformat()}",
            timestamp=datetime.utcnow()
        )
        
        # Эмитируем в Redis Stream
        stream_key = "stream:posts:vision"
        event_data = event.model_dump(mode="json")
        
        # Конвертируем UUID и datetime в строки для Redis
        for key, value in event_data.items():
            if isinstance(value, (UUID, datetime)):
                event_data[key] = str(value)
            elif isinstance(value, list):
                event_data = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in event_data.items()}
                break
        
        event_id = await redis_client.xadd(
            stream_key,
            event_data,
            maxlen=10000  # Context7: ограничение размера стрима
        )
        
        result = {
            "success": True,
            "event_id": event_id.decode() if isinstance(event_id, bytes) else event_id,
            "post_id": str(post_id),
            "media_files_count": len(media_files),
            "stream_key": stream_key
        }
        
        print(f"✅ Событие отправлено: {result['event_id']}")
        print(f"✅ Media files: {len(media_files)}")
        
        return result
        
    except Exception as e:
        print(f"❌ Ошибка эмиссии события: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
        
    finally:
        await conn.close()
        await redis_client.close()


async def full_pipeline_test() -> Dict[str, Any]:
    """
    Context7: Полный функциональный тест пайплайна.
    
    Returns:
        Dict с результатами тестирования
    """
    print("\n" + "="*80)
    print("🧪 ПОЛНЫЙ ФУНКЦИОНАЛЬНЫЙ ТЕСТ VISION + S3 PIPELINE")
    print("="*80)
    
    results = {}
    
    # 1. Проверка БД
    results["database"] = await check_database_status()
    
    # 2. Проверка Storage Quota
    results["storage_quota"] = await check_storage_quota()
    
    # 3. Проверка Redis Streams
    results["redis_streams"] = await check_redis_streams()
    
    # 4. Если есть посты с медиа без vision анализа - эмитируем событие для первого
    if results["database"].get("posts_without_vision", 0) > 0:
        sample_post_id = results["database"]["sample_posts"][0]["id"]
        print(f"\n🔄 Триггер Vision события для поста {sample_post_id}...")
        results["triggered_event"] = await trigger_vision_event_for_post(UUID(sample_post_id))
        
        # Ждём немного для обработки
        print("\n⏳ Ожидание обработки события (10 секунд)...")
        await asyncio.sleep(10)
        
        # Повторная проверка БД
        print("\n📊 Повторная проверка БД после обработки...")
        results["database_after"] = await check_database_status()
    else:
        print("\n⚠️  Нет постов с медиа для тестирования")
    
    return results


async def main():
    """Context7: Главная функция тестирования."""
    parser = argparse.ArgumentParser(description="Функциональное тестирование Vision + S3 Pipeline")
    parser.add_argument("--check-status", action="store_true", help="Проверка текущего статуса")
    parser.add_argument("--trigger-vision", action="store_true", help="Эмиссия Vision события")
    parser.add_argument("--post-id", type=str, help="UUID поста для триггера")
    parser.add_argument("--full-test", action="store_true", help="Полный тест пайплайна")
    
    args = parser.parse_args()
    
    if args.check_status:
        await check_database_status()
        try:
            await check_storage_quota()
        except Exception as e:
            print(f"\n⚠️  Storage Quota проверка пропущена: {e}")
        await check_redis_streams()
    elif args.trigger_vision:
        if not args.post_id:
            print("❌ Требуется --post-id для триггера")
            sys.exit(1)
        result = await trigger_vision_event_for_post(UUID(args.post_id))
        print("\n📋 Результат:")
        print(json.dumps(result, indent=2, default=str))
    elif args.full_test:
        results = await full_pipeline_test()
        print("\n" + "="*80)
        print("📋 ИТОГОВЫЙ ОТЧЁТ")
        print("="*80)
        print(json.dumps(results, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
