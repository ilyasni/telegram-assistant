#!/usr/bin/env python3
"""
Context7: Комплексный тест нововведений аудита медиа на реальных данных.

Проверяет:
1. Обработку медиа-альбомов
2. Интеграцию Vision в tagging (посты с коротким текстом + медиа)
3. Метрики обработки медиа
4. Media_sha256_list в событиях posts.parsed
5. Полный пайплайн: Parse → Media → Vision → Tagging → Enrichment

Использование:
    python scripts/test_media_audit_features.py --check-real-data
    python scripts/test_media_audit_features.py --test-post-id <uuid>
    python scripts/test_media_audit_features.py --full-pipeline-test
"""

import asyncio
import os
import sys
import argparse
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from uuid import UUID

# Context7: Настройка путей
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from worker.events.schemas.posts_parsed_v1 import PostParsedEventV1
    from worker.events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile
except ImportError:
    from events.schemas.posts_parsed_v1 import PostParsedEventV1
    from events.schemas.posts_vision_v1 import VisionUploadedEventV1, MediaFile

import asyncpg
import redis.asyncio as redis
from sqlalchemy import create_engine, text


def get_db_connection_string() -> str:
    """Context7: Получение строки подключения к БД (используем единую утилиту)."""
    from shared.utils.db_connection import get_database_url
    return get_database_url(kind="rw", async_=False)


