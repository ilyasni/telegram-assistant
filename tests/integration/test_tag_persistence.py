"""Интеграционные тесты для tag_persistence_task."""

import asyncio
import json
import pytest
import asyncpg
from redis.asyncio import Redis


@pytest.mark.asyncio
async def test_upsert_tags_updates_only_on_diff(db_dsn):
    """UPSERT обновляет запись только при изменении тегов."""
    pool = await asyncpg.create_pool(db_dsn)
    
    try:
        async with pool.acquire() as conn:
            # Очистка
            await conn.execute(
                "DELETE FROM post_enrichment WHERE post_id='test_p1' AND kind='tags'"
            )
            
            # Первая вставка
            await conn.execute("""
                INSERT INTO post_enrichment (
                    post_id, kind, tags, enrichment_provider,
                    enriched_at, metadata, updated_at
                )
                VALUES (
                    'test_p1', 'tags', ARRAY['a','b'], 'gigachain',
                    NOW(), '{}'::jsonb, NOW()
                )
            """)
            
            # Запомнить updated_at
            before = await conn.fetchrow(
                "SELECT updated_at FROM post_enrichment WHERE post_id='test_p1' AND kind='tags'"
            )
            
            await asyncio.sleep(0.1)  # Небольшая задержка
            
            # Повторная вставка с теми же тегами
            await conn.execute("""
                INSERT INTO post_enrichment (
                    post_id, kind, tags, enrichment_provider,
                    enriched_at, metadata, updated_at
                )
                VALUES (
                    'test_p1', 'tags', ARRAY['a','b'], 'gigachain',
                    NOW(), '{}'::jsonb, NOW()
                )
                ON CONFLICT (post_id, kind)
                DO UPDATE SET
                    tags = EXCLUDED.tags,
                    updated_at = NOW()
                WHERE post_enrichment.tags IS DISTINCT FROM EXCLUDED.tags
            """)
            
            # Проверить, что updated_at НЕ изменился
            after = await conn.fetchrow(
                "SELECT updated_at FROM post_enrichment WHERE post_id='test_p1' AND kind='tags'"
            )
            
            assert before["updated_at"] == after["updated_at"], \
                "updated_at не должен меняться при одинаковых тегах"
            
            # Очистка
            await conn.execute(
                "DELETE FROM post_enrichment WHERE post_id='test_p1' AND kind='tags'"
            )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_upsert_tags_updates_on_diff(db_dsn):
    """UPSERT обновляет запись при изменении тегов."""
    pool = await asyncpg.create_pool(db_dsn)
    
    try:
        async with pool.acquire() as conn:
            # Очистка
            await conn.execute(
                "DELETE FROM post_enrichment WHERE post_id='test_p2' AND kind='tags'"
            )
            
            # Первая вставка
            await conn.execute("""
                INSERT INTO post_enrichment (
                    post_id, kind, tags, enrichment_provider,
                    enriched_at, metadata, updated_at
                )
                VALUES (
                    'test_p2', 'tags', ARRAY['a','b'], 'gigachain',
                    NOW(), '{}'::jsonb, NOW()
                )
            """)
            
            # Запомнить updated_at
            before = await conn.fetchrow(
                "SELECT updated_at FROM post_enrichment WHERE post_id='test_p2' AND kind='tags'"
            )
            
            await asyncio.sleep(0.1)  # Небольшая задержка
            
            # Вставка с новыми тегами
            await conn.execute("""
                INSERT INTO post_enrichment (
                    post_id, kind, tags, enrichment_provider,
                    enriched_at, metadata, updated_at
                )
                VALUES (
                    'test_p2', 'tags', ARRAY['c','d'], 'gigachat',
                    NOW(), '{"model": "GigaChat:latest"}'::jsonb, NOW()
                )
                ON CONFLICT (post_id, kind)
                DO UPDATE SET
                    tags = EXCLUDED.tags,
                    enrichment_provider = EXCLUDED.enrichment_provider,
                    metadata = post_enrichment.metadata || EXCLUDED.metadata,
                    updated_at = NOW()
                WHERE post_enrichment.tags IS DISTINCT FROM EXCLUDED.tags
            """)
            
            # Проверить, что updated_at изменился
            after = await conn.fetchrow(
                "SELECT updated_at, tags, enrichment_provider FROM post_enrichment WHERE post_id='test_p2' AND kind='tags'"
            )
            
            assert before["updated_at"] < after["updated_at"], \
                "updated_at должен измениться при изменении тегов"
            assert after["tags"] == ['c', 'd'], \
                "Теги должны обновиться"
            assert after["enrichment_provider"] == 'gigachat', \
                "Провайдер должен обновиться"
            
            # Очистка
            await conn.execute(
                "DELETE FROM post_enrichment WHERE post_id='test_p2' AND kind='tags'"
            )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_tags_hash_prevents_duplicate_events():
    """Хеш тегов предотвращает дублирование событий."""
    from worker.events.schemas.posts_tagged_v1 import PostTaggedEventV1
    
    tags1 = ["тег1", "тег2"]
    tags2 = ["тег2", "тег1"]  # Тот же набор, другой порядок
    
    hash1 = PostTaggedEventV1.compute_hash(tags1)
    hash2 = PostTaggedEventV1.compute_hash(tags2)
    
    # Хеши должны совпадать (sorted set)
    assert hash1 == hash2, "Хеши должны совпадать для одинаковых наборов тегов"
    
    # Проверка с пустыми тегами
    empty_hash = PostTaggedEventV1.compute_hash([])
    assert empty_hash == PostTaggedEventV1.compute_hash([]), "Пустые теги должны давать одинаковый хеш"
    
    # Проверка с разными тегами
    tags3 = ["тег3", "тег4"]
    hash3 = PostTaggedEventV1.compute_hash(tags3)
    assert hash1 != hash3, "Разные теги должны давать разные хеши"


