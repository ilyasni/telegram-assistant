"""
Trend Editor Agent — агент для проверки и улучшения карточек трендов.

Context7: Мультиагентная система для улучшения качества трендов.
Подписывается на trends.emerging, проверяет качество карточек по чеклисту,
улучшает заголовки, summary и why_important через LLM.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
import httpx
import structlog
from prometheus_client import Counter, Histogram

from event_bus import EventConsumer, RedisStreamsClient, ConsumerConfig
from config import settings
from trends_taxonomy_agent import create_taxonomy_agent

logger = structlog.get_logger()

# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

trend_editor_requests_total = Counter(
    "trend_editor_requests_total",
    "Trend Editor Agent requests",
    ["outcome"],
)

trend_editor_quality_score = Histogram(
    "trend_editor_quality_score",
    "Quality score distribution from Editor Agent",
    buckets=(0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

trend_editor_latency_seconds = Histogram(
    "trend_editor_latency_seconds",
    "Latency of editing trend card",
    ["outcome"],
)

# ============================================================================
# TREND EDITOR AGENT
# ============================================================================


class TrendEditorAgent:
    """
    Агент-редактор карточек трендов:
    - подписывается на trends.emerging,
    - проверяет качество карточки по чеклисту,
    - улучшает title, summary, why_important через LLM,
    - обновляет quality_score и quality_flags в trend_clusters.
    """

    def __init__(
        self,
        redis_url: str,
        database_url: str,
    ):
        self.redis_url = redis_url
        self.database_url = database_url

        self.redis_client: Optional[RedisStreamsClient] = None
        self.event_consumer: Optional[EventConsumer] = None
        self.db_pool: Optional[asyncpg.Pool] = None
        self.taxonomy_agent = create_taxonomy_agent()

        self.editor_enabled = os.getenv("TREND_EDITOR_ENABLED", "true").lower() == "true"
        self.editor_min_score = float(os.getenv("TREND_EDITOR_MIN_SCORE", "0.6"))
        self.editor_llm_model = os.getenv("TREND_EDITOR_LLM_MODEL", "GigaChat")
        self.editor_llm_max_tokens = int(os.getenv("TREND_EDITOR_LLM_MAX_TOKENS", "500"))
        self.editor_cooldown_sec = int(os.getenv("TREND_EDITOR_COOLDOWN_SEC", "300"))  # 5 минут

        logger.info(
            "TrendEditorAgent initialized",
            redis_url=self.redis_url,
            editor_enabled=self.editor_enabled,
            editor_min_score=self.editor_min_score,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        """Initialize dependencies and begin consumption loop."""
        if not self.editor_enabled:
            logger.info("TrendEditorAgent disabled, skipping start")
            return

        start_ts = time.time()
        await self._initialize()
        trend_editor_requests_total.labels(outcome="ready").inc()
        logger.info("TrendEditorAgent initialization completed", took=time.time() - start_ts)
        await self.event_consumer.consume_forever("trends.emerging", self._handle_message)

    async def stop(self):
        """Graceful shutdown."""
        if self.event_consumer:
            self.event_consumer.running = False
        if self.redis_client:
            await self.redis_client.disconnect()
        if self.db_pool:
            await self.db_pool.close()
        logger.info("TrendEditorAgent stopped")

    async def _initialize(self):
        """Prepare Redis, DB and event bus clients."""
        self.redis_client = RedisStreamsClient(self.redis_url)
        await self.redis_client.connect()

        consumer_config = ConsumerConfig(
            group_name=os.getenv("TREND_EDITOR_CONSUMER_GROUP", "trend_editors"),
            consumer_name=os.getenv(
                "TREND_EDITOR_CONSUMER_NAME", f"trend_editor_{uuid.uuid4().hex[:6]}"
            ),
            batch_size=int(os.getenv("TREND_EDITOR_BATCH_SIZE", "32")),
            block_time=int(os.getenv("TREND_EDITOR_BLOCK_MS", "2000")),
            retry_delay=5,
            idle_timeout=120,
        )
        self.event_consumer = EventConsumer(self.redis_client, consumer_config)

        dsn = self._normalize_database_url(self.database_url)
        self.db_pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=int(os.getenv("TREND_EDITOR_DB_POOL_MAX", "10")),
            command_timeout=30,
        )
        logger.info("TrendEditorAgent DB pool ready", dsn=dsn)

    # ------------------------------------------------------------------ #
    # Event processing
    # ------------------------------------------------------------------ #

    async def _handle_message(self, message: Dict[str, Any]):
        """Process single Redis message from trends.emerging."""
        process_start = time.time()
        payload = self._extract_payload(message)
        cluster_id = payload.get("cluster_id")
        if not cluster_id:
            trend_editor_requests_total.labels(outcome="invalid").inc()
            logger.warning(
                "trend_editor_invalid_event",
                error="cluster_id missing",
                payload_keys=list(payload.keys()),
            )
            return

        try:
            cluster_data = await self._fetch_cluster(cluster_id)
            if not cluster_data:
                trend_editor_requests_total.labels(outcome="missing_cluster").inc()
                return

            # Проверяем cooldown
            if await self._is_cluster_in_cooldown(cluster_id):
                trend_editor_requests_total.labels(outcome="cooldown").inc()
                return

            # Обработка card_payload для использования в _edit_card и taxonomy_agent
            raw_card_payload = cluster_data.get("card_payload")
            if isinstance(raw_card_payload, str):
                try:
                    card_payload_processed = json.loads(raw_card_payload)
                except (json.JSONDecodeError, TypeError):
                    card_payload_processed = {}
            elif isinstance(raw_card_payload, dict):
                card_payload_processed = raw_card_payload
            else:
                card_payload_processed = {}
            
            # Обновляем cluster_data с обработанным card_payload
            cluster_data["card_payload"] = card_payload_processed

            # Проверяем качество и улучшаем карточку
            result = await self._edit_card(cluster_data)
            if not result:
                trend_editor_requests_total.labels(outcome="no_improvement").inc()
                trend_editor_latency_seconds.labels(outcome="skipped").observe(
                    time.time() - process_start
                )
                return

            # Категоризация через Taxonomy Agent
            taxonomy_result = await self.taxonomy_agent.categorize_trend(
                card_payload_processed,
                card_payload_processed.get("example_posts", []),
            )
            if taxonomy_result:
                result["taxonomy_categories"] = taxonomy_result.get("categories", [])
                result["normalized_topics"] = taxonomy_result.get("normalized_topics")

            # Обновляем cluster в БД
            await self._update_cluster(cluster_id, result)

            trend_editor_requests_total.labels(outcome="success").inc()
            trend_editor_quality_score.observe(result.get("quality_score", 0.0))
            trend_editor_latency_seconds.labels(outcome="success").observe(
                time.time() - process_start
            )

        except Exception as exc:
            trend_editor_requests_total.labels(outcome="error").inc()
            trend_editor_latency_seconds.labels(outcome="error").observe(
                time.time() - process_start
            )
            logger.error(
                "trend_editor_processing_error",
                error=str(exc),
                cluster_id=cluster_id,
                exc_info=True,
            )
            raise

    # ------------------------------------------------------------------ #
    # Card editing
    # ------------------------------------------------------------------ #

    async def _edit_card(self, cluster_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Проверка качества и улучшение карточки через LLM."""
        # Обработка card_payload: может быть dict, str (JSON) или None
        raw_card_payload = cluster_data.get("card_payload")
        if isinstance(raw_card_payload, str):
            try:
                card_payload = json.loads(raw_card_payload)
            except (json.JSONDecodeError, TypeError):
                card_payload = {}
        elif isinstance(raw_card_payload, dict):
            card_payload = raw_card_payload
        else:
            card_payload = {}
        
        sample_posts = card_payload.get("example_posts", [])

        # Подготовка промпта
        checklist = {
            "title_not_single_word": "title не должен быть одиночным словом или стоп-словом",
            "summary_informative": "summary должен быть информативным (минимум 50 символов)",
            "why_important_present": "why_important должен объяснять значимость",
            "topics_relevant": "topics должны быть релевантными (не generic)",
        }

        prompt_payload = {
            "current_title": card_payload.get("title") or cluster_data.get("label") or cluster_data.get("primary_topic"),
            "current_summary": card_payload.get("summary") or cluster_data.get("summary"),
            "current_why_important": card_payload.get("why_important") or cluster_data.get("why_important"),
            "current_topics": card_payload.get("topics") or cluster_data.get("topics") or [],
            "keywords": card_payload.get("keywords") or cluster_data.get("keywords") or [],
            "sample_posts": [
                {
                    "source": (post.get("channel_title") if isinstance(post, dict) else str(post.get("channel_title", ""))) or "Источник",
                    "snippet": post.get("content_snippet") if isinstance(post, dict) else str(post.get("content_snippet", "")),
                    "posted_at": post.get("posted_at") if isinstance(post, dict) else post.get("posted_at"),
                }
                for post in sample_posts[:5]
                if isinstance(post, dict) and post.get("content_snippet")
            ],
            "checklist": checklist,
            "stats": card_payload.get("stats", {}),
        }

        # Вызов LLM
        llm_result = await self._call_editor_llm(prompt_payload)
        if not llm_result:
            return None

        quality_score = float(llm_result.get("quality_score", 0.0))
        if quality_score < self.editor_min_score:
            logger.debug(
                "trend_editor_low_quality",
                cluster_id=cluster_data.get("id"),
                quality_score=quality_score,
                min_score=self.editor_min_score,
            )
            # Всё равно сохраняем результат, но с низким quality_score
            return llm_result

        return llm_result

    async def _call_editor_llm(self, prompt_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Вызов LLM для редактирования карточки."""
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
            getattr(settings, "openai_api_key", None) or os.getenv("OPENAI_API_KEY") or ""
        )
        if credentials:
            auth_header = f"Bearer giga-cred-{credentials}:{scope}"
        elif api_key:
            auth_header = f"Bearer {api_key}"
        else:
            auth_header = None

        system_message = (
            "Ты — редактор карточек трендов. Проверь карточку по чеклисту:\n"
            "1. title не должен быть одиночным словом или стоп-словом\n"
            "2. summary должен быть информативным (минимум 50 символов)\n"
            "3. why_important должен объяснять значимость\n"
            "4. topics должны быть релевантными (не generic)\n\n"
            "Верни JSON:\n"
            '{\n'
            '  "quality_score": 0.0-1.0,\n'
            '  "quality_flags": ["generic_title", "missing_summary", ...],\n'
            '  "improved_title": "...",\n'
            '  "improved_summary": "...",\n'
            '  "improved_why_important": "...",\n'
            '  "editor_notes": "..."\n'
            '}'
        )
        user_message = (
            "Данные карточки тренда:\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}\n\n"
            "Ответь строго JSON объектом."
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

            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": self.editor_llm_model,
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.2,
                        "max_tokens": self.editor_llm_max_tokens,
                    },
                )
            if response.status_code != 200:
                logger.debug(
                    "trend_editor_llm_response_error",
                    status=response.status_code,
                    body=response.text[:200],
                )
                return None
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = self._safe_parse_json_obj(content)
            return parsed
        except Exception as exc:
            logger.debug("trend_editor_llm_failure", error=str(exc))
            return None

    # ------------------------------------------------------------------ #
    # Database operations
    # ------------------------------------------------------------------ #

    async def _fetch_cluster(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """Загрузка кластера из БД."""
        if not self.db_pool:
            return None
        try:
            cluster_uuid = uuid.UUID(str(cluster_id))
        except (ValueError, TypeError):
            logger.warning("trend_editor_invalid_cluster_id", cluster_id=cluster_id)
            return None

        query = """
            SELECT
                id,
                cluster_key,
                label,
                summary,
                keywords,
                primary_topic,
                topics,
                why_important,
                card_payload,
                quality_score,
                quality_flags
            FROM trend_clusters
            WHERE id = $1
            LIMIT 1;
        """
        async with self.db_pool.acquire() as conn:
            record = await conn.fetchrow(query, cluster_uuid)
        if not record:
            logger.debug("trend_editor_cluster_not_found", cluster_id=cluster_id)
            return None

        # Обработка card_payload: asyncpg может вернуть JSONB как dict или str
        raw_card_payload = record.get("card_payload")
        if isinstance(raw_card_payload, str):
            try:
                card_payload = json.loads(raw_card_payload)
            except (json.JSONDecodeError, TypeError):
                card_payload = {}
        elif isinstance(raw_card_payload, dict):
            card_payload = raw_card_payload
        else:
            card_payload = {}
        
        # Обработка quality_flags: может быть list, str (JSON) или None
        raw_quality_flags = record.get("quality_flags")
        if isinstance(raw_quality_flags, str):
            try:
                quality_flags = json.loads(raw_quality_flags)
            except (json.JSONDecodeError, TypeError):
                quality_flags = []
        elif isinstance(raw_quality_flags, list):
            quality_flags = raw_quality_flags
        else:
            quality_flags = []
        
        return {
            "id": str(record.get("id")),
            "cluster_key": record.get("cluster_key"),
            "label": record.get("label"),
            "summary": record.get("summary"),
            "keywords": record.get("keywords") or [],
            "primary_topic": record.get("primary_topic"),
            "topics": record.get("topics") or [],
            "why_important": record.get("why_important"),
            "card_payload": card_payload,
            "quality_score": record.get("quality_score"),
            "quality_flags": quality_flags,
        }

    async def _update_cluster(
        self, cluster_id: str, editor_result: Dict[str, Any]
    ) -> None:
        """Обновление кластера с результатами редактирования."""
        if not self.db_pool:
            return
        try:
            cluster_uuid = uuid.UUID(str(cluster_id))
        except (ValueError, TypeError):
            return

        quality_score = editor_result.get("quality_score")
        quality_flags = editor_result.get("quality_flags", [])
        improved_title = editor_result.get("improved_title")
        improved_summary = editor_result.get("improved_summary")
        improved_why_important = editor_result.get("improved_why_important")
        editor_notes = editor_result.get("editor_notes")
        taxonomy_categories = editor_result.get("taxonomy_categories")
        normalized_topics = editor_result.get("normalized_topics")

        # Обновляем card_payload
        taxonomy_categories_json = json.dumps(taxonomy_categories) if taxonomy_categories else None
        normalized_topics_json = json.dumps(normalized_topics) if normalized_topics else None
        
        query = """
            UPDATE trend_clusters
            SET
                quality_score = COALESCE($2, quality_score),
                quality_flags = CASE
                    WHEN $3::jsonb IS NOT NULL THEN $3::jsonb
                    ELSE quality_flags
                END,
                editor_notes = COALESCE($4, editor_notes),
                last_edited_at = NOW(),
                taxonomy_categories = CASE
                    WHEN $8::jsonb IS NOT NULL THEN $8::jsonb
                    ELSE taxonomy_categories
                END,
                topics = CASE
                    WHEN $9::jsonb IS NOT NULL THEN $9::jsonb
                    ELSE topics
                END,
                label = CASE
                    WHEN $5 IS NOT NULL AND $5 != '' THEN $5
                    ELSE label
                END,
                summary = CASE
                    WHEN $6 IS NOT NULL AND $6 != '' THEN $6
                    ELSE summary
                END,
                why_important = CASE
                    WHEN $7 IS NOT NULL AND $7 != '' THEN $7
                    ELSE why_important
                END,
                card_payload = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                COALESCE(card_payload, '{}'::jsonb),
                                '{title}',
                                to_jsonb(COALESCE($5, card_payload->>'title', label, 'Тренд'))
                            ),
                            '{summary}',
                            to_jsonb(COALESCE($6, card_payload->>'summary', summary, ''))
                        ),
                        '{why_important}',
                        to_jsonb(COALESCE($7, card_payload->>'why_important', why_important, ''))
                    ),
                    '{topics}',
                    to_jsonb(COALESCE($9::jsonb, card_payload->'topics', '[]'::jsonb))
                )
            WHERE id = $1;
        """
        quality_flags_json = json.dumps(quality_flags) if quality_flags else None
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                query,
                cluster_uuid,
                quality_score,
                quality_flags_json,
                editor_notes,
                improved_title,
                improved_summary,
                improved_why_important,
                taxonomy_categories_json,
                normalized_topics_json,
            )

        # Устанавливаем cooldown
        await self._set_cluster_cooldown(cluster_id)

    # ------------------------------------------------------------------ #
    # Redis helpers
    # ------------------------------------------------------------------ #

    async def _is_cluster_in_cooldown(self, cluster_id: str) -> bool:
        """Проверка cooldown для кластера."""
        if not self.redis_client:
            return False
        redis = self.redis_client.client
        key = f"trend:editor:{cluster_id}:cooldown"
        exists = await redis.exists(key)
        return exists > 0

    async def _set_cluster_cooldown(self, cluster_id: str) -> None:
        """Установка cooldown для кластера."""
        if not self.redis_client:
            return
        redis = self.redis_client.client
        key = f"trend:editor:{cluster_id}:cooldown"
        await redis.setex(key, self.editor_cooldown_sec, "1")

    # ------------------------------------------------------------------ #
    # Utility helpers
    # ------------------------------------------------------------------ #

    def _extract_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Redis message shape to dict."""
        if "payload" in message:
            payload = message["payload"]
        elif "data" in message:
            raw = message["data"]
            if isinstance(raw, (bytes, bytearray)):
                payload = json.loads(raw)
            elif isinstance(raw, str):
                payload = json.loads(raw)
            else:
                payload = raw
        else:
            payload = message
        if not isinstance(payload, dict):
            raise ValueError("Unsupported payload format")
        return payload

    def _normalize_database_url(self, url: str) -> str:
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "")
        return url

    def _safe_parse_json_obj(self, content: str) -> Optional[Dict[str, Any]]:
        """Безопасный парсинг JSON из ответа LLM."""
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
# FACTORY
# ============================================================================


async def create_trend_editor_agent() -> TrendEditorAgent:
    """Factory for run_all_tasks integration."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres"
    )
    agent = TrendEditorAgent(redis_url, database_url)
    return agent

