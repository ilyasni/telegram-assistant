#!/usr/bin/env python3
"""
Context7: Ручной триггер Vision события для существующего поста с медиа из post_media.

Использование:
    python scripts/test_vision_manual_trigger.py --post-id <uuid>
    python scripts/test_vision_manual_trigger.py --process-oldest 5
"""

import asyncio
import os
import sys
import argparse
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
import hashlib

# Context7: Настройка путей для cross-service импортов
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Worker контейнер: /app
if os.path.exists('/app'):
    if '/app' not in sys.path:
        sys.path.insert(0, '/app')
    api_mount = '/opt/telegram-assistant/api'
    if api_mount not in sys.path and os.path.exists(api_mount):
        sys.path.insert(0, api_mount)

import asyncpg
import redis.asyncio as redis

# Context7: Импорт схем событий с fallback
try:
    # Попытка 1: стандартный импорт
    from events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
except ImportError:
    try:
        # Попытка 2: прямой импорт из файла
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'posts_vision_v1',
            '/app/events/schemas/posts_vision_v1.py'
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            VisionUploadedEventV1 = mod.VisionUploadedEventV1
            MediaFile = mod.MediaFile
        else:
            raise ImportError("Cannot load posts_vision_v1 module")
    except Exception:
        # Попытка 3: через worker.events
        from worker.events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile


def get_db_connection_string() -> str:
    """Context7: Получение строки подключения к БД (используем единую утилиту)."""
    from shared.utils.db_connection import get_database_url
    return get_database_url(kind="rw", async_=False)


def get_redis_url() -> str:
    """Context7: Получение URL Redis."""
    return os.getenv("REDIS_URL", "redis://redis:6379")


