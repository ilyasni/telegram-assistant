"""
Unit tests for User Trend Profile Service.

Context7: Тесты проверяют профилирование интересов пользователей.
"""

import json
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from types import ModuleType

# Mock dependencies before importing
sys.modules["sqlalchemy"] = MagicMock()
sys.modules["sqlalchemy.orm"] = MagicMock()
sys.modules["sqlalchemy"] = MagicMock()

from api.services.user_trend_profile_service import UserTrendProfileService
from api.models.database import TrendInteraction, TrendCluster


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = MagicMock()
    return db


@pytest.fixture
def profile_service(mock_db):
    """User Trend Profile Service instance."""
    return UserTrendProfileService(mock_db)


@pytest.mark.asyncio
async def test_update_profile_from_interaction(profile_service, mock_db):
    """Тест обновления профиля на основе взаимодействия."""
    user_id = uuid4()
    cluster_id = uuid4()

    profile_service.update_profile_from_interaction(
        user_id, cluster_id, "view"
    )

    # Проверяем, что взаимодействие было добавлено
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


def test_get_user_profile(profile_service, mock_db):
    """Тест загрузки профиля пользователя."""
    user_id = uuid4()
    profile = MagicMock()
    profile.preferred_topics = ["tech"]
    profile.ignored_topics = ["politics"]
    profile.preferred_categories = ["tech"]
    profile.typical_time_windows = ["morning"]
    profile.interaction_stats = {}

    mock_db.query.return_value.filter.return_value.first.return_value = profile

    result = profile_service.get_user_profile(user_id)

    assert result is not None
    assert result["preferred_topics"] == ["tech"]


def test_get_user_profile_not_found(profile_service, mock_db):
    """Тест загрузки профиля, когда его нет."""
    user_id = uuid4()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    result = profile_service.get_user_profile(user_id)

    assert result is None


@pytest.mark.asyncio
async def test_build_profile_agent(profile_service, mock_db):
    """Тест построения профиля через LLM."""
    user_id = uuid4()
    cluster_id = uuid4()

    # Mock interactions
    interaction = MagicMock()
    interaction.cluster_id = cluster_id
    interaction.created_at = datetime.now(timezone.utc)

    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        interaction
    ]

    # Mock clusters
    cluster = MagicMock()
    cluster.id = cluster_id
    cluster.topics = ["tech", "ai"]
    cluster.taxonomy_categories = ["tech"]

    mock_db.query.return_value.filter.return_value.all.return_value = [cluster]

    with patch.object(profile_service, "_call_profile_llm") as mock_llm:
        mock_llm.return_value = {
            "preferred_topics": ["tech", "ai"],
            "ignored_topics": ["politics"],
            "preferred_categories": ["tech"],
            "typical_time_windows": ["morning"],
        }

        result = await profile_service.build_profile_agent(user_id, days=30)

        assert result is not None
        assert "tech" in result["preferred_topics"]
        mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_build_profile_agent_fallback(profile_service, mock_db):
    """Тест fallback построения профиля без LLM."""
    user_id = uuid4()
    cluster_id = uuid4()

    interaction = MagicMock()
    interaction.cluster_id = cluster_id
    interaction.created_at = datetime.now(timezone.utc)

    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        interaction
    ]

    cluster = MagicMock()
    cluster.id = cluster_id
    cluster.topics = ["tech"]
    cluster.taxonomy_categories = ["tech"]

    mock_db.query.return_value.filter.return_value.all.return_value = [cluster]

    with patch.object(profile_service, "_call_profile_llm") as mock_llm:
        mock_llm.return_value = None

        result = await profile_service.build_profile_agent(user_id, days=30)

        # Fallback должен вернуть простой профиль
        assert result is not None
        assert "preferred_topics" in result
        assert isinstance(result["preferred_topics"], list)


@pytest.mark.asyncio
async def test_build_profile_agent_no_interactions(profile_service, mock_db):
    """Тест построения профиля без взаимодействий."""
    user_id = uuid4()

    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

    result = await profile_service.build_profile_agent(user_id, days=30)

    assert result is not None
    assert result["preferred_topics"] == []
    assert result["ignored_topics"] == []


def test_save_profile_new(profile_service, mock_db):
    """Тест сохранения нового профиля."""
    user_id = uuid4()
    profile = {
        "preferred_topics": ["tech"],
        "ignored_topics": ["politics"],
        "preferred_categories": ["tech"],
        "typical_time_windows": ["morning"],
        "interaction_stats": {},
    }

    mock_db.query.return_value.filter.return_value.first.return_value = None

    profile_service.save_profile(user_id, profile)

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


def test_save_profile_update(profile_service, mock_db):
    """Тест обновления существующего профиля."""
    user_id = uuid4()
    profile = {
        "preferred_topics": ["tech", "ai"],
        "ignored_topics": [],
        "preferred_categories": ["tech"],
        "typical_time_windows": [],
        "interaction_stats": {},
    }

    existing_profile = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = existing_profile

    profile_service.save_profile(user_id, profile)

    assert existing_profile.preferred_topics == ["tech", "ai"]
    mock_db.commit.assert_called_once()

