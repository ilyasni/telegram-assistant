"""
E2E tests for Trend Agents pipeline.

Context7: Полный пайплайн от создания тренда до показа пользователю через агентов.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from api.models.database import TrendCluster, TrendInteraction, UserTrendProfile
from api.worker.trends_editor_agent import TrendEditorAgent
from api.routers.trends import _filter_trends_with_qa, _call_qa_agent
from api.services.user_trend_profile_service import UserTrendProfileService


@pytest.fixture
def sample_cluster_with_bad_title():
    """Кластер с плохим заголовком (одиночное слово)."""
    cluster = MagicMock(spec=TrendCluster)
    cluster.id = uuid4()
    cluster.cluster_key = "test_key"
    cluster.status = "emerging"
    cluster.label = "первый"
    cluster.summary = "Краткое описание"
    cluster.primary_topic = "первый"
    cluster.topics = ["технологии", "ai"]
    cluster.keywords = ["ключ", "слово"]
    cluster.why_important = None
    cluster.quality_score = None
    cluster.quality_flags = []
    cluster.taxonomy_categories = []
    cluster.window_mentions = 10
    cluster.sources_count = 5
    cluster.burst_score = 3.5
    cluster.coherence_score = 0.7
    cluster.card_payload = {
        "title": "первый",
        "summary": "Краткое",
        "why_important": None,
        "topics": ["технологии"],
        "keywords": ["ключ"],
        "example_posts": [
            {
                "channel_title": "Канал",
                "content_snippet": "Пример поста",
                "posted_at": "2025-01-15T12:00:00Z",
            }
        ],
    }
    return cluster


@pytest.mark.asyncio
async def test_e2e_editor_improves_bad_title(sample_cluster_with_bad_title):
    """E2E: Editor Agent улучшает плохой заголовок."""
    editor = TrendEditorAgent(
        redis_url="redis://test:6379",
        database_url="postgresql://test:5432/test",
    )
    editor.editor_enabled = True

    cluster_data = {
        "id": str(sample_cluster_with_bad_title.id),
        "cluster_key": sample_cluster_with_bad_title.cluster_key,
        "label": sample_cluster_with_bad_title.label,
        "summary": sample_cluster_with_bad_title.summary,
        "keywords": sample_cluster_with_bad_title.keywords,
        "primary_topic": sample_cluster_with_bad_title.primary_topic,
        "topics": sample_cluster_with_bad_title.topics,
        "why_important": sample_cluster_with_bad_title.why_important,
        "card_payload": sample_cluster_with_bad_title.card_payload,
        "quality_score": sample_cluster_with_bad_title.quality_score,
        "quality_flags": sample_cluster_with_bad_title.quality_flags,
    }

    with patch.object(editor, "_call_editor_llm") as mock_llm, patch.object(
        editor.taxonomy_agent, "categorize_trend"
    ) as mock_taxonomy:
        mock_llm.return_value = {
            "quality_score": 0.8,
            "quality_flags": ["generic_title"],
            "improved_title": "Важная новость — обновление технологий",
            "improved_summary": "Подробное описание важной новости",
            "improved_why_important": "Важно потому что затрагивает технологии",
            "editor_notes": "Улучшен заголовок с одиночного слова",
        }
        mock_taxonomy.return_value = {
            "categories": ["tech"],
            "normalized_topics": ["технологии"],
            "primary_category": "tech",
        }

        result = await editor._edit_card(cluster_data)

        assert result is not None
        assert result["quality_score"] == 0.8
        assert result["improved_title"] != "первый"
        assert "taxonomy_categories" in result
        assert "tech" in result["taxonomy_categories"]


@pytest.mark.asyncio
async def test_e2e_qa_filters_low_quality(sample_cluster_with_bad_title):
    """E2E: QA Agent фильтрует тренды с низким качеством."""
    sample_cluster_with_bad_title.quality_score = 0.4

    mock_db = MagicMock()

    with patch("api.routers.trends.os.getenv", return_value="true"):
        result = await _call_qa_agent(
            sample_cluster_with_bad_title, None, None, mock_db
        )

        assert result is not None
        assert result["should_show"] is False


@pytest.mark.asyncio
async def test_e2e_qa_passes_high_quality(sample_cluster_with_bad_title):
    """E2E: QA Agent пропускает тренды с высоким качеством."""
    sample_cluster_with_bad_title.quality_score = 0.9

    mock_db = MagicMock()

    with patch("api.routers.trends.os.getenv", return_value="true"):
        result = await _call_qa_agent(
            sample_cluster_with_bad_title, None, None, mock_db
        )

        assert result is not None
        assert result["should_show"] is True
        assert result["relevance_score"] >= 0.8


@pytest.mark.asyncio
async def test_e2e_personalizer_updates_profile():
    """E2E: Personalizer Agent обновляет профиль на основе взаимодействий."""
    mock_db = MagicMock()
    profile_service = UserTrendProfileService(mock_db)

    user_id = uuid4()
    cluster_id = uuid4()

    # Mock interactions
    interaction = MagicMock()
    interaction.cluster_id = cluster_id
    interaction.created_at = datetime.now(timezone.utc)

    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        interaction
    ]

    # Mock cluster
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

        profile = await profile_service.build_profile_agent(user_id, days=30)

        assert profile is not None
        assert "tech" in profile["preferred_topics"]
        assert "ai" in profile["preferred_topics"]


@pytest.mark.asyncio
async def test_e2e_full_pipeline_improves_and_filters():
    """
    E2E: Полный пайплайн - Editor улучшает, QA фильтрует.
    """
    # 1. Кластер с плохим заголовком
    cluster = MagicMock(spec=TrendCluster)
    cluster.id = uuid4()
    cluster.quality_score = None
    cluster.label = "работы"
    cluster.summary = "Описание"
    cluster.topics = ["технологии"]
    cluster.keywords = ["ключ"]
    cluster.window_mentions = 10
    cluster.sources_count = 5
    cluster.burst_score = 3.5
    cluster.card_payload = {
        "title": "работы",
        "summary": "Описание",
        "topics": ["технологии"],
    }

    # 2. Editor улучшает
    editor = TrendEditorAgent(
        redis_url="redis://test:6379",
        database_url="postgresql://test:5432/test",
    )

    cluster_data = {
        "id": str(cluster.id),
        "label": cluster.label,
        "summary": cluster.summary,
        "topics": cluster.topics,
        "keywords": cluster.keywords,
        "card_payload": cluster.card_payload,
        "quality_score": None,
        "quality_flags": [],
    }

    with patch.object(editor, "_call_editor_llm") as mock_llm, patch.object(
        editor.taxonomy_agent, "categorize_trend"
    ) as mock_taxonomy:
        mock_llm.return_value = {
            "quality_score": 0.85,
            "quality_flags": [],
            "improved_title": "Новые технологии в работе",
            "improved_summary": "Подробное описание",
            "improved_why_important": "Важно",
            "editor_notes": "Улучшен",
        }
        mock_taxonomy.return_value = {
            "categories": ["tech"],
            "normalized_topics": ["технологии"],
            "primary_category": "tech",
        }

        editor_result = await editor._edit_card(cluster_data)
        assert editor_result is not None
        assert editor_result["quality_score"] == 0.85

        # 3. Обновляем кластер с результатом редактора
        cluster.quality_score = 0.85
        cluster.label = "Новые технологии в работе"

        # 4. QA проверяет и пропускает
        mock_db = MagicMock()
        with patch("api.routers.trends.os.getenv", return_value="true"):
            qa_result = await _call_qa_agent(cluster, None, None, mock_db)

            assert qa_result is not None
            assert qa_result["should_show"] is True
            assert qa_result["relevance_score"] >= 0.8

