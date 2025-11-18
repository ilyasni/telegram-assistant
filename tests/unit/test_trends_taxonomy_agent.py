"""
Unit tests for Taxonomy Agent.

Context7: Тесты проверяют категоризацию трендов и нормализацию topics.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from api.worker.trends_taxonomy_agent import TaxonomyAgent, TAXONOMY_CATEGORIES


@pytest.fixture
def taxonomy_agent():
    """Taxonomy Agent instance."""
    return TaxonomyAgent()


@pytest.fixture
def sample_card_payload():
    """Sample card payload."""
    return {
        "title": "Рынок авто растёт",
        "summary": "Продажи автомобилей выросли на 20%",
        "topics": ["рынок авто", "продажи"],
        "keywords": ["автомобили", "рынок"],
        "example_posts": [
            {
                "channel_title": "Автоновости",
                "content_snippet": "Продажи выросли",
            }
        ],
    }


@pytest.mark.asyncio
async def test_taxonomy_agent_categorizes_trend(taxonomy_agent, sample_card_payload):
    """Тест категоризации тренда."""
    with patch.object(taxonomy_agent, "_call_taxonomy_agent") as mock_llm:
        mock_llm.return_value = {
            "categories": ["auto", "finance"],
            "normalized_topics": ["автомобили", "рынок"],
            "primary_category": "auto",
        }

        result = await taxonomy_agent.categorize_trend(sample_card_payload, [])

        assert result is not None
        assert "auto" in result["categories"]
        assert result["primary_category"] == "auto"
        assert "автомобили" in result["normalized_topics"]


@pytest.mark.asyncio
async def test_taxonomy_agent_fallback_categorization(taxonomy_agent, sample_card_payload):
    """Тест fallback категоризации без LLM."""
    taxonomy_agent.taxonomy_enabled = False

    result = await taxonomy_agent.categorize_trend(sample_card_payload, [])

    assert result is not None
    assert len(result["categories"]) > 0
    assert result["primary_category"] is not None


@pytest.mark.asyncio
async def test_taxonomy_agent_validates_categories(taxonomy_agent, sample_card_payload):
    """Тест валидации категорий из LLM."""
    with patch.object(taxonomy_agent, "_call_taxonomy_agent") as mock_llm:
        # LLM возвращает невалидную категорию
        mock_llm.return_value = {
            "categories": ["auto", "invalid_category", "finance"],
            "normalized_topics": ["автомобили"],
            "primary_category": "auto",
        }

        result = await taxonomy_agent.categorize_trend(sample_card_payload, [])

        # Невалидная категория должна быть отфильтрована
        assert "invalid_category" not in result["categories"]
        assert "auto" in result["categories"]
        assert "finance" in result["categories"]


def test_taxonomy_agent_fallback_categorize_auto(taxonomy_agent):
    """Тест fallback категоризации для авто."""
    card_payload = {
        "keywords": ["автомобиль", "машина"],
        "topics": ["авто"],
    }

    result = taxonomy_agent._fallback_categorize(card_payload)

    assert "auto" in result["categories"]


def test_taxonomy_agent_fallback_categorize_finance(taxonomy_agent):
    """Тест fallback категоризации для финансов."""
    card_payload = {
        "keywords": ["финансы", "деньги"],
        "topics": ["экономика"],
    }

    result = taxonomy_agent._fallback_categorize(card_payload)

    assert "finance" in result["categories"]


def test_taxonomy_agent_fallback_categorize_ai(taxonomy_agent):
    """Тест fallback категоризации для AI."""
    card_payload = {
        "keywords": ["ai", "нейросеть"],
        "topics": ["искусственный интеллект"],
    }

    result = taxonomy_agent._fallback_categorize(card_payload)

    assert "ai" in result["categories"]


def test_taxonomy_agent_fallback_categorize_other(taxonomy_agent):
    """Тест fallback категоризации для other."""
    card_payload = {
        "keywords": ["разное"],
        "topics": [],
    }

    result = taxonomy_agent._fallback_categorize(card_payload)

    assert "other" in result["categories"]


def test_taxonomy_agent_normalize_topic(taxonomy_agent):
    """Тест нормализации темы."""
    # Удаление лишних пробелов
    result = taxonomy_agent.normalize_topic("  рынок   авто  ")
    assert result == "рынок авто"

    # Обычная тема
    result = taxonomy_agent.normalize_topic("технологии")
    assert result == "технологии"

