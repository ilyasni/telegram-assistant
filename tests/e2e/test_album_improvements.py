"""
E2E тесты для улучшений пайплайна альбомов (Phase 1)
Context7 best practice: trace_id, проверка всех этапов, идемпотентность

Тестирует:
- Redis negative cache для grouped_id
- iter_messages() вместо get_messages()
- Новые поля в media_groups (caption_text, cover_media_id, posted_at)
- Новые поля в media_group_items (media_object_id, media_kind)
"""

import pytest
import asyncio
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch

import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
async def test_redis_negative_cache(test_db, test_redis, test_tenant_data):
    """Тест Redis negative cache для избежания повторных get_messages()."""
    
    from telethon_ingest.services.media_processor import MediaProcessor
    from telethon.tl.types import MessageMediaPhoto
    
    tenant_id = test_tenant_data["tenant_id"]
    channel_id = str(test_tenant_data["channel_id"])
    grouped_id = 54321
    
    # Создаём mock сообщение с альбомом
    message = Mock()
    message.grouped_id = grouped_id
    message.id = 500
    message.date = datetime.now(timezone.utc)
    message.media = MessageMediaPhoto()
    message.peer_id = Mock()
    
    # Создаём mock TelegramClient
    mock_client = AsyncMock()
    mock_client.iter_messages = AsyncMock(return_value=AsyncMock())
    
    # Mock для S3 и storage_quota
    mock_s3 = Mock()
    mock_quota = Mock()
    
    processor = MediaProcessor(
        telegram_client=mock_client,
        s3_service=mock_s3,
        storage_quota=mock_quota,
        redis_client=test_redis,
        tenant_id=tenant_id
    )
    
    # Первый вызов - должен вызвать iter_messages()
    result1 = await processor._process_media_group(
        message=message,
        tenant_id=tenant_id,
        trace_id=f"test_{uuid4()}",
        channel_id=channel_id
    )
    
    # Проверяем, что iter_messages был вызван
    assert mock_client.iter_messages.called, "iter_messages должен быть вызван при первом обращении"
    
    # Очищаем мок для следующего вызова
    mock_client.iter_messages.reset_mock()
    
    # Второй вызов - должен пропустить из-за Redis cache
    result2 = await processor._process_media_group(
        message=message,
        tenant_id=tenant_id,
        trace_id=f"test_{uuid4()}",
        channel_id=channel_id
    )
    
    # Проверяем, что iter_messages НЕ был вызван (используется cache)
    assert not mock_client.iter_messages.called, "iter_messages НЕ должен быть вызван при наличии cache"
    
    # Проверяем, что cache установлен в Redis
    cache_key = f"album_seen:{channel_id}:{grouped_id}"
    cache_value = await test_redis.get(cache_key)
    assert cache_value is not None, "Redis cache должен быть установлен"
    assert cache_value == b"1", "Cache значение должно быть '1'"
    
    logger.info("Redis negative cache test passed", grouped_id=grouped_id)


@pytest.mark.asyncio
async def test_iter_messages_usage(test_db, test_redis, test_tenant_data):
    """Тест использования iter_messages() вместо get_messages()."""
    
    from telethon_ingest.services.media_processor import MediaProcessor
    from telethon.tl.types import MessageMediaPhoto
    
    tenant_id = test_tenant_data["tenant_id"]
    grouped_id = 98765
    
    # Создаём mock сообщение
    current_date = datetime.now(timezone.utc)
    message = Mock()
    message.grouped_id = grouped_id
    message.id = 600
    message.date = current_date
    message.media = MessageMediaPhoto()
    message.peer_id = Mock()
    
    # Создаём mock альбомные сообщения
    album_messages = [
        Mock(id=600, grouped_id=grouped_id, date=current_date, media=MessageMediaPhoto()),
        Mock(id=601, grouped_id=grouped_id, date=current_date + timedelta(seconds=1), media=MessageMediaPhoto()),
        Mock(id=602, grouped_id=grouped_id, date=current_date + timedelta(seconds=2), media=MessageMediaPhoto()),
    ]
    
    # Создаём async iterator для iter_messages
    async def mock_iter_messages(peer, limit, offset_date, reverse):
        for msg in album_messages:
            yield msg
    
    mock_client = AsyncMock()
    mock_client.iter_messages = mock_iter_messages
    
    mock_s3 = Mock()
    mock_quota = Mock()
    
    processor = MediaProcessor(
        telegram_client=mock_client,
        s3_service=mock_s3,
        storage_quota=mock_quota,
        redis_client=test_redis,
        tenant_id=tenant_id
    )
    
    # Обрабатываем альбом
    result = await processor._process_media_group(
        message=message,
        tenant_id=tenant_id,
        trace_id=f"test_{uuid4()}",
        channel_id=str(test_tenant_data["channel_id"])
    )
    
    # Проверяем, что iter_messages был вызван с правильными параметрами
    assert mock_client.iter_messages.called, "iter_messages должен быть вызван"
    
    call_args = mock_client.iter_messages.call_args
    assert call_args.kwargs['limit'] == 30, "limit должен быть 30"
    assert call_args.kwargs['offset_date'] == current_date, "offset_date должен быть текущей датой"
    assert call_args.kwargs['reverse'] == False, "reverse должен быть False"
    
    logger.info("iter_messages usage test passed", grouped_id=grouped_id)