async def check_real_data_status() -> Dict[str, Any]:
    """
    Context7: Проверка реальных данных для тестирования нововведений.
    
    Проверяет:
    - Посты с альбомами (grouped_id или несколько медиа)
    - Посты с коротким текстом + медиа (для проверки новой логики tagging)
    - Посты с Vision результатами
    - Посты с media_sha256_list в событиях
    """
    print("=" * 80)
    print("📊 ПРОВЕРКА РЕАЛЬНЫХ ДАННЫХ ДЛЯ ТЕСТИРОВАНИЯ НОВОВВЕДЕНИЙ")
    print("=" * 80)
    
    conn = await asyncpg.connect(get_db_connection_string())
    
    try:
        results = {}
        
        # 1. Посты с медиа (потенциальные альбомы)
        posts_with_media = await conn.fetch("""
            SELECT 
                p.id,
                p.channel_id,
                p.telegram_message_id,
                p.content,
                p.has_media,
                p.created_at,
                (SELECT COUNT(*) FROM post_media_map pmm WHERE pmm.post_id = p.id) as media_count,
                (SELECT COUNT(*) FROM post_media pm WHERE pm.post_id = p.id) as legacy_media_count
            FROM posts p
            WHERE p.has_media = true
            ORDER BY p.created_at DESC
            LIMIT 20
        """)
        
        results["posts_with_media"] = [
            {
                "id": str(row["id"]),
                "channel_id": str(row["channel_id"]),
                "telegram_message_id": row["telegram_message_id"],
                "content_length": len(row["content"] or ""),
                "has_media": row["has_media"],
                "media_count": row["media_count"] or 0,
                "legacy_media_count": row["legacy_media_count"] or 0,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "is_album_candidate": (row["media_count"] or 0) > 1
            }
            for row in posts_with_media
        ]
        
        # 2. Посты с коротким текстом + медиа (для проверки новой логики tagging)
        MIN_CHARS = int(os.getenv("TAGGING_MIN_CHARS", "80"))
        posts_short_text_with_media = await conn.fetch("""
            SELECT 
                p.id,
                p.channel_id,
                p.content,
                LENGTH(p.content) as text_length,
                (SELECT COUNT(*) FROM post_media_map pmm WHERE pmm.post_id = p.id) as media_count
            FROM posts p
            WHERE p.has_media = true
            AND LENGTH(COALESCE(p.content, '')) < $1
            ORDER BY p.created_at DESC
            LIMIT 10
        """, MIN_CHARS)
        
        results["posts_short_text_with_media"] = [
            {
                "id": str(row["id"]),
                "channel_id": str(row["channel_id"]),
                "text_length": row["text_length"],
                "media_count": row["media_count"] or 0,
                "content_preview": (row["content"] or "")[:100]
            }
            for row in posts_short_text_with_media
        ]
        
        # 3. Посты с Vision результатами
        posts_with_vision = await conn.fetch("""
            SELECT 
                p.id,
                p.channel_id,
                pe.vision_provider,
                pe.vision_analyzed_at,
                pe.vision_description,
                pe.vision_ocr_text,
                (SELECT COUNT(*) FROM post_media_map pmm WHERE pmm.post_id = p.id) as media_count
            FROM posts p
            JOIN post_enrichment pe ON pe.post_id = p.id
            WHERE pe.vision_analyzed_at IS NOT NULL
            ORDER BY pe.vision_analyzed_at DESC
            LIMIT 10
        """)
        
        results["posts_with_vision"] = [
            {
                "id": str(row["id"]),
                "channel_id": str(row["channel_id"]),
                "provider": row["vision_provider"],
                "analyzed_at": row["vision_analyzed_at"].isoformat() if row["vision_analyzed_at"] else None,
                "has_description": bool(row["vision_description"]),
                "has_ocr": bool(row["vision_ocr_text"]),
                "media_count": row["media_count"] or 0
            }
            for row in posts_with_vision
        ]
        
        # 4. Media objects статистика
        media_stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_objects,
                COUNT(DISTINCT mime) as unique_mimes,
                SUM(size_bytes) as total_size_bytes,
                AVG(size_bytes) as avg_size_bytes
            FROM media_objects
        """)
        
        results["media_statistics"] = {
            "total_objects": media_stats["total_objects"],
            "unique_mimes": media_stats["unique_mimes"],
            "total_size_gb": (media_stats["total_size_bytes"] or 0) / (1024**3),
            "avg_size_kb": (media_stats["avg_size_bytes"] or 0) / 1024
        }
        
        # 5. Post-media-map статистика (новый способ хранения)
        map_stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total_links,
                COUNT(DISTINCT post_id) as posts_with_media,
                COUNT(DISTINCT file_sha256) as unique_media_files,
                COUNT(*) FILTER (WHERE role = 'primary') as primary_media,
                COUNT(*) FILTER (WHERE role = 'attachment') as attachment_media
            FROM post_media_map
        """)
        
        results["post_media_map_statistics"] = {
            "total_links": map_stats["total_links"],
            "posts_with_media": map_stats["posts_with_media"],
            "unique_media_files": map_stats["unique_media_files"],
            "primary_media": map_stats["primary_media"],
            "attachment_media": map_stats["attachment_media"]
        }
        
        # Вывод результатов
        print(f"\n✅ Постов с медиа: {len(results['posts_with_media'])}")
        print(f"   Альбомы (candidates): {sum(1 for p in results['posts_with_media'] if p['is_album_candidate'])}")
        print(f"\n✅ Постов с коротким текстом + медиа: {len(results['posts_short_text_with_media'])}")
        print(f"   (текст < {MIN_CHARS} символов, но есть медиа)")
        print(f"\n✅ Постов с Vision результатами: {len(results['posts_with_vision'])}")
        print(f"\n📊 Media Objects:")
        print(f"   Всего: {results['media_statistics']['total_objects']}")
        print(f"   Уникальных MIME: {results['media_statistics']['unique_mimes']}")
        print(f"   Общий размер: {results['media_statistics']['total_size_gb']:.2f} GB")
        print(f"\n📊 Post-Media-Map:")
        print(f"   Всего связей: {results['post_media_map_statistics']['total_links']}")
        print(f"   Постов с медиа: {results['post_media_map_statistics']['posts_with_media']}")
        print(f"   Уникальных медиа: {results['post_media_map_statistics']['unique_media_files']}")
        
        return results
        
    finally:
        await conn.close()


