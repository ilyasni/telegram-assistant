#!/usr/bin/env python3
"""
Создание тестового альбома для проверки пайплайна
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from uuid import uuid4

project_root = '/opt/telegram-assistant'
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

async def create_test_album():
    """Создаёт тестовый альбом с постами для проверки пайплайна."""
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Используем существующий канал
            result = await session.execute(text("SELECT id FROM channels LIMIT 1"))
            row = result.fetchone()
            
            if not row:
                print("❌ Нет каналов в БД для создания тестового альбома")
                return None, None
            
            channel_id = str(row[0])
            user_id = str(uuid4())
            grouped_id = 999999999  # Тестовый grouped_id
            
            print(f"  ℹ️  Используем существующий канал: {channel_id}")
            
            # Создаём несколько постов для альбома
            post_ids = []
            for i in range(3):
                post_id = str(uuid4())
                post_ids.append(post_id)
                
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
                    "message_id": 1000 + i,
                    "num": str(i + 1)  # Преобразуем в строку
                })
            
            await session.commit()
            
            # Создаём альбом напрямую через SQL (имитируя save_media_group)
            caption_text = "Тестовый альбом для проверки пайплайна"
            posted_at = datetime.now(timezone.utc)
            album_kind = "photo"  # Все элементы - фото
            
            # Определяем items_count
            items_count = len(post_ids)
            
            # Вычисляем content_hash
            import hashlib
            content_parts = [str(grouped_id)] + sorted(post_ids)
            content_string = "|".join(content_parts)
            content_hash = hashlib.sha256(content_string.encode()).hexdigest()[:16]
            
            # Создаём запись в media_groups
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
                "album_kind": album_kind,
                "items_count": items_count,
                "caption_text": caption_text,
                "posted_at": posted_at,
                "content_hash": content_hash
            })
            
            group_id = result.scalar()
            
            if not group_id:
                print("❌ Не удалось создать альбом (group_id is None)")
                return None, None
            
            # Создаём записи в media_group_items (media_type обязателен)
            for position, post_id in enumerate(post_ids, start=0):
                await session.execute(text("""
                    INSERT INTO media_group_items (
                        group_id, post_id, position, media_type, media_kind
                    ) VALUES (
                        :group_id, :post_id, :position, :media_type, :media_kind
                    )
                """), {
                    "group_id": group_id,
                    "post_id": post_id,
                    "position": position,
                    "media_type": "photo",  # Обязательное поле
                    "media_kind": "photo"   # Новое поле
                })
            
            await session.commit()
            
            print(f"✅ Тестовый альбом создан:")
            print(f"   - album_id (group_id): {group_id}")
            print(f"   - grouped_id: {grouped_id}")
            print(f"   - channel_id: {channel_id}")
            print(f"   - post_ids: {len(post_ids)}")
            print(f"   - caption: {caption_text}")
            return group_id, post_ids
                
    finally:
        await engine.dispose()


if __name__ == "__main__":
    result = asyncio.run(create_test_album())
    if result[0]:
        sys.exit(0)
    else:
        sys.exit(1)

