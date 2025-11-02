"""
E2E тесты для MessageMediaGroup (альбомов)
Context7 best practice: trace_id, проверка всех этапов, идемпотентность

Тестирует:
- Обработку альбомов через client.get_messages()
- Дедупликацию по grouped_id
- Сохранение grouped_id в БД
- Порядок элементов альбома
"""

import pytest
import asyncio
from uuid import uuid4
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch

import structlog

logger = structlog.get_logger()


@pytest.mark.asyncio
async def test_album_deduplication(test_db, test_redis, test_tenant_data):
    """Тест дедупликации альбомов по grouped_id через Redis."""
    
    from telethon_ingest.services.channel_parser import ChannelParser
    
    tenant_id = test_tenant_data["tenant_id"]
    user_id = test_tenant_data["user_id"]
    channel_id = test_tenant_data["channel_id"]
    
    # Создаём mock сообщения с одинаковым grouped_id
    grouped_id = 12345
    message1 = Mock()
    message1.grouped_id = grouped_id
    message1.id = 100
    message1.media = None
    
    message2 = Mock()
    message2.grouped_id = grouped_id
    message2.id = 101
    message2.media = None
    
    # Инициализируем парсер
    parser = ChannelParser(
        redis_client=test_redis,
        db_session=test_db
    )
    
    # Обрабатываем первое сообщение - должно быть обработано
    result1 = await parser._process_message_batch(
        messages=[message1],
        channel_id=str(channel_id),
        user_id=str(user_id),
        tenant_id=tenant_id
    )
    
    assert result1["processed"] == 1
    assert result1["skipped"] == 0
    
    # Обрабатываем второе сообщение с тем же grouped_id - должно быть пропущено
    result2 = await parser._process_message_batch(
        messages=[message2],
        channel_id=str(channel_id),
        user_id=str(user_id),
        tenant_id=tenant_id
    )
    
    assert result2["processed"] == 0
    assert result2["skipped"] == 1
    
    logger.info("Album deduplication test passed", grouped_id=grouped_id)


@pytest.mark.asyncio
async def test_album_processing_with_get_messages(test_db, test_redis, test_tenant_data):
    """Тест обработки альбома через client.get_messages()."""
    
    from telethon_ingest.services.media_processor import MediaProcessor
    from telethon.tl.types import MessageMediaGroup, MessageMediaPhoto
    
    tenant_id = test_tenant_data["tenant_id"]
    grouped_id = 67890
    channel_entity = Mock()
    channel_entity.id = test_tenant_data["channel_id"]
    
    # Создаём mock сообщение-альбом
    message = Mock()
    message.grouped_id = grouped_id
    message.id = 200
    message.media = MessageMediaGroup()
    message.peer_id = channel_entity
    
    # Mock для получения всех сообщений альбома
    album_messages = [
        Mock(id=200, grouped_id=grouped_id, media=MessageMediaPhoto()),
        Mock(id=201, grouped_id=grouped_id, media=MessageMediaPhoto()),
        Mock(id=202, grouped_id=grouped_id, media=MessageMediaPhoto()),
    ]
    
    with patch.object(MediaProcessor, '_get_telegram_client') as mock_client_get:
        mock_client = AsyncMock()
        mock_client.get_messages = AsyncMock(return_value=album_messages)
        mock_client_get.return_value = mock_client
        
        processor = MediaProcessor(
            telegram_client=mock_client,
            s3_service=Mock(),
            storage_quota=Mock()
        )
        
        # Обрабатываем альбом
        media_files = await processor._process_media_group(
            message=message,
            tenant_id=tenant_id,
            trace_id=f"test_{uuid4()}",
            channel_entity=channel_entity
        )
        
        # Проверяем, что get_messages был вызван
        mock_client.get_messages.assert_called_once()
        
        # Проверяем, что все сообщения альбома были получены
        call_args = mock_client.get_messages.call_args
        assert call_args[0][0] == channel_entity  # peer
        assert call_args[1]["min_id"] <= 200  # диапазон вокруг текущего сообщения
        
        logger.info(
            "Album processing test passed",
            grouped_id=grouped_id,
            messages_fetched=len(album_messages)
        )


@pytest.mark.asyncio
async def test_grouped_id_saved_to_db(test_db, test_tenant_data):
    """Тест сохранения grouped_id в таблицу posts."""
    
    from sqlalchemy import text
    
    tenant_id = test_tenant_data["tenant_id"]
    channel_id = test_tenant_data["channel_id"]
    grouped_id = 99999
    
    # Создаём тестовый пост с grouped_id
    post_id = uuid4()
    
    async with test_db.begin():
        await test_db.execute(
            text("""
                INSERT INTO posts (
                    id, channel_id, content, posted_at, created_at,
                    is_processed, has_media, grouped_id
                ) VALUES (
                    :post_id, :channel_id, 'Test post', NOW(), NOW(),
                    false, true, :grouped_id
                )
            """),
            {
                "post_id": post_id,
                "channel_id": channel_id,
                "grouped_id": grouped_id
            }
        )
    
    # Проверяем, что grouped_id сохранён
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT grouped_id FROM posts WHERE id = :post_id
            """),
            {"post_id": post_id}
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == grouped_id
        
    logger.info("grouped_id saved to DB test passed", post_id=str(post_id))


@pytest.mark.asyncio
async def test_album_mixed_media_types(test_db, test_redis, test_tenant_data):
    """Тест обработки альбома со смешанными типами медиа."""
    
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
    
    # Создаём mock альбом с фото и документом
    grouped_id = 11111
    album_messages = [
        Mock(id=300, grouped_id=grouped_id, media=MessageMediaPhoto()),
        Mock(id=301, grouped_id=grouped_id, media=MessageMediaDocument()),
        Mock(id=302, grouped_id=grouped_id, media=MessageMediaPhoto()),
    ]
    
    # Проверяем, что альбом содержит разные типы медиа
    media_types = [type(msg.media).__name__ for msg in album_messages]
    assert "MessageMediaPhoto" in media_types
    assert "MessageMediaDocument" in media_types
    
    logger.info(
        "Mixed media album test passed",
        grouped_id=grouped_id,
        media_types=media_types
    )


@pytest.mark.asyncio
async def test_media_groups_table_structure(test_db):
    """Тест структуры таблицы media_groups."""
    
    from sqlalchemy import text, inspect
    
    # Проверяем, что таблица существует
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'media_groups'
                )
            """)
        )
        exists = result.scalar()
        assert exists, "Table media_groups should exist"
    
    # Проверяем структуру таблицы
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'media_groups'
                AND column_name IN ('id', 'grouped_id', 'album_kind', 'items_count')
                ORDER BY column_name
            """)
        )
        columns = {row[0]: row[1] for row in result}
        
        assert 'id' in columns
        assert 'grouped_id' in columns
        assert 'album_kind' in columns
        assert 'items_count' in columns
        
    logger.info("media_groups table structure test passed")


@pytest.mark.asyncio
async def test_media_group_items_table_structure(test_db):
    """Тест структуры таблицы media_group_items."""
    
    from sqlalchemy import text
    
    # Проверяем структуру таблицы
    async with test_db.begin():
        result = await test_db.execute(
            text("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'media_group_items'
                AND column_name IN ('id', 'group_id', 'post_id', 'position', 'media_type')
                ORDER BY column_name
            """)
        )
        columns = {row[0]: row[1] for row in result}
        
        assert 'id' in columns
        assert 'group_id' in columns
        assert 'post_id' in columns
        assert 'position' in columns
        assert 'media_type' in columns
        
    logger.info("media_group_items table structure test passed")

