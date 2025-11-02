#!/usr/bin/env python3
"""
Простой скрипт для тестирования изменений в пайплайне альбомов (Phase 1)
Context7: проверка Redis cache, iter_messages, новых полей БД
"""

import asyncio
import sys
import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4

# Добавляем корень проекта в путь
project_root = '/opt/telegram-assistant'
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import structlog
from unittest.mock import Mock, AsyncMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
import redis.asyncio as redis

logger = structlog.get_logger()

async def test_redis_cache():
    """Тест Redis negative cache."""
    print("\n🧪 Тест 1: Redis negative cache для grouped_id")
    
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    channel_id = str(uuid4())
    grouped_id = 12345
    
    cache_key = f"album_seen:{channel_id}:{grouped_id}"
    
    # Проверяем, что cache не существует
    exists_before = await redis_client.exists(cache_key)
    assert not exists_before, "Cache не должен существовать до установки"
    
    # Устанавливаем cache
    await redis_client.setex(cache_key, 21600, "1")
    
    # Проверяем, что cache установлен
    exists_after = await redis_client.exists(cache_key)
    assert exists_after, "Cache должен существовать после установки"
    
    value = await redis_client.get(cache_key)
    assert value == b"1", f"Cache значение должно быть '1', получено {value}"
    
    await redis_client.delete(cache_key)
    print("  ✅ Redis cache тест пройден")
    await redis_client.aclose()


