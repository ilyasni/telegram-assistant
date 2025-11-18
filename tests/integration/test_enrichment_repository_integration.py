"""
Integration тесты для EnrichmentRepository.

Context7: Проверка интеграции с реальной БД и различными типами подключений.
"""

import uuid
from contextlib import asynccontextmanager
from typing import List
from unittest.mock import AsyncMock

import pytest

import asyncpg

from shared.repositories.enrichment_repository import EnrichmentRepository


class DummyAsyncpgPool(asyncpg.Pool):
    def __init__(self):
        self.connection = AsyncMock()
        self.connection.execute = AsyncMock(return_value="INSERT 0 1")
        self.connection.fetchrow = AsyncMock(return_value={"data": {}, "params_hash": "hash"})

    def acquire(self):
        @asynccontextmanager
        async def _manager():
            yield self.connection

        return _manager()


@pytest.mark.asyncio
async def test_enrichment_repository_with_asyncpg_pool():
    repo = EnrichmentRepository(DummyAsyncpgPool())

    assert repo._is_asyncpg is True

    await repo.upsert_enrichment(
        post_id=str(uuid.uuid4()),
        kind="vision",
        provider="gigachat-vision",
        data={"model": "gigachat-vision", "labels": []},
    )

    assert repo.db_session.connection.execute.await_count >= 1
    first_sql = repo.db_session.connection.execute.await_args_list[0].args[0]
    assert "INSERT INTO post_enrichment" in first_sql


@pytest.mark.asyncio
async def test_enrichment_repository_with_sqlalchemy_session():
    from sqlalchemy.ext.asyncio import AsyncSession

    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    repo = EnrichmentRepository(session)

    assert repo._is_sqlalchemy is True

    await repo.upsert_enrichment(
        post_id=str(uuid.uuid4()),
        kind="crawl",
        provider="crawl4ai",
        data={"crawl_md": "Test content"},
    )

    assert session.execute.await_count >= 1
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_enrichment_repository_idempotency():
    pool = DummyAsyncpgPool()
    repo = EnrichmentRepository(pool)

    post_id = str(uuid.uuid4())
    data = {"model": "gigachat-vision", "labels": []}

    params_hash = repo.compute_params_hash(model="gigachat-vision", version="2025-10", inputs={})
    await repo.upsert_enrichment(
        post_id=post_id,
        kind="vision",
        provider="gigachat-vision",
        data=data,
        params_hash=params_hash,
    )

    await repo.upsert_enrichment(
        post_id=post_id,
        kind="vision",
        provider="gigachat-vision",
        data=data,
        params_hash=params_hash,
    )

    sql_statements: List[str] = [call.args[0] for call in pool.connection.execute.await_args_list]
    assert any("ON CONFLICT" in sql for sql in sql_statements)


@pytest.mark.asyncio
async def test_enrichment_repository_multiple_kinds():
    pool = DummyAsyncpgPool()
    repo = EnrichmentRepository(pool)

    post_id = str(uuid.uuid4())

    await repo.upsert_enrichment(
        post_id=post_id,
        kind="vision",
        provider="gigachat-vision",
        data={"labels": []},
    )

    await repo.upsert_enrichment(
        post_id=post_id,
        kind="tags",
        provider="gigachat",
        data={"tags": ["test"]},
    )

    await repo.upsert_enrichment(
        post_id=post_id,
        kind="crawl",
        provider="crawl4ai",
        data={"crawl_md": "Content"},
    )

    sql_statements: List[str] = [call.args[0] for call in pool.connection.execute.await_args_list]
    assert any("kind = 'vision'" in sql for sql in sql_statements)
    assert any("kind = 'tags'" in sql for sql in sql_statements)
    assert any("kind = $3" in sql for sql in sql_statements)


@pytest.mark.asyncio
async def test_enrichment_repository_validation():
    repo = EnrichmentRepository(DummyAsyncpgPool())

    post_id = str(uuid.uuid4())

    with pytest.raises(ValueError, match="Invalid kind"):
        await repo.upsert_enrichment(
            post_id=post_id,
            kind="invalid_kind",
            provider="test",
            data={},
        )

    await repo.upsert_enrichment(
        post_id=post_id,
        kind="vision",
        provider="gigachat-vision",
        data={},
    )

