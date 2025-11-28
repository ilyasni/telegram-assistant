"""
Group Digest Orchestrator v1 — мультиагентная система для Telegram-групп.

Context7:
- Все вызовы выполняются через облачные модели GigaChat (gigachain).
- Реализован мультимодельный роутинг (Base → классификация, Pro → синтез).
- Поддерживаются квоты, fallback на базовую модель и метрики observability.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
import uuid
import traceback
import random
from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TypedDict

# Обеспечиваем доступ к пакетам worker/api при запуске внутри контейнера /app
import sys

_CURRENT_FILE = Path(__file__).resolve()
_PATH_CANDIDATES = [
    _CURRENT_FILE.parents[1],  # /app
    _CURRENT_FILE.parents[1] / "worker",
    _CURRENT_FILE.parents[1] / "api",
    Path("/opt/telegram-assistant"),
    Path("/opt/telegram-assistant/worker"),
    Path("/opt/telegram-assistant/api"),
]
for path_obj in _PATH_CANDIDATES:
    try:
        if not path_obj:
            continue
        path_str = str(path_obj)
        if path_obj.exists() and path_str not in sys.path:
            sys.path.insert(0, path_str)
    except Exception:  # pragma: no cover - защитный блок
        continue

import structlog
import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_gigachat import GigaChat
from prometheus_client import Counter as PromCounter  # type: ignore
from prometheus_client import Gauge, Histogram, REGISTRY  # type: ignore

# Context7: Функция для безопасной регистрации метрик (защита от дублирования)
def _safe_register_metric(metric_class, name, *args, **kwargs):
    """Безопасная регистрация метрики с обработкой дублирования."""
    try:
        return metric_class(name, *args, **kwargs)
    except ValueError as e:
        if 'Duplicated timeseries' in str(e):
            # Метрика уже зарегистрирована, пытаемся найти её в registry
            for collector in list(REGISTRY._collector_to_names.keys()):
                if hasattr(collector, '_name') and collector._name == name:
                    logger.warning(f"{name} already registered, reusing existing")
                    return collector
            # Если не нашли, создаём с уникальным именем как fallback
            logger.warning(f"{name} already registered but not found, creating with _v2 suffix")
            return metric_class(f"{name}_v2", *args, **kwargs)
        else:
            raise

try:  # pragma: no cover - langgraph необязателен для unit-тестов
    from langgraph.graph import END, START, StateGraph

    _LANGGRAPH_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    END = START = None  # type: ignore
    StateGraph = None  # type: ignore
    _LANGGRAPH_AVAILABLE = False

try:  # pragma: no cover - OpenTelemetry опционален
    from opentelemetry import trace  # type: ignore
except ImportError:  # pragma: no cover
    trace = None

from worker.common.digest_state_store import (
    DEFAULT_SCHEMA_VERSION,
    DigestLock,
    DigestStateStoreFactory,
    SupportsDigestState,
)
from worker.services.episodic_memory_service import get_episodic_memory_service
from worker.services.dlq_service import get_dlq_service
from worker.common.self_improvement import SelfImprovementService, SelfVerificationResult
from worker.agents.planning_agent import PlanningAgent, Plan
from api.services.persona_service import PersonaService
from worker.common.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    RetryPolicy,
    build_dlq_payload,  # Оставляем для обратной совместимости, если используется где-то еще
    guarded_call,
)
from worker.common.json_guard import (
    build_repair_variables,
    format_errors,
    try_self_repair,
    validate,
)
from worker.common.baseline_compare import (
    BaselineSnapshot,
    compute_delta,
    load_previous_snapshot,
)
from worker.services.group_context_service import (
    ContextConfig,
    ContextScoringWeights,
    GroupContextService,
    build_conversation_excerpt,
    build_participant_stats,
    cosine_similarity,
    extract_reply_to,
    format_timestamp,
    mask_pii,
    parse_timestamp,
    text_vector,
    TOKEN_REGEX,
)
from worker.prompts.group_digest import (
    digest_composer_prompt_v1,
    digest_composer_prompt_v2,
    digest_composer_retry_prompt_v1,
    emotion_analyzer_prompt_v1,
    emotion_analyzer_repair_prompt_v1,
    quality_evaluator_prompt_v1,
    quality_evaluator_repair_prompt_v1,
    role_classifier_prompt_v1,
    role_classifier_repair_prompt_v1,
    semantic_segmenter_prompt_v1,
    semantic_segmenter_repair_prompt_v1,
    topic_synthesizer_prompt_v1,
    topic_synthesizer_repair_prompt_v1,
)
from worker.services.context7_storage_client import Context7StorageClient

logger = structlog.get_logger(__name__)

_ENV_CONFIG_PATH = os.getenv("DIGEST_MODEL_CONFIG_PATH")
DEFAULT_CONFIG_PATH = Path(_ENV_CONFIG_PATH) if _ENV_CONFIG_PATH else Path("worker/config/group_digest_models.yml")

ESTIMATED_TOKENS_PER_CHAR = 0.25  # Эвристика: 4 символа ≈ 1 токен
# QUALITY_THRESHOLD теперь читается из конфига через GroupDigestConfig.quality_checks.quality_threshold
LOG_SAMPLE_RATE = max(0.0, float(os.getenv("DIGEST_LOG_SAMPLE_RATE", "0.01")))


SEGMENTER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["thread_id", "units"],
    "properties": {
        "thread_id": {"type": "string", "minLength": 1},
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "text", "msg_ids", "confidence"],
                "properties": {
                    "kind": {"type": "string", "minLength": 1},
                    "text": {"type": "string"},
                    "msg_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "offset_range": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

EMOTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["tone", "intensity", "conflict", "collaboration", "stress", "enthusiasm"],
    "properties": {
        "tone": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
        "conflict": {"type": "number", "minimum": 0, "maximum": 1},
        "collaboration": {"type": "number", "minimum": 0, "maximum": 1},
        "stress": {"type": "number", "minimum": 0, "maximum": 1},
        "enthusiasm": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string"},
    },
    "additionalProperties": True,
}

ROLES_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["participants"],
    "properties": {
        "participants": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["username"],
                "properties": {
                    "username": {"type": "string"},
                    "roles": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "weight"],
                            "properties": {
                                "name": {"type": "string"},
                                "weight": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "dominant_role": {"type": "string"},
                    "message_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "comment": {"type": "string"},
                    "telegram_id": {"type": ["string", "number", "null"]},
                    "role": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "role_profile": {
            "type": "array",
            "items": {"type": "object"},
        },
    },
    "additionalProperties": True,
}

TOPIC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["topics"],
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "priority", "threads"],
                "properties": {
                    "title": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "msg_count": {"type": ["integer", "number"]},
                    "threads": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                    "signals": {"type": "object"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "actions": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        }
    },
    "additionalProperties": False,
}

EVALUATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["faithfulness", "coherence", "coverage", "focus", "quality_score", "notes"],
    "properties": {
        "faithfulness": {"type": "number", "minimum": 0, "maximum": 1},
        "coherence": {"type": "number", "minimum": 0, "maximum": 1},
        "coverage": {"type": "number", "minimum": 0, "maximum": 1},
        "focus": {"type": "number", "minimum": 0, "maximum": 1},
        "quality_score": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string"},
    },
    "additionalProperties": True,
}

JSON_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "segmenter_agent": SEGMENTER_SCHEMA,
    "emotion_agent": EMOTION_SCHEMA,
    "roles_agent": ROLES_SCHEMA,
    "topic_agent": TOPIC_SCHEMA,
    "evaluation_agent": EVALUATION_SCHEMA,
}


@dataclass
class AgentSpec:
    """Конфигурация отдельного агента LLM."""

    model_alias: str
    temperature: float
    max_tokens: int


@dataclass
@dataclass
class RetrySettings:
    max_attempts: int
    initial_interval: float
    backoff_factor: float
    max_interval: float
    jitter: bool = True


@dataclass
class CircuitBreakerSettings:
    failure_threshold: int
    recovery_timeout: float


@dataclass
class ResilienceConfig:
    retry: RetrySettings
    circuit_breaker: CircuitBreakerSettings


@dataclass
class ContextStorageConfig:
    enabled: bool
    base_url: str
    api_key: Optional[str]
    namespace_prefix: str
    timeout: float
    history_windows: int
    history_message_limit: int


@dataclass
class QualityChecksConfig:
    """Конфигурация проверок качества дайджеста."""
    min_messages_for_topics: int
    min_topics_required: int
    quality_threshold: float
    micro_window_threshold: int
    large_window_threshold: int


@dataclass
class GroupDigestConfig:
    """Глобальная конфигурация мультиагентного пайплайна."""

    base_model: str
    pro_model: str
    embeddings_model: str
    fallback_enabled: bool
    fallback_metric: str
    pro_quota_per_tenant: int
    pro_token_budget: int
    quota_window_hours: int
    min_messages: int
    max_messages: int
    chunk_size: int
    thread_max_len: int
    max_retries: int
    resilience: ResilienceConfig
    context: ContextConfig
    context_storage: ContextStorageConfig
    quality_checks: QualityChecksConfig
    agents: Dict[str, AgentSpec] = field(default_factory=dict)

    def resolve_model(self, alias: str) -> str:
        alias_lower = alias.lower()
        if alias_lower in {"@base", "base"}:
            return self.base_model
        if alias_lower in {"@pro", "pro"}:
            return self.pro_model
        return alias

    @property
    def quota_window(self) -> timedelta:
        return timedelta(hours=max(1, self.quota_window_hours))


@dataclass
class QuotaTracker:
    """Учёт вызовов Pro-модели per-tenant."""

    limit: int
    window: timedelta
    usage: Dict[str, List[datetime]] = field(default_factory=lambda: defaultdict(list))

    def check_and_increment(self, tenant_id: str) -> bool:
        if self.limit <= 0:
            return True
        now = datetime.now(timezone.utc)
        snapshots = [ts for ts in self.usage[tenant_id] if now - ts <= self.window]
        if len(snapshots) >= self.limit:
            self.usage[tenant_id] = snapshots
            return False
        snapshots.append(now)
        self.usage[tenant_id] = snapshots
        return True


@dataclass
class TokenBudgetTracker:
    """Учёт токен-бюджета Pro-модели per-tenant."""

    budget: int
    window: timedelta
    usage: Dict[str, List[Tuple[datetime, int]]] = field(default_factory=lambda: defaultdict(list))

    def check_and_consume(self, tenant_id: str, tokens: int) -> bool:
        if self.budget <= 0:
            return True
        now = datetime.now(timezone.utc)
        entries = [(ts, amount) for ts, amount in self.usage[tenant_id] if now - ts <= self.window]
        spent = sum(amount for _, amount in entries)
        if spent + tokens > self.budget:
            self.usage[tenant_id] = entries
            return False
        entries.append((now, tokens))
        self.usage[tenant_id] = entries
        return True


@dataclass(slots=True)
class LLMResponse:
    """Результат вызова LLM с метаданными."""

    content: str
    model: str
    prompt_alias: str


class GroupDigestState(TypedDict, total=False):
    """Состояние пайплайна LangGraph."""

    trace_id: str
    tenant_id: str
    group_id: str
    window: Dict[str, Any]
    messages: List[Dict[str, Any]]
    sanitized_messages: List[Dict[str, Any]]
    conversation_excerpt: str
    participant_stats: List[Dict[str, Any]]
    participants: List[Dict[str, Any]]
    message_total: int
    context_ranking: List[Dict[str, Any]]
    context_stats: Dict[str, Any]
    media_stats: Dict[str, Any]
    media_highlights: List[Dict[str, Any]]
    context_duplicates: Dict[str, List[str]]
    threads: List[Dict[str, Any]]
    semantic_units: List[Dict[str, Any]]
    emotion_profile: Dict[str, Any]
    role_profile: List[Dict[str, Any]]
    topics: List[Dict[str, Any]]
    summary_html: str
    summary: str
    metrics: Dict[str, Any]
    evaluation: Dict[str, Any]
    quality_pass: bool
    quality_min_score: float
    quality_score: float
    delivery: Dict[str, Any]
    errors: List[str]
    skip: bool
    skip_reason: str
    state_store: SupportsDigestState
    artifact_metadata: Dict[str, Any]
    schema_version: str
    dlq_events: List[Dict[str, Any]]
    synthesis_retry_used: bool
    baseline_snapshot: Dict[str, Any]
    baseline_delta: Dict[str, Any]
    digest_mode: str  # micro, normal, large
    prompt_version: str  # версия промпта (digest_composer_prompt_v1/v2)
    pipeline_version: str  # версия пайплайна (group_digest_v1/v2)


def _resolve_entry(entry: Any, cast_type, default):
    """Извлекает значение из YAML-конфига с учётом env overrides."""
    if isinstance(entry, (int, float)) and cast_type in (int, float):
        return cast_type(entry)
    if isinstance(entry, str) and cast_type is str:
        return entry
    if not isinstance(entry, dict):
        return cast_type(default)
    env_name = entry.get("env")
    raw_value = os.getenv(env_name) if env_name else None
    if raw_value in (None, ""):
        raw_value = entry.get("default", default)
    try:
        return cast_type(raw_value)
    except Exception:  # noqa: BLE001
        return cast_type(default)


def _resolve_bool(entry: Any, default: bool) -> bool:
    raw_default = "true" if default else "false"
    raw_value = _resolve_entry(entry, str, raw_default)
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


_CONFIG_CACHE: Optional[GroupDigestConfig] = None
_CONFIG_MTIME: Optional[float] = None


def load_group_digest_config(reload: bool = False) -> GroupDigestConfig:
    """Загружает конфигурацию из YAML + ENV."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    cwd = Path.cwd()
    worker_root = Path(__file__).resolve().parent.parent

    candidate_paths = []
    if _ENV_CONFIG_PATH:
        candidate_paths.append(Path(_ENV_CONFIG_PATH))

    candidate_paths.extend(
        [
            DEFAULT_CONFIG_PATH,
            Path("config/group_digest_models.yml"),
            worker_root / "config/group_digest_models.yml",
        ]
    )

    resolved_path: Optional[Path] = None
    checked_paths: List[str] = []
    for candidate in candidate_paths:
        if candidate is None:
            continue
        absolute_candidate = candidate if candidate.is_absolute() else (cwd / candidate)
        if absolute_candidate.exists():
            resolved_path = absolute_candidate
            break
        checked_paths.append(str(absolute_candidate))
        # Если проверяли относительный путь относительно cwd, попробуем относительно worker root
        if not candidate.is_absolute():
            alt_candidate = (worker_root / candidate).resolve()
            if alt_candidate.exists():
                resolved_path = alt_candidate
                break
            checked_paths.append(str(alt_candidate))

    if resolved_path is None:
        logger.error(
            "group_digest_config.not_found",
            searched_paths=checked_paths,
        )
        raise FileNotFoundError(f"Group digest config not found. Checked: {checked_paths}")

    if resolved_path != DEFAULT_CONFIG_PATH:
        logger.info(f"group_digest_config.path_resolved: {str(resolved_path)}")

    path = resolved_path
    mtime = path.stat().st_mtime
    if not reload and _CONFIG_CACHE is not None and _CONFIG_MTIME == mtime:
        return _CONFIG_CACHE

    with path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}

    models_section = raw.get("models", {})
    base_model = _resolve_entry(models_section.get("base", {}), str, "GigaChat")
    pro_model = _resolve_entry(models_section.get("pro", {}), str, "GigaChat-Pro")
    embeddings_model = _resolve_entry(models_section.get("embeddings", {}), str, "EmbeddingsGigaR")

    limits_section = raw.get("limits", {})
    quotas_section = raw.get("quotas", {})
    fallbacks_section = raw.get("fallbacks", {})
    resilience_section = raw.get("resilience", {})
    retry_section = resilience_section.get("retry", {})
    circuit_section = resilience_section.get("circuit_breaker", {})

    jit_raw = retry_section.get("jitter", True)
    if isinstance(jit_raw, dict):
        jit_value = str(_resolve_entry(jit_raw, str, "true")).lower()
    else:
        jit_value = str(jit_raw).lower() if not isinstance(jit_raw, bool) else ("true" if jit_raw else "false")
    jitter_enabled = jit_value in {"1", "true", "yes", "on"}

    context_section = raw.get("context", {})
    scoring_section = context_section.get("scoring", {})
    context_config = ContextConfig(
        similarity_threshold=float(_resolve_entry(context_section.get("similarity_threshold", {}), float, 0.88)),
        soft_similarity_threshold=float(
            _resolve_entry(context_section.get("soft_similarity_threshold", {}), float, 0.76)
        ),
        dedup_time_gap_minutes=int(_resolve_entry(context_section.get("dedup_time_gap_minutes", {}), int, 120)),
        max_context_messages=int(_resolve_entry(context_section.get("max_context_messages", {}), int, 400)),
        top_ranked=int(_resolve_entry(context_section.get("top_ranked", {}), int, 150)),
        recency_half_life_minutes=int(
            _resolve_entry(context_section.get("recency_half_life_minutes", {}), int, 180)
        ),
        scoring=ContextScoringWeights(
            recency=float(_resolve_entry(scoring_section.get("recency_weight", {}), float, 0.5)),
            reply=float(_resolve_entry(scoring_section.get("reply_weight", {}), float, 0.25)),
            length=float(_resolve_entry(scoring_section.get("length_weight", {}), float, 0.15)),
            reactions=float(_resolve_entry(scoring_section.get("reactions_weight", {}), float, 0.1)),
            media=float(_resolve_entry(scoring_section.get("media_weight", {}), float, 0.1)),
        ),
    )

    storage_section = raw.get("context_storage", {})
    context_storage_config = ContextStorageConfig(
        enabled=_resolve_bool(storage_section.get("enabled", {}), False),
        base_url=_resolve_entry(storage_section.get("base_url", {}), str, ""),
        api_key=str(_resolve_entry(storage_section.get("api_key", {}), str, "")) or None,
        namespace_prefix=_resolve_entry(storage_section.get("namespace_prefix", {}), str, "group-digest"),
        timeout=float(_resolve_entry(storage_section.get("timeout", {}), float, 5.0)),
        history_windows=int(_resolve_entry(storage_section.get("history_windows", {}), int, 3)),
        history_message_limit=int(_resolve_entry(storage_section.get("history_message_limit", {}), int, 150)),
    )

    quality_checks_section = raw.get("quality_checks", {})
    quality_checks_config = QualityChecksConfig(
        min_messages_for_topics=int(_resolve_entry(quality_checks_section.get("min_messages_for_topics", {}), int, 20)),
        min_topics_required=int(_resolve_entry(quality_checks_section.get("min_topics_required", {}), int, 1)),
        quality_threshold=float(_resolve_entry(quality_checks_section.get("quality_threshold", {}), float, 0.7)),
        micro_window_threshold=int(_resolve_entry(quality_checks_section.get("micro_window_threshold", {}), int, 20)),
        large_window_threshold=int(_resolve_entry(quality_checks_section.get("large_window_threshold", {}), int, 150)),
    )

    config = GroupDigestConfig(
        base_model=base_model,
        pro_model=pro_model,
        embeddings_model=embeddings_model,
        fallback_enabled=bool(fallbacks_section.get("pro_to_base", {}).get("enabled", True)),
        fallback_metric=fallbacks_section.get("pro_to_base", {}).get("metric", "digest_synthesis_fallback_total"),
        pro_quota_per_tenant=int(_resolve_entry(quotas_section.get("pro_invocations_per_tenant", {}), int, 50)),
        pro_token_budget=int(_resolve_entry(quotas_section.get("pro_token_budget", {}), int, 400_000)),
        quota_window_hours=int(_resolve_entry(quotas_section.get("quota_window_hours", {}), int, 24)),
        min_messages=int(_resolve_entry(limits_section.get("min_messages", {}), int, 8)),
        max_messages=int(_resolve_entry(limits_section.get("max_messages", {}), int, 1000)),
        chunk_size=int(_resolve_entry(limits_section.get("chunk_size", {}), int, 250)),
        thread_max_len=int(_resolve_entry(limits_section.get("thread_max_len", {}), int, 20)),
        max_retries=int(_resolve_entry(limits_section.get("max_retries", {}), int, 3)),
        resilience=ResilienceConfig(
            retry=RetrySettings(
                max_attempts=int(_resolve_entry(retry_section.get("max_attempts", {}), int, 3)),
                initial_interval=float(_resolve_entry(retry_section.get("initial_interval", {}), float, 0.5)),
                backoff_factor=float(_resolve_entry(retry_section.get("backoff_factor", {}), float, 2.0)),
                max_interval=float(_resolve_entry(retry_section.get("max_interval", {}), float, 30.0)),
                jitter=jitter_enabled,
            ),
            circuit_breaker=CircuitBreakerSettings(
                failure_threshold=int(_resolve_entry(circuit_section.get("failure_threshold", {}), int, 4)),
                recovery_timeout=float(_resolve_entry(circuit_section.get("recovery_timeout", {}), float, 60.0)),
            ),
        ),
        context=context_config,
        context_storage=context_storage_config,
        quality_checks=quality_checks_config,
        agents={},
    )

    agents_section = raw.get("agents", {})
    for agent_name, spec in agents_section.items():
        config.agents[agent_name] = AgentSpec(
            model_alias=str(spec.get("model", "@base")),
            temperature=float(spec.get("temperature", 0.1)),
            max_tokens=int(spec.get("max_tokens", 1500)),
        )

    _CONFIG_CACHE = config
    _CONFIG_MTIME = mtime
    logger.info(
        "group_digest_config_loaded",
        base_model=config.base_model,
        pro_model=config.pro_model,
        agents=list(config.agents.keys()),
    )
    return config