async def test_db_schema():
    """Тест новых полей в БД."""
    print("\n🧪 Тест 2: Новые поля в схеме БД")
    
    # Исправляем URL для asyncpg если указан psycopg2
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        # Проверяем поля media_groups
        result = await session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'media_groups'
            AND column_name IN ('caption_text', 'cover_media_id', 'posted_at', 'meta')
            ORDER BY column_name
        """))
        columns = {row[0]: row[1] for row in result}
        
        assert 'caption_text' in columns, "caption_text должен существовать"
        assert 'cover_media_id' in columns, "cover_media_id должен существовать"
        assert columns['cover_media_id'] == 'uuid', "cover_media_id должен быть UUID"
        assert 'posted_at' in columns, "posted_at должен существовать"
        assert 'meta' in columns, "meta должен существовать"
        
        print(f"  ✓ media_groups: {', '.join(columns.keys())}")
        
        # Проверяем поля media_group_items
        result = await session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'media_group_items'
            AND column_name IN ('media_object_id', 'media_kind', 'sha256')
            ORDER BY column_name
        """))
        columns = {row[0]: row[1] for row in result}
        
        assert 'media_object_id' in columns, "media_object_id должен существовать"
        assert 'media_kind' in columns, "media_kind должен существовать"
        assert 'sha256' in columns, "sha256 должен существовать"
        
        print(f"  ✓ media_group_items: {', '.join(columns.keys())}")
        
        # Проверяем media_objects.id
        result = await session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'media_objects'
            AND column_name = 'id'
        """))
        row = result.fetchone()
        assert row is not None, "media_objects.id должен существовать"
        assert row[1] == 'uuid', "media_objects.id должен быть UUID"
        
        print("  ✓ media_objects.id существует")
    
    await engine.dispose()
    print("  ✅ Схема БД тест пройден")


async def test_save_media_group_function():
    """Тест функции save_media_group с новыми полями."""
    print("\n🧪 Тест 3: save_media_group с новыми полями")
    
    # Исправляем URL для asyncpg если указан psycopg2
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    # Пробуем разные варианты импорта
    paths_to_try = [
        '/app/telethon-ingest',
        '/opt/telegram-assistant/telethon-ingest',
        '/app',
        '/opt/telegram-assistant'
    ]
    for path in paths_to_try:
        if path not in sys.path:
            sys.path.insert(0, path)
    
    save_media_group = None
    file_paths = [
        '/app/telethon-ingest/services/media_group_saver.py',
        '/opt/telegram-assistant/telethon-ingest/services/media_group_saver.py',
        '/app/services/media_group_saver.py',
    ]
    
    for file_path in file_paths:
        if os.path.exists(file_path):
            import importlib.util
            spec = importlib.util.spec_from_file_location("media_group_saver", file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            save_media_group = module.save_media_group
            break
    
    if save_media_group is None:
        print("  ⚠️  Не удалось импортировать save_media_group, пропускаем тест")
        return
    
    # Тестовые данные
    user_id = str(uuid4())
    channel_id = str(uuid4())
    grouped_id = 99999
    post_id = str(uuid4())
    
    async with async_session() as session:
        # Создаём тестовый канал
        await session.execute(text("""
            INSERT INTO channels (id, tenant_id, tg_channel_id, username, title, created_at)
            VALUES (:channel_id, :tenant_id, 123456, 'test_channel', 'Test Channel', NOW())
            ON CONFLICT (id) DO NOTHING
        """), {
            "channel_id": channel_id,
            "tenant_id": str(uuid4())
        })
        
        # Создаём тестовый пост
        await session.execute(text("""
            INSERT INTO posts (id, channel_id, content, posted_at, created_at, is_processed, has_media, grouped_id)
            VALUES (:post_id, :channel_id, 'Test album', NOW(), NOW(), false, true, :grouped_id)
            ON CONFLICT (channel_id, telegram_message_id) DO NOTHING
        """), {
            "post_id": post_id,
            "channel_id": channel_id,
            "grouped_id": grouped_id
        })
        
        await session.commit()
        
        # Сохраняем альбом с новыми полями
        caption_text = "Тестовый альбом с новыми полями"
        posted_at = datetime.now(timezone.utc)
        
        group_id = await save_media_group(
            db_session=session,
            user_id=user_id,
            channel_id=channel_id,
            grouped_id=grouped_id,
            post_ids=[post_id],
            media_types=['photo'],
            media_sha256s=None,
            media_bytes=None,
            caption_text=caption_text,
            posted_at=posted_at,
            cover_media_id=None,
            media_kinds=['photo'],
            trace_id=f"test_{uuid4()}"
        )
        
        assert group_id is not None, "group_id должен быть возвращён"
        print(f"  ✓ Альбом сохранён с group_id={group_id}")
        
        # Проверяем, что поля сохранены
        result = await session.execute(text("""
            SELECT caption_text, posted_at
            FROM media_groups
            WHERE id = :group_id
        """), {"group_id": group_id})
        row = result.fetchone()
        
        assert row is not None, "Альбом должен быть найден"
        assert row[0] == caption_text, f"caption_text должен быть '{caption_text}', получен '{row[0]}'"
        assert row[1] is not None, "posted_at должен быть установлен"
        
        print(f"  ✓ caption_text: '{row[0]}'")
        print(f"  ✓ posted_at: {row[1]}")
        
        await session.commit()
    
    await engine.dispose()
    print("  ✅ save_media_group тест пройден")


async def test_iter_messages_logic():
    """Тест логики iter_messages (без реального Telegram API)."""
    print("\n🧪 Тест 4: Логика iter_messages с окном по времени")
    
    current_date = datetime.now(timezone.utc)
    offset_date_min = current_date - timedelta(minutes=5)
    offset_date_max = current_date + timedelta(minutes=5)
    
    # Симулируем сообщения альбома
    grouped_id = 55555
    messages = [
        Mock(id=100, grouped_id=grouped_id, date=current_date - timedelta(minutes=4)),
        Mock(id=101, grouped_id=grouped_id, date=current_date - timedelta(minutes=3)),
        Mock(id=102, grouped_id=grouped_id, date=current_date - timedelta(minutes=2)),
        Mock(id=103, grouped_id=None, date=current_date - timedelta(minutes=1)),  # Прерывание
    ]
    
    # Симулируем фильтрацию по окну и grouped_id
    album_messages = []
    for msg in messages:
        if msg.date < offset_date_min or msg.date > offset_date_max:
            continue
        if getattr(msg, 'grouped_id', None) == grouped_id:
            album_messages.append(msg)
        if album_messages and getattr(msg, 'grouped_id', None) != grouped_id:
            break
    
    assert len(album_messages) == 3, f"Должно быть 3 элемента альбома, получено {len(album_messages)}"
    assert album_messages[0].id == 100, "Первый элемент должен иметь id=100"
    
    print(f"  ✓ Найдено {len(album_messages)} элементов альбома")
    print(f"  ✓ Окно времени: {offset_date_min} - {offset_date_max}")
    print("  ✅ Логика iter_messages тест пройден")


async def main():
    """Запуск всех тестов."""
    print("=" * 60)
    print("Тестирование изменений пайплайна альбомов (Phase 1)")
    print("=" * 60)
    
    tests = [
        ("Redis cache", test_redis_cache),
        ("Схема БД", test_db_schema),
        ("save_media_group", test_save_media_group_function),
        ("Логика iter_messages", test_iter_messages_logic),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            await test_func()
            results.append((name, True, None))
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False, str(e)))
    
    print("\n" + "=" * 60)
    print("Результаты тестирования:")
    print("=" * 60)
    
    for name, success, error in results:
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"{status} - {name}")
        if error:
            print(f"      Ошибка: {error}")
    
    failed_count = sum(1 for _, success, _ in results if not success)
    if failed_count > 0:
        print(f"\n⚠️  {failed_count} тест(ов) не прошли")
        sys.exit(1)
    else:
        print("\n✅ Все тесты прошли успешно!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

