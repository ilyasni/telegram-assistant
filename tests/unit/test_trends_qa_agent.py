"""
Unit tests for Trend QA/Filter Agent.

Context7: Тесты проверяют фильтрацию трендов по качеству и релевантности.
"""

import json
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from types import ModuleType

# Mock prometheus_client before importing
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

# Mock other dependencies (but not os, as it's used in the code)
sys.modules["httpx"] = MagicMock()

from api.routers.trends import _call_qa_agent, _filter_trends_with_qa, _load_user_profile
from api.models.database import TrendCluster, UserTrendProfile


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = MagicMock()
    return db


@pytest.fixture
def sample_cluster():
    """Sample trend cluster."""
    cluster = MagicMock(spec=TrendCluster)
    cluster.id = uuid4()
    cluster.quality_score = 0.8
    cluster.label = "Важная новость"
    cluster.summary = "Подробное описание"
    cluster.topics = ["технологии", "ai"]
    cluster.keywords = ["ключ", "слово"]
    cluster.window_mentions = 10
    cluster.sources_count = 5
    cluster.burst_score = 3.5
    return cluster


@pytest.mark.asyncio
async def test_qa_agent_checks_quality_score(mock_db, sample_cluster):
    """Тест проверки quality_score."""
    # Низкий quality_score
    sample_cluster.quality_score = 0.4

    with patch("api.routers.trends.os.getenv", return_value="true"):
        result = await _call_qa_agent(sample_cluster, None, None, mock_db)

        assert result is not None
        assert result["should_show"] is False
        assert "low_quality_score" in result.get("reasoning", "")


@pytest.mark.asyncio
async def test_qa_agent_passes_high_quality(mock_db, sample_cluster):
    """Тест пропуска трендов с высоким качеством."""
    sample_cluster.quality_score = 0.9

    with patch("api.routers.trends.os.getenv", return_value="true"):
        result = await _call_qa_agent(sample_cluster, None, None, mock_db)

        assert result is not None
        assert result["should_show"] is True
        assert result["relevance_score"] >= 0.8


@pytest.mark.asyncio
async def test_qa_agent_checks_relevance_with_user(mock_db, sample_cluster):
    """Тест проверки релевантности для пользователя."""
    user_id = uuid4()
    user_profile = {
        "preferred_topics": ["технологии"],
        "preferred_categories": ["tech"],
    }

    with patch("api.routers.trends.os.getenv", return_value="true"), patch(
        "api.routers.trends._load_user_channel_ids", return_value={uuid4()}
    ), patch("api.routers.trends._build_trend_card") as mock_card, patch(
        "api.routers.trends.httpx.AsyncClient"
    ) as mock_client:
        mock_card.return_value = MagicMock(
            title="Тест",
            summary="Описание",
            topics=["технологии"],
            keywords=["ключ"],
            stats=MagicMock(mentions=10, sources=5, burst_score=3.5),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "should_show": True,
                                "relevance_score": 0.9,
                                "reasoning": "Релевантно",
                                "user_message": "Для вас важно",
                            }
                        )
                    }
                }
            ]
        }
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await _call_qa_agent(sample_cluster, user_id, user_profile, mock_db)

        assert result is not None
        assert result["should_show"] is True
        assert result["relevance_score"] == 0.9


@pytest.mark.asyncio
async def test_qa_agent_fail_open_on_error(mock_db, sample_cluster):
    """Тест fail-open стратегии при ошибке LLM."""
    with patch("api.routers.trends.os.getenv", return_value="true"), patch(
        "api.routers.trends.httpx.AsyncClient"
    ) as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=Exception("LLM error")
        )

        result = await _call_qa_agent(sample_cluster, None, None, mock_db)

        # При ошибке показываем тренд (fail-open)
        assert result is not None
        assert result["should_show"] is True
        assert result["relevance_score"] == 0.7


@pytest.mark.asyncio
async def test_filter_trends_with_qa(mock_db, sample_cluster):
    """Тест фильтрации списка трендов."""
    clusters = [sample_cluster]

    with patch("api.routers.trends.os.getenv", return_value="true"), patch(
        "api.routers.trends._call_qa_agent"
    ) as mock_qa:
        mock_qa.return_value = {
            "should_show": True,
            "relevance_score": 0.9,
        }

        filtered = await _filter_trends_with_qa(clusters, None, mock_db, limit=10)

        assert len(filtered) == 1
        assert filtered[0] == sample_cluster
        mock_qa.assert_called_once()


@pytest.mark.asyncio
async def test_filter_trends_excludes_low_quality(mock_db, sample_cluster):
    """Тест исключения трендов с низким качеством."""
    clusters = [sample_cluster]

    with patch("api.routers.trends.os.getenv", return_value="true"), patch(
        "api.routers.trends._call_qa_agent"
    ) as mock_qa:
        mock_qa.return_value = {
            "should_show": False,
            "relevance_score": 0.3,
            "reasoning": "Низкое качество",
        }

        filtered = await _filter_trends_with_qa(clusters, None, mock_db, limit=10)

        assert len(filtered) == 0


def test_load_user_profile(mock_db):
    """Тест загрузки профиля пользователя."""
    user_id = uuid4()
    profile = MagicMock(spec=UserTrendProfile)
    profile.preferred_topics = ["tech", "ai"]
    profile.ignored_topics = ["politics"]
    profile.preferred_categories = ["tech"]
    profile.typical_time_windows = ["morning"]
    profile.interaction_stats = {"views": 10}

    mock_db.query.return_value.filter.return_value.first.return_value = profile

    result = _load_user_profile(mock_db, user_id)

    assert result is not None
    assert result["preferred_topics"] == ["tech", "ai"]
    assert result["ignored_topics"] == ["politics"]


def test_load_user_profile_not_found(mock_db):
    """Тест загрузки профиля, когда его нет."""
    user_id = uuid4()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    result = _load_user_profile(mock_db, user_id)

    assert result is None

