"""
Trend Coherence Agent для валидации тематической когерентности кластеров.

Context7: Проверяет, принадлежит ли пост теме кластера на основе
summary, keywords, topics кластера и контента поста.
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional

import httpx
import structlog

from config import settings

logger = structlog.get_logger()


# ============================================================================
# TREND COHERENCE AGENT
# ============================================================================


class TrendCoherenceAgent:
    """
    Агент валидации тематической когерентности:
    - проверяет, принадлежит ли пост теме кластера,
    - использует LLM для оценки тематической связности,
    - возвращает решение: accept/reject/split.
    """

    def __init__(self):
        self.coherence_enabled = os.getenv("TREND_COHERENCE_AGENT_ENABLED", "true").lower() == "true"
        self.coherence_llm_model = os.getenv("TREND_COHERENCE_AGENT_LLM_MODEL", "GigaChat")
        self.coherence_llm_max_tokens = int(os.getenv("TREND_COHERENCE_AGENT_LLM_MAX_TOKENS", "200"))
        self.coherence_min_confidence = float(os.getenv("TREND_COHERENCE_AGENT_MIN_CONFIDENCE", "0.7"))

        logger.info(
            "TrendCoherenceAgent initialized",
            coherence_enabled=self.coherence_enabled,
            llm_model=self.coherence_llm_model,
        )

    async def validate_post_cluster_match(
        self,
        post_content: str,
        post_keywords: List[str],
        post_topics: List[str],
        cluster_label: str,
        cluster_summary: str,
        cluster_keywords: List[str],
        cluster_topics: List[str],
        similarity: float,
    ) -> Dict[str, Any]:
        """
        Валидация тематической когерентности поста и кластера.
        
        Args:
            post_content: Контент поста
            post_keywords: Ключевые слова поста
            post_topics: Темы поста
            cluster_label: Название кластера
            cluster_summary: Краткое описание кластера
            cluster_keywords: Ключевые слова кластера
            cluster_topics: Темы кластера
            similarity: Семантическое сходство (cosine similarity)
        
        Returns:
            Dict с полями:
            - decision: "accept" | "reject" | "split"
            - confidence: float (0.0-1.0)
            - reasoning: str
        """
        if not self.coherence_enabled:
            return {
                "decision": "accept",
                "confidence": 1.0,
                "reasoning": "Coherence agent disabled",
            }

        try:
            llm_result = await self._call_coherence_llm(
                post_content=post_content,
                post_keywords=post_keywords,
                post_topics=post_topics,
                cluster_label=cluster_label,
                cluster_summary=cluster_summary,
                cluster_keywords=cluster_keywords,
                cluster_topics=cluster_topics,
                similarity=similarity,
            )
            
            if not llm_result:
                # При ошибке LLM принимаем решение на основе similarity
                if similarity >= 0.70:
                    return {
                        "decision": "accept",
                        "confidence": similarity,
                        "reasoning": "LLM unavailable, using similarity threshold",
                    }
                else:
                    return {
                        "decision": "reject",
                        "confidence": 1.0 - similarity,
                        "reasoning": "LLM unavailable, similarity too low",
                    }
            
            decision = llm_result.get("decision", "accept").lower()
            confidence = float(llm_result.get("confidence", 0.5))
            reasoning = llm_result.get("reasoning", "")
            
            # Нормализуем decision
            if decision not in ["accept", "reject", "split"]:
                if confidence >= self.coherence_min_confidence:
                    decision = "accept"
                else:
                    decision = "reject"
            
            return {
                "decision": decision,
                "confidence": confidence,
                "reasoning": reasoning,
            }
        except Exception as exc:
            logger.error(
                "trend_coherence_agent_validation_failed",
                error=str(exc),
                cluster_label=cluster_label,
            )
            # При ошибке принимаем решение на основе similarity
            if similarity >= 0.70:
                return {
                    "decision": "accept",
                    "confidence": similarity,
                    "reasoning": f"Error in validation: {str(exc)}",
                }
            else:
                return {
                    "decision": "reject",
                    "confidence": 1.0 - similarity,
                    "reasoning": f"Error in validation: {str(exc)}",
                }

    async def _call_coherence_llm(
        self,
        post_content: str,
        post_keywords: List[str],
        post_topics: List[str],
        cluster_label: str,
        cluster_summary: str,
        cluster_keywords: List[str],
        cluster_topics: List[str],
        similarity: float,
    ) -> Optional[Dict[str, Any]]:
        """Вызов LLM для валидации тематической когерентности."""
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
        api_key = (
            getattr(settings, "openai_api_key", None)
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        if credentials:
            auth_header = f"Bearer giga-cred-{credentials}:{scope}"
        elif api_key:
            auth_header = f"Bearer {api_key}"
        else:
            auth_header = None

        system_message = (
            "Ты — валидатор тематической когерентности для кластеризации трендов. "
            "Твоя задача — определить, принадлежит ли пост теме кластера на основе "
            "контента, ключевых слов и тем. "
            "Ответь строго JSON объектом с полями: decision (accept/reject/split), "
            "confidence (0.0-1.0), reasoning (краткое объяснение).\n\n"
            "Правила:\n"
            "- accept: пост явно относится к теме кластера\n"
            "- reject: пост не относится к теме кластера\n"
            "- split: пост частично относится, но может потребовать разделения кластера"
        )

        user_message = (
            f"Пост:\n"
            f"Контент: {post_content[:500]}\n"
            f"Ключевые слова: {', '.join(post_keywords[:10])}\n"
            f"Темы: {', '.join(post_topics[:5])}\n\n"
            f"Кластер:\n"
            f"Название: {cluster_label}\n"
            f"Описание: {cluster_summary[:300]}\n"
            f"Ключевые слова: {', '.join(cluster_keywords[:10])}\n"
            f"Темы: {', '.join(cluster_topics[:5])}\n\n"
            f"Семантическое сходство: {similarity:.2f}\n\n"
            f"Принадлежит ли пост теме кластера? Ответь JSON объектом."
        )

        try:
            headers = {"Content-Type": "application/json"}
            if auth_header:
                headers["Authorization"] = auth_header
            endpoint_base = api_base.rstrip("/")
            if endpoint_base.endswith("/chat/completions"):
                endpoint = endpoint_base
            elif endpoint_base.endswith("/v1"):
                endpoint = f"{endpoint_base}/chat/completions"
            else:
                endpoint = f"{endpoint_base}/v1/chat/completions"

            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": self.coherence_llm_model,
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.2,
                        "max_tokens": self.coherence_llm_max_tokens,
                    },
                )
            if response.status_code != 200:
                logger.debug(
                    "trend_coherence_agent_llm_error",
                    status=response.status_code,
                    body=response.text[:200],
                )
                return None
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = self._safe_parse_json_obj(content)
            return parsed
        except Exception as exc:
            logger.debug("trend_coherence_agent_llm_failure", error=str(exc))
            return None

    def _safe_parse_json_obj(self, content: str) -> Optional[Dict[str, Any]]:
        """Безопасный парсинг JSON объекта."""
        if not content:
            return None
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return None


# ============================================================================
# Factory
# ============================================================================


_coherence_agent: Optional[TrendCoherenceAgent] = None


def create_coherence_agent() -> TrendCoherenceAgent:
    """Создание singleton экземпляра TrendCoherenceAgent."""
    global _coherence_agent
    if _coherence_agent is None:
        _coherence_agent = TrendCoherenceAgent()
    return _coherence_agent

