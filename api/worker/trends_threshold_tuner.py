"""
Threshold Tuner Agent — offline-анализ эффективности порогов трендов.

Context7: Анализ истории трендов и их показателей для предложения оптимальных порогов.
Работает как периодическая задача, сохраняет предложения в trend_threshold_suggestions.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg
import httpx
import structlog

from config import settings

logger = structlog.get_logger()

# Пороги для анализа
THRESHOLDS_TO_ANALYZE = [
    "TREND_FREQ_RATIO_THRESHOLD",
    "TREND_MIN_SOURCE_DIVERSITY",
    "TREND_COHERENCE_THRESHOLD",
    "TREND_EMERGING_COOLDOWN_SEC",
]

# ============================================================================
# THRESHOLD TUNER AGENT
# ============================================================================


class ThresholdTunerAgent:
    """
    Агент для анализа и оптимизации порогов трендов.
    
    Context7: Offline-анализ эффективности порогов, предложения для ручного review.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.db_pool: Optional[asyncpg.Pool] = None
        self.tuner_enabled = os.getenv("TREND_THRESHOLD_TUNER_ENABLED", "true").lower() == "true"
        self.tuner_llm_model = os.getenv("TREND_THRESHOLD_TUNER_LLM_MODEL", "GigaChat")

    async def initialize(self):
        """Инициализация пула подключений к БД."""
        dsn = self._normalize_database_url(self.database_url)
        self.db_pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=5,
            command_timeout=60,
        )
        logger.info("ThresholdTunerAgent DB pool ready", dsn=dsn)

    async def close(self):
        """Закрытие пула подключений."""
        if self.db_pool:
            await self.db_pool.close()

    async def analyze_all_thresholds(self, period_days: int = 30) -> List[Dict[str, Any]]:
        """
        Анализ всех порогов за указанный период.
        
        Context7: Для каждого порога анализирует эффективность и предлагает оптимальное значение.
        """
        if not self.tuner_enabled:
            logger.debug("Threshold tuner disabled, skipping analysis")
            return []

        suggestions = []
        for threshold_name in THRESHOLDS_TO_ANALYZE:
            try:
                suggestion = await self.analyze_threshold(threshold_name, period_days)
                if suggestion:
                    suggestions.append(suggestion)
            except Exception as exc:
                logger.warning(
                    "threshold_analysis_failed",
                    threshold_name=threshold_name,
                    error=str(exc),
                )
        return suggestions

    async def analyze_threshold(
        self, threshold_name: str, period_days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Анализ эффективности конкретного порога.
        
        Context7: Собирает метрики за период и вызывает LLM для анализа.
        """
        if not self.db_pool:
            return None

        # Получаем текущее значение порога
        current_value = self._get_current_threshold_value(threshold_name)
        if current_value is None:
            logger.debug("threshold_not_found", threshold_name=threshold_name)
            return None

        # Собираем метрики за период
        metrics = await self._collect_threshold_metrics(threshold_name, period_days)
        if not metrics:
            logger.debug("no_metrics_for_threshold", threshold_name=threshold_name)
            return None

        # Вызов LLM для анализа
        analysis = await self._call_tuner_agent(threshold_name, current_value, metrics)
        if not analysis:
            return None

        # Сохранение предложения в БД
        suggestion_id = await self._save_suggestion(
            threshold_name=threshold_name,
            current_value=current_value,
            suggested_value=analysis.get("suggested_value", current_value),
            reasoning=analysis.get("reasoning"),
            confidence=analysis.get("confidence"),
            period_days=period_days,
        )

        return {
            "id": suggestion_id,
            "threshold_name": threshold_name,
            "current_value": current_value,
            "suggested_value": analysis.get("suggested_value"),
            "reasoning": analysis.get("reasoning"),
            "confidence": analysis.get("confidence"),
        }

    def _get_current_threshold_value(self, threshold_name: str) -> Optional[float]:
        """Получение текущего значения порога из env."""
        env_key = threshold_name
        value_str = os.getenv(env_key)
        if not value_str:
            # Fallback на дефолтные значения
            defaults = {
                "TREND_FREQ_RATIO_THRESHOLD": "3.0",
                "TREND_MIN_SOURCE_DIVERSITY": "3",
                "TREND_COHERENCE_THRESHOLD": "0.55",
                "TREND_EMERGING_COOLDOWN_SEC": "900",
            }
            value_str = defaults.get(threshold_name)
        if not value_str:
            return None
        try:
            return float(value_str)
        except ValueError:
            return None

    async def _collect_threshold_metrics(
        self, threshold_name: str, period_days: int
    ) -> Dict[str, Any]:
        """Сбор метрик для анализа порога."""
        if not self.db_pool:
            return {}

        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

        # Анализируем тренды за период
        query = """
            SELECT
                COUNT(*) as total_clusters,
                COUNT(CASE WHEN status = 'emerging' THEN 1 END) as emerging_count,
                COUNT(CASE WHEN status = 'stable' THEN 1 END) as stable_count,
                AVG(burst_score) as avg_burst_score,
                AVG(source_diversity) as avg_source_diversity,
                AVG(coherence_score) as avg_coherence_score,
                AVG(quality_score) as avg_quality_score,
                COUNT(CASE WHEN quality_score < 0.6 THEN 1 END) as low_quality_count
            FROM trend_clusters
            WHERE last_activity_at >= $1
        """
        async with self.db_pool.acquire() as conn:
            record = await conn.fetchrow(query, cutoff)

        if not record:
            return {}

        return {
            "total_clusters": record.get("total_clusters", 0),
            "emerging_count": record.get("emerging_count", 0),
            "stable_count": record.get("stable_count", 0),
            "avg_burst_score": float(record.get("avg_burst_score") or 0),
            "avg_source_diversity": float(record.get("avg_source_diversity") or 0),
            "avg_coherence_score": float(record.get("avg_coherence_score") or 0),
            "avg_quality_score": float(record.get("avg_quality_score") or 0),
            "low_quality_count": record.get("low_quality_count", 0),
            "period_days": period_days,
        }

    async def _call_tuner_agent(
        self, threshold_name: str, current_value: float, metrics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Вызов LLM для анализа порога."""
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

        system_message = (
            "Проанализируй эффективность порога {threshold_name} = {current_value}:\n"
            "- Какие тренды были пропущены из-за высокого порога?\n"
            "- Какие мусорные тренды прошли из-за низкого порога?\n"
            "- Какое значение оптимально?\n\n"
            "Верни JSON:\n"
            '{\n'
            '  "suggested_value": ...,\n'
            '  "reasoning": "...",\n'
            '  "confidence": 0.0-1.0,\n'
            '  "analysis_period": {...}\n'
            '}'
        ).format(threshold_name=threshold_name, current_value=current_value)

        user_message = (
            "Метрики за период:\n"
            f"{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
            "Ответь строго JSON объектом."
        )

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": self.tuner_llm_model,
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 500,
                    },
                )
            if response.status_code != 200:
                logger.debug("threshold_tuner_llm_error", status=response.status_code)
                return None
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content.strip().strip("```json").strip("```"))
            return parsed
        except Exception as exc:
            logger.debug("threshold_tuner_llm_failure", error=str(exc))
            return None

    async def _save_suggestion(
        self,
        threshold_name: str,
        current_value: float,
        suggested_value: float,
        reasoning: Optional[str],
        confidence: Optional[float],
        period_days: int,
    ) -> str:
        """Сохранение предложения в БД."""
        if not self.db_pool:
            return ""

        period_start = datetime.now(timezone.utc) - timedelta(days=period_days)
        period_end = datetime.now(timezone.utc)

        query = """
            INSERT INTO trend_threshold_suggestions (
                id,
                threshold_name,
                current_value,
                suggested_value,
                reasoning,
                confidence,
                analysis_period_start,
                analysis_period_end,
                status
            )
            VALUES (
                gen_random_uuid(),
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7,
                'pending'
            )
            RETURNING id::text;
        """
        async with self.db_pool.acquire() as conn:
            suggestion_id = await conn.fetchval(
                query,
                threshold_name,
                current_value,
                suggested_value,
                reasoning,
                confidence,
                period_start,
                period_end,
            )
        return suggestion_id or ""

    def _normalize_database_url(self, url: str) -> str:
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "")
        return url


# ============================================================================
# FACTORY
# ============================================================================


async def create_threshold_tuner_agent() -> ThresholdTunerAgent:
    """Factory for scheduler integration."""
    database_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres"
    )
    agent = ThresholdTunerAgent(database_url)
    await agent.initialize()
    return agent

