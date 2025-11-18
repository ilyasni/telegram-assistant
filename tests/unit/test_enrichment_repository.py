"""
Unit тесты для EnrichmentRepository.

Context7: Проверка единого репозитория для всех видов обогащений.
"""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from shared.repositories.enrichment_repository import EnrichmentRepository


def test_compute_params_hash():
    """Проверка вычисления params_hash."""
    repo = EnrichmentRepository(MagicMock())

    hash1 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.35})
    hash2 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.35})
    hash3 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.40})

    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 64


@pytest.mark.asyncio
async def test_upsert_enrichment_with_asyncpg():
    """Проверка upsert с pseudo asyncpg.Pool."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

    @asynccontextmanager
    async def acquire():
        yield mock_conn

    class DummyPool:
        def acquire(self):
            return acquire()

    repo = EnrichmentRepository(DummyPool())
    repo._is_asyncpg = True  # Форсируем asyncpg путь

    await repo.upsert_enrichment(
        post_id='test-post-id',
        kind='vision',
        provider='gigachat-vision',
        data={'model': 'gigachat-vision', 'labels': []},
        trace_id='test-trace-id'
    )

    mock_conn.execute.assert_called()


@pytest.mark.asyncio
async def test_upsert_enrichment_validation():
    """Проверка валидации kind."""
    repo = EnrichmentRepository(MagicMock())

    with pytest.raises(ValueError, match="Invalid kind"):
        await repo.upsert_enrichment(
            post_id='test-post-id',
            kind='invalid_kind',
            provider='test',
            data={}
        )

