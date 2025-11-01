"""
Unit тесты для EnrichmentRepository.

Context7: Проверка единого репозитория для всех видов обогащений.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# Тест импорта и базовой функциональности
def test_enrichment_repository_import():
    """Проверка импорта EnrichmentRepository."""
    import sys
    import os
    shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
    if shared_path not in sys.path:
        sys.path.insert(0, shared_path)
    
    from shared.repositories.enrichment_repository import EnrichmentRepository
    assert EnrichmentRepository is not None


def test_compute_params_hash():
    """Проверка вычисления params_hash."""
    import sys
    import os
    shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
    if shared_path not in sys.path:
        sys.path.insert(0, shared_path)
    
    from shared.repositories.enrichment_repository import EnrichmentRepository
    
    # Мок db_session
    mock_db = MagicMock()
    repo = EnrichmentRepository(mock_db)
    
    # Тест вычисления hash
    hash1 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.35})
    hash2 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.35})
    hash3 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.40})
    
    # Одинаковые параметры дают одинаковый hash
    assert hash1 == hash2
    # Разные параметры дают разный hash
    assert hash1 != hash3
    # Hash имеет правильную длину (SHA256 hex = 64 символа)
    assert len(hash1) == 64


@pytest.mark.asyncio
async def test_upsert_enrichment_with_asyncpg():
    """Проверка upsert с asyncpg.Pool."""
    import sys
    import os
    shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
    if shared_path not in sys.path:
        sys.path.insert(0, shared_path)
    
    from shared.repositories.enrichment_repository import EnrichmentRepository
    import asyncpg
    
    # Мок asyncpg.Pool
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
    
    repo = EnrichmentRepository(mock_pool)
    
    # Вызов upsert
    await repo.upsert_enrichment(
        post_id='test-post-id',
        kind='vision',
        provider='gigachat-vision',
        data={'model': 'gigachat-vision', 'labels': []},
        trace_id='test-trace-id'
    )
    
    # Проверка вызова execute
    assert mock_conn.execute.called
    call_args = mock_conn.execute.call_args
    assert 'INSERT INTO post_enrichment' in call_args[0][0]


@pytest.mark.asyncio
async def test_upsert_enrichment_validation():
    """Проверка валидации kind."""
    import sys
    import os
    shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
    if shared_path not in sys.path:
        sys.path.insert(0, shared_path)
    
    from shared.repositories.enrichment_repository import EnrichmentRepository
    
    mock_db = MagicMock()
    repo = EnrichmentRepository(mock_db)
    
    # Невалидный kind должен вызывать ошибку
    with pytest.raises(ValueError, match="Invalid kind"):
        await repo.upsert_enrichment(
            post_id='test-post-id',
            kind='invalid_kind',
            provider='test',
            data={}
        )


if __name__ == '__main__':
    # Простой запуск без pytest для проверки базовой функциональности
    print("Testing EnrichmentRepository import...")
    test_enrichment_repository_import()
    print("✓ Import test passed")
    
    print("Testing params_hash computation...")
    test_compute_params_hash()
    print("✓ Params hash test passed")
    
    print("All basic tests passed!")

