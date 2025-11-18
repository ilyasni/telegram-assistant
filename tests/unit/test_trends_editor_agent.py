"""
Unit tests for Trend Editor Agent.

Context7: Тесты проверяют качество карточек, улучшение через LLM, интеграцию с Taxonomy Agent.
"""

import json
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from types import ModuleType

# Mock dependencies before importing TrendEditorAgent
prometheus_module = ModuleType("prometheus_client")
_counter_stub = MagicMock()
_counter_stub.labels = MagicMock(return_value=_counter_stub)
_counter_stub.inc = MagicMock()
_histogram_stub = MagicMock()
_histogram_stub.labels = MagicMock(return_value=_histogram_stub)
_histogram_stub.observe = MagicMock()
prometheus_module.Counter = MagicMock(return_value=_counter_stub)
prometheus_module.Histogram = MagicMock(return_value=_histogram_stub)
sys.modules["prometheus_client"] = prometheus_module

# Mock other dependencies
sys.modules["asyncpg"] = MagicMock()
sys.modules["event_bus"] = MagicMock()
sys.modules["config"] = MagicMock()
sys.modules["trends_taxonomy_agent"] = MagicMock()

from api.worker.trends_editor_agent import TrendEditorAgent


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    client = AsyncMock()
    client.client = AsyncMock()
    client.client.exists = AsyncMock(return_value=0)
    client.client.setex = AsyncMock()
    return client


@pytest.fixture
def mock_db_pool():
    """Mock database pool."""
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire = AsyncMock(return_value=conn.__aenter__())
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()
    return pool


@pytest.fixture
def editor_agent(mock_redis_client, mock_db_pool):
    """Trend Editor Agent instance."""
    # Mock taxonomy agent before creating TrendEditorAgent
    mock_taxonomy_agent = MagicMock()
    mock_taxonomy_agent.categorize_trend = AsyncMock(return_value={
        "categories": ["tech"],
        "normalized_topics": ["технологии"],
        "primary_category": "tech",
    })
    
    with patch("api.worker.trends_editor_agent.create_taxonomy_agent", return_value=mock_taxonomy_agent):
        agent = TrendEditorAgent(
            redis_url="redis://test:6379",
            database_url="postgresql://test:5432/test",
        )
        agent.redis_client = mock_redis_client
        agent.db_pool = mock_db_pool
        agent.taxonomy_agent = mock_taxonomy_agent
        agent.editor_enabled = True
        return agent


@pytest.fixture
def sample_cluster_data():
    """Sample cluster data for testing."""
    return {
        "id": str(uuid4()),
        "cluster_key": "test_cluster_key",
        "label": "первый",
        "summary": "Краткое описание",
        "keywords": ["ключ", "слово"],
        "primary_topic": "тема",
        "topics": ["тема1", "тема2"],
        "why_important": None,
        "card_payload": {
            "title": "первый",
            "summary": "Краткое",
            "why_important": None,
            "topics": ["тема1"],
            "keywords": ["ключ"],
            "example_posts": [
                {
                    "channel_title": "Канал",
                    "content_snippet": "Пример поста с важной информацией",
                    "posted_at": "2025-01-15T12:00:00Z",
                }
            ],
        },
        "quality_score": None,
        "quality_flags": [],
    }


@pytest.mark.asyncio
async def test_editor_agent_checks_card_quality(editor_agent, sample_cluster_data):
    """Тест проверки качества карточки."""
    with patch.object(editor_agent, "_call_editor_llm") as mock_llm:
        mock_llm.return_value = {
            "quality_score": 0.7,
            "quality_flags": ["generic_title"],
            "improved_title": "Важная новость — обновление",
            "improved_summary": "Подробное описание важной новости",
            "improved_why_important": "Важно потому что...",
            "editor_notes": "Улучшен заголовок",
        }

        result = await editor_agent._edit_card(sample_cluster_data)

        assert result is not None
        assert result["quality_score"] == 0.7
        assert "improved_title" in result
        mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_editor_agent_filters_low_quality(editor_agent, sample_cluster_data):
    """Тест фильтрации карточек с низким качеством."""
    editor_agent.editor_min_score = 0.8

    with patch.object(editor_agent, "_call_editor_llm") as mock_llm:
        mock_llm.return_value = {
            "quality_score": 0.5,
            "quality_flags": ["generic_title", "missing_summary"],
            "improved_title": "Улучшенный заголовок",
            "improved_summary": "Улучшенное описание",
            "improved_why_important": "Важно",
            "editor_notes": "Низкое качество",
        }

        result = await editor_agent._edit_card(sample_cluster_data)

        # Результат всё равно возвращается, но с низким quality_score
        assert result is not None
        assert result["quality_score"] == 0.5


