"""
Baseline comparison helpers for group digest pipeline.

Используется для Stage 2 (Quality Loop & Self-Checks):
- Загрузка предыдущего дайджеста (по артефактам synthesis/topics/evaluation).
- Расчёт дельты между прошлым и текущим результатом.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

import structlog
from sqlalchemy import desc

from worker.shared.database import GroupDigestStageArtifact, SessionLocal

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BaselineSnapshot:
    """Минимальный набор данных прошлого дайджеста для сравнения."""

    window_id: str
    topics: Sequence[Dict[str, Any]]
    metrics: Dict[str, Any]
    summary_html: str


def load_previous_snapshot(
    tenant_id: str,
    group_id: str,
    current_window_id: str,
) -> Optional[BaselineSnapshot]:
    """
    Загружает самый свежий артефакт дайджеста, кроме текущего окна.

    Возвращает None, если для арендатора/группы нет завершённых артефактов.
    """
    session = SessionLocal()
    try:
        # Сначала пытаемся найти предыдущий синтез.
        record = (
            session.query(GroupDigestStageArtifact)
            .filter(
                GroupDigestStageArtifact.tenant_id == _safe_uuid(tenant_id),
                GroupDigestStageArtifact.group_id == _safe_uuid(group_id),
                GroupDigestStageArtifact.stage == "synthesis_agent",
                GroupDigestStageArtifact.window_id != _safe_uuid(current_window_id),
            )
            .order_by(desc(GroupDigestStageArtifact.updated_at))
            .first()
        )
        if not record:
            return None

        topics_payload = _load_stage_payload(
            session,
            tenant_id,
            group_id,
            record.window_id,
            "topic_agent",
        )
        evaluation_payload = _load_stage_payload(
            session,
            tenant_id,
            group_id,
            record.window_id,
            "evaluation_agent",
        )
        return BaselineSnapshot(
            window_id=str(record.window_id),
            topics=(topics_payload.get("topics") if isinstance(topics_payload, dict) else []) or [],
            metrics=(evaluation_payload.get("evaluation") if isinstance(evaluation_payload, dict) else {}) or {},
            summary_html=(record.payload or {}).get("summary_html", "") if isinstance(record.payload, dict) else "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "baseline.load_previous_snapshot_failed",
            tenant_id=tenant_id,
            group_id=group_id,
            error=str(exc),
        )
        return None
    finally:
        session.close()


def compute_delta(
    previous: Optional[BaselineSnapshot],
    current_topics: Sequence[Dict[str, Any]],
    current_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Вычисляет основные метрики отличий между прошлым и текущим дайджестом.
    """
    if not previous:
        return {
            "has_baseline": False,
            "coverage_change": 0.0,
            "topic_overlap": 0.0,
            "novel_topics": len(current_topics),
            "quality_delta": None,
        }

    prev_titles = {(_normalize_topic(t.get("title"))) for t in previous.topics if t}
    curr_titles = {(_normalize_topic(t.get("title"))) for t in current_topics if t}
    overlap = len(prev_titles & curr_titles)
    overlap_ratio = overlap / max(1, len(prev_titles)) if prev_titles else 0.0

    prev_coverage = sum(int(t.get("msg_count", 0) or 0) for t in previous.topics)
    curr_coverage = sum(int(t.get("msg_count", 0) or 0) for t in current_topics)
    if prev_coverage == 0:
        coverage_change = float(curr_coverage > 0)
    else:
        coverage_change = (curr_coverage - prev_coverage) / max(1, prev_coverage)

    quality_prev = _min_metric(previous.metrics)
    quality_curr = _min_metric(current_metrics)
    quality_delta = None
    if quality_prev is not None and quality_curr is not None:
        quality_delta = round(quality_curr - quality_prev, 4)

    return {
        "has_baseline": True,
        "coverage_change": round(coverage_change, 4),
        "topic_overlap": round(overlap_ratio, 4),
        "novel_topics": max(0, len(curr_titles - prev_titles)),
        "quality_delta": quality_delta,
    }


def _load_stage_payload(
    session,
    tenant_id: str,
    group_id: str,
    window_id: uuid.UUID,
    stage: str,
) -> Dict[str, Any]:
    record = (
        session.query(GroupDigestStageArtifact)
        .filter(
            GroupDigestStageArtifact.tenant_id == _safe_uuid(tenant_id),
            GroupDigestStageArtifact.group_id == _safe_uuid(group_id),
            GroupDigestStageArtifact.window_id == window_id,
            GroupDigestStageArtifact.stage == stage,
        )
        .one_or_none()
    )
    if not record or not isinstance(record.payload, dict):
        return {}
    return record.payload


def _safe_uuid(value: str) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _normalize_topic(title: Optional[str]) -> str:
    return (title or "").strip().lower()


def _min_metric(metrics: Dict[str, Any]) -> Optional[float]:
    if not metrics:
        return None
    candidates = [
        float(metrics.get(key))
        for key in ("faithfulness", "coherence", "coverage", "focus", "quality_score")
        if _is_number(metrics.get(key))
    ]
    if not candidates:
        return None
    return min(candidates)


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(float(value))