@pytest.mark.asyncio
async def test_post_tagged_event_validation():
    """Проверка валидации PostTaggedEventV1."""
    from worker.events.schemas.posts_tagged_v1 import PostTaggedEventV1
    
    # Валидное событие
    event = PostTaggedEventV1(
        idempotency_key="test:tagged:v1",
        post_id="test_post_123",
        tags=["тег1", "тег2"],
        tags_hash=PostTaggedEventV1.compute_hash(["тег1", "тег2"]),
        provider="gigachat",
        latency_ms=1500,
        metadata={"model": "GigaChat:latest", "language": "ru"}
    )
    
    assert event.post_id == "test_post_123"
    assert event.tags == ["тег1", "тег2"]
    assert event.provider == "gigachat"
    assert event.latency_ms == 1500
    assert event.metadata["model"] == "GigaChat:latest"
    
    # Проверка хеша
    expected_hash = PostTaggedEventV1.compute_hash(["тег1", "тег2"])
    assert event.tags_hash == expected_hash


@pytest.mark.asyncio
async def test_post_enrichment_kind_field_exists(db_dsn):
    """Проверка, что поле kind существует в post_enrichment."""
    pool = await asyncpg.create_pool(db_dsn)
    
    try:
        async with pool.acquire() as conn:
            # Проверка структуры таблицы
            columns = await conn.fetch("""
                SELECT column_name, data_type, column_default
                FROM information_schema.columns
                WHERE table_name = 'post_enrichment'
                AND column_name = 'kind'
            """)
            
            assert len(columns) > 0, "Поле kind должно существовать в post_enrichment"
            
            kind_column = columns[0]
            assert kind_column['data_type'] == 'text', "Поле kind должно быть типа text"
            assert kind_column['column_default'] == "'tags'::text", "Значение по умолчанию должно быть 'tags'"
            
            # Проверка уникального индекса
            indexes = await conn.fetch("""
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'post_enrichment'
                AND indexname = 'ux_post_enrichment_post_kind'
            """)
            
            assert len(indexes) > 0, "Уникальный индекс ux_post_enrichment_post_kind должен существовать"
            
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_post_enrichment_tags_array_type(db_dsn):
    """Проверка, что поле tags имеет тип text[]."""
    pool = await asyncpg.create_pool(db_dsn)
    
    try:
        async with pool.acquire() as conn:
            # Проверка типа поля tags
            columns = await conn.fetch("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'post_enrichment'
                AND column_name = 'tags'
            """)
            
            assert len(columns) > 0, "Поле tags должно существовать в post_enrichment"
            
            tags_column = columns[0]
            assert tags_column['data_type'] == 'ARRAY', "Поле tags должно быть типа ARRAY"
            
            # Тест вставки массива
            await conn.execute("""
                INSERT INTO post_enrichment (
                    post_id, kind, tags, enrichment_provider,
                    enriched_at, metadata, updated_at
                )
                VALUES (
                    'test_array', 'tags', ARRAY['тег1','тег2','тег3'], 'gigachat',
                    NOW(), '{}'::jsonb, NOW()
                )
            """)
            
            # Проверка чтения
            result = await conn.fetchrow("""
                SELECT tags FROM post_enrichment 
                WHERE post_id = 'test_array' AND kind = 'tags'
            """)
            
            assert result['tags'] == ['тег1', 'тег2', 'тег3'], "Массив тегов должен сохраняться корректно"
            
            # Очистка
            await conn.execute(
                "DELETE FROM post_enrichment WHERE post_id='test_array' AND kind='tags'"
            )
            
    finally:
        await pool.close()
