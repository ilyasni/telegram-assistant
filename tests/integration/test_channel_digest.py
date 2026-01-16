"""
Интеграционные тесты для канального дайджеста.
Context7: Проверка всех компонентов - сбор постов, генерация контекста, LLM генерация.
"""

import pytest
import asyncio
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone, date
from sqlalchemy.orm import Session
from unittest.mock import Mock, AsyncMock, patch

from api.services.digest_service import DigestService, DigestContent
from models.database import Post, Channel, User, UserChannel, PostEnrichment


@pytest.fixture
def mock_db_session():
    """Mock DB сессии для тестов."""
    session = Mock(spec=Session)
    session.query = Mock()
    return session


@pytest.fixture
def mock_redis_client():
    """Mock Redis клиента."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock()
    return client


@pytest.fixture
def mock_qdrant_client():
    """Mock Qdrant клиента."""
    client = Mock()
    client.get_collections = Mock(return_value=Mock(collections=[]))
    return client


@pytest.fixture
def digest_service(mock_redis_client, mock_qdrant_client):
    """Создание DigestService для тестов."""
    service = DigestService(
        qdrant_url="http://localhost:6333",
        qdrant_client=mock_qdrant_client,
        redis_client=mock_redis_client
    )
    return service


@pytest.mark.asyncio
async def test_collect_channel_posts_ranking(digest_service, mock_db_session):
    """Тест ранжирования постов по engagement + freshness."""
    # Создаем тестовые посты
    channel_id = uuid4()
    now = datetime.now(timezone.utc)
    
    posts = [
        Mock(
            spec=Post,
            id=uuid4(),
            channel_id=channel_id,
            content=f"Post {i}",
            engagement_score=10.0 - i,
            posted_at=now - timedelta(hours=i),
            views_count=100 - i * 10,
            reactions_count=10 - i,
            forwards_count=5 - i,
            replies_count=2,
            telegram_post_url=f"https://t.me/channel/1{i}"
        )
        for i in range(5)
    ]
    
    # Mock запросов к БД
    mock_query = Mock()
    mock_query.filter = Mock(return_value=mock_query)
    mock_query.order_by = Mock(return_value=posts)
    mock_db_session.query.return_value = mock_query
    
    # Mock запросов для enrichments
    mock_enrichment_query = Mock()
    mock_enrichment_query.filter = Mock(return_value=[])
    mock_db_session.query.return_value = mock_enrichment_query
    
    result = await digest_service._collect_channel_posts_for_digest(
        channel_id=channel_id,
        period_days=7,
        db=mock_db_session,
        max_candidates=10
    )
    
    # Проверяем, что посты отсортированы по engagement (убывание)
    assert len(result) > 0
    if len(result) > 1:
        assert result[0].engagement_score >= result[1].engagement_score


@pytest.mark.asyncio
async def test_assemble_context_token_budget(digest_service):
    """Тест сборки контекста с токен-бюджетом."""
    # Создаем тестовые посты
    posts = [
        Mock(
            spec=Post,
            id=uuid4(),
            content="A" * 1000,  # Длинный пост
            engagement_score=10.0,
            views_count=100,
            reactions_count=10,
            forwards_count=5,
            replies_count=2,
            telegram_post_url="https://t.me/channel/1"
        )
        for _ in range(10)
    ]
    
    # Тест для дня (бюджет 7K токенов, обрезка до 600 символов)
    context = await digest_service._assemble_channel_context(posts, period_days=1, max_tokens=7000)
    
    assert context
    # Проверяем, что посты обрезаны
    assert len(context) < len("A" * 1000) * 10  # Обрезка произошла
    
    # Проверяем оценку токенов
    estimated_tokens = digest_service._estimate_tokens(context)
    assert estimated_tokens <= 7000  # В пределах бюджета


@pytest.mark.asyncio
async def test_cache_operations(digest_service, mock_redis_client):
    """Тест операций кеширования."""
    cache_key = "test:key"
    content = DigestContent(
        content="Test digest",
        posts_count=5,
        topics=[],
        sections=[]
    )
    ttl = 3600
    
    # Тест сохранения в кеш
    await digest_service._save_to_cache(cache_key, content, ttl)
    
    # Проверяем, что setex был вызван
    mock_redis_client.setex.assert_called_once()
    call_args = mock_redis_client.setex.call_args
    assert call_args[0][0] == cache_key
    assert call_args[0][1] == ttl
    
    # Тест получения из кеша (пустой кеш)
    mock_redis_client.get = AsyncMock(return_value=None)
    cached = await digest_service._get_cached_digest(cache_key)
    assert cached is None
    
    # Тест получения из кеша (данные в кеше)
    import json
    cached_data = json.dumps(content.model_dump())
    mock_redis_client.get = AsyncMock(return_value=cached_data)
    cached = await digest_service._get_cached_digest(cache_key)
    assert cached is not None
    assert cached.posts_count == content.posts_count


@pytest.mark.asyncio
async def test_map_reduce_digest_structure(digest_service):
    """Тест структуры двухступенчатого саммари."""
    # Создаем посты за месяц (группировка по неделям)
    channel_id = uuid4()
    now = datetime.now(timezone.utc)
    
    posts = []
    for week in range(4):
        for day in range(7):
            posts.append(Mock(
                spec=Post,
                id=uuid4(),
                channel_id=channel_id,
                content=f"Week {week}, Day {day} post",
                engagement_score=5.0,
                posted_at=now - timedelta(weeks=week, days=day),
                views_count=50,
                reactions_count=5,
                forwards_count=2,
                replies_count=1,
                telegram_post_url=f"https://t.me/channel/{week}{day}"
            ))
    
    # Mock LLM для генерации саммари
    with patch.object(digest_service.llm, 'ainvoke', new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = Mock(content="Test summary for chunk")
        
        result = await digest_service._generate_map_reduce_digest(
            posts=posts,
            period_days=30,
            tenant_id="test-tenant"
        )
        
        assert result.posts_count == len(posts)
        assert result.content
        # Проверяем, что LLM был вызван несколько раз (для каждого чанка + финальный)
        assert mock_llm.call_count > 1


def test_token_estimation():
    """Тест оценки токенов."""
    service = DigestService(qdrant_url="http://localhost:6333")
    
    # Русский текст: примерно 4 символа = 1 токен
    text = "А" * 400
    tokens = service._estimate_tokens(text)
    assert 90 <= tokens <= 110  # Примерно 100 токенов


def test_cache_key_generation():
    """Тест генерации ключа кеша."""
    service = DigestService(qdrant_url="http://localhost:6333")
    
    tenant_id = "tenant-123"
    user_id = uuid4()
    channel_id = uuid4()
    period_days = 7
    window_end_date = date(2025, 1, 20)
    
    key = service._get_cache_key(
        tenant_id=tenant_id,
        user_id=user_id,
        channel_id=channel_id,
        period_days=period_days,
        window_end_date=window_end_date
    )
    
    assert tenant_id in key
    assert str(user_id) in key
    assert str(channel_id) in key
    assert str(period_days) in key
    assert "2025-01-20" in key


def test_cache_ttl_by_period():
    """Тест TTL кеша в зависимости от периода."""
    service = DigestService(qdrant_url="http://localhost:6333")
    
    assert service._get_cache_ttl(1) == 3600 * 2  # 2 часа
    assert service._get_cache_ttl(7) == 3600 * 8  # 8 часов
    assert service._get_cache_ttl(30) == 3600 * 24  # 24 часа