@pytest.mark.asyncio
async def test_new_media_groups_fields(test_db, test_tenant_data):
    """Тест новых полей в таблице media_groups."""
    
    from sqlalchemy import text
    
    tenant_id = test_tenant_data["tenant_id"]
    user_id = test_tenant_data["user_id"]
    channel_id = test_tenant_data["channel_id"]
    grouped_id = 77777
    
    # Проверяем наличие новых полей
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'media_groups'
                AND column_name IN ('caption_text', 'cover_media_id', 'posted_at', 'meta')
                ORDER BY column_name
            """)
        )
        columns = {row[0]: row[1] for row in result}
        
        assert 'caption_text' in columns, "Поле caption_text должно существовать"
        assert 'cover_media_id' in columns, "Поле cover_media_id должно существовать"
        assert columns['cover_media_id'] == 'uuid', "cover_media_id должен быть UUID"
        assert 'posted_at' in columns, "Поле posted_at должно существовать"
        assert 'meta' in columns, "Поле meta должно существовать"
        assert 'jsonb' in columns['meta'].lower(), "meta должен быть JSONB"
    
    logger.info("New media_groups fields test passed")


@pytest.mark.asyncio
async def test_new_media_group_items_fields(test_db, test_tenant_data):
    """Тест новых полей в таблице media_group_items."""
    
    from sqlalchemy import text
    
    # Проверяем наличие новых полей
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'media_group_items'
                AND column_name IN ('media_object_id', 'media_kind', 'sha256', 'meta')
                ORDER BY column_name
            """)
        )
        columns = {row[0]: row[1] for row in result}
        
        assert 'media_object_id' in columns, "Поле media_object_id должно существовать"
        assert columns['media_object_id'] == 'uuid', "media_object_id должен быть UUID"
        assert 'media_kind' in columns, "Поле media_kind должно существовать"
        assert 'sha256' in columns, "Поле sha256 должно существовать"
        assert 'meta' in columns, "Поле meta должно существовать"
        assert 'jsonb' in columns['meta'].lower(), "meta должен быть JSONB"
    
    logger.info("New media_group_items fields test passed")


@pytest.mark.asyncio
async def test_media_objects_id_field(test_db):
    """Тест добавления id UUID в media_objects."""
    
    from sqlalchemy import text
    
    # Проверяем наличие поля id
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'media_objects'
                AND column_name = 'id'
            """)
        )
        row = result.fetchone()
        
        assert row is not None, "Поле id должно существовать в media_objects"
        assert row[1] == 'uuid', "id должен быть UUID"
    
    # Проверяем, что все существующие записи имеют id
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT COUNT(*) as total, COUNT(id) as with_id
                FROM media_objects
            """)
        )
        row = result.fetchone()
        
        assert row[0] == row[1], "Все записи должны иметь id"
    
    logger.info("media_objects.id field test passed")


@pytest.mark.asyncio
async def test_save_media_group_with_new_fields(test_db, test_tenant_data):
    """Тест сохранения альбома с новыми полями через save_media_group."""
    
    from telethon_ingest.services.media_group_saver import save_media_group
    from sqlalchemy import text
    
    user_id = str(test_tenant_data["user_id"])
    channel_id = str(test_tenant_data["channel_id"])
    grouped_id = 88888
    
    # Создаём тестовый post_id
    post_id = str(uuid4())
    
    # Сначала создаём пост в БД
    async with test_db.begin():
        await test_db.execute(
            text("""
                INSERT INTO posts (
                    id, channel_id, content, posted_at, created_at,
                    is_processed, has_media, grouped_id
                ) VALUES (
                    :post_id, :channel_id, 'Test album post', NOW(), NOW(),
                    false, true, :grouped_id
                )
            """),
            {
                "post_id": post_id,
                "channel_id": channel_id,
                "grouped_id": grouped_id
            }
        )
    
    # Сохраняем альбом с новыми полями
    caption_text = "Тестовый альбом"
    posted_at = datetime.now(timezone.utc)
    
    group_id = await save_media_group(
        db_session=test_db,
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
    
    # Проверяем, что новые поля сохранены
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT caption_text, posted_at, cover_media_id
                FROM media_groups
                WHERE id = :group_id
            """),
            {"group_id": group_id}
        )
        row = result.fetchone()
        
        assert row is not None, "Альбом должен быть найден"
        assert row[0] == caption_text, "caption_text должен быть сохранён"
        assert row[1] is not None, "posted_at должен быть установлен"
    
    logger.info("save_media_group with new fields test passed", group_id=group_id)