async def create_vision_event_from_post_media(
    post_id: UUID,
    tenant_id: str = None
) -> Dict[str, Any]:
    """
    Context7: Создание VisionUploadedEventV1 для поста с медиа из post_media.
    
    Используется когда медиа есть в post_media, но не загружено в S3 через MediaProcessor.
    В этом случае создаём синтетическое событие для тестирования Vision pipeline.
    
    Args:
        post_id: UUID поста
        tenant_id: ID tenant (опционально)
        
    Returns:
        Dict с результатом операции
    """
    print(f"\n🚀 Создание Vision события для поста {post_id}...")
    
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
            return {"error": f"Пост {post_id} не содержит медиа"}
        
        # Получаем post_media записи
        post_media_records = await conn.fetch("""
            SELECT pm.id, pm.media_type, pm.sha256, pm.file_size_bytes, pm.media_url,
                   pm.thumbnail_url, pm.width, pm.height
            FROM post_media pm
            WHERE pm.post_id = $1
            ORDER BY pm.id
        """, post_id)
        
        # Context7: Если нет post_media записей, но has_media=true - создаём синтетическое событие
        if not post_media_records:
            print(f"⚠️  Создание синтетического MediaFile для тестирования (медиа не обработано через MediaProcessor)")
            
            # Создаём один синтетический MediaFile для тестирования
            sha256_source = f"{post_id}:test_media"
            sha256 = hashlib.sha256(sha256_source.encode()).hexdigest()
            
            # Context7: Для тестирования используем синтетический s3_key
            # В реальности MediaProcessor скачает медиа и загрузит в S3
            mime_type = "image/jpeg"
            ext = "jpg"
            s3_key = f"media/{tenant_id}/{sha256[:2]}/{sha256}.{ext}"
            
            media_file = MediaFile(
                sha256=sha256,
                s3_key=s3_key,
                mime_type=mime_type,
                size_bytes=1024 * 100,  # 100 KB для теста
                telegram_file_id=None
            )
            
            media_files = [media_file]
            print(f"  ✅ Создан синтетический MediaFile: SHA256={sha256[:16]}..., size=100KB")
            print(f"  ⚠️  ВАЖНО: Этот файл не существует в S3 - Vision Analysis может упасть")
            print(f"  ⚠️  Для реального тестирования нужно обработать пост через MediaProcessor")
        
        # Context7: Получаем tenant_id из поста или используем default
        if not tenant_id:
            # Пробуем получить из канала
            channel_data = await conn.fetchrow("""
                SELECT tenant_id FROM channels WHERE id = $1
            """, post_data["channel_id"])
            
            tenant_id = channel_data["tenant_id"] if channel_data and channel_data.get("tenant_id") else os.getenv("S3_DEFAULT_TENANT_ID", "")
        
        # Формируем MediaFile объекты из post_media
        # Context7: Если sha256 нет - генерируем из media_url или используем синтетический
        media_files = []
        for idx, pm in enumerate(post_media_records):
            # Пробуем получить sha256
            sha256 = pm["sha256"]
            
            if not sha256:
                # Context7: Если sha256 нет - генерируем синтетический для тестирования
                # В реальности MediaProcessor вычисляет SHA256 из содержимого файла
                sha256_source = pm["media_url"] or f"{post_id}:{pm['id']}"
                sha256 = hashlib.sha256(sha256_source.encode()).hexdigest()
                print(f"⚠️  Генерирован синтетический SHA256 для media {pm['id']}")
            
            # Формируем s3_key (контент-адресуемый путь)
            # Context7: Формат: media/{tenant}/{sha256[:2]}/{sha256}.{ext}
            mime_type = "image/jpeg"  # Default, можно определить из media_type
            if pm["media_type"] == "photo":
                mime_type = "image/jpeg"
            elif pm["media_type"] == "video":
                mime_type = "video/mp4"
            elif pm["media_type"] == "document":
                mime_type = "application/octet-stream"
            
            ext = "jpg" if "image" in mime_type else "mp4" if "video" in mime_type else "bin"
            s3_key = f"media/{tenant_id}/{sha256[:2]}/{sha256}.{ext}"
            
            media_file = MediaFile(
                sha256=sha256,
                s3_key=s3_key,
                mime_type=mime_type,
                size_bytes=pm["file_size_bytes"] or 0,
                telegram_file_id=None  # Не доступно для старых постов
            )
            
            media_files.append(media_file)
            print(f"  ✅ Media {idx+1}: {mime_type}, {pm['file_size_bytes'] or 0} bytes, SHA256={sha256[:16]}...")
        
        # Создаём событие
        trace_id = f"manual-test-{datetime.utcnow().isoformat()}"
        event = VisionUploadedEventV1(
            post_id=str(post_id),
            tenant_id=tenant_id,
            media_files=media_files,
            trace_id=trace_id,
            timestamp=datetime.utcnow()
        )
        
        # Эмитируем в Redis Stream
        stream_key = "stream:posts:vision"
        
        # Context7: Конвертируем event в формат для Redis
        event_data = {}
        event_data["event"] = "posts.vision.uploaded"
        event_data["data"] = event.model_dump_json()
        event_data["trace_id"] = trace_id
        event_data["timestamp"] = datetime.utcnow().isoformat()
        
        # Конвертируем все значения в строки для Redis
        redis_event_data = {}
        for key, value in event_data.items():
            if isinstance(value, (UUID, datetime)):
                redis_event_data[key] = str(value)
            elif isinstance(value, (dict, list)):
                redis_event_data[key] = json.dumps(value)
            else:
                redis_event_data[key] = str(value)
        
        event_id = await redis_client.xadd(
            stream_key,
            redis_event_data,
            maxlen=10000  # Context7: ограничение размера стрима
        )
        
        result = {
            "success": True,
            "event_id": event_id.decode() if isinstance(event_id, bytes) else event_id,
            "post_id": str(post_id),
            "media_files_count": len(media_files),
            "stream_key": stream_key,
            "trace_id": trace_id
        }
        
        print(f"\n✅ Событие отправлено в Redis Stream:")
        print(f"   Stream: {stream_key}")
        print(f"   Event ID: {result['event_id']}")
        print(f"   Media files: {len(media_files)}")
        print(f"   Trace ID: {trace_id}")
        
        return result
        
    except Exception as e:
        print(f"❌ Ошибка создания события: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
        
    finally:
        await conn.close()
        await redis_client.aclose()  # Context7: Используем aclose() вместо close()


async def process_oldest_posts_with_media(count: int = 5) -> Dict[str, Any]:
    """
    Context7: Обработка старейших постов с медиа для тестирования.
    
    Args:
        count: Количество постов для обработки
        
    Returns:
        Dict с результатами
    """
    print(f"\n🔄 Обработка {count} старейших постов с медиа...")
    
    conn = await asyncpg.connect(get_db_connection_string())
    
    try:
        # Получаем старейшие посты с медиа
        posts = await conn.fetch("""
            SELECT p.id, p.channel_id, p.telegram_message_id, p.has_media,
                   (SELECT COUNT(*) FROM post_media pm WHERE pm.post_id = p.id) as media_count
            FROM posts p
            WHERE p.has_media = true
            ORDER BY p.created_at ASC
            LIMIT $1
        """, count)
        
        if not posts:
            return {"error": "Посты с медиа не найдены"}
        
        results = []
        for post in posts:
            post_id = post["id"]
            print(f"\n📋 Обработка поста {post_id}...")
            result = await create_vision_event_from_post_media(post_id)
            results.append({
                "post_id": str(post_id),
                "result": result
            })
            
            # Небольшая задержка между постами
            await asyncio.sleep(1)
        
        return {
            "processed": len(results),
            "results": results
        }
        
    finally:
        await conn.close()


async def main():
    """Context7: Главная функция."""
    parser = argparse.ArgumentParser(description="Ручной триггер Vision событий для тестирования")
    parser.add_argument("--post-id", type=str, help="UUID поста для триггера")
    parser.add_argument("--process-oldest", type=int, help="Количество старейших постов для обработки")
    
    args = parser.parse_args()
    
    if args.post_id:
        result = await create_vision_event_from_post_media(UUID(args.post_id))
        print("\n📋 Результат:")
        print(json.dumps(result, indent=2, default=str))
    elif args.process_oldest:
        result = await process_oldest_posts_with_media(args.process_oldest)
        print("\n📋 Результаты:")
        print(json.dumps(result, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())