@pytest.mark.asyncio
async def test_editor_agent_integrates_taxonomy(editor_agent, sample_cluster_data):
    """Тест интеграции с Taxonomy Agent."""
    with patch.object(editor_agent, "_call_editor_llm") as mock_llm, patch.object(
        editor_agent.taxonomy_agent, "categorize_trend"
    ) as mock_taxonomy:
        mock_llm.return_value = {
            "quality_score": 0.8,
            "quality_flags": [],
            "improved_title": "Заголовок",
            "improved_summary": "Описание",
            "improved_why_important": "Важно",
            "editor_notes": "OK",
        }
        mock_taxonomy.return_value = {
            "categories": ["tech", "ai"],
            "normalized_topics": ["технологии", "искусственный интеллект"],
            "primary_category": "tech",
        }

        result = await editor_agent._edit_card(sample_cluster_data)

        assert result is not None
        assert "taxonomy_categories" in result
        assert result["taxonomy_categories"] == ["tech", "ai"]
        mock_taxonomy.assert_called_once()


@pytest.mark.asyncio
async def test_editor_agent_handles_llm_error(editor_agent, sample_cluster_data):
    """Тест обработки ошибок LLM."""
    with patch.object(editor_agent, "_call_editor_llm") as mock_llm:
        mock_llm.return_value = None

        result = await editor_agent._edit_card(sample_cluster_data)

        assert result is None


@pytest.mark.asyncio
async def test_editor_agent_cooldown(editor_agent):
    """Тест механизма cooldown."""
    cluster_id = str(uuid4())

    # Первая проверка - cooldown не установлен
    is_cooldown = await editor_agent._is_cluster_in_cooldown(cluster_id)
    assert is_cooldown is False

    # Устанавливаем cooldown
    await editor_agent._set_cluster_cooldown(cluster_id)

    # Вторая проверка - cooldown установлен
    editor_agent.redis_client.client.exists = AsyncMock(return_value=1)
    is_cooldown = await editor_agent._is_cluster_in_cooldown(cluster_id)
    assert is_cooldown is True


@pytest.mark.asyncio
async def test_editor_agent_fetch_cluster(editor_agent, mock_db_pool):
    """Тест загрузки кластера из БД."""
    cluster_id = str(uuid4())
    mock_record = MagicMock()
    mock_record.get = MagicMock(side_effect=lambda k: {
        "id": cluster_id,
        "cluster_key": "test_key",
        "label": "Тест",
        "summary": "Описание",
        "keywords": ["ключ"],
        "primary_topic": "тема",
        "topics": ["тема1"],
        "why_important": "Важно",
        "card_payload": {"title": "Тест"},
        "quality_score": None,
        "quality_flags": [],
    }.get(k))

    async with mock_db_pool.acquire() as conn:
        conn.fetchrow = AsyncMock(return_value=mock_record)

    editor_agent.db_pool = mock_db_pool
    result = await editor_agent._fetch_cluster(cluster_id)

    assert result is not None
    assert result["id"] == cluster_id
    assert result["label"] == "Тест"


def test_editor_agent_safe_parse_json(editor_agent):
    """Тест безопасного парсинга JSON из LLM."""
    # Нормальный JSON
    result = editor_agent._safe_parse_json_obj('{"key": "value"}')
    assert result == {"key": "value"}

    # JSON в markdown code block
    result = editor_agent._safe_parse_json_obj('```json\n{"key": "value"}\n```')
    assert result == {"key": "value"}

    # Невалидный JSON
    result = editor_agent._safe_parse_json_obj("not json")
    assert result is None

    # Пустая строка
    result = editor_agent._safe_parse_json_obj("")
    assert result is None