async def test_full_pipeline_for_post(post_id: UUID) -> Dict[str, Any]:
    """
    Context7: Полный E2E тест пайплайна для конкретного поста.
    
    Проверяет все этапы:
    1. Наличие медиа в БД (media_objects, post_media_map)
    2. Vision результаты (если есть)
    3. Tagging результаты с учетом Vision
    4. События posts.parsed с media_sha256_list
    """
    print("\n" + "=" * 80)
    print(f"🧪 ПОЛНЫЙ E2E ТЕСТ ПАЙПЛАЙНА ДЛЯ POST_ID: {post_id}")
    print("=" * 80)
    
    conn = await asyncpg.connect(get_db_connection_string())
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_client = redis.from_url(redis_url, decode_responses=False)
    
    try:
        results = {
            "post_id": str(post_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stages": {}
        }
        
        # Шаг 1: Проверка поста в БД
        print("\n📋 Шаг 1: Проверка поста в БД...")
        post_data = await conn.fetchrow("""
            SELECT 
                p.id,
                p.channel_id,
                p.telegram_message_id,
                p.content,
                p.has_media,
                p.created_at,
                LENGTH(p.content) as text_length
            FROM posts p
            WHERE p.id = $1
        """, post_id)
        
        if not post_data:
            results["error"] = f"Пост {post_id} не найден"
            print(f"❌ {results['error']}")
            return results
        
        results["stages"]["post_found"] = {
            "channel_id": str(post_data["channel_id"]),
            "telegram_message_id": post_data["telegram_message_id"],
            "has_media": post_data["has_media"],
            "text_length": post_data["text_length"],
            "created_at": post_data["created_at"].isoformat() if post_data["created_at"] else None
        }
        print(f"✅ Пост найден: channel_id={post_data['channel_id']}, has_media={post_data['has_media']}")
        
        # Шаг 2: Проверка медиа в post_media_map (новый способ)
        print("\n📋 Шаг 2: Проверка медиа в post_media_map...")
        media_files = await conn.fetch("""
            SELECT 
                mo.file_sha256,
                mo.mime,
                mo.size_bytes,
                mo.s3_key,
                pmm.position,
                pmm.role
            FROM post_media_map pmm
            JOIN media_objects mo ON pmm.file_sha256 = mo.file_sha256
            WHERE pmm.post_id = $1
            ORDER BY pmm.position
        """, post_id)
        
        results["stages"]["media_files"] = [
            {
                "sha256": row["file_sha256"],
                "mime": row["mime"],
                "size_bytes": row["size_bytes"],
                "size_kb": row["size_bytes"] / 1024,
                "position": row["position"],
                "role": row["role"],
                "s3_key": row["s3_key"]
            }
            for row in media_files
        ]
        
        print(f"✅ Найдено медиа файлов: {len(media_files)}")
        if media_files:
            for i, mf in enumerate(results["stages"]["media_files"]):
                print(f"   {i+1}. {mf['mime']} ({mf['size_kb']:.1f} KB) - {mf['sha256'][:16]}...")
        
        # Шаг 3: Проверка Vision результатов
        print("\n📋 Шаг 3: Проверка Vision результатов...")
        vision_data = await conn.fetchrow("""
            SELECT 
                pe.vision_provider,
                pe.vision_model,
                pe.vision_analyzed_at,
                pe.vision_description,
                pe.vision_ocr_text,
                pe.vision_is_meme,
                pe.vision_tokens_used,
                pe.data->>'description' as data_description,
                pe.data->>'ocr_text' as data_ocr
            FROM post_enrichment pe
            WHERE pe.post_id = $1 AND pe.kind = 'vision'
        """, post_id)
        
        if vision_data:
            results["stages"]["vision_analysis"] = {
                "provider": vision_data["vision_provider"],
                "model": vision_data["vision_model"],
                "analyzed_at": vision_data["vision_analyzed_at"].isoformat() if vision_data["vision_analyzed_at"] else None,
                "has_description": bool(vision_data["vision_description"] or vision_data["data_description"]),
                "has_ocr": bool(vision_data["vision_ocr_text"] or vision_data["data_ocr"]),
                "is_meme": vision_data["vision_is_meme"],
                "tokens_used": vision_data["vision_tokens_used"],
                "description_preview": (vision_data["vision_description"] or vision_data["data_description"] or "")[:200],
                "ocr_preview": (vision_data["vision_ocr_text"] or vision_data["data_ocr"] or "")[:200]
            }
            print(f"✅ Vision анализ выполнен:")
            print(f"   Provider: {vision_data['vision_provider']}")
            print(f"   Description: {'✅' if results['stages']['vision_analysis']['has_description'] else '❌'}")
            print(f"   OCR: {'✅' if results['stages']['vision_analysis']['has_ocr'] else '❌'}")
            if results["stages"]["vision_analysis"]["description_preview"]:
                print(f"   Preview: {results['stages']['vision_analysis']['description_preview'][:100]}...")
        else:
            results["stages"]["vision_analysis"] = None
            print("⚠️  Vision анализ не найден")
        
        # Шаг 4: Проверка Tagging результатов
        print("\n📋 Шаг 4: Проверка Tagging результатов...")
        tagging_data = await conn.fetchrow("""
            SELECT 
                pe.provider,
                pe.data->>'tags' as tags_json,
                pe.tags as legacy_tags,
                pe.created_at
            FROM post_enrichment pe
            WHERE pe.post_id = $1 AND pe.kind = 'tags'
            ORDER BY pe.created_at DESC
            LIMIT 1
        """, post_id)
        
        if tagging_data:
            # Парсим теги из data или legacy поля
            import json as json_lib
            try:
                tags = json_lib.loads(tagging_data["tags_json"]) if tagging_data["tags_json"] else []
            except:
                tags = tagging_data["legacy_tags"] if tagging_data["legacy_tags"] else []
            
            results["stages"]["tagging"] = {
                "provider": tagging_data["provider"],
                "tags_count": len(tags) if isinstance(tags, list) else 0,
                "tags": tags[:10] if isinstance(tags, list) else [],
                "created_at": tagging_data["created_at"].isoformat() if tagging_data["created_at"] else None
            }
            print(f"✅ Tagging выполнен:")
            print(f"   Provider: {tagging_data['provider']}")
            print(f"   Тегов: {results['stages']['tagging']['tags_count']}")
            if results["stages"]["tagging"]["tags"]:
                print(f"   Примеры: {', '.join(results['stages']['tagging']['tags'][:5])}")
        else:
            results["stages"]["tagging"] = None
            print("⚠️  Tagging не выполнен")
        
        # Шаг 5: Проверка событий posts.parsed с media_sha256_list
        print("\n📋 Шаг 5: Проверка событий posts.parsed...")
        # Проверяем Redis Stream для последних событий
        try:
            stream_name = "stream:posts:parsed"
            stream_length = await redis_client.xlen(stream_name)
            print(f"✅ Stream posts:parsed: {stream_length} событий")
            
            # Получаем последние события
            last_events = await redis_client.xrevrange(stream_name, count=100)
            
            # Ищем событие для нашего поста
            post_event_found = False
            for event_id, event_data in last_events:
                try:
                    if isinstance(event_data.get(b'data'), bytes):
                        event_json = json.loads(event_data[b'data'].decode())
                    else:
                        event_json = json.loads(event_data.get('data', '{}'))
                    
                    if event_json.get('post_id') == str(post_id):
                        post_event_found = True
                        media_sha256_list = event_json.get('media_sha256_list', [])
                        results["stages"]["parsed_event"] = {
                            "event_id": event_id.decode() if isinstance(event_id, bytes) else event_id,
                            "has_media_sha256_list": bool(media_sha256_list),
                            "media_sha256_count": len(media_sha256_list),
                            "media_sha256_list": media_sha256_list[:5]  # Первые 5
                        }
                        print(f"✅ Событие posts.parsed найдено:")
                        print(f"   Event ID: {results['stages']['parsed_event']['event_id']}")
                        print(f"   Media SHA256 в событии: {len(media_sha256_list)}")
                        break
                except Exception as e:
                    continue
            
            if not post_event_found:
                results["stages"]["parsed_event"] = None
                print("⚠️  Событие posts.parsed для этого поста не найдено в последних 100 событиях")
                
        except Exception as e:
            results["stages"]["parsed_event"] = {"error": str(e)}
            print(f"⚠️  Ошибка проверки событий: {e}")
        
        # Шаг 6: Валидация полного пайплайна
        print("\n📋 Шаг 6: Валидация полного пайплайна...")
        validation_results = {
            "media_processed": len(media_files) > 0,
            "vision_completed": results["stages"]["vision_analysis"] is not None,
            "tagging_completed": results["stages"]["tagging"] is not None,
            "event_has_media_sha256": results["stages"].get("parsed_event", {}).get("has_media_sha256_list", False),
            "short_text_with_media": post_data["text_length"] < int(os.getenv("TAGGING_MIN_CHARS", "80")) and post_data["has_media"]
        }
        
        results["stages"]["validation"] = validation_results
        
        print("\n✅ Валидация:")
        print(f"   Медиа обработаны: {'✅' if validation_results['media_processed'] else '❌'}")
        print(f"   Vision выполнен: {'✅' if validation_results['vision_completed'] else '❌'}")
        print(f"   Tagging выполнен: {'✅' if validation_results['tagging_completed'] else '❌'}")
        print(f"   Event содержит media_sha256_list: {'✅' if validation_results['event_has_media_sha256'] else '❌'}")
        print(f"   Короткий текст + медиа: {'✅' if validation_results['short_text_with_media'] else '❌'}")
        
        if all([
            validation_results["media_processed"],
            validation_results["vision_completed"],
            validation_results["tagging_completed"],
            validation_results["event_has_media_sha256"]
        ]):
            results["success"] = True
            print("\n" + "=" * 80)
            print("✅ ВСЕ ЭТАПЫ ПАЙПЛАЙНА УСПЕШНО ПРОЙДЕНЫ!")
            print("=" * 80)
        else:
            results["success"] = False
            print("\n⚠️  Не все этапы пайплайна завершены")
        
        return results
        
    finally:
        await conn.close()
        await redis_client.close()


async def main():
    """Context7: Главная функция тестирования."""
    parser = argparse.ArgumentParser(
        description="Тестирование нововведений аудита медиа на реальных данных",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Проверка реальных данных
  python scripts/test_media_audit_features.py --check-real-data
  
  # Тест конкретного поста
  python scripts/test_media_audit_features.py --test-post-id <uuid>
  
  # Полный тест пайплайна для поста с медиа
  python scripts/test_media_audit_features.py --test-post-id <uuid> --full
        """
    )
    parser.add_argument("--check-real-data", action="store_true", 
                       help="Проверка реальных данных для тестирования")
    parser.add_argument("--test-post-id", type=str, 
                       help="UUID поста для тестирования")
    parser.add_argument("--full", action="store_true",
                       help="Полный тест пайплайна")
    
    args = parser.parse_args()
    
    if args.check_real_data:
        results = await check_real_data_status()
        print("\n" + "=" * 80)
        print("📋 ИТОГОВЫЙ ОТЧЁТ")
        print("=" * 80)
        print(json.dumps(results, indent=2, default=str, ensure_ascii=False))
    
    elif args.test_post_id:
        post_id = UUID(args.test_post_id)
        results = await test_full_pipeline_for_post(post_id)
        print("\n" + "=" * 80)
        print("📋 ИТОГОВЫЙ ОТЧЁТ")
        print("=" * 80)
        print(json.dumps(results, indent=2, default=str, ensure_ascii=False))
    
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())

