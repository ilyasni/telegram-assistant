#!/usr/bin/env python3
"""
Тестирование пайплайна альбомов end-to-end
- Создание тестового альбома
- Эмуляция события albums.parsed
- Эмуляция событий posts.vision.analyzed
- Проверка сборки альбома
"""

import asyncio
import sys
import os
import json
from datetime import datetime, timezone
from uuid import uuid4

project_root = '/opt/telegram-assistant'
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
import redis.asyncio as redis

async def test_album_pipeline():
    """Тестирует пайплайн альбомов end-to-end."""
    
    print("🧪 Тестирование пайплайна альбомов")
    print("=" * 60)
    
    # Подключение к БД
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    # Подключение к Redis
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    try:
        async with async_session() as session:
            # 1. Получаем существующий канал
            result = await session.execute(text("SELECT id FROM channels LIMIT 1"))
            row = result.fetchone()
            
            if not row:
                print("❌ Нет каналов в БД")
                return False
            
            channel_id = str(row[0])
            user_id = str(uuid4())
            tenant_id = str(uuid4())
            grouped_id = 999999999
            
            print(f"✅ Используем канал: {channel_id}")
            
            # 2. Создаём посты для альбома (используем уникальные telegram_message_id)
            import time
            timestamp = int(time.time())
            post_ids = []
            for i in range(3):
                post_id = str(uuid4())
                post_ids.append(post_id)
                
                # Используем уникальный telegram_message_id с timestamp
                telegram_message_id = timestamp + i
                
                await session.execute(text("""
                    INSERT INTO posts (
                        id, channel_id, content, posted_at, created_at,
                        is_processed, has_media, grouped_id, telegram_message_id
                    ) VALUES (
                        :post_id, :channel_id, 'Test album post ' || :num, NOW(), NOW(),
                        false, true, :grouped_id, :message_id
                    )
                    ON CONFLICT (channel_id, telegram_message_id) DO NOTHING
                """), {
                    "post_id": post_id,
                    "channel_id": channel_id,
                    "grouped_id": grouped_id,
                    "message_id": telegram_message_id,
                    "num": str(i + 1)
                })
            
            await session.commit()
            print(f"✅ Создано постов: {len(post_ids)}")
            
            # 3. Создаём альбом в media_groups
            import hashlib
            content_parts = [str(grouped_id)] + sorted(post_ids)
            content_string = "|".join(content_parts)
            content_hash = hashlib.sha256(content_string.encode()).hexdigest()[:16]
            
            result = await session.execute(text("""
                INSERT INTO media_groups (
                    user_id, channel_id, grouped_id, album_kind, items_count,
                    caption_text, posted_at, content_hash
                ) VALUES (
                    :user_id, :channel_id, :grouped_id, :album_kind, :items_count,
                    :caption_text, :posted_at, :content_hash
                )
                ON CONFLICT (user_id, channel_id, grouped_id)
                DO UPDATE SET
                    album_kind = EXCLUDED.album_kind,
                    items_count = EXCLUDED.items_count,
                    caption_text = EXCLUDED.caption_text,
                    posted_at = EXCLUDED.posted_at
                RETURNING id
            """), {
                "user_id": user_id,
                "channel_id": channel_id,
                "grouped_id": grouped_id,
                "album_kind": "photo",
                "items_count": len(post_ids),
                "caption_text": "Тестовый альбом для проверки пайплайна",
                "posted_at": datetime.now(timezone.utc),
                "content_hash": content_hash
            })
            
            group_id = result.scalar()
            if not group_id:
                print("❌ Не удалось создать альбом")
                return False
            
            print(f"✅ Альбом создан: group_id={group_id}")
            
            # 4. Создаём записи в media_group_items
            # Context7: Удаляем старые элементы перед вставкой (как в save_media_group)
            await session.execute(text("""
                DELETE FROM media_group_items WHERE group_id = :group_id
            """), {"group_id": group_id})
            
            for position, post_id in enumerate(post_ids, start=0):
                await session.execute(text("""
                    INSERT INTO media_group_items (
                        group_id, post_id, position, media_type, media_kind
                    ) VALUES (
                        :group_id, :post_id, :position, :media_type, :media_kind
                    )
                    ON CONFLICT (group_id, position) DO UPDATE SET
                        post_id = EXCLUDED.post_id,
                        media_type = EXCLUDED.media_type,
                        media_kind = EXCLUDED.media_kind
                """), {
                    "group_id": group_id,
                    "post_id": post_id,
                    "position": position,
                    "media_type": "photo",
                    "media_kind": "photo"
                })
            
            await session.commit()
            print(f"✅ Элементы альбома созданы")
            
            # 5. Создаём vision результаты для постов
            for post_id in post_ids:
                await session.execute(text("""
                    INSERT INTO post_enrichment (
                        post_id, kind, provider, status, data, updated_at
                    ) VALUES (
                        :post_id, 'vision', :provider, :status, :data, NOW()
                    )
                    ON CONFLICT (post_id, kind) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        status = EXCLUDED.status,
                        data = EXCLUDED.data,
                        updated_at = EXCLUDED.updated_at
                """), {
                    "post_id": post_id,
                    "provider": "gigachat",
                    "status": "ok",
                    "data": json.dumps({
                        "description": f"Vision description for post {post_id[:8]}",
                        "labels": ["test", "album"],
                        "is_meme": False,
                        "ocr_text": f"Test text {post_id[:8]}"
                    })
                })
            
            await session.commit()
            print(f"✅ Vision результаты созданы")
            
            # 6. Эмулируем событие albums.parsed
            album_event = {
                "album_id": str(group_id),
                "grouped_id": grouped_id,
                "channel_id": channel_id,
                "tenant_id": tenant_id,
                "post_ids": json.dumps(post_ids),
                "items_count": len(post_ids),
                "album_kind": "photo",
                "caption_text": "Тестовый альбом для проверки пайплайна"
            }
            
            await redis_client.xadd(
                "albums.parsed",
                album_event
            )
            print(f"✅ Событие albums.parsed отправлено")
            
            # 7. Эмулируем события posts.vision.analyzed
            for post_id in post_ids:
                vision_event = {
                    "post_id": post_id,
                    "channel_id": channel_id,
                    "tenant_id": tenant_id,
                    "status": "success",
                    "analyzed_at": datetime.now(timezone.utc).isoformat()
                }
                
                await redis_client.xadd(
                    "posts.vision.analyzed",
                    vision_event
                )
                print(f"✅ Событие posts.vision.analyzed отправлено для post_id={post_id[:8]}")
                await asyncio.sleep(0.5)  # Небольшая задержка между событиями
            
            # 8. Ждём обработки
            print("\n⏳ Ожидание обработки (10 секунд)...")
            await asyncio.sleep(10)
            
            # 9. Проверяем результаты
            print("\n🔍 Проверка результатов:")
            
            # Проверяем состояние альбома в Redis
            state_key = f"album:state:{group_id}"
            state_json = await redis_client.get(state_key)
            if state_json:
                state = json.loads(state_json)
                print(f"✅ Состояние альбома в Redis:")
                print(f"   - items_count: {state.get('items_count')}")
                print(f"   - items_analyzed: {len(state.get('items_analyzed', []))}")
                print(f"   - vision_summaries: {len(state.get('vision_summaries', []))}")
            else:
                print(f"⚠️  Состояние альбома не найдено в Redis")
            
            # Проверяем событие album.assembled
            assembled_events = await redis_client.xread(
                {"album.assembled": "0"},
                count=10
            )
            
            if assembled_events:
                print(f"✅ Событие album.assembled найдено: {len(assembled_events)}")
                for stream, messages in assembled_events:
                    for msg_id, fields in messages:
                        print(f"   - Message ID: {msg_id}")
                        print(f"   - Fields: {fields}")
            else:
                print(f"⚠️  Событие album.assembled не найдено")
            
            # Проверяем метаданные альбома в БД
            result = await session.execute(text("""
                SELECT meta FROM media_groups WHERE id = :group_id
            """), {"group_id": group_id})
            
            row = result.fetchone()
            if row and row[0]:
                meta = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                print(f"✅ Метаданные альбома в БД:")
                print(f"   - meta: {json.dumps(meta, indent=2)}")
            else:
                print(f"⚠️  Метаданные альбома не найдены")
            
            print("\n" + "=" * 60)
            print("✅ Тестирование завершено")
            return True
                
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        await engine.dispose()
        await redis_client.aclose()


if __name__ == "__main__":
    success = asyncio.run(test_album_pipeline())
    sys.exit(0 if success else 1)