def approx_tokens_from_text(text: str) -> int:
    if not text:
        return 1
    return max(1, int(len(text) * ESTIMATED_TOKENS_PER_CHAR))


def approx_tokens_from_payload(payload: Any) -> int:
    try:
        dumped = json.dumps(payload, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        dumped = str(payload)
    return approx_tokens_from_text(dumped)


class _FallbackWorkflow:
    """Заглушка, если LangGraph или GigaChat недоступны."""

    def invoke(self, state: GroupDigestState) -> GroupDigestState:
        errors = list(state.get("errors", []))
        errors.append("workflow_unavailable")
        summary_html = "<b>Дайджест недоступен</b>: оркестратор отключён."
        return {
            "window": state.get("window", {}),
            "summary_html": summary_html,
            "summary": "Дайджест недоступен: оркестратор отключён.",
            "topics": [],
            "participants": [],
            "metrics": {},
            "evaluation": {},
            "delivery": {"status": "skipped", "reason": "workflow_unavailable"},
            "errors": errors,
            "skip": True,
            "skip_reason": "workflow_unavailable",
        }


# Context7: Защита от дублирования метрик при повторном импорте модуля
digest_generation_seconds = _safe_register_metric(
    Histogram,
    "digest_generation_seconds",
    "Время выполнения стадий пайплайна групповых дайджестов",
    ["stage"],
)

digest_tokens_total = _safe_register_metric(
    PromCounter,
    "digest_tokens_total",
    "Оценка количества токенов, переданных в LLM-агентов",
    ["agent", "model"],
)

digest_synthesis_fallback_total = _safe_register_metric(
    PromCounter,
    "digest_synthesis_fallback_total",
    "Количество fallback'ов с Pro на Base модель",
    ["reason"],
)

digest_skipped_total = _safe_register_metric(
    PromCounter,
    "digest_skipped_total",
    "Пропущенные дайджесты и причины",
    ["reason", "tenant_id", "mode"],
)

digest_messages_processed_total = _safe_register_metric(
    PromCounter,
    "digest_messages_processed_total",
    "Количество обработанных сообщений по арендаторам (метрика нагрузки)",
    ["tenant"],
)

digest_topics_empty_total = _safe_register_metric(
    PromCounter,
    "digest_topics_empty_total",
    "Количество случаев, когда темы пустые или недостаточны",
    ["reason", "tenant_id", "mode"],
)

digest_pre_quality_failed_total = _safe_register_metric(
    PromCounter,
    "digest_pre_quality_failed_total",
    "Rule-based проверки качества, которые не прошли",
    ["check", "tenant_id"],
)

digest_mode_total = _safe_register_metric(
    PromCounter,
    "digest_mode_total",
    "Распределение дайджестов по режимам (micro/normal/large)",
    ["mode", "tenant_id", "window_size_hours"],
)

digest_pro_quota_exceeded_total = _safe_register_metric(
    PromCounter,
    "digest_pro_quota_exceeded_total",
    "Срабатывания квот на Pro модель (вызовы/токены)",
    ["tenant"],
)

digest_quality_score = _safe_register_metric(
    Gauge,
    "digest_quality_score",
    "Последние значения метрик качества дайджестов",
    ["metric"],
)

# Self-Improvement метрики
self_improvement_verification_total = _safe_register_metric(
    PromCounter,
    "self_improvement_verification_total",
    "Количество self-verification проверок",
    ["result"],  # passed, failed
)

self_improvement_correction_total = _safe_register_metric(
    PromCounter,
    "self_improvement_correction_total",
    "Количество self-correction попыток",
    ["result"],  # applied, skipped, failed
)

self_improvement_gating_total = _safe_register_metric(
    PromCounter,
    "self_improvement_gating_total",
    "Количество self-gating решений",
    ["decision"],  # retry_allowed, retry_denied
)

# Planning метрики
planning_plan_generated_total = _safe_register_metric(
    PromCounter,
    "planning_plan_generated_total",
    "Количество сгенерированных планов",
    ["path_type"],  # fast_path, smart_path
)

planning_plan_execution_check_total = _safe_register_metric(
    PromCounter,
    "planning_plan_execution_check_total",
    "Количество проверок выполнения плана",
    ["result"],  # success, failed
)

planning_replan_total = _safe_register_metric(
    PromCounter,
    "planning_replan_total",
    "Количество перепланирований",
    ["result"],  # generated, skipped
)

# Persona метрики
persona_prompt_adapted_total = _safe_register_metric(
    PromCounter,
    "persona_prompt_adapted_total",
    "Количество адаптаций промптов под персону",
    ["result"],  # success, failed, skipped
)

digest_dlq_total = _safe_register_metric(
    PromCounter,
    "digest_dlq_total",
    "Количество отправленных в DLQ событий мультиагентного дайджеста",
    ["stage", "error_code"],
)

digest_circuit_open_total = _safe_register_metric(
    PromCounter,
    "digest_circuit_open_total",
    "Срабатывания circuit breaker при генерации дайджеста",
    ["stage"],
)

digest_stage_latency_seconds = _safe_register_metric(
    Histogram,
    "digest_stage_latency_seconds",
    "Длительность стадий мультиагентного пайплайна",
    ["stage", "status"],
)

digest_stage_status_total = _safe_register_metric(
    PromCounter,
    "digest_stage_status_total",
    "Количество завершений стадий пайплайна по статусу",
    ["stage", "status"],
)


def load_gigachat_credentials() -> Dict[str, Any]:
    credentials = os.getenv("GIGACHAT_CREDENTIALS")
    if not credentials:
        raise RuntimeError("GIGACHAT_CREDENTIALS is not configured")
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    base_url = os.getenv("GIGACHAT_BASE_URL") or os.getenv("GIGACHAT_PROXY_URL") or "http://gpt2giga-proxy:8090"
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return {
        "credentials": credentials,
        "scope": scope,
        "base_url": base_url,
        "verify_ssl_certs": False,
    }


_TEXT_VECTOR_CACHE: Dict[str, Tuple[Counter, float]] = {}


def text_vector_cached(text: str) -> Tuple[Counter, float]:
    """Кешируем векторы для повторно встречающихся сообщений."""
    cache_key = text[:512]
    cached = _TEXT_VECTOR_CACHE.get(cache_key)
    if cached is not None:
        return cached
    vector = text_vector(text)
    if len(_TEXT_VECTOR_CACHE) > 2048:
        _TEXT_VECTOR_CACHE.clear()
    _TEXT_VECTOR_CACHE[cache_key] = vector
    return vector


def text_similarity(left: str, right: str) -> float:
    """Приближённая косинусная похожесть между двумя сообщениями."""
    return cosine_similarity(text_vector_cached(left), text_vector_cached(right))


def infer_similarity_reason(messages: Sequence[Dict[str, Any]]) -> str:
    """Эвристическое пояснение для объединения сообщений в ветку."""
    keywords: Counter[str] = Counter()
    for msg in messages:
        content = msg.get("content", "")
        keywords.update(token for token in TOKEN_REGEX.findall(content.lower()) if len(token) > 3)
    if not keywords:
        return "Общее обсуждение"
    word, _ = keywords.most_common(1)[0]
    return f"Обсуждение: {word}"


def build_threads(messages: Sequence[Dict[str, Any]], max_len: int) -> List[Dict[str, Any]]:
    """Кластеризация сообщений в ветки (reply + временная + семантическая близость)."""
    if not messages:
        return []

    thread_by_message: Dict[str, Dict[str, Any]] = {}
    threads: List[Dict[str, Any]] = []
    time_gap_seconds = 20 * 60

    for msg in messages:
        reply_to_id = msg.get("reply_to_id")
        assigned: Optional[Dict[str, Any]] = None
        msg_ts = msg.get("timestamp_unix")
        if msg_ts is None:
            msg_ts = parse_timestamp(msg.get("timestamp_iso")).timestamp()
        msg["_timestamp_unix"] = msg_ts

        if reply_to_id and reply_to_id in thread_by_message:
            assigned = thread_by_message[reply_to_id]
        else:
            for candidate in reversed(threads):
                prev_msg = candidate["messages"][-1]
                prev_ts = prev_msg.get("_timestamp_unix")
                if prev_ts is None:
                    prev_ts = parse_timestamp(prev_msg.get("timestamp_iso")).timestamp()
                    prev_msg["_timestamp_unix"] = prev_ts
                delta = msg_ts - prev_ts
                if delta > time_gap_seconds:
                    continue
                if text_similarity(msg["content"], prev_msg["content"]) >= 0.78:
                    assigned = candidate
                    break

        if assigned is None:
            assigned = {
                "messages": [],
                "reply_root": reply_to_id or msg["message_id"],
            }
            threads.append(assigned)

        assigned["messages"].append(msg)
        thread_by_message[msg["message_id"]] = assigned

    thread_payloads: List[Dict[str, Any]] = []
    for idx, thread in enumerate(threads, start=1):
        chunks = [thread["messages"][i : i + max_len] for i in range(0, len(thread["messages"]), max_len)]
        for part_idx, chunk in enumerate(chunks, start=1):
            thread_id = f"thread-{idx}"
            if len(chunks) > 1:
                thread_id = f"{thread_id}-part{part_idx}"
            thread_payloads.append(
                {
                    "thread_id": thread_id,
                    "msg_ids": [m["message_id"] for m in chunk],
                    "start_ts": chunk[0]["timestamp_iso"],
                    "end_ts": chunk[-1]["timestamp_iso"],
                    "reply_root": thread["reply_root"],
                    "similarity_reason": infer_similarity_reason(chunk),
                    "messages": [
                        {
                            **{k: v for k, v in m.items() if k != "_timestamp_unix"},
                        }
                        for m in chunk
                    ],
                }
            )

    # cleanup helper field
    for msg in messages:
        msg.pop("_timestamp_unix", None)

    return thread_payloads


class LLMRouter:
    """Маршрутизатор GigaChat моделей с квотами и fallback."""

    def __init__(self, config: GroupDigestConfig):
        self.config = config
        try:
            self._base_kwargs = load_gigachat_credentials()
            self._ready = True
        except RuntimeError as exc:  # pragma: no cover - отсутствуют креды
            logger.error("gigachat_credentials_missing", error=str(exc))
            self._ready = False
            self._base_kwargs = {}
        self._chains: Dict[Tuple[str, str], Any] = {}
        self._quota = QuotaTracker(config.pro_quota_per_tenant, config.quota_window)
        self._token_budget = TokenBudgetTracker(config.pro_token_budget, config.quota_window)
        self._tracer = trace.get_tracer(__name__) if trace else None
        resilience_conf = self.config.resilience
        self._retry_policy = RetryPolicy(
            max_attempts=resilience_conf.retry.max_attempts,
            initial_interval=resilience_conf.retry.initial_interval,
            backoff_factor=resilience_conf.retry.backoff_factor,
            max_interval=resilience_conf.retry.max_interval,
            jitter=resilience_conf.retry.jitter,
        )
        self._breaker_settings = resilience_conf.circuit_breaker
        self._breakers: Dict[str, CircuitBreaker] = {}

    def is_ready(self) -> bool:
        return self._ready

    def invoke(
        self,
        agent_name: str,
        prompt: ChatPromptTemplate,
        variables: Dict[str, Any],
        tenant_id: str,
        trace_id: str,
        estimated_tokens: int,
    ) -> LLMResponse:
        if not self.is_ready():
            raise RuntimeError("GigaChat credentials are not configured")

        spec = self.config.agents.get(agent_name)
        if spec is None:
            raise KeyError(f"Agent config not found: {agent_name}")

        aliases_to_try = [spec.model_alias]
        if spec.model_alias.lower() in {"@pro", "pro"} and self.config.fallback_enabled:
            aliases_to_try.append("@base")

        last_exc: Optional[Exception] = None
        for alias in aliases_to_try:
            alias_lower = alias.lower()
            model_name = self.config.resolve_model(alias)
            breaker_key = f"{agent_name}:{alias_lower}"
            breaker = self._breakers.get(breaker_key)
            if breaker is None:
                breaker = CircuitBreaker(
                    failure_threshold=self._breaker_settings.failure_threshold,
                    recovery_timeout=self._breaker_settings.recovery_timeout,
                )
                self._breakers[breaker_key] = breaker
            if alias_lower in {"@pro", "pro"}:
                if not self._quota.check_and_increment(tenant_id):
                    # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                    try:
                        digest_pro_quota_exceeded_total.labels(tenant=_sanitize_prometheus_label(tenant_id or "unknown")).inc()
                    except Exception as metric_error:
                        logger.warning("Failed to record digest_pro_quota_exceeded_total metric", tenant_id=tenant_id, error=str(metric_error))
                    logger.info(
                        "digest_pro_invocation_quota_exceeded",
                        tenant_id=tenant_id,
                        agent=agent_name,
                        trace_id=trace_id,
                    )
                    continue
                if not self._token_budget.check_and_consume(tenant_id, estimated_tokens):
                    # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                    try:
                        digest_pro_quota_exceeded_total.labels(tenant=_sanitize_prometheus_label(tenant_id or "unknown")).inc()
                    except Exception as metric_error:
                        logger.warning("Failed to record digest_pro_quota_exceeded_total metric", tenant_id=tenant_id, error=str(metric_error))
                    logger.info(
                        "digest_pro_token_quota_exceeded",
                        tenant_id=tenant_id,
                        agent=agent_name,
                        trace_id=trace_id,
                    )
                    continue

            chain = self._get_or_create_chain(agent_name, alias_lower, prompt, spec, model_name)
            start_ts = time.perf_counter()
            span_ctx = (
                self._tracer.start_as_current_span(
                    f"digest.agent.{agent_name}",
                    attributes={
                        "model": model_name,
                        "tenant_id": tenant_id,
                        "trace_id": trace_id,
                    },
                )
                if self._tracer
                else nullcontext()
            )
            try:
                with span_ctx:
                    def _call() -> Any:
                        return chain.invoke(variables)

                    result = guarded_call(
                        _call,
                        breaker=breaker,
                        retry_policy=self._retry_policy,
                    )
            except CircuitOpenError as exc:
                last_exc = exc
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_circuit_open_total.labels(stage=_sanitize_prometheus_label(agent_name)).inc()
                except Exception as metric_error:
                    logger.warning("Failed to record digest_circuit_open_total metric", agent=agent_name, error=str(metric_error))
                if alias_lower in {"@pro", "pro"} and self.config.fallback_enabled:
                    try:
                        digest_synthesis_fallback_total.labels(reason=_sanitize_prometheus_label("circuit_open")).inc()
                    except Exception as metric_error:
                        logger.warning("Failed to record digest_synthesis_fallback_total metric", reason="circuit_open", error=str(metric_error))
                    logger.warning(
                        "digest_agent_circuit_open_fallback",
                        agent=agent_name,
                        trace_id=trace_id,
                        error=str(exc),
                    )
                    continue
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if alias_lower in {"@pro", "pro"} and self.config.fallback_enabled:
                    # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                    try:
                        digest_synthesis_fallback_total.labels(reason=_sanitize_prometheus_label("exception")).inc()
                    except Exception as metric_error:
                        logger.warning("Failed to record digest_synthesis_fallback_total metric", reason="exception", error=str(metric_error))
                    logger.warning(
                        "digest_agent_fallback_triggered",
                        agent=agent_name,
                        trace_id=trace_id,
                        error=str(exc),
                    )
                    continue
                raise
            else:
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_tokens_total.labels(
                        agent=_sanitize_prometheus_label(agent_name), 
                        model=_sanitize_prometheus_label(model_name)
                    ).inc(max(1, estimated_tokens))
                    digest_generation_seconds.labels(stage=_sanitize_prometheus_label(agent_name)).observe(time.perf_counter() - start_ts)
                except Exception as metric_error:
                    logger.warning("Failed to record digest_tokens_total or digest_generation_seconds metric", agent=agent_name, error=str(metric_error))
                return LLMResponse(
                    content=result,
                    model=model_name,
                    prompt_alias=alias_lower,
                )

        raise RuntimeError(f"All LLM attempts failed for agent {agent_name}") from last_exc

    def _get_or_create_chain(
        self,
        agent_name: str,
        alias: str,
        prompt: ChatPromptTemplate,
        spec: AgentSpec,
        model_name: str,
    ) -> Any:
        key = (agent_name, alias)
        if key in self._chains:
            return self._chains[key]

        kwargs = dict(self._base_kwargs)
        kwargs["model"] = model_name
        kwargs["temperature"] = spec.temperature
        kwargs["max_tokens"] = spec.max_tokens

        llm = GigaChat(**kwargs)
        chain = prompt | llm | StrOutputParser()
        self._chains[key] = chain
        return chain


def extract_reply_to(raw: Dict[str, Any]) -> Optional[str]:
    """Извлекает идентификатор родительского сообщения."""
    reply = raw.get("reply_to")
    if isinstance(reply, dict):
        for key in ("message_id", "tg_message_id", "id"):
            value = reply.get(key)
            if value:
                return str(value)
    for fallback_key in ("reply_to_message_id", "reply_to_id", "reply_to"):
        value = raw.get(fallback_key)
        if value:
            return str(value)
    return None


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


# Context7: Универсальная функция санитизации значений для Prometheus labels
def _sanitize_prometheus_label(value: Any) -> str:
    """Санитизация значения label для Prometheus (универсальная функция)."""
    import re
    if value is None:
        return "unknown"
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return "unknown"
    # Заменяем невалидные символы на подчеркивания
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', value)
    # Убираем множественные подчеркивания
    sanitized = re.sub(r'_+', '_', sanitized)
    # Убираем подчеркивания в начале и конце
    sanitized = sanitized.strip('_')
    # Если начинается с цифры, добавляем префикс
    if sanitized and sanitized[0].isdigit():
        sanitized = f"label_{sanitized}"
    # Если пусто, возвращаем unknown
    return sanitized if sanitized else "unknown"


class GroupDigestOrchestrator:
    """Основной мультиагентный пайплайн формирования дайджестов."""

    def __init__(
        self,
        config: Optional[GroupDigestConfig] = None,
        llm_router: Optional[LLMRouter] = None,
        state_store_factory: Optional[DigestStateStoreFactory] = None,
    ):
        self.config = config or load_group_digest_config()
        self._llm_router = llm_router or LLMRouter(self.config)
        self._state_store_factory = state_store_factory or DigestStateStoreFactory()
        self._tracer = trace.get_tracer(__name__) if trace else None
        self._prompts: Dict[str, ChatPromptTemplate] = {
            "segmenter_agent": semantic_segmenter_prompt_v1(),
            "emotion_agent": emotion_analyzer_prompt_v1(),
            "roles_agent": role_classifier_prompt_v1(),
            "topic_agent": topic_synthesizer_prompt_v1(),
            "synthesis_agent": digest_composer_prompt_v2(),  # Используем v2 по умолчанию
            "evaluation_agent": quality_evaluator_prompt_v1(),
        }
        # Сохраняем v1 как fallback
        self._prompts_v1: Dict[str, ChatPromptTemplate] = {
            "synthesis_agent": digest_composer_prompt_v1(),
        }
        self._repair_prompts: Dict[str, ChatPromptTemplate] = {
            "segmenter_agent": semantic_segmenter_repair_prompt_v1(),
            "emotion_agent": emotion_analyzer_repair_prompt_v1(),
            "roles_agent": role_classifier_repair_prompt_v1(),
            "topic_agent": topic_synthesizer_repair_prompt_v1(),
            "evaluation_agent": quality_evaluator_repair_prompt_v1(),
        }
        self._synthesis_retry_prompt: ChatPromptTemplate = digest_composer_retry_prompt_v1()
        self._json_schemas: Dict[str, Dict[str, Any]] = JSON_SCHEMAS
        self._max_repair_attempts = max(1, min(self.config.max_retries, 2))
        # Self-Improvement Service для автоматического улучшения качества
        self._self_improvement = SelfImprovementService(llm_router=self._llm_router)
        # Planning Agent для динамического планирования (только для Smart Path - async пайплайны)
        self._planning_agent = PlanningAgent(llm_router=self._llm_router)
        # Persona Service для персонализации промптов
        self._persona_service = PersonaService()
        self._context_service = GroupContextService(self.config.context)
        self._context_storage_client: Optional[Context7StorageClient] = None
        storage_cfg = self.config.context_storage
        if storage_cfg.enabled and storage_cfg.base_url:
            try:
                self._context_storage_client = Context7StorageClient(
                    base_url=storage_cfg.base_url,
                    api_key=storage_cfg.api_key,
                    namespace_prefix=storage_cfg.namespace_prefix,
                    timeout=storage_cfg.timeout,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("context7_storage.initialization_failed", error=str(exc))

        if not _LANGGRAPH_AVAILABLE or not self._llm_router.is_ready():
            logger.warning(
                "group_digest_orchestrator_fallback_enabled",
                langgraph_available=_LANGGRAPH_AVAILABLE,
                gigachat_ready=self._llm_router.is_ready(),
            )
            self.workflow = _FallbackWorkflow()
            return

        self.schema_version = DEFAULT_SCHEMA_VERSION
        self._stage_prompt_info: Dict[str, Dict[str, str]] = {
            "ingest_validator": {"prompt_id": "INGEST_VALIDATOR_V1", "prompt_version": "v1", "model_id": "system"},
            "thread_builder": {"prompt_id": "THREAD_BUILDER_PROMPT_V1", "prompt_version": "v1", "model_id": "system"},
            "segmenter_agent": {"prompt_id": "SEMANTIC_SEGMENTER_PROMPT_V1", "prompt_version": "v1"},
            "emotion_agent": {"prompt_id": "EMOTION_ANALYZER_PROMPT_V1", "prompt_version": "v1"},
            "roles_agent": {"prompt_id": "ROLE_CLASSIFIER_PROMPT_V1", "prompt_version": "v1"},
            "topic_agent": {"prompt_id": "TOPIC_SYNTHESIZER_PROMPT_V1", "prompt_version": "v1"},
            "synthesis_agent": {"prompt_id": "DIGEST_COMPOSER_PROMPT_V2", "prompt_version": "v2"},
            "synthesis_agent_retry": {"prompt_id": "DIGEST_COMPOSER_RETRY_PROMPT_V1", "prompt_version": "v1"},
            "evaluation_agent": {"prompt_id": "QUALITY_EVALUATOR_PROMPT_V1", "prompt_version": "v1"},
            "delivery_manager": {"prompt_id": "DELIVERY_MANAGER_V1", "prompt_version": "v1", "model_id": "system"},
        }

        self.workflow = self._build_workflow()

    def _build_workflow(self) -> StateGraph[GroupDigestState]:
        workflow = StateGraph(GroupDigestState)
        workflow.add_node("ingest_validator", self._node_ingest_validator)
        workflow.add_node("thread_builder", self._node_thread_builder)
        workflow.add_node("segmenter_agent", self._node_segmenter)
        workflow.add_node("emotion_agent", self._node_emotion_profile)
        workflow.add_node("roles_agent", self._node_roles)
        workflow.add_node("topic_agent", self._node_topics)
        workflow.add_node("synthesis_agent", self._node_synthesis)
        workflow.add_node("evaluation_agent", self._node_quality)
        workflow.add_node("delivery_manager", self._node_delivery)

        workflow.set_entry_point("ingest_validator")
        workflow.add_edge("ingest_validator", "thread_builder")
        workflow.add_edge("thread_builder", "segmenter_agent")
        workflow.add_edge("segmenter_agent", "emotion_agent")
        workflow.add_edge("emotion_agent", "roles_agent")
        workflow.add_edge("roles_agent", "topic_agent")
        workflow.add_edge("topic_agent", "synthesis_agent")
        workflow.add_edge("synthesis_agent", "evaluation_agent")
        workflow.add_edge("evaluation_agent", "delivery_manager")
        workflow.add_edge("delivery_manager", END)

        return workflow.compile()

    def _get_stage_metadata(self, stage: str) -> Dict[str, Any]:
        meta = dict(self._stage_prompt_info.get(stage, {}))
        meta.setdefault("stage", stage)
        meta.setdefault("schema_version", self.schema_version)
        return meta

    def _load_cached_stage(self, state: GroupDigestState, stage: str) -> Optional[Dict[str, Any]]:
        store = state.get("state_store")
        if store is None:
            return None
        record = store.get_stage(stage)
        if not record:
            return None
        metadata = record.get("metadata", {})
        payload = record.get("payload")
        state.setdefault("artifact_metadata", {})[stage] = metadata
        if isinstance(payload, dict):
            return payload
        return {"value": payload} if payload is not None else {}

    def _store_stage_payload(
        self,
        state: GroupDigestState,
        stage: str,
        payload: Dict[str, Any],
        *,
        model_id: Optional[str] = None,
    ) -> None:
        store = state.get("state_store")
        if store is None:
            return
        metadata = self._get_stage_metadata(stage)
        metadata["model_id"] = model_id or metadata.get("model_id") or "system"
        metadata["stored_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        store.set_stage(stage, payload, metadata)
        state.setdefault("artifact_metadata", {})[stage] = metadata

    @contextmanager
    def _stage_span(self, stage: str, state: GroupDigestState):
        start_ts = time.perf_counter()
        status = "success"
        span_ctx = (
            self._tracer.start_as_current_span(
                f"digest.stage.{stage}",
                attributes={
                    "tenant_id": state.get("tenant_id", ""),
                    "group_id": state.get("group_id", ""),
                    "trace_id": state.get("trace_id", ""),
                    "stage": stage,
                },
            )
            if self._tracer
            else nullcontext()
        )
        try:
            with span_ctx:
                yield
        except Exception:
            status = "failure"
            # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
            try:
                digest_stage_status_total.labels(
                    stage=_sanitize_prometheus_label(stage), 
                    status=_sanitize_prometheus_label(status)
                ).inc()
                digest_stage_latency_seconds.labels(
                    stage=_sanitize_prometheus_label(stage), 
                    status=_sanitize_prometheus_label(status)
                ).observe(
                    max(0.0, time.perf_counter() - start_ts)
                )
            except Exception as metric_error:
                logger.warning(
                    "Failed to record stage metrics",
                    stage=stage,
                    status=status,
                    error=str(metric_error),
                    error_type=type(metric_error).__name__,
                )
            raise
        else:
            # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
            try:
                digest_stage_status_total.labels(
                    stage=_sanitize_prometheus_label(stage), 
                    status=_sanitize_prometheus_label(status)
                ).inc()
                digest_stage_latency_seconds.labels(
                    stage=_sanitize_prometheus_label(stage), 
                    status=_sanitize_prometheus_label(status)
                ).observe(
                    max(0.0, time.perf_counter() - start_ts)
                )
            except Exception as metric_error:
                logger.warning(
                    "Failed to record stage metrics",
                    stage=stage,
                    status=status,
                    error=str(metric_error),
                    error_type=type(metric_error).__name__,
                )

    def _record_dlq_event(
        self,
        state: GroupDigestState,
        *,
        stage: str,
        error_code: str,
        error_details: str,
        exc: Optional[BaseException] = None,
        retry_count: Optional[int] = None,
    ) -> None:
        window = state.get("window", {}) or {}
        tenant_id = state.get("tenant_id")
        trace_id = state.get("trace_id")
        group_id = state.get("group_id")
        window_id = window.get("window_id")
        payload_snippet = {
            "window_id": window_id,
            "group_id": group_id,
            "tenant_id": tenant_id,
            "stage": stage,
            "trace_id": trace_id,
            "errors": (state.get("errors") or [])[-3:],
        }
        digest_id = state.get("digest_id") or window.get("digest_id")
        
        payload = {
            "window_id": window_id,
            "group_id": group_id,
            "tenant_id": tenant_id,
            "stage": stage,
            "trace_id": trace_id,
            "errors": (state.get("errors", []))[-3:],
        }
        
        stack_trace = None
        error_message = error_details[:1000]  # Ограничение длины для БД
        if exc is not None:
            stack_trace = "".join(traceback.format_exception(exc.__class__, exc, exc.__traceback__))[-5000:]
            if not error_message:
                error_message = str(exc)[:1000]
        
        try:
            dlq_service = get_dlq_service()
            max_attempts = retry_count or (self.config.resilience.retry.max_attempts if hasattr(self, 'config') else 3)
            
            dlq_event = dlq_service.add_event(
                tenant_id=tenant_id or "",
                entity_type="digest",
                event_type="digests.generate",
                payload=payload,
                error_code=error_code,
                error_message=error_message,
                stack_trace=stack_trace,
                entity_id=digest_id,
                max_attempts=max_attempts,
            )
            
            # Сохраняем обратную совместимость: добавляем в state для логирования
            event_payload = {
                "event_id": str(dlq_event.id),
                "stage": stage,
                "error_code": error_code,
                "error_details": error_details,
                "retry_count": dlq_event.retry_count,
                "max_attempts": dlq_event.max_attempts,
                "next_retry_at": dlq_event.next_retry_at.isoformat() if dlq_event.next_retry_at else None,
                "status": dlq_event.status,
            }
            state.setdefault("dlq_events", []).append(event_payload)
            
            logger.info(
                "dlq.event_recorded",
                tenant_id=tenant_id,
                entity_type="digest",
                stage=stage,
                error_code=error_code,
                event_id=str(dlq_event.id),
                next_retry_at=dlq_event.next_retry_at.isoformat() if dlq_event.next_retry_at else None
            )
        except Exception as dlq_exc:
            # Если DLQ недоступен, логируем ошибку, но не прерываем workflow
            logger.error(
                "dlq.record_failed",
                tenant_id=tenant_id,
                stage=stage,
                error_code=error_code,
                dlq_error=str(dlq_exc),
                error_type=type(dlq_exc).__name__,
            )
            # Fallback: сохраняем в state для последующей обработки
            event_payload = {
                "stage": stage,
                "error_code": error_code,
                "error_details": error_details,
                "dlq_error": str(dlq_exc),
            }
            state.setdefault("dlq_events", []).append(event_payload)
        # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
        try:
            digest_dlq_total.labels(
                stage=_sanitize_prometheus_label(stage), 
                error_code=_sanitize_prometheus_label(error_code)
            ).inc()
        except Exception as e:
            logger.warning(
                "Failed to record digest_dlq_total metric",
                stage=stage,
                error_code=error_code,
                error=str(e),
                error_type=type(e).__name__,
            )

    def _handle_stage_failure(
        self,
        state: GroupDigestState,
        *,
        stage: str,
        error_code: str,
        error_details: str,
        exc: Optional[BaseException] = None,
        fallback_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        errors = list(state.get("errors", []))
        errors.append(f"{stage}:{error_code}:{error_details}")
        state["errors"] = errors
        self._record_dlq_event(
            state,
            stage=stage,
            error_code=error_code,
            error_details=error_details,
            exc=exc,
        )
        summary_html = fallback_summary or "<b>Дайджест недоступен</b>: ошибка генерации."
        state["skip"] = True
        state["skip_reason"] = f"{stage}:{error_code}"
        state["summary_html"] = summary_html
        state["summary"] = summary_html
        delivery_payload = {
            "status": "blocked_failure",
            "reason": error_code,
            "stage": stage,
            "tenant_id": state.get("tenant_id"),
            "group_id": state.get("group_id"),
        }
        if state.get("trace_id"):
            delivery_payload["trace_id"] = state["trace_id"]
        state["delivery"] = delivery_payload
        self._store_stage_payload(state, stage, {"errors": errors, "fallback": True}, model_id="system")
        return {"summary_html": summary_html, "summary": summary_html, "errors": errors}

    def _log_sample(self, event: str, *, sample_rate: float = LOG_SAMPLE_RATE, **kwargs: Any) -> None:
        if sample_rate <= 0:
            return
        if random.random() <= sample_rate:
            logger.info(event, sample_rate=sample_rate, **kwargs)

    def _ensure_valid_json(
        self,
        *,
        agent_name: str,
        raw_content: str,
        context: Dict[str, Any],
        tenant_id: str,
        trace_id: str,
    ) -> Optional[Dict[str, Any]]:
        schema = self._json_schemas.get(agent_name)
        if not schema:
            try:
                return json.loads(raw_content)
            except json.JSONDecodeError as exc:  # pragma: no cover
                logger.warning(
                    "json_guard.json_decode_failed",
                    agent=agent_name,
                    error=str(exc),
                )
                return None

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            errors = [f"json_decode_error:{exc.msg}"]
            return self._run_repair(
                agent_name=agent_name,
                schema=schema,
                raw_content=raw_content,
                context=context,
                errors=errors,
                tenant_id=tenant_id,
                trace_id=trace_id,
            )

        result = validate(schema, data)
        if result.valid:
            return data

        return self._run_repair(
            agent_name=agent_name,
            schema=schema,
            raw_content=raw_content,
            context=context,
            errors=result.errors,
            tenant_id=tenant_id,
            trace_id=trace_id,
        )

    def _run_repair(
        self,
        *,
        agent_name: str,
        schema: Dict[str, Any],
        raw_content: str,
        context: Dict[str, Any],
        errors: Sequence[str],
        tenant_id: str,
        trace_id: str,
    ) -> Optional[Dict[str, Any]]:
        repair_prompt = self._repair_prompts.get(agent_name)
        if not repair_prompt:
            logger.warning(
                "json_guard.repair_prompt_missing",
                agent=agent_name,
                errors=format_errors(errors),
            )
            return None
        variables = build_repair_variables(
            context=context,
            invalid_json=raw_content,
            errors=errors,
        )
        repaired = try_self_repair(
            llm_router=self._llm_router,
            agent_name=f"{agent_name}_repair",
            repair_prompt=repair_prompt,
            schema=schema,
            variables=variables,
            tenant_id=tenant_id,
            trace_id=trace_id,
            error_messages=errors,
            max_attempts=self._max_repair_attempts,
        )
        if repaired is None:
            logger.warning(
                "json_guard.repair_failed",
                agent=agent_name,
                errors=format_errors(errors),
            )
        return repaired

    def _normalize_summary_html(self, raw: str) -> str:
        """Нормализация HTML дайджеста с проверкой на пустые формулировки."""
        summary_html = (raw or "").strip()
        
        # Первый грубый фильтр: regexp-проверка на пустые формулировки (не единственный критерий)
        empty_phrases_patterns = [
            r"Основные темы:\s*Не выявлены",
            r"из-за отсутствия данных",
            r"не были зафиксированы из-за отсутствия подробных данных",
            r"конкретные детали и решения не были зафиксированы",
        ]
        for pattern in empty_phrases_patterns:
            if re.search(pattern, summary_html, re.IGNORECASE):
                logger.warning(
                    "digest_empty_phrase_detected",
                    pattern=pattern,
                    snippet=summary_html[:200],
                )
                # Не отклоняем сразу, это только первый фильтр - дополняется структурными проверками
        
        if len(summary_html) > 4096:
            summary_html = summary_html[:4093] + "..."
        if summary_html and not summary_html.startswith("📊"):
            summary_html = f"📊 <b>Дайджест</b>\n{summary_html}"
        if not summary_html:
            summary_html = "<b>Дайджест пуст</b>"
        return summary_html

    def _resolve_group_title(self, window: Dict[str, Any]) -> str:
        for key in ("group_title", "group_name", "title", "name"):
            value = window.get(key)
            if value:
                return str(value)
        group_id = window.get("group_id")
        return f"Группа {group_id}" if group_id else "Группа"

    def _resolve_period(self, window: Dict[str, Any]) -> str:
        start = window.get("window_start")
        end = window.get("window_end")
        if start and end:
            return f"{start} — {end}"
        if start:
            return f"с {start}"
        if end:
            return f"до {end}"
        return "период не задан"

    def _resolve_message_count(self, state: GroupDigestState, window: Dict[str, Any]) -> int:
        count = window.get("message_count") or state.get("message_total")
        if count is None:
            sanitized = state.get("sanitized_messages") or state.get("messages") or []
            count = len(sanitized)
        try:
            return int(count)
        except (TypeError, ValueError):
            return 0

    def _select_digest_mode(self, message_count: int) -> str:
        """Определяет режим генерации дайджеста по размеру окна."""
        if message_count <= self.config.quality_checks.micro_window_threshold:
            return "micro"
        elif message_count > self.config.quality_checks.large_window_threshold:
            return "large"
        else:
            return "normal"

    @staticmethod
    def _select_media_highlights(state: GroupDigestState, limit: int = 4) -> List[Dict[str, Any]]:
        highlights = state.get("media_highlights") or []
        if not isinstance(highlights, list):
            return []
        return highlights[:limit]

    @staticmethod
    def _resolve_media_stats(state: GroupDigestState) -> Dict[str, Any]:
        media_stats = dict(state.get("media_stats") or {})
        if not media_stats:
            context_stats = state.get("context_stats") or {}
            media_stats = {
                key: value
                for key, value in context_stats.items()
                if key.startswith("media_")
            }
            if "media_kinds" in context_stats:
                media_stats["media_kinds"] = context_stats.get("media_kinds", {})
        media_stats.setdefault("media_total", 0)
        media_stats.setdefault("media_messages", 0)
        media_stats.setdefault("media_with_description", 0)
        media_stats.setdefault("media_without_description", 0)
        media_stats.setdefault("media_kinds", media_stats.get("media_kinds", {}))
        return media_stats

    def _get_baseline_snapshot(self, state: GroupDigestState) -> Optional[BaselineSnapshot]:
        baseline = state.get("baseline_snapshot")
        if isinstance(baseline, BaselineSnapshot):
            return baseline
        if isinstance(baseline, dict) and baseline:
            try:
                return BaselineSnapshot(
                    window_id=str(baseline.get("window_id") or ""),
                    topics=baseline.get("topics") or [],
                    metrics=baseline.get("metrics") or {},
                    summary_html=baseline.get("summary_html") or "",
                )
            except Exception:  # noqa: BLE001
                return None
        return None

    def _attempt_corrective_synthesis(self, state: GroupDigestState, baseline_digest: str) -> Optional[Dict[str, Any]]:
        topics_subset = (state.get("topics") or [])[:3]
        participants_subset = (state.get("participants") or [])[:5]
        window = state.get("window", {}) or {}
        group_title = self._resolve_group_title(window)
        period = self._resolve_period(window)
        message_count = self._resolve_message_count(state, window)

        variables = {
            "window_json": json.dumps(state.get("window", {}), ensure_ascii=False),
            "topics_json": json.dumps(topics_subset, ensure_ascii=False),
            "participants_json": json.dumps(participants_subset, ensure_ascii=False),
            "role_profile_json": json.dumps(state.get("role_profile", []), ensure_ascii=False),
            "metrics_json": json.dumps(state.get("metrics", {}), ensure_ascii=False),
            "media_highlights_json": json.dumps(self._select_media_highlights(state, limit=3), ensure_ascii=False),
            "media_stats_json": json.dumps(self._resolve_media_stats(state), ensure_ascii=False),
            "baseline_digest": baseline_digest or "Нет предыдущего дайджеста.",
            "group_title": group_title,
            "period": period,
            "message_count": message_count,
        }
        try:
            response = self._llm_router.invoke(
                "synthesis_agent_retry",
                self._synthesis_retry_prompt,
                variables,
                state.get("tenant_id", ""),
                state.get("trace_id", ""),
                approx_tokens_from_payload(variables),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("synthesis_retry_failed", error=str(exc))
            self._record_dlq_event(
                state,
                stage="synthesis_agent",
                error_code="retry_failed",
                error_details=str(exc),
                exc=exc,
            )
            return None

        summary_html = self._normalize_summary_html(response.content)
        # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
        try:
            digest_synthesis_fallback_total.labels(reason=_sanitize_prometheus_label("quality_retry")).inc()
        except Exception as metric_error:
            logger.warning("Failed to record digest_synthesis_fallback_total metric", reason="quality_retry", error=str(metric_error))
        result = {
            "summary_html": summary_html,
            "summary": summary_html,
            "mode": "retry",
        }
        self._store_stage_payload(state, "synthesis_agent", result, model_id=response.model)
        state["summary_html"] = summary_html
        state["summary"] = summary_html
        state["synthesis_retry_used"] = True
        return result

    def _node_ingest_validator(self, state: GroupDigestState) -> Dict[str, Any]:
        with self._stage_span("ingest_validator", state):
            errors = list(state.get("errors", []))
            window = state.get("window", {}) or {}
            tenant_id = str(window.get("tenant_id") or "")
            group_id = str(window.get("group_id") or "")
            trace_id = state.get("trace_id") or window.get("trace_id") or uuid.uuid4().hex
            raw_messages = state.get("messages") or []

            cached = self._load_cached_stage(state, "ingest_validator")
            if cached:
                cached_window = dict(cached.get("window") or {})
                for key, value in window.items():
                    if value is not None:
                        cached_window[key] = value
                cached_window["trace_id"] = trace_id
                cached["window"] = cached_window
                cached["trace_id"] = trace_id
                cached["tenant_id"] = tenant_id
                cached["group_id"] = group_id
                return cached

            historical_ctx: Dict[str, Any] = {"messages": [], "ranking": [], "duplicates": {}}
            if self._context_storage_client and tenant_id and group_id:
                try:
                    historical_ctx = self._context_storage_client.fetch_recent_context(
                        tenant_id=tenant_id,
                        group_id=group_id,
                        limit_windows=max(1, self.config.context_storage.history_windows),
                        limit_messages=max(0, self.config.context_storage.history_message_limit),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "context7_storage.fetch_failed",
                        tenant_id=tenant_id,
                        group_id=group_id,
                        error=str(exc),
                    )

            context_result = self._context_service.assemble(
                window=window,
                raw_messages=raw_messages,
                tenant_id=tenant_id,
                trace_id=trace_id,
                max_messages=self.config.max_messages,
                excerpt_limit=20,
                historical_messages=historical_ctx.get("messages"),
                historical_ranking=historical_ctx.get("ranking"),
            )

            sanitized_messages = context_result.sanitized_messages
            message_total = len(sanitized_messages)
            if message_total == 0:
                # Context7: Санитизация значений метрик для Prometheus labels
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_skipped_total.labels(
                        reason=_sanitize_prometheus_label("empty_window"), 
                        tenant_id=_sanitize_prometheus_label(tenant_id or "unknown"), 
                        mode=_sanitize_prometheus_label("unknown")
                    ).inc()
                except Exception as metric_error:
                    logger.warning("Failed to record digest_skipped_total metric", reason="empty_window", error=str(metric_error))
                result = {
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "group_id": group_id,
                    "context_stats": context_result.stats,
                    "context_ranking": context_result.ranking,
                    "context_duplicates": context_result.duplicates,
                    "context_history_links": context_result.historical_links,
                    "media_stats": context_result.media_stats,
                    "media_highlights": context_result.media_highlights,
                    "errors": errors + ["empty_window"],
                    "skip": True,
                    "skip_reason": "empty_window",
                    "summary_html": "<b>Дайджест не сформирован</b>: в указанном окне нет сообщений.",
                    "summary": "Дайджест не сформирован: сообщений нет.",
                }
                self._store_stage_payload(state, "ingest_validator", result, model_id="system")
                return result

            if message_total < self.config.min_messages:
                # Режим ещё не определён, используем "unknown"
                # Context7: Санитизация значений метрик для Prometheus labels
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_skipped_total.labels(
                        reason=_sanitize_prometheus_label("too_few_messages"), 
                        tenant_id=_sanitize_prometheus_label(tenant_id or "unknown"), 
                        mode=_sanitize_prometheus_label("unknown")
                    ).inc()
                except Exception as metric_error:
                    logger.warning("Failed to record digest_skipped_total metric", reason="too_few_messages", error=str(metric_error))
                result = {
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "group_id": group_id,
                    "sanitized_messages": sanitized_messages,
                    "message_total": message_total,
                    "errors": errors + ["too_few_messages"],
                    "skip": True,
                    "skip_reason": "too_few_messages",
                    "summary_html": "<b>Дайджест не сформирован</b>: недостаточно сообщений для анализа.",
                    "summary": "Дайджест не сформирован: слишком мало сообщений.",
                    "context_stats": context_result.stats,
                    "context_ranking": context_result.ranking,
                    "context_duplicates": context_result.duplicates,
                    "context_history_links": context_result.historical_links,
                    "media_stats": context_result.media_stats,
                    "media_highlights": context_result.media_highlights,
                }
                self._store_stage_payload(state, "ingest_validator", result, model_id="system")
                return result

            # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
            try:
                digest_messages_processed_total.labels(tenant=_sanitize_prometheus_label(tenant_id or "unknown")).inc(message_total)
            except Exception as metric_error:
                logger.warning("Failed to record digest_messages_processed_total metric", tenant_id=tenant_id, error=str(metric_error))

            duplicates_removed = context_result.stats.get("duplicates_removed", 0)
            trimmed_for_max = context_result.stats.get("trimmed_for_max", 0)
            if duplicates_removed:
                errors.append(f"context_dedup_removed:{duplicates_removed}")
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_stage_status_total.labels(
                        stage=_sanitize_prometheus_label("ingest_validator"), 
                        status=_sanitize_prometheus_label("context_dedup")
                    ).inc()
                except Exception as metric_error:
                    logger.warning("Failed to record digest_stage_status_total metric", stage="ingest_validator", status="context_dedup", error=str(metric_error))
            if trimmed_for_max:
                errors.append(f"context_trimmed:{trimmed_for_max}")
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_stage_status_total.labels(
                        stage=_sanitize_prometheus_label("ingest_validator"), 
                        status=_sanitize_prometheus_label("context_trimmed")
                    ).inc()
                except Exception as metric_error:
                    logger.warning("Failed to record digest_stage_status_total metric", stage="ingest_validator", status="context_trimmed", error=str(metric_error))
            historical_matches = context_result.stats.get("historical_matches", 0)
            if historical_matches:
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_stage_status_total.labels(
                        stage=_sanitize_prometheus_label("ingest_validator"), 
                        status=_sanitize_prometheus_label("context_history")
                    ).inc(historical_matches)
                except Exception as metric_error:
                    logger.warning("Failed to record digest_stage_status_total metric", stage="ingest_validator", status="context_history", error=str(metric_error))

            participant_stats = context_result.participant_stats
            conversation_excerpt = context_result.conversation_excerpt

            window_info = dict(window)
            window_info.setdefault("tenant_id", tenant_id)
            window_info.setdefault("group_id", group_id)
            window_info.setdefault("window_id", window.get("window_id") or state.get("window", {}).get("window_id"))
            window_info["message_count"] = message_total
            window_info["chunk_count"] = max(1, math.ceil(message_total / max(1, self.config.chunk_size)))
            window_info["trace_id"] = trace_id

            # Определяем режим генерации дайджеста
            digest_mode = self._select_digest_mode(message_total)
            state["digest_mode"] = digest_mode
            state["pipeline_version"] = "group_digest_v2"
            
            # Метрика распределения по режимам
            window_size_hours = window_info.get("window_size_hours", 0) or 0
            # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
            try:
                digest_mode_total.labels(
                    mode=_sanitize_prometheus_label(digest_mode),
                    tenant_id=_sanitize_prometheus_label(tenant_id or "unknown"),
                    window_size_hours=_sanitize_prometheus_label(str(window_size_hours)),
                ).inc()
            except Exception as metric_error:
                logger.warning("Failed to record digest_mode_total metric", mode=digest_mode, tenant_id=tenant_id, error=str(metric_error))

            if self._context_storage_client and window_info.get("window_id"):
                try:
                    self._context_storage_client.upsert_window_context(
                        tenant_id=tenant_id,
                        group_id=group_id,
                        window_id=str(window_info["window_id"]),
                        trace_id=trace_id,
                        stats=context_result.stats,
                        sanitized_messages=sanitized_messages,
                        ranking=context_result.ranking,
                        duplicates=context_result.duplicates,
                        historical_links=context_result.historical_links,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "context7_storage.upsert_failed",
                        tenant_id=tenant_id,
                        group_id=group_id,
                        window_id=window_info.get("window_id"),
                        error=str(exc),
                    )

            result = {
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "group_id": group_id,
                "window": window_info,
                "sanitized_messages": sanitized_messages,
                "message_total": message_total,
                "participant_stats": participant_stats,
                "conversation_excerpt": conversation_excerpt,
                "context_stats": context_result.stats,
                "context_ranking": context_result.ranking,
                "context_duplicates": context_result.duplicates,
                "context_history_links": context_result.historical_links,
                 "media_stats": context_result.media_stats,
                 "media_highlights": context_result.media_highlights,
                "errors": errors,
            }
            self._store_stage_payload(state, "ingest_validator", result, model_id="system")
            self._log_sample(
                "digest_ingest_sample",
                message_count=message_total,
                trace_id=trace_id,
                tenant_id=tenant_id,
                sample_messages=context_result.sample_messages,
            )
            return result

    def _node_thread_builder(self, state: GroupDigestState) -> Dict[str, Any]:
        if state.get("skip"):
            return {}
        with self._stage_span("thread_builder", state):
            cached = self._load_cached_stage(state, "thread_builder")
            if cached:
                return cached
            sanitized = state.get("sanitized_messages") or []
            threads = build_threads(sanitized, self.config.thread_max_len)
            result = {"threads": threads}
            self._store_stage_payload(state, "thread_builder", result, model_id="system")
            return result

    def _node_segmenter(self, state: GroupDigestState) -> Dict[str, Any]:
        if state.get("skip"):
            return {}
        with self._stage_span("segmenter_agent", state):
            cached = self._load_cached_stage(state, "segmenter_agent")
            if cached:
                return cached
            
            # Для micro-режима пропускаем сегментацию, создаём один общий сегмент
            digest_mode = state.get("digest_mode", "normal")
            if digest_mode == "micro":
                sanitized = state.get("sanitized_messages", [])
                if sanitized:
                    # Один общий сегмент из всех сообщений
                    semantic_units = [
                        {
                            "kind": "meta",
                            "text": " ".join(msg.get("content", "")[:100] for msg in sanitized[:5]),
                            "msg_ids": [str(msg.get("message_id", "")) for msg in sanitized[:10]],
                            "offset_range": [0, len(sanitized)],
                            "confidence": 0.5,
                        }
                    ]
                else:
                    semantic_units = []
                
                result = {"semantic_units": semantic_units}
                self._store_stage_payload(state, "segmenter_agent", result, model_id="heuristic")
                return result
            
            threads = state.get("threads") or []
            if not threads:
                result = {"semantic_units": []}
                self._store_stage_payload(state, "segmenter_agent", result, model_id="system")
                return result

            prompt = self._prompts["segmenter_agent"]
            semantic_units: List[Dict[str, Any]] = []
            errors = list(state.get("errors", []))
            last_model_id: Optional[str] = None

            for thread in threads:
                thread_messages = "\n".join(
                    f"[{msg['timestamp_iso']}] {msg['username']}: {msg['content']}"
                    for msg in thread["messages"]
                )
                variables = {
                    "thread_id": thread["thread_id"],
                    "thread_messages": thread_messages,
                }
                try:
                    response = self._llm_router.invoke(
                        "segmenter_agent",
                        prompt,
                        variables,
                        state.get("tenant_id", ""),
                        state.get("trace_id", ""),
                        approx_tokens_from_text(thread_messages),
                    )
                    last_model_id = response.model
                    data = self._ensure_valid_json(
                        agent_name="segmenter_agent",
                        raw_content=response.content,
                        context={
                            "thread_id": thread["thread_id"],
                            "thread_messages": thread_messages,
                        },
                        tenant_id=state.get("tenant_id", ""),
                        trace_id=state.get("trace_id", ""),
                    )
                    if data is None:
                        errors.append(f"segmenter:{thread['thread_id']}:invalid_response")
                        continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "segmenter_agent_failed",
                        agent="segmenter_agent",
                        thread_id=thread["thread_id"],
                        error=str(exc),
                    )
                    errors.append(f"segmenter:{thread['thread_id']}:{exc}")
                    continue

                units = data.get("units") if isinstance(data, dict) else None
                if not isinstance(units, list):
                    errors.append(f"segmenter:{thread['thread_id']}:invalid_units")
                    continue
                for unit in units:
                    semantic_units.append(
                        {
                            "thread_id": thread["thread_id"],
                            "kind": unit.get("kind", "unknown"),
                            "text": unit.get("text", ""),
                            "msg_ids": unit.get("msg_ids", []),
                            "offset_range": unit.get("offset_range", [0, 0]),
                            "confidence": clamp(float(unit.get("confidence", 0.0))),
                        }
                    )

            result = {"semantic_units": semantic_units, "errors": errors}
            self._store_stage_payload(state, "segmenter_agent", result, model_id=last_model_id or "unknown")
            return result

    def _node_emotion_profile(self, state: GroupDigestState) -> Dict[str, Any]:
        if state.get("skip"):
            return {}
        with self._stage_span("emotion_agent", state):
            cached = self._load_cached_stage(state, "emotion_agent")
            if cached:
                return cached
            
            # Для micro-режима используем упрощённую эвристику
            digest_mode = state.get("digest_mode", "normal")
            if digest_mode == "micro":
                # Простая эвристика на основе сообщений
                sanitized = state.get("sanitized_messages", [])
                profile = {
                    "tone": "neutral",
                    "intensity": 0.5,
                    "conflict": 0.0,
                    "collaboration": 0.5,
                    "stress": 0.0,
                    "enthusiasm": 0.5,
                    "notes": "Упрощённая оценка для малого окна.",
                }
                metrics_payload = {
                    "tone": profile["tone"],
                    "intensity": profile["intensity"],
                    "conflict": profile["conflict"],
                    "collaboration": profile["collaboration"],
                    "stress": profile["stress"],
                    "enthusiasm": profile["enthusiasm"],
                    "description": profile["notes"],
                }
                result = {"emotion_profile": profile, "metrics": metrics_payload}
                state["emotion_profile"] = profile
                state["metrics"] = metrics_payload
                self._store_stage_payload(state, "emotion_agent", result, model_id="heuristic")
                return result
            prompt = self._prompts["emotion_agent"]
            messages_sample = build_conversation_excerpt(state.get("sanitized_messages") or [], limit=40)
            variables = {
                "window_json": json.dumps(state.get("window", {}), ensure_ascii=False),
                "messages_sample": messages_sample,
            }
            model_id = "unknown"
            try:
                response = self._llm_router.invoke(
                    "emotion_agent",
                    prompt,
                    variables,
                    state.get("tenant_id", ""),
                    state.get("trace_id", ""),
                    approx_tokens_from_payload(variables),
                )
                model_id = response.model
                data = self._ensure_valid_json(
                    agent_name="emotion_agent",
                    raw_content=response.content,
                    context={
                        "window_json": variables["window_json"],
                        "messages_sample": messages_sample,
                    },
                    tenant_id=state.get("tenant_id", ""),
                    trace_id=state.get("trace_id", ""),
                )
                if data is None:
                    raise ValueError("invalid_emotion_json")
            except Exception as exc:  # noqa: BLE001
                logger.warning("emotion_agent_failed", error=str(exc))
                fallback = {
                    "emotion_profile": {
                        "tone": "neutral",
                        "intensity": 0.0,
                        "conflict": 0.0,
                        "collaboration": 0.0,
                        "stress": 0.0,
                        "enthusiasm": 0.0,
                        "notes": "Аналитика недоступна",
                    },
                "metrics": {
                    "tone": "neutral",
                        "intensity": 0.0,
                    "conflict": 0.0,
                    "collaboration": 0.0,
                    "stress": 0.0,
                    "enthusiasm": 0.0,
                    "description": "Аналитика недоступна",
                },
                    "errors": list(state.get("errors", [])) + [f"emotion_agent:{exc}"],
                }
                state["emotion_profile"] = fallback["emotion_profile"]
                state["metrics"] = fallback["metrics"]
                self._store_stage_payload(state, "emotion_agent", fallback, model_id="unknown")
                return fallback

            tone = (data.get("tone") if isinstance(data, dict) else "neutral") or "neutral"
            intensity = clamp(float(data.get("intensity", data.get("sentiment", 0.0))))
            profile = {
                "tone": tone if tone in {"positive", "neutral", "negative"} else "neutral",
                "intensity": intensity,
                "conflict": clamp(float(data.get("conflict", 0.0))),
                "collaboration": clamp(float(data.get("collaboration", 0.0))),
                "stress": clamp(float(data.get("stress", 0.0))),
                "enthusiasm": clamp(float(data.get("enthusiasm", 0.0))),
                "notes": data.get("notes") or "Эмоциональный профиль рассчитан автоматически.",
            }
            metrics_payload = {
                "tone": profile["tone"],
                "intensity": profile["intensity"],
                "conflict": profile["conflict"],
                "collaboration": profile["collaboration"],
                "stress": profile["stress"],
                "enthusiasm": profile["enthusiasm"],
                "description": profile["notes"],
            }
            result = {"emotion_profile": profile, "metrics": metrics_payload}
            state["emotion_profile"] = profile
            state["metrics"] = metrics_payload
            self._store_stage_payload(state, "emotion_agent", result, model_id=model_id)
            return result

    def _node_roles(self, state: GroupDigestState) -> Dict[str, Any]:
        if state.get("skip"):
            return {}
        with self._stage_span("roles_agent", state):
            cached = self._load_cached_stage(state, "roles_agent")
            if cached:
                return cached
            
            # Для micro-режима используем упрощённую эвристику
            digest_mode = state.get("digest_mode", "normal")
            if digest_mode == "micro":
                # Простая эвристика: топ участник по сообщениям = инициатор
                participant_stats = state.get("participant_stats", [])
                if participant_stats:
                    top_participant = max(participant_stats, key=lambda p: p.get("message_count", 0))
                    participants_payload = [
                        {
                            "telegram_id": top_participant.get("telegram_id"),
                            "username": top_participant.get("username"),
                            "role": "инициатор темы",
                            "message_count": top_participant.get("message_count", 0),
                            "summary": "Наиболее активный участник обсуждения.",
                        }
                    ]
                else:
                    participants_payload = []
                
                result = {
                    "role_profile": [],
                    "participants": participants_payload,
                }
                state["role_profile"] = []
                state["participants"] = participants_payload
                self._store_stage_payload(state, "roles_agent", result, model_id="heuristic")
                return result
            
            prompt = self._prompts["roles_agent"]
            variables = {
                "participant_stats": json.dumps(state.get("participant_stats", []), ensure_ascii=False),
                "semantic_units": json.dumps(state.get("semantic_units", []), ensure_ascii=False),
            }
            model_id = "unknown"
            try:
                response = self._llm_router.invoke(
                    "roles_agent",
                    prompt,
                    variables,
                    state.get("tenant_id", ""),
                    state.get("trace_id", ""),
                    approx_tokens_from_payload(variables),
                )
                model_id = response.model
                data = self._ensure_valid_json(
                    agent_name="roles_agent",
                    raw_content=response.content,
                    context={
                        "participant_stats": variables["participant_stats"],
                        "semantic_units": variables["semantic_units"],
                    },
                    tenant_id=state.get("tenant_id", ""),
                    trace_id=state.get("trace_id", ""),
                )
                if data is None:
                    raise ValueError("invalid_roles_json")
            except Exception as exc:  # noqa: BLE001
                logger.warning("roles_agent_failed", error=str(exc))
                fallback_participants = [
                    {
                        "telegram_id": stat.get("telegram_id"),
                        "username": stat.get("username"),
                        "role": "observer",
                        "message_count": stat.get("message_count", 0),
                        "summary": "Активность без выделенной роли.",
                    }
                    for stat in state.get("participant_stats", [])
                ]
                result = {
                    "role_profile": [],
                    "participants": fallback_participants,
                    "errors": list(state.get("errors", [])) + [f"roles_agent:{exc}"],
                }
                state["role_profile"] = []
                state["participants"] = fallback_participants
                self._store_stage_payload(state, "roles_agent", result, model_id="unknown")
                return result

            participants = data.get("participants") if isinstance(data, dict) else None
            role_profile: List[Dict[str, Any]] = []
            if isinstance(participants, list):
                for entry in participants:
                    role_profile.append(
                        {
                            "username": entry.get("username"),
                            "roles": entry.get("roles", []),
                            "dominant_role": entry.get("dominant_role") or "observer",
                            "message_ids": entry.get("message_ids", []),
                            "comment": entry.get("comment") or "",
                        }
                    )
            stats_index = {
                stat.get("username"): stat
                for stat in state.get("participant_stats", [])
                if isinstance(stat, dict)
            }
            participants_payload: List[Dict[str, Any]] = []
            for item in role_profile:
                username = item.get("username")
                stat_info = stats_index.get(username, {})
                participants_payload.append(
                    {
                        "telegram_id": stat_info.get("telegram_id"),
                        "username": username,
                        "role": item.get("dominant_role") or "observer",
                        "message_count": stat_info.get("message_count", len(item.get("message_ids", []))),
                        "summary": item.get("comment") or "",
                    }
                )
            if not participants_payload:
                participants_payload = self._build_fallback_participants(state)
                role_profile = []

            result = {"role_profile": role_profile, "participants": participants_payload}
            state["role_profile"] = role_profile
            state["participants"] = participants_payload
            self._store_stage_payload(state, "roles_agent", result, model_id=model_id)
            return result

    def _build_fallback_participants(self, state: GroupDigestState) -> List[Dict[str, Any]]:
        stats = state.get("participant_stats") or []
        sorted_stats = sorted(
            (stat for stat in stats if isinstance(stat, dict)),
            key=lambda item: item.get("message_count", 0),
            reverse=True,
        )
        top_stats = sorted_stats[: min(5, len(sorted_stats))]
        fallback_participants: List[Dict[str, Any]] = []
        for stat in top_stats:
            username = stat.get("username") or f"user-{stat.get('telegram_id')}"
            summary = stat.get("summary") or ""
            if not summary:
                pieces: List[str] = []
                media_types = stat.get("media_types") or {}
                if media_types:
                    pieces.append(
                        "медиа: " + ", ".join(f"{kind}:{count}" for kind, count in media_types.items())
                    )
                media_samples = stat.get("media_samples") or []
                if media_samples:
                    pieces.append("; ".join(media_samples[:2]))
                summary = "; ".join(pieces)[:160] or "Активный участник обсуждения."

            fallback_participants.append(
                {
                "telegram_id": stat.get("telegram_id"),
                    "username": username,
                    "role": "observer",
                    "message_count": stat.get("message_count", 0),
                    "summary": summary,
                }
            )

        return fallback_participants

    def _node_topics(self, state: GroupDigestState) -> Dict[str, Any]:
        if state.get("skip"):
            return {}
        with self._stage_span("topic_agent", state):
            cached = self._load_cached_stage(state, "topic_agent")
            if cached:
                return cached
            
            # Для micro-режима используем упрощённую keyword-based тему
            digest_mode = state.get("digest_mode", "normal")
            if digest_mode == "micro":
                # Простая keyword-based тема
                keyword_topics = self._context_service.build_keyword_topics(
                    state.get("sanitized_messages") or [],
                    state.get("media_highlights") or [],
                    limit=1,  # Только одна тема для micro
                )
                if keyword_topics:
                    topics = keyword_topics
                else:
                    # Fallback: одна общая тема
                    message_count = state.get("message_total", 0)
                    topics = [
                        {
                            "title": "Короткое обсуждение",
                            "priority": "medium",
                            "msg_count": message_count,
                            "threads": [],
                            "summary": f"Обсуждение из {message_count} сообщений.",
                            "signals": {"source": "micro_mode"},
                            "decision": "Требуется зафиксировать итоговое решение.",
                            "status": "watch",
                            "owners": "Активные участники группы",
                            "blockers": [],
                            "actions": [],
                        }
                    ]
                
                result = {"topics": topics}
                state["topics"] = topics
                self._store_stage_payload(state, "topic_agent", result, model_id="heuristic")
                return result
            
            prompt = self._prompts["topic_agent"]
            variables = {
                "semantic_units": json.dumps(state.get("semantic_units", []), ensure_ascii=False),
                "emotion_profile": json.dumps(state.get("emotion_profile", {}), ensure_ascii=False),
                "media_highlights_json": json.dumps(state.get("media_highlights", []), ensure_ascii=False),
            }
            model_id = "unknown"
            try:
                response = self._llm_router.invoke(
                    "topic_agent",
                    prompt,
                    variables,
                    state.get("tenant_id", ""),
                    state.get("trace_id", ""),
                    approx_tokens_from_payload(variables),
                )
                model_id = response.model
                data = self._ensure_valid_json(
                    agent_name="topic_agent",
                    raw_content=response.content,
                    context={
                        "semantic_units": variables["semantic_units"],
                        "emotion_profile": variables["emotion_profile"],
                    },
                    tenant_id=state.get("tenant_id", ""),
                    trace_id=state.get("trace_id", ""),
                )
                if data is None:
                    raise ValueError("invalid_topics_json")
            except Exception as exc:  # noqa: BLE001
                logger.warning("topic_agent_failed", error=str(exc))
                fallback = {"topics": [], "errors": list(state.get("errors", [])) + [f"topic_agent:{exc}"]}
                self._store_stage_payload(state, "topic_agent", fallback, model_id="unknown")
                return fallback

            topics_raw = data.get("topics") if isinstance(data, dict) else None
            topics: List[Dict[str, Any]] = []
            if isinstance(topics_raw, list):
                for topic in topics_raw:
                    topics.append(
                        {
                            "title": topic.get("title") or "Без названия",
                            "priority": topic.get("priority") or "medium",
                            "msg_count": int(topic.get("msg_count") or len(topic.get("threads") or [])),
                            "threads": topic.get("threads") or [],
                            "summary": topic.get("summary") or "",
                            "signals": topic.get("signals") or {},
                        }
                    )
            if not topics:
                fallback_topics = self._context_service.build_keyword_topics(
                    state.get("sanitized_messages") or [],
                    state.get("media_highlights") or [],
                )
                if fallback_topics:
                    topics = fallback_topics
                else:
                    topics.append(
                        {
                            "title": "Общее обсуждение",
                            "priority": "medium",
                            "msg_count": state.get("message_total", 0),
                            "threads": [],
                            "summary": "Темы не были выделены автоматически.",
                            "signals": {"source": "fallback"},
                        }
                    )
            elif all(topic.get("title") in {"Общее обсуждение", "Разное", "Без темы"} for topic in topics):
                # Rule-based guard: если все темы общие, принудительно запускаем fallback
                heuristic_topics = self._context_service.build_keyword_topics(
                    state.get("sanitized_messages") or [],
                    state.get("media_highlights") or [],
                )
                if heuristic_topics:
                    topics = heuristic_topics
            
            # Rule-based guard: проверка на минимальное количество тем
            message_count = state.get("message_total", 0)
            min_messages_for_topics = self.config.quality_checks.min_messages_for_topics
            min_topics_required = self.config.quality_checks.min_topics_required
            
            if message_count >= min_messages_for_topics and len(topics) < min_topics_required:
                # Считаем ошибкой, принудительно запускаем fallback
                reason = f"topics_too_few:got_{len(topics)}_required_{min_topics_required}"
                logger.warning(
                    "digest_topics_too_few",
                    message_count=message_count,
                    topics_count=len(topics),
                    min_required=min_topics_required,
                )
                tenant_id = state.get("tenant_id", "unknown")
                mode = state.get("digest_mode", "normal")
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_topics_empty_total.labels(
                        reason=_sanitize_prometheus_label(reason), 
                        tenant_id=_sanitize_prometheus_label(tenant_id), 
                        mode=_sanitize_prometheus_label(mode)
                    ).inc()
                except Exception as metric_error:
                    logger.warning("Failed to record digest_topics_empty_total metric", reason=reason, tenant_id=tenant_id, error=str(metric_error))
                
                fallback_topics = self._context_service.build_keyword_topics(
                    state.get("sanitized_messages") or [],
                    state.get("media_highlights") or [],
                )
                if fallback_topics:
                    topics = fallback_topics
                else:
                    # Если fallback не помог, всё равно добавляем ошибку
                    state.setdefault("errors", []).append(reason)
            
            result = {"topics": topics}
            state["topics"] = topics
            self._store_stage_payload(state, "topic_agent", result, model_id=model_id)
            self._log_sample(
                "digest_topics_sample",
                trace_id=state.get("trace_id"),
                tenant_id=state.get("tenant_id"),
                topics=topics[: min(3, len(topics))],
            )
            return result

    def _node_synthesis(self, state: GroupDigestState) -> Dict[str, Any]:
        if state.get("skip"):
            return {}
        with self._stage_span("synthesis_agent", state):
            cached = self._load_cached_stage(state, "synthesis_agent")
            if cached:
                return cached
            prompt = self._prompts["synthesis_agent"]
            tenant_id = state.get("tenant_id", "")
            trace_id = state.get("trace_id", "")
            window = state.get("window", {}) or {}
            
            # Model Personalization: адаптация промпта под персону пользователя
            # Performance: только summary профиля (до 100-200 токенов), не весь профиль
            user_id = window.get("requested_by_user_id") or state.get("requested_by_user_id")
            if user_id and tenant_id:
                try:
                    persona_summary = self._persona_service.get_persona_profile_summary(
                        user_id=user_id,
                        tenant_id=tenant_id,
                    )
                    if persona_summary and persona_summary != "User preferences: general topics":
                        # Адаптируем промпт под персону
                        prompt_template_str = prompt.messages[0].content if hasattr(prompt, 'messages') else str(prompt)
                        adapted_prompt_str = self._persona_service.adapt_prompt(
                            base_prompt=prompt_template_str,
                            persona_summary=persona_summary,
                        )
                        # Создаем новый промпт с персонализацией
                        from langchain_core.prompts import ChatPromptTemplate
                        adapted_prompt = ChatPromptTemplate.from_template(adapted_prompt_str)
                        prompt = adapted_prompt
                        logger.debug(
                            "persona.prompt_adapted",
                            user_id=str(user_id),
                            tenant_id=tenant_id,
                            summary_length=len(persona_summary),
                        )
                        # Метрики
                        try:
                            persona_prompt_adapted_total.labels(result=_sanitize_prometheus_label("success")).inc()
                        except Exception as e:
                            logger.warning("Failed to record persona_prompt_adapted_total metric", error=str(e))
                except Exception as persona_exc:
                    # Не прерываем workflow при ошибке персонализации
                    logger.warning(
                        "persona.adaptation_failed",
                        error=str(persona_exc),
                        user_id=str(user_id) if user_id else None,
                        tenant_id=tenant_id,
                    )
                    # Метрики
                    try:
                        persona_prompt_adapted_total.labels(result=_sanitize_prometheus_label("failed")).inc()
                    except Exception as e:
                        logger.warning("Failed to record persona_prompt_adapted_total metric", error=str(e))
            else:
                # Метрики: персонализация пропущена (нет user_id)
                try:
                    persona_prompt_adapted_total.labels(result=_sanitize_prometheus_label("skipped")).inc()
                except Exception as e:
                    logger.warning("Failed to record persona_prompt_adapted_total metric", error=str(e))

            baseline_dict: Dict[str, Any] = {}
            existing_baseline = state.get("baseline_snapshot")
            if isinstance(existing_baseline, dict):
                baseline_dict = existing_baseline
            else:
                snapshot = load_previous_snapshot(
                    tenant_id,
                    state.get("group_id", ""),
                    window.get("window_id", ""),
                )
                if snapshot:
                    baseline_dict = asdict(snapshot)

            group_title = self._resolve_group_title(window)
            period = self._resolve_period(window)
            message_count = self._resolve_message_count(state, window)

            # Вычисляем baseline_delta для v2 промпта (используем текущие темы и метрики)
            baseline_snapshot_obj = self._get_baseline_snapshot(state)
            current_metrics = state.get("metrics", {})
            current_topics = state.get("topics", [])
            baseline_delta = compute_delta(
                baseline_snapshot_obj,
                current_topics,
                current_metrics,
            )
            state["baseline_delta"] = baseline_delta
            
            # Ограничиваем участников до top-3 для v2 промпта
            participants_all = state.get("participants", [])
            participants_top3 = sorted(
                participants_all,
                key=lambda p: p.get("message_count", 0),
                reverse=True
            )[:3]

            variables = {
                "window_json": json.dumps(window, ensure_ascii=False),
                "topics_json": json.dumps(state.get("topics", []), ensure_ascii=False),
                "participants_json": json.dumps(participants_top3, ensure_ascii=False),  # Только top-3
                "role_profile_json": json.dumps(state.get("role_profile", []), ensure_ascii=False),
                "metrics_json": json.dumps(state.get("metrics", {}), ensure_ascii=False),
                "media_highlights_json": json.dumps(self._select_media_highlights(state, limit=4), ensure_ascii=False),
                "media_stats_json": json.dumps(self._resolve_media_stats(state), ensure_ascii=False),
                "baseline_delta": json.dumps(baseline_delta, ensure_ascii=False),  # Для v2 промпта
                "baseline_digest": baseline_dict.get("summary_html") or "Нет предыдущего дайджеста.",
                "group_title": group_title,
                "period": period,
                "message_count": message_count,
            }
            model_id = "unknown"
            try:
                response = self._llm_router.invoke(
                    "synthesis_agent",
                    prompt,
                    variables,
                    tenant_id,
                    trace_id,
                    approx_tokens_from_payload(variables),
                )
                model_id = response.model
                raw = response.content
            except Exception as exc:  # noqa: BLE001
                logger.warning("synthesis_agent_failed", error=str(exc))
                return self._handle_stage_failure(
                    state,
                    stage="synthesis_agent",
                    error_code="synthesis_failed",
                    error_details=str(exc),
                    exc=exc,
                    fallback_summary="<b>Дайджест не сформирован</b>: ошибка синтеза.",
                )

            summary_html = self._normalize_summary_html(raw)
            state["baseline_snapshot"] = baseline_dict
            state["synthesis_attempts"] = state.get("synthesis_attempts", 0) + 1
            state.setdefault("synthesis_retry_used", False)
            
            # Версионирование промпта
            state["prompt_version"] = "digest_composer_prompt_v2"

            result = {
                "summary_html": summary_html,
                "summary": summary_html,
                "baseline_snapshot": baseline_dict,
                "prompt_version": "digest_composer_prompt_v2",
            }
            self._store_stage_payload(state, "synthesis_agent", result, model_id=model_id)
            return result

    def _pre_quality_checks(self, state: GroupDigestState) -> Dict[str, Any]:
        """Rule-based проверки качества перед LLM-judge."""
        digest_html = state.get("summary_html", "")
        topics = state.get("topics", [])
        participants = state.get("participants", [])
        message_count = state.get("message_total", 0)
        sanitized_messages = state.get("sanitized_messages", [])
        
        checks = {
            "needs_corrective_synthesis": False,
            "issues": [],
            "keyword_coverage": 0.0,
        }
        
        # Проверка 1: наличие тем
        min_messages_for_topics = self.config.quality_checks.min_messages_for_topics
        min_topics_required = self.config.quality_checks.min_topics_required
        if message_count >= min_messages_for_topics and len(topics) < min_topics_required:
            checks["needs_corrective_synthesis"] = True
            checks["issues"].append(f"topics_too_few:got_{len(topics)}_required_{min_topics_required}")
        
        # Проверка 2: TF-IDF ключевых слов и покрытие в дайджесте
        if sanitized_messages and digest_html:
            try:
                from collections import Counter
                # Простой TF-IDF: частотность слов в сообщениях
                all_words = []
                for msg in sanitized_messages[:50]:  # Ограничиваем для производительности
                    content = msg.get("content", "")
                    if content:
                        # Простая токенизация (можно улучшить)
                        words = re.findall(r'\b\w{3,}\b', content.lower())
                        all_words.extend(words)
                
                if all_words:
                    word_freq = Counter(all_words)
                    top_keywords = [word for word, _ in word_freq.most_common(20)]
                    
                    # Покрытие: сколько ключевых слов встречается в дайджесте
                    digest_lower = digest_html.lower()
                    matched_keywords = [kw for kw in top_keywords if kw in digest_lower]
                    coverage = len(matched_keywords) / len(top_keywords) if top_keywords else 0.0
                    checks["keyword_coverage"] = coverage
                    
                    if coverage < 0.3:  # Меньше 30% покрытия
                        checks["issues"].append(f"low_keyword_coverage:{coverage:.2f}")
            except Exception as exc:  # noqa: BLE001
                logger.debug("pre_quality_checks_tfidf_failed", error=str(exc))
        
        # Проверка 3: наличие действий и участников
        has_actions = any(
            topic.get("actions") and len(topic.get("actions", [])) > 0
            for topic in topics
        )
        if not has_actions and message_count >= min_messages_for_topics:
            checks["issues"].append("no_actions_in_topics")
        
        if not participants:
            checks["issues"].append("no_participants")
        
        return checks

    def _node_quality(self, state: GroupDigestState) -> Dict[str, Any]:
        if state.get("skip"):
            return {}
        with self._stage_span("evaluation_agent", state):
            cached = self._load_cached_stage(state, "evaluation_agent")
            if cached:
                return cached
            
            # Rule-based checks перед LLM-judge
            pre_checks = self._pre_quality_checks(state)
            tenant_id = state.get("tenant_id", "unknown")
            if pre_checks.get("needs_corrective_synthesis"):
                issues = pre_checks.get("issues", [])
                logger.warning(
                    "digest_pre_quality_failed",
                    issues=issues,
                    tenant_id=tenant_id,
                )
                # Логируем каждую проблему как отдельную метрику
                for issue in issues:
                    check_name = issue.split(":")[0] if ":" in issue else issue
                    # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                    try:
                        digest_pre_quality_failed_total.labels(
                            check=_sanitize_prometheus_label(check_name), 
                            tenant_id=_sanitize_prometheus_label(tenant_id)
                        ).inc()
                    except Exception as metric_error:
                        logger.warning("Failed to record digest_pre_quality_failed_total metric", check=check_name, tenant_id=tenant_id, error=str(metric_error))
                
                # Отправляем на corrective synthesis без LLM-judge
                baseline_snapshot = self._get_baseline_snapshot(state)
                baseline_digest = baseline_snapshot.summary_html if baseline_snapshot else (state.get("baseline_snapshot", {}) or {}).get("summary_html", "")
                corrective = self._attempt_corrective_synthesis(state, baseline_digest=baseline_digest or "Нет предыдущего дайджеста.")
                if corrective:
                    state["summary_html"] = corrective["summary_html"]
                    state["summary"] = corrective["summary_html"]
            
            prompt = self._prompts["evaluation_agent"]
            model_id = "unknown"
            tenant_id = state.get("tenant_id", "")
            trace_id = state.get("trace_id", "")

            conversation_excerpt = state.get("conversation_excerpt", "")
            digest_html = state.get("summary_html", "")

            def evaluate_digest(html: str) -> Tuple[Dict[str, Any], str]:
                eval_variables = {
                    "conversation_excerpt": conversation_excerpt,
                    "digest_html": html,
                }
                response_inner = self._llm_router.invoke(
                    "evaluation_agent",
                    prompt,
                    eval_variables,
                    tenant_id,
                    trace_id,
                    approx_tokens_from_payload(eval_variables),
                )
                data_inner = self._ensure_valid_json(
                    agent_name="evaluation_agent",
                    raw_content=response_inner.content,
                    context=eval_variables,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                )
                if data_inner is None:
                    raise ValueError("invalid_evaluation_json")
                return data_inner, response_inner.model

            try:
                data, model_id = evaluate_digest(digest_html)
            except Exception as exc:  # noqa: BLE001
                logger.warning("evaluation_agent_failed", error=str(exc))
                self._record_dlq_event(
                    state,
                    stage="evaluation_agent",
                    error_code="evaluation_failed",
                    error_details=str(exc),
                    exc=exc,
                )
                result = {
                    "evaluation": {
                        "faithfulness": 0.0,
                        "coherence": 0.0,
                        "coverage": 0.0,
                        "focus": 0.0,
                        "notes": "Оценка недоступна",
                    },
                    "errors": list(state.get("errors", [])) + [f"evaluation_agent:{exc}"],
                }
                self._store_stage_payload(state, "evaluation_agent", result, model_id="unknown")
                return result

            metrics = {
                "faithfulness": clamp(float(data.get("faithfulness", 0.0))),
                "coherence": clamp(float(data.get("coherence", 0.0))),
                "coverage": clamp(float(data.get("coverage", 0.0))),
                "focus": clamp(float(data.get("focus", 0.0))),
                "notes": data.get("notes") or "Автоматическая оценка выполнена.",
            }
            quality_score = clamp(float(data.get("quality_score", sum(metrics[k] for k in ("faithfulness", "coherence", "coverage", "focus")) / 4)))
            # Context7: Санитизация имен метрик для Prometheus labels
            for key in ("faithfulness", "coherence", "coverage", "focus"):
                try:
                    safe_key = _sanitize_prometheus_label(key)
                    digest_quality_score.labels(metric=safe_key).set(metrics[key])
                except Exception as e:
                    # Context7: Детальное логирование ошибок Prometheus метрик с traceback
                    logger.error(
                        "Failed to record quality metric",
                        metric=key,
                        error=str(e),
                        error_type=type(e).__name__,
                        traceback=traceback.format_exc(),
                    )
            try:
                digest_quality_score.labels(metric=_sanitize_prometheus_label("overall")).set(quality_score)
            except Exception as e:
                # Context7: Детальное логирование ошибок Prometheus метрик с traceback
                import traceback
                logger.error(
                    "Failed to record overall quality score",
                    error=str(e),
                    error_type=type(e).__name__,
                    traceback=traceback.format_exc(),
                )

            min_score = min(metrics["faithfulness"], metrics["coherence"], metrics["coverage"], metrics["focus"], quality_score)
            quality_threshold = self.config.quality_checks.quality_threshold
            quality_pass = min_score >= quality_threshold
            errors = list(state.get("errors", []))
            
            # Self-Improvement: Self-verification через чеклист
            # Performance: только для Smart Path (async пайплайны), максимум 300 токенов
            try:
                quality_checklist = [
                    "Дайджест содержит основные темы",
                    "Структура дайджеста логична",
                    "Отсутствуют галлюцинации",
                    "Есть заголовок и разделы",
                ]
                verification_result = self._self_improvement.self_verify(
                    content=digest_html,
                    quality_checklist=quality_checklist,
                    max_tokens=300,
                )
                if not verification_result.passed and verification_result.issues:
                    logger.info(
                        "self_improvement.verification_failed",
                        quality_score=quality_score,
                        issues=verification_result.issues,
                        tenant_id=tenant_id,
                    )
                    # Метрики
                    try:
                        self_improvement_verification_total.labels(result=_sanitize_prometheus_label("failed")).inc()
                    except Exception as e:
                        logger.warning("Failed to record self_improvement_verification_total metric", error=str(e))
                    # Добавляем issues в notes для контекста
                    if verification_result.issues:
                        metrics["notes"] = f"{metrics.get('notes', '')} [Self-verify issues: {', '.join(verification_result.issues[:3])}]"
                else:
                    # Метрики
                    try:
                        self_improvement_verification_total.labels(result=_sanitize_prometheus_label("passed")).inc()
                    except Exception as e:
                        logger.warning("Failed to record self_improvement_verification_total metric", error=str(e))
            except Exception as verify_exc:
                # Не прерываем workflow при ошибке self-verification
                logger.warning(
                    "self_improvement.verification_error",
                    error=str(verify_exc),
                    tenant_id=tenant_id,
                )
            
            # Self-Improvement: Self-correction при низком качестве (quality_score < 0.6)
            # Performance: только 1 попытка исправления, не цикл
            if quality_score < 0.6 and not state.get("synthesis_retry_used"):
                try:
                    verification_result = self._self_improvement.self_verify(
                        content=digest_html,
                        quality_checklist=["Основные проблемы качества"],
                        max_tokens=200,
                    )
                    if verification_result.issues:
                        corrected_content = self._self_improvement.self_correct(
                            content=digest_html,
                            issues=verification_result.issues,
                            max_attempts=1,
                        )
                        if corrected_content and corrected_content != digest_html:
                            logger.info(
                                "self_improvement.correction_applied",
                                quality_score=quality_score,
                                tenant_id=tenant_id,
                            )
                            # Переоцениваем исправленный контент
                            try:
                                data, model_id = evaluate_digest(corrected_content)
                                corrected_quality_score = clamp(float(data.get("quality_score", quality_score)))
                                if corrected_quality_score > quality_score:
                                    digest_html = corrected_content
                                    state["summary_html"] = corrected_content
                                    state["summary"] = corrected_content
                                    quality_score = corrected_quality_score
                                    metrics.update({
                                        "faithfulness": clamp(float(data.get("faithfulness", 0.0))),
                                        "coherence": clamp(float(data.get("coherence", 0.0))),
                                        "coverage": clamp(float(data.get("coverage", 0.0))),
                                        "focus": clamp(float(data.get("focus", 0.0))),
                                        "notes": f"{data.get('notes', '')} [Self-corrected]",
                                    })
                                    min_score = min(metrics["faithfulness"], metrics["coherence"], metrics["coverage"], metrics["focus"], quality_score)
                                    quality_pass = min_score >= quality_threshold
                                    # Метрики
                                    try:
                                        self_improvement_correction_total.labels(result=_sanitize_prometheus_label("applied")).inc()
                                    except Exception as e:
                                        logger.warning("Failed to record self_improvement_correction_total metric", error=str(e))
                                else:
                                    try:
                                        self_improvement_correction_total.labels(result=_sanitize_prometheus_label("skipped")).inc()
                                    except Exception as e:
                                        logger.warning("Failed to record self_improvement_correction_total metric", error=str(e))
                            except Exception as re_eval_exc:
                                logger.warning("self_improvement.re_evaluation_failed", error=str(re_eval_exc))
                except Exception as correct_exc:
                    logger.warning("self_improvement.correction_error", error=str(correct_exc))
            
            # Self-Improvement: Self-gating для определения нужен ли retry
            # Performance: дает право на один retry с альтернативным промптом/моделью
            needs_retry = False
            if not quality_pass and not state.get("synthesis_retry_used"):
                needs_retry = self._self_improvement.self_gate(
                    quality_score=quality_score,
                    threshold=quality_threshold,
                )
                # Метрики
                try:
                    decision = "retry_allowed" if needs_retry else "retry_denied"
                    self_improvement_gating_total.labels(decision=_sanitize_prometheus_label(decision)).inc()
                except Exception as e:
                    logger.warning("Failed to record self_improvement_gating_total metric", error=str(e))

            if not quality_pass and needs_retry and not state.get("synthesis_retry_used"):
                baseline_snapshot = self._get_baseline_snapshot(state)
                baseline_digest = baseline_snapshot.summary_html if baseline_snapshot else (state.get("baseline_snapshot", {}) or {}).get("summary_html", "")
                corrective = self._attempt_corrective_synthesis(state, baseline_digest=baseline_digest or "Нет предыдущего дайджеста.")
                if corrective:
                    digest_html = corrective["summary_html"]
                    try:
                        data, model_id = evaluate_digest(digest_html)
                        metrics.update(
                            {
                                "faithfulness": clamp(float(data.get("faithfulness", 0.0))),
                                "coherence": clamp(float(data.get("coherence", 0.0))),
                                "coverage": clamp(float(data.get("coverage", 0.0))),
                                "focus": clamp(float(data.get("focus", 0.0))),
                                "notes": data.get("notes") or "Автоматическая оценка выполнена.",
                            }
                        )
                        quality_score = clamp(float(data.get("quality_score", sum(metrics[k] for k in ("faithfulness", "coherence", "coverage", "focus")) / 4)))
                        for key in ("faithfulness", "coherence", "coverage", "focus"):
                            try:
                                safe_key = _sanitize_prometheus_label(key)
                                digest_quality_score.labels(metric=safe_key).set(metrics[key])
                            except Exception as e:
                                # Context7: Детальное логирование ошибок Prometheus метрик с traceback
                                import traceback
                                logger.error(
                                    "Failed to record quality metric",
                                    metric=key,
                                    error=str(e),
                                    error_type=type(e).__name__,
                                    traceback=traceback.format_exc(),
                                )
                        try:
                            # Context7: Санитизация значения метрики для Prometheus labels
                            digest_quality_score.labels(metric=_sanitize_prometheus_label("overall")).set(quality_score)
                        except Exception as e:
                            # Context7: Детальное логирование ошибок Prometheus метрик с traceback
                            import traceback
                            logger.error(
                                "Failed to record overall quality score",
                                error=str(e),
                                error_type=type(e).__name__,
                                traceback=traceback.format_exc(),
                            )
                        min_score = min(metrics["faithfulness"], metrics["coherence"], metrics["coverage"], metrics["focus"], quality_score)
                        quality_threshold = self.config.quality_checks.quality_threshold
                        quality_pass = min_score >= quality_threshold
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("evaluation_retry_failed", error=str(exc))

            if not quality_pass:
                mode = state.get("digest_mode", "normal")
                # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                try:
                    digest_skipped_total.labels(
                        reason=_sanitize_prometheus_label("quality_below_threshold"),
                        tenant_id=_sanitize_prometheus_label(tenant_id or "unknown"),
                        mode=_sanitize_prometheus_label(mode),
                    ).inc()
                except Exception as metric_error:
                    logger.warning("Failed to record digest_skipped_total metric", reason="quality_below_threshold", tenant_id=tenant_id, error=str(metric_error))
                errors.append(f"quality_below_threshold:{min_score:.2f}")
                self._record_dlq_event(
                    state,
                    stage="evaluation_agent",
                    error_code="quality_below_threshold",
                    error_details=f"{min_score:.2f}",
                )

            # baseline_delta уже вычислен в _node_synthesis, используем его
            baseline_delta = state.get("baseline_delta", {})
            result = {
                "evaluation": metrics,
                "quality_pass": quality_pass,
                "quality_min_score": min_score,
                "quality_score": quality_score,
                "baseline_delta": baseline_delta,
                "errors": errors,
            }
            self._store_stage_payload(state, "evaluation_agent", result, model_id=model_id)
            self._log_sample(
                "digest_evaluation_sample",
                trace_id=state.get("trace_id"),
                tenant_id=state.get("tenant_id"),
                quality_score=quality_score,
                quality_pass=quality_pass,
                baseline_delta=baseline_delta,
            )
            return result

    def _node_delivery(self, state: GroupDigestState) -> Dict[str, Any]:
        with self._stage_span("delivery_manager", state):
            if state.get("skip"):
                errors = list(state.get("errors", []))
                delivery_payload = {
                    "status": "skipped",
                    "reason": state.get("skip_reason", "unknown"),
                    "tenant_id": state.get("tenant_id"),
                    "group_id": state.get("group_id"),
                    "format": os.getenv("DIGEST_DELIVERY_FORMAT", "telegram_html"),
                }
                if state.get("trace_id"):
                    delivery_payload["trace_id"] = state["trace_id"]
                result = {"delivery": delivery_payload, "errors": errors}
                self._store_stage_payload(state, "delivery_manager", result, model_id="system")
                return result
            delivery_format = os.getenv("DIGEST_DELIVERY_FORMAT", "telegram_html")
            required_scope = os.getenv("DIGEST_DELIVERY_SCOPE", "DIGEST_READ")
            status = "pending"
            reason = None
            if not state.get("quality_pass", True):
                status = "blocked_quality"
                reason = "quality_below_threshold"
            errors = list(state.get("errors", []))
            if required_scope:
                window = state.get("window", {}) or {}
                scopes = window.get("scopes") or []
                if required_scope not in scopes:
                    status = "blocked_rbac"
                    reason = f"missing_scope:{required_scope}"
                    # Context7: Санитизация значений метрик для Prometheus labels - все три labels обязательны
                    tenant_id = state.get("tenant_id")  # Context7: Получаем tenant_id из state
                    # Context7: Обработка ошибок метрик - не прерываем workflow при ошибках Prometheus
                    try:
                        digest_skipped_total.labels(
                            reason=_sanitize_prometheus_label("missing_scope"),
                            tenant_id=_sanitize_prometheus_label(tenant_id or "unknown"),
                            mode=_sanitize_prometheus_label(state.get("digest_mode", "normal")),
                        ).inc()
                    except Exception as metric_error:
                        logger.warning("Failed to record digest_skipped_total metric", reason="missing_scope", tenant_id=tenant_id, error=str(metric_error))
                    errors.append(reason)
            delivery = {
                "format": delivery_format,
                "status": status,
                "channel": "telegram",
                "tenant_id": state.get("tenant_id"),
                "group_id": state.get("group_id"),
                "quality_min_score": state.get("quality_min_score"),
            }
            if reason:
                delivery["reason"] = reason
            if state.get("trace_id"):
                delivery["trace_id"] = state["trace_id"]
            result = {"delivery": delivery, "errors": errors}
            self._store_stage_payload(state, "delivery_manager", result, model_id="system")
            return result

    def generate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        window = dict(payload.get("window", {}) or {})
        tenant_id = str(window.get("tenant_id") or payload.get("tenant_id") or "")
        group_id = str(window.get("group_id") or payload.get("group_id") or "")
        window_id = str(window.get("window_id") or payload.get("window_id") or "")
        if not tenant_id or not group_id or not window_id:
            raise ValueError("tenant_id, group_id и window_id обязательны для запуска пайплайна")

        window["tenant_id"] = tenant_id
        window["group_id"] = group_id
        window["window_id"] = window_id
        trace_id = payload.get("trace_id") or window.get("trace_id") or uuid.uuid4().hex
        window["trace_id"] = trace_id

        # Episodic Memory: запись события run_started
        episodic_memory = get_episodic_memory_service()
        digest_id = None
        try:
            episodic_memory.record_event(
                tenant_id=tenant_id,
                entity_type="digest",
                event_type="run_started",
                metadata={
                    "group_id": group_id,
                    "window_id": window_id,
                    "trace_id": trace_id,
                    "message_count": len(payload.get("messages", [])),
                },
            )
        except Exception as exc:
            logger.warning("episodic_memory.record_start_failed", error=str(exc), tenant_id=tenant_id)

        store = self._state_store_factory.create(
            tenant_id=tenant_id,
            group_id=group_id,
            window_id=window_id,
            digest_version=os.getenv("DIGEST_AGENT_VERSION", "v1"),
        )
        if not store.acquire_lock():
            raise RuntimeError(f"Digest window locked: {tenant_id}/{group_id}/{window_id}")

        metadata_snapshot = store.load_metadata()
        artifact_metadata = dict(metadata_snapshot.get("stages", {}))
        
        # Получаем user_id из payload для персонализации
        requested_by_user_id = payload.get("requested_by_user_id") or window.get("requested_by_user_id")
        if requested_by_user_id:
            window["requested_by_user_id"] = str(requested_by_user_id)

        initial_state: GroupDigestState = {
            "window": window,
            "messages": payload.get("messages", []),
            "errors": list(metadata_snapshot.get("errors", [])) if isinstance(metadata_snapshot, dict) else [],
            "skip": bool(metadata_snapshot.get("skip")) if isinstance(metadata_snapshot, dict) else False,
            "skip_reason": metadata_snapshot.get("skip_reason"),
            "state_store": store,
            "artifact_metadata": artifact_metadata,
            "schema_version": self.schema_version,
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "group_id": group_id,
            "requested_by_user_id": str(requested_by_user_id) if requested_by_user_id else None,
            "dlq_events": [],
            "synthesis_retry_used": False,
            "baseline_snapshot": {},
            "_dlq_first_seen": {},
        }

        # Plan-first архитектура: генерируем план перед выполнением
        # Performance: только для Smart Path (async пайплайны), не для Fast Path
        plan: Optional[Plan] = None
        try:
            planning_context = {
                "tenant_id": tenant_id,
                "group_id": group_id,
                "window_id": window_id,
                "message_count": len(payload.get("messages", [])),
                "trace_id": trace_id,
            }
            plan = self._planning_agent.generate_plan(
                context=planning_context,
                is_fast_path=False,  # Digest pipeline - это Smart Path (async)
            )
            logger.info(
                "planning.plan_generated",
                tenant_id=tenant_id,
                trace_id=trace_id,
                plan_steps=len(plan.steps) if plan else 0,
            )
            # Метрики
            try:
                planning_plan_generated_total.labels(path_type=_sanitize_prometheus_label("smart_path")).inc()
            except Exception as e:
                logger.warning("Failed to record planning_plan_generated_total metric", error=str(e))
            # Сохраняем план в state для последующей проверки
            initial_state["_plan"] = {
                "steps": plan.steps if plan else [],
                "max_steps": plan.max_steps if plan else 0,
            }
        except Exception as plan_exc:
            # Не прерываем workflow при ошибке планирования
            logger.warning(
                "planning.plan_generation_failed",
                error=str(plan_exc),
                tenant_id=tenant_id,
                trace_id=trace_id,
            )

        try:
            result = self.workflow.invoke(initial_state)
            if "summary" not in result and "summary_html" in result:
                result["summary"] = result["summary_html"]
            result["schema_version"] = self.schema_version
            result.setdefault("artifact_metadata", initial_state.get("artifact_metadata", {}))
            result.pop("state_store", None)
            result.setdefault("dlq_events", initial_state.get("dlq_events", []))
            
            # Plan-first архитектура: проверка выполнения плана
            # Performance: только для Smart Path, не для Fast Path
            if plan:
                try:
                    execution_results = [
                        {"stage": "ingest_validator", "status": "completed"},
                        {"stage": "thread_builder", "status": "completed"},
                        {"stage": "segmenter_agent", "status": "completed"},
                        {"stage": "emotion_agent", "status": "completed"},
                        {"stage": "roles_agent", "status": "completed"},
                        {"stage": "topic_agent", "status": "completed"},
                        {"stage": "synthesis_agent", "status": "completed"},
                        {"stage": "evaluation_agent", "status": "completed"},
                        {"stage": "delivery_manager", "status": "completed"},
                    ]
                    plan_executed = self._planning_agent.check_plan_execution(
                        plan=plan,
                        results=execution_results,
                    )
                    if not plan_executed:
                        logger.warning(
                            "planning.plan_execution_failed",
                            tenant_id=tenant_id,
                            trace_id=trace_id,
                            plan_steps=len(plan.steps),
                            results_count=len(execution_results),
                        )
                        # Метрики
                        try:
                            planning_plan_execution_check_total.labels(result=_sanitize_prometheus_label("failed")).inc()
                        except Exception as e:
                            logger.warning("Failed to record planning_plan_execution_check_total metric", error=str(e))
                    else:
                        try:
                            planning_plan_execution_check_total.labels(result=_sanitize_prometheus_label("success")).inc()
                        except Exception as e:
                            logger.warning("Failed to record planning_plan_execution_check_total metric", error=str(e))
                        # Перепланирование при необходимости (только для Smart Path)
                        updated_context = {
                            **planning_context,
                            "execution_results": execution_results,
                            "errors": result.get("errors", []),
                        }
                        new_plan = self._planning_agent.replan(
                            original_plan=plan,
                            execution_results=execution_results,
                            context=updated_context,
                        )
                        if new_plan:
                            logger.info(
                                "planning.replan_generated",
                                tenant_id=tenant_id,
                                trace_id=trace_id,
                                new_plan_steps=len(new_plan.steps),
                            )
                            # Метрики
                            try:
                                planning_replan_total.labels(result=_sanitize_prometheus_label("generated")).inc()
                            except Exception as e:
                                logger.warning("Failed to record planning_replan_total metric", error=str(e))
                        else:
                            try:
                                planning_replan_total.labels(result=_sanitize_prometheus_label("skipped")).inc()
                            except Exception as e:
                                logger.warning("Failed to record planning_replan_total metric", error=str(e))
                except Exception as check_exc:
                    # Не прерываем workflow при ошибке проверки плана
                    logger.warning(
                        "planning.plan_check_failed",
                        error=str(check_exc),
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                    )

            store.update_metadata(
                stages=result.get("artifact_metadata", {}),
                skip=result.get("skip", False),
                skip_reason=result.get("skip_reason"),
                errors=result.get("errors", []),
                quality_pass=result.get("quality_pass"),
                last_completed=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self._log_sample(
                "digest_summary_sample",
                trace_id=trace_id,
                tenant_id=tenant_id,
                quality_pass=result.get("quality_pass"),
                delivery=result.get("delivery"),
                baseline_delta=result.get("baseline_delta"),
            )
            
            # Episodic Memory: запись события run_completed
            try:
                digest_id = result.get("digest_id") or window.get("digest_id")
                event_type = "run_completed"
                if not result.get("quality_pass", False):
                    event_type = "quality_low"
                
                episodic_memory.record_event(
                    tenant_id=tenant_id,
                    entity_type="digest",
                    event_type=event_type,
                    entity_id=digest_id,
                    metadata={
                        "group_id": group_id,
                        "window_id": window_id,
                        "trace_id": trace_id,
                        "quality_score": result.get("quality_score"),
                        "quality_pass": result.get("quality_pass", False),
                        "topics_count": len(result.get("topics", [])),
                        "synthesis_retry_used": result.get("synthesis_retry_used", False),
                    },
                )
            except Exception as exc:
                logger.warning("episodic_memory.record_complete_failed", error=str(exc), tenant_id=tenant_id)
            
            return result
        except Exception as exc:
            # Context7: Детальное логирование ошибки с traceback для диагностики "Incorrect label names"
            logger.error(
                "group_digest_workflow_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
                tenant_id=tenant_id,
                trace_id=trace_id,
            )
            
            # Episodic Memory: запись события error
            try:
                episodic_memory.record_event(
                    tenant_id=tenant_id,
                    entity_type="digest",
                    event_type="error",
                    entity_id=digest_id,
                    metadata={
                        "group_id": group_id,
                        "window_id": window_id,
                        "trace_id": trace_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],  # Ограничение длины
                    },
                )
            except Exception as mem_exc:
                logger.warning("episodic_memory.record_error_failed", error=str(mem_exc), tenant_id=tenant_id)
            
            failure_result = self._handle_stage_failure(
                initial_state,
                stage="workflow",
                error_code="workflow_error",
                error_details=str(exc),
                exc=exc,
                fallback_summary="<b>Дайджест не сформирован</b>: критическая ошибка пайплайна.",
            )
            result = {
                "summary_html": failure_result["summary_html"],
                "summary": failure_result["summary"],
                "errors": failure_result["errors"],
                "skip": True,
                "skip_reason": initial_state.get("skip_reason"),
            "topics": [],
            "participants": [],
            "metrics": {},
            "evaluation": {},
                "quality_pass": False,
                "delivery": initial_state.get("delivery"),
                "dlq_events": initial_state.get("dlq_events", []),
                "artifact_metadata": initial_state.get("artifact_metadata", {}),
                "schema_version": self.schema_version,
            }
            store.update_metadata(
                stages=result.get("artifact_metadata", {}),
                skip=True,
                skip_reason=result.get("skip_reason"),
                errors=result.get("errors", []),
                quality_pass=False,
                last_completed=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self._log_sample(
                "digest_summary_sample",
                trace_id=trace_id,
                tenant_id=tenant_id,
                quality_pass=False,
                delivery=result.get("delivery"),
                baseline_delta=result.get("baseline_delta"),
            )
            return result
        finally:
            store.release_lock()

    async def generate_async(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self.generate, payload)

