"""
Taxonomy Agent — каталогизатор трендов по рубрикам.

Context7: Маппинг трендов на фиксированные рубрики и нормализация topics.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx
import structlog

from config import settings

logger = structlog.get_logger()

# Фиксированные рубрики
TAXONOMY_CATEGORIES = [
    "auto",
    "finance",
    "ai",
    "tech",
    "macro",
    "design",
    "marketing",
    "crypto",
    "politics",
    "sports",
    "other",
]

# ============================================================================
# TAXONOMY AGENT
# ============================================================================


class TaxonomyAgent:
    """
    Агент для категоризации и нормализации трендов.
    
    Context7: Маппинг трендов на фиксированные рубрики, нормализация topics.
    """

    def __init__(self):
        self.taxonomy_enabled = os.getenv("TREND_TAXONOMY_ENABLED", "true").lower() == "true"
        self.taxonomy_llm_model = os.getenv("TREND_TAXONOMY_LLM_MODEL", "GigaChat")

    async def categorize_trend(
        self, card_payload: Dict[str, Any], sample_posts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Категоризация тренда по рубрикам и нормализация topics.
        
        Context7: Относит тренд к одной или нескольким рубрикам из фиксированного списка.
        """
        if not self.taxonomy_enabled:
            return {
                "categories": [],
                "normalized_topics": card_payload.get("topics", []),
                "primary_category": None,
            }

        # Вызов LLM для категоризации
        result = await self._call_taxonomy_agent(card_payload, sample_posts)
        if not result:
            # Fallback: простая категоризация по keywords
            return self._fallback_categorize(card_payload)

        return {
            "categories": result.get("categories", [])[:5],
            "normalized_topics": result.get("normalized_topics", card_payload.get("topics", []))[:10],
            "primary_category": result.get("primary_category"),
        }

    async def _call_taxonomy_agent(
        self, card_payload: Dict[str, Any], sample_posts: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Вызов LLM для категоризации."""
        api_base = (
            getattr(settings, "openai_api_base", None)
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("GIGACHAT_PROXY_URL")
            or "http://gpt2giga-proxy:8090"
        )
        api_base = api_base.rstrip("/")
        if not api_base.endswith("/v1"):
            api_base = f"{api_base}/v1"

        credentials = os.getenv("GIGACHAT_CREDENTIALS")
        scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        api_key = getattr(settings, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
        headers = {"Content-Type": "application/json"}
        if credentials:
            headers["Authorization"] = f"Bearer giga-cred-{credentials}:{scope}"
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        endpoint = (
            api_base if api_base.endswith("/chat/completions") else f"{api_base}/chat/completions"
        )

        categories_str = ", ".join(f'"{cat}"' for cat in TAXONOMY_CATEGORIES)
        system_message = (
            f"Отнеси тренд к одной или нескольким рубрикам из списка:\n"
            f"[{categories_str}]\n\n"
            "Также нормализуй topics: приведи к единому формату "
            "(например, 'рынок авто' → 'автомобили').\n\n"
            "Верни JSON:\n"
            '{\n'
            '  "categories": ["auto", "finance"],\n'
            '  "normalized_topics": ["автомобили", "рынок"],\n'
            '  "primary_category": "auto"\n'
            '}'
        )

        prompt_payload = {
            "title": card_payload.get("title"),
            "summary": card_payload.get("summary"),
            "topics": card_payload.get("topics", []),
            "keywords": card_payload.get("keywords", []),
            "sample_posts": [
                {
                    "source": post.get("channel_title") or "Источник",
                    "snippet": post.get("content_snippet"),
                }
                for post in sample_posts[:3]
                if post.get("content_snippet")
            ],
        }

        user_message = (
            "Данные тренда:\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}\n\n"
            "Ответь строго JSON объектом."
        )

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": self.taxonomy_llm_model,
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 300,
                    },
                )
            if response.status_code != 200:
                logger.debug("taxonomy_agent_llm_error", status=response.status_code)
                return None
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content.strip().strip("```json").strip("```"))
            # Валидация categories
            categories = parsed.get("categories", [])
            valid_categories = [c for c in categories if c in TAXONOMY_CATEGORIES]
            parsed["categories"] = valid_categories
            if valid_categories:
                parsed["primary_category"] = valid_categories[0]
            return parsed
        except Exception as exc:
            logger.debug("taxonomy_agent_llm_failure", error=str(exc))
            return None

    def _fallback_categorize(self, card_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Простая категоризация без LLM на основе keywords."""
        keywords = [k.lower() for k in card_payload.get("keywords", [])]
        topics = [t.lower() for t in card_payload.get("topics", [])]
        all_terms = keywords + topics

        categories = []
        # Простая эвристика
        if any(term in ["авто", "машина", "автомобиль"] for term in all_terms):
            categories.append("auto")
        if any(term in ["финанс", "деньг", "валют", "рубл"] for term in all_terms):
            categories.append("finance")
        if any(term in ["ai", "ии", "нейросет", "машин"] for term in all_terms):
            categories.append("ai")
        if any(term in ["технолог", "it", "программ"] for term in all_terms):
            categories.append("tech")
        if any(term in ["крипт", "биткоин", "блокчейн"] for term in all_terms):
            categories.append("crypto")

        if not categories:
            categories = ["other"]

        return {
            "categories": categories[:3],
            "normalized_topics": card_payload.get("topics", []),
            "primary_category": categories[0] if categories else None,
        }

    def normalize_topic(self, topic: str) -> str:
        """
        Нормализация названия темы.
        
        Context7: Приведение к единому формату (например, "рынок авто" → "автомобили").
        """
        # Простая нормализация (можно расширить через LLM)
        normalized = topic.strip()
        # Удаление лишних пробелов
        normalized = " ".join(normalized.split())
        return normalized


# ============================================================================
# FACTORY
# ============================================================================


def create_taxonomy_agent() -> TaxonomyAgent:
    """Factory for integration."""
    return TaxonomyAgent()

