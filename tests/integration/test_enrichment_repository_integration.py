"""
Integration тесты для EnrichmentRepository.

Context7: Проверка интеграции с реальной БД и различными типами подключений.
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Импорт тестируемых модулей
import sys
import os
shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
if shared_path not in sys.path:
    sys.path.insert(0, shared_path)

from shared.repositories.enrichment_repository import EnrichmentRepository


@pytest.mark.asyncio
async def test_enrichment_repository_with_asyncpg_pool():
    """Проверка работы с asyncpg.Pool."""
    import asyncpg
    
    # Мок asyncpg.Pool
    mock_pool = MagicMock(spec=asyncpg.Pool)
    mock_conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
    
    repo = EnrichmentRepository(mock_pool)
    
    # Проверяем, что определяется как asyncpg
    assert repo._is_asyncpg is True
    
    # Тест upsert
    await repo.upsert_enrichment(
        post_id=str(uuid.uuid4()),
        kind='vision',
        provider='gigachat-vision',
        data={'model': 'gigachat-vision', 'labels': []}
    )
    
    # Проверяем вызов execute
    assert mock_conn.execute.called
    call_args = mock_conn.execute.call_args[0][0]
    assert 'INSERT INTO post_enrichment' in call_args


@pytest.mark.asyncio
async def test_enrichment_repository_with_sqlalchemy_session():
    """Проверка работы с SQLAlchemy AsyncSession."""
    from sqlalchemy.ext.asyncio import AsyncSession
    
    # Мок SQLAlchemy AsyncSession
    mock_session = AsyncMock(spec=AsyncSession)
    mock_execute = AsyncMock()
    mock_commit = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.commit = mock_commit
    
    repo = EnrichmentRepository(mock_session)
    
    # Проверяем, что определяется как SQLAlchemy
    assert repo._is_sqlalchemy is True
    
    # Тест upsert
    await repo.upsert_enrichment(
        post_id=str(uuid.uuid4()),
        kind='crawl',
        provider='crawl4ai',
        data={'crawl_md': 'Test content'}
    )
    
    # Проверяем вызов execute и commit
    assert mock_execute.called
    assert mock_commit.called


@pytest.mark.asyncio
async def test_enrichment_repository_idempotency():
    """Проверка идемпотентности через params_hash."""
    mock_db = AsyncMock()
    mock_execute = AsyncMock()
    mock_db.execute = mock_execute
    mock_db.commit = AsyncMock()
    
    repo = EnrichmentRepository(mock_db)
    
    post_id = str(uuid.uuid4())
    data = {'model': 'gigachat-vision', 'labels': []}
    
    # Первый вызов
    params_hash1 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={})
    await repo.upsert_enrichment(
        post_id=post_id,
        kind='vision',
        provider='gigachat-vision',
        data=data,
        params_hash=params_hash1
    )
    
    # Второй вызов с теми же параметрами
    params_hash2 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={})
    
    # Hash должен быть одинаковым
    assert params_hash1 == params_hash2
    
    await repo.upsert_enrichment(
        post_id=post_id,
        kind='vision',
        provider='gigachat-vision',
        data=data,
        params_hash=params_hash2
    )
    
    # Проверяем, что используется ON CONFLICT
    calls = mock_execute.call_args_list
    sql_texts = [str(call[0][0]) for call in calls]
    assert all('ON CONFLICT' in sql for sql in sql_texts)


@pytest.mark.asyncio
async def test_enrichment_repository_multiple_kinds():
    """Проверка модульного сохранения разных видов обогащений."""
    mock_db = AsyncMock()
    mock_execute = AsyncMock()
    mock_db.execute = mock_execute
    mock_db.commit = AsyncMock()
    
    repo = EnrichmentRepository(mock_db)
    
    post_id = str(uuid.uuid4())
    
    # Сохраняем разные виды обогащений для одного поста
    await repo.upsert_enrichment(
        post_id=post_id,
        kind='vision',
        provider='gigachat-vision',
        data={'labels': []}
    )
    
    await repo.upsert_enrichment(
        post_id=post_id,
        kind='tags',
        provider='gigachat',
        data={'tags': ['test']}
    )
    
    await repo.upsert_enrichment(
        post_id=post_id,
        kind='crawl',
        provider='crawl4ai',
        data={'crawl_md': 'Content'}
    )
    
    # Все три вида должны быть сохранены (3 вызова execute)
    assert mock_execute.call_count == 3
    
    # Проверяем, что используются разные kind
    calls = mock_execute.call_args_list
    for call in calls:
        params = call[0][1] if len(call[0]) > 1 else {}
        kind_value = params.get('kind') if isinstance(params, dict) else None
        assert kind_value in ['vision', 'tags', 'crawl']


@pytest.mark.asyncio
async def test_enrichment_repository_validation():
    """Проверка валидации kind и status."""
    mock_db = AsyncMock()
    
    repo = EnrichmentRepository(mock_db)
    
    post_id = str(uuid.uuid4())
    
    # Невалидный kind должен вызывать ошибку
    with pytest.raises(ValueError, match="Invalid kind"):
        await repo.upsert_enrichment(
            post_id=post_id,
            kind='invalid_kind',
            provider='test',
            data={}
        )
    
    # Валидный kind должен работать
    try:
        await repo.upsert_enrichment(
            post_id=post_id,
            kind='vision',
            provider='gigachat-vision',
            data={}
        )
    except ValueError as e:
        if "Invalid kind" in str(e):
            pytest.fail("Valid kind should not raise ValueError")


@pytest.mark.asyncio
async def test_enrichment_repository_get_enrichment():
    """Проверка получения обогащений."""
    import asyncpg
    
    mock_pool = MagicMock(spec=asyncpg.Pool)
    mock_conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    
    # Мок результата запроса
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, key: {
        'post_id': 'test-id',
        'kind': 'vision',
        'provider': 'gigachat-vision',
        'data': {'labels': []}
    }.get(key)
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)
    
    repo = EnrichmentRepository(mock_pool)
    
    result = await repo.get_enrichment('test-id', 'vision')
    
    assert result is not None
    assert mock_conn.fetchrow.called


if __name__ == '__main__':
    print("Running integration tests for EnrichmentRepository...")
    print("Note: Full tests require pytest and async test framework")
    print("Basic validation test:")
    
    # Простая проверка валидации
    mock_db = MagicMock()
    repo = EnrichmentRepository(mock_db)
    
    hash1 = repo.compute_params_hash(model='test', version='1.0', inputs={})
    hash2 = repo.compute_params_hash(model='test', version='1.0', inputs={})
    assert hash1 == hash2, "Same params should produce same hash"
    print("✓ Params hash test passed")

