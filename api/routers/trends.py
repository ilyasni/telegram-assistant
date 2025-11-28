"""
Trends API endpoints for accessing detected trends.
Context7: фильтрация по min_frequency, min_growth, min_engagement
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Set, Tuple
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Depends, Query, Body
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models.database import (
    get_db,
    TrendDetection,
    TrendCluster,
    TrendMetrics,
    ChatTrendSubscription,
    TrendClusterPost,
    UserChannel,
    UserTrendProfile,
    TrendInteraction,
)
from services.trend_detection_service import get_trend_detection_service
from config import settings
from trends.card_utils import (
    fallback_summary_from_posts,
    fallback_why_from_stats,
    serialize_example_posts,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/trends", tags=["trends"])

ALLOWED_SUBSCRIPTION_FREQUENCIES = {"1h", "3h", "daily"}

# Personalization metrics
try:
    from prometheus_client import Counter, Histogram
    trends_personal_requests_total = Counter(
        "trends_personal_requests_total",
        "Personalized trends API requests",
        ["endpoint", "outcome"],
    )
    trend_qa_filtered_total = Counter(
        "trend_qa_filtered_total",
        "Trends filtered by QA agent",
        ["reason"],
    )
    trend_qa_latency_seconds = Histogram(
        "trend_qa_latency_seconds",
        "Latency of QA agent filtering",
        ["outcome"],
    )
except Exception:  # prometheus not available in some run modes
    class _Noop:
        def labels(self, *args, **kwargs):
            return self
        def inc(self, *args, **kwargs):
            return None
        def observe(self, *args, **kwargs):
            return None
    trends_personal_requests_total = _Noop()
    trend_qa_filtered_total = _Noop()
    trend_qa_latency_seconds = _Noop()
# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class TrendResponse(BaseModel):
    """Ответ с информацией о тренде."""
    id: UUID
    trend_keyword: str
    frequency_count: int
    growth_rate: Optional[float]
    engagement_score: Optional[float]
    first_mentioned_at: Optional[datetime]
    last_mentioned_at: Optional[datetime]
    channels_affected: List[str]
    posts_sample: List[dict]
    detected_at: datetime
    status: str


class TrendListResponse(BaseModel):
    """Ответ со списком трендов."""
    trends: List[TrendResponse]
    total: int
    page: int
    page_size: int


class TrendTimeWindow(BaseModel):
    start: datetime
    end: datetime
    duration_minutes: int


class TrendStats(BaseModel):
    mentions: int
    baseline: int
    burst_score: Optional[float]
    sources: int
    channels: int
    coherence: Optional[float]


class TrendExamplePost(BaseModel):
    post_id: Optional[str]
    channel_id: Optional[str]
    channel_title: Optional[str]
    posted_at: Optional[datetime]
    content_snippet: Optional[str]


class TrendCard(BaseModel):
    title: str
    summary: Optional[str]
    why_important: Optional[str]
    keywords: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    time_window: TrendTimeWindow
    stats: TrendStats
    example_posts: List[TrendExamplePost] = Field(default_factory=list)


class TrendMetricsResponse(BaseModel):
    """Метрики по кластеру тренда."""

    freq_short: int
    freq_long: int
    freq_baseline: Optional[int]
    rate_of_change: Optional[float]
    burst_score: Optional[float]
    source_diversity: Optional[int]
    coherence_score: Optional[float]
    metrics_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TrendClusterResponse(BaseModel):
    """Ответ по кластеру тренда."""

    id: UUID
    cluster_key: str
    status: str
    label: Optional[str]
    summary: Optional[str]
    keywords: List[str]
    primary_topic: Optional[str]
    novelty_score: Optional[float]
    coherence_score: Optional[float]
    source_diversity: Optional[int]
    first_detected_at: Optional[datetime]
    last_activity_at: Optional[datetime]
    resolved_trend_id: Optional[UUID]
    latest_metrics: Optional[TrendMetricsResponse] = None
    card: Optional[TrendCard] = None
    # Context7: Поля для иерархической кластеризации
    parent_cluster_id: Optional[UUID] = None
    cluster_level: int = 1
    subclusters: Optional[List["TrendClusterResponse"]] = None

    model_config = ConfigDict(from_attributes=True)


class TrendClusterListResponse(BaseModel):
    """Ответ со списком кластеров трендов."""

    clusters: List[TrendClusterResponse]
    total: int
    page: int
    page_size: int
    window: Optional[str] = None


class SummarizeClusterRequest(BaseModel):
    cluster_id: UUID
    max_posts: int = Field(default=5, ge=1, le=10)
    force: bool = False


class TrendSubscriptionRequest(BaseModel):
    chat_id: int
    frequency: str = Field(pattern="^(1h|3h|daily)$")
    topics: List[str] = Field(default_factory=list)


class TrendSubscriptionResponse(BaseModel):
    chat_id: int
    frequency: str
    topics: List[str]
    last_sent_at: Optional[datetime]
    is_active: bool


class TrendSubscriptionListResponse(BaseModel):
    subscriptions: List[TrendSubscriptionResponse]



def _load_latest_metrics_map(db: Session, clusters: List[TrendCluster]) -> Dict[UUID, TrendMetrics]:
    """Возвращает map cluster_id -> последний TrendMetrics."""
    metrics_map: Dict[UUID, TrendMetrics] = {}
    for cluster in clusters:
        metric = (
            db.query(TrendMetrics)
            .filter(TrendMetrics.cluster_id == cluster.id)
            .order_by(TrendMetrics.metrics_at.desc())
            .first()
        )
        if metric:
            metrics_map[cluster.id] = metric
    return metrics_map


def _parse_window_param(window: str) -> timedelta:
    """Парсит window вида '30m', '3h', '7d'."""
    if not window:
        return timedelta(hours=1)
    value = window.strip().lower()
    unit = value[-1]
    try:
        amount = int(value[:-1])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid window parameter") from exc
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    raise HTTPException(status_code=422, detail="Unsupported window unit")


# Стоп-слова для фильтрации generic-трендов (синхронизировано с worker)
DEFAULT_TREND_STOPWORDS = {
    "можно", "тащусь", "рублей", "сервис", "крупнейший", "мужчина", "женщина",
    "первый", "просто", "очень", "сегодня", "которые", "начали", "против", "начнут",
}
EXPANDED_STOPWORDS = {
    "это", "как", "так", "его", "еще", "уже", "ли", "или", "для", "при", "без",
    "по", "во", "на", "в", "и", "а", "но", "же", "то", "не", "ни", "да",
    "к", "ко", "из", "под", "над", "от", "до", "если", "то", "чтобы",
    "почти", "вышел", "вышла", "своего", "свои", "наш", "ваш", "их", "его",
    "могут", "может", "нужно", "надо", "будет", "есть", "нет",
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "is", "are", "was", "were",
}
TREND_STOPWORDS = DEFAULT_TREND_STOPWORDS | EXPANDED_STOPWORDS


def _is_generic_trend_keyword(keyword: str) -> bool:
    """
    Проверяет, является ли keyword generic-трендом.
    Использует ту же логику, что и TrendDetectionWorker._is_generic_label.
    """
    if not keyword:
        return True
    lower = keyword.lower().strip()
    if lower in TREND_STOPWORDS:
        return True
    if len(lower) < 4:
        return True
    # одиночное слово без хэштега/сущности
    if " " not in lower and not lower.startswith("#"):
        return True
    return False


def _json_dict(data: Any) -> Dict[str, Any]:
    if not data:
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}
    return {}


def _build_trend_card(cluster: TrendCluster) -> Optional[TrendCard]:
    if not cluster.window_start or not cluster.window_end:
        return None
    duration_minutes = max(
        1, int((cluster.window_end - cluster.window_start).total_seconds() // 60)
    )
    def _is_generic_title(value: Optional[str]) -> bool:
        if not value:
            return True
        s = (value or "").strip()
        if len(s) < 4:
            return True
        low = s.lower()
        generic = {"trend", "тренд", "индивидуальные", "работы", "жизнь летаю"}
        if low in generic:
            return True
        # Проверка на бессмысленные комбинации слов (типа "жизнь летаю")
        if " " in s:
            words = s.split()
            if len(words) == 2:
                # Проверяем, не являются ли оба слова стоп-словами или generic
                if all(w.lower() in TREND_STOPWORDS or len(w) < 4 for w in words):
                    return True
        if " " not in s and not s.startswith("#"):
            return True
        return False

    def _derive_better_title(raw_payload: Dict[str, Any]) -> Optional[str]:
        # 1) из topics: взять 2 самые первые и соединить
        topics = raw_payload.get("topics") or []
        topics = [t.strip() for t in topics if isinstance(t, str) and t.strip()]
        if len(topics) >= 2:
            return f"{topics[0]} — {topics[1]}"[:120]
        if len(topics) == 1 and " " in topics[0]:
            return topics[0][:120]
        # 2) из keywords: склеить 2–3 ключевые
        kws = raw_payload.get("keywords") or []
        kws = [k.strip() for k in kws if isinstance(k, str) and k.strip()]
        if len(kws) >= 2:
            return f"{kws[0]} — {kws[1]}"[:120]
        if len(kws) == 1 and " " in kws[0]:
            return kws[0][:120]
        # 3) из example_posts: взять первые 3–5 слов из первого сниппета
        ex = raw_payload.get("example_posts") or []
        for p in ex:
            snippet = (p.get("content_snippet") or "").strip()
            if snippet:
                words = snippet.replace("\n", " ").split()
                candidate = " ".join(words[:6])
                if len(candidate) >= 10 and " " in candidate:
                    return candidate[:120]
        return None

    stats = TrendStats(
        mentions=cluster.window_mentions or 0,
        baseline=cluster.freq_baseline or 0,
        burst_score=cluster.burst_score,
        sources=cluster.sources_count or cluster.source_diversity or 0,
        channels=cluster.channels_count or cluster.source_diversity or 0,
        coherence=cluster.coherence_score,
    )
    raw_payload = _json_dict(cluster.card_payload)
    # Санитизация заголовка: приоритет primary_topic над label, если label generic
    # Context7: label может быть устаревшим или generic, primary_topic более актуален
    if cluster.label and not _is_generic_title(cluster.label):
        base_title = cluster.label
    elif cluster.primary_topic and not _is_generic_title(cluster.primary_topic):
        base_title = cluster.primary_topic
    else:
        base_title = cluster.label or cluster.primary_topic
    
    if _is_generic_title(base_title):
        derived = _derive_better_title(raw_payload)
        if derived and not _is_generic_title(derived):
            base_title = derived
    if _is_generic_title(base_title):
        # финальный fallback — «Тренд», но лучше короткая фраза из keywords/topics
        base_title = _derive_better_title(raw_payload) or "Тренд"
    example_posts = [
        TrendExamplePost(**post)
        for post in raw_payload.get("example_posts", [])
        if isinstance(post, dict)
    ]
    return TrendCard(
        title=base_title or "Без названия",
        summary=cluster.summary,
        why_important=cluster.why_important,
        keywords=cluster.keywords or [],
        topics=cluster.topics or raw_payload.get("topics", []),
        time_window=TrendTimeWindow(
            start=cluster.window_start,
            end=cluster.window_end,
            duration_minutes=duration_minutes,
        ),
        stats=stats,
        example_posts=example_posts,
    )

def _load_user_channel_ids(db: Session, user_id: UUID) -> Set[UUID]:
    """Возвращает множество channel_id для заданного пользователя."""
    rows = db.query(UserChannel.channel_id).filter(UserChannel.user_id == user_id, UserChannel.is_active == True).all()  # noqa: E712
    return {row[0] for row in rows}

def _personalize_cluster_sample_posts(
    db: Session,
    cluster_id: UUID,
    user_channel_ids: Set[UUID],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    if not user_channel_ids:
        return []
    posts = (
        db.query(TrendClusterPost)
        .filter(TrendClusterPost.cluster_id == cluster_id)
        .filter(TrendClusterPost.channel_id.in_(list(user_channel_ids)))
        .order_by(TrendClusterPost.posted_at.desc().nullslast(), TrendClusterPost.created_at.desc())
        .limit(limit)
        .all()
    )
    results: List[Dict[str, Any]] = []
    for post in posts:
        results.append(
            {
                "post_id": str(post.post_id) if post.post_id else None,
                "channel_id": str(post.channel_id) if post.channel_id else None,
                "channel_title": post.channel_title,
                "posted_at": post.posted_at,
                "content_snippet": post.content_snippet,
            }
        )
    return results

def _apply_personalization(
    db: Session,
    cluster: TrendCluster,
    base_card: TrendCard,
    user_id: Optional[UUID],
) -> TrendCard:
    """Если передан user_id — фильтруем example_posts и пересчитываем mentions/sources/channels."""
    if not user_id:
        return base_card
    user_channels = _load_user_channel_ids(db, user_id)
    if not user_channels:
        # нет подписок — вернём пустую карточку, чтобы далее кластер отфильтровался вызывающим кодом
        return TrendCard(
            title=base_card.title,
            summary=base_card.summary,
            why_important=base_card.why_important,
            keywords=base_card.keywords,
            topics=base_card.topics,
            time_window=base_card.time_window,
            stats=TrendStats(
                mentions=0,
                baseline=base_card.stats.baseline,
                burst_score=base_card.stats.burst_score,
                sources=0,
                channels=0,
                coherence=base_card.stats.coherence,
            ),
            example_posts=[],
        )
    posts = _personalize_cluster_sample_posts(db, cluster.id, user_channels, limit=10)
    mentions = len(posts)
    sources = len({p.get("channel_id") for p in posts if p.get("channel_id")})
    channels = sources
    stats = TrendStats(
        mentions=mentions,
        baseline=base_card.stats.baseline,  # используем глобальный baseline
        burst_score=base_card.stats.burst_score,
        sources=sources,
        channels=channels,
        coherence=base_card.stats.coherence,
    )
    return TrendCard(
        title=base_card.title,
        summary=base_card.summary,
        why_important=base_card.why_important,
        keywords=base_card.keywords,
        topics=base_card.topics,
        time_window=base_card.time_window,
        stats=stats,
        example_posts=[
            TrendExamplePost(**p)
            for p in serialize_example_posts(posts, limit=5)
        ],
    )

def _normalize_topics(topics: List[str]) -> List[str]:
    normalized: List[str] = []
    for topic in topics:
        clean = topic.strip()
        if clean and clean not in normalized:
            normalized.append(clean)
    return normalized[:10]


async def _call_qa_agent(
    cluster: TrendCluster,
    user_id: Optional[UUID],
    user_profile: Optional[Dict[str, Any]],
    db: Session,
) -> Optional[Dict[str, Any]]:
    """
    Context7: QA-агент для оценки качества и релевантности тренда.
    Возвращает решение: показывать ли тренд пользователю.
    """
    qa_start = time.time()
    qa_enabled = os.getenv("TREND_QA_ENABLED", "true").lower() == "true"
    if not qa_enabled:
        return {"should_show": True, "relevance_score": 1.0}

    # Проверка quality_score
    if cluster.quality_score is not None:
        qa_min_score = float(os.getenv("TREND_QA_MIN_SCORE", "0.6"))
        if cluster.quality_score < qa_min_score:
            trend_qa_filtered_total.labels(reason="low_quality_score").inc()
            trend_qa_latency_seconds.labels(outcome="filtered").observe(time.time() - qa_start)
            return {"should_show": False, "relevance_score": cluster.quality_score, "reasoning": "quality_score ниже порога"}

    # Если quality_score хороший, можно пропустить LLM-проверку для скорости
    # Но если есть user_id, лучше проверить релевантность
    if not user_id:
        # Без пользователя - проверяем только базовое качество
        trend_qa_latency_seconds.labels(outcome="success").observe(time.time() - qa_start)
        return {"should_show": True, "relevance_score": cluster.quality_score or 0.8}

    # С пользователем - вызываем LLM для оценки релевантности
    user_channels = _load_user_channel_ids(db, user_id)
    user_channels_list = [str(ch_id) for ch_id in user_channels]

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

    card = _build_trend_card(cluster)
    prompt_payload = {
        "title": card.title if card else cluster.label or cluster.primary_topic,
        "summary": card.summary if card else cluster.summary,
        "topics": card.topics if card else cluster.topics or [],
        "keywords": card.keywords if card else cluster.keywords or [],
        "stats": {
            "mentions": card.stats.mentions if card else cluster.window_mentions or 0,
            "sources": card.stats.sources if card else cluster.sources_count or 0,
            "burst_score": card.stats.burst_score if card else cluster.burst_score,
        },
        "user_channels": user_channels_list[:10],
        "user_interests": user_profile.get("preferred_topics", [])[:10] if user_profile else [],
        "user_categories": user_profile.get("preferred_categories", [])[:10] if user_profile else [],
    }

    system_message = (
        "Ты — QA-фильтр трендов. Оцени карточку:\n"
        "1. Достаточно ли информативна для показа?\n"
        "2. Релевантна ли для пользователя (каналы: {user_channels}, интересы: {user_interests})?\n"
        "3. Нет ли мусорных тем?\n\n"
        "Верни JSON:\n"
        '{\n'
        '  "should_show": true/false,\n'
        '  "relevance_score": 0.0-1.0,\n'
        '  "reasoning": "...",\n'
        '  "user_message": "Для вас сейчас важнее всего: ... потому что ..."\n'
        '}'
    )
    user_message = (
        "Данные по тренду:\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}\n\n"
        "Ответь строго JSON объектом."
    )

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json={
                    "model": os.getenv("TREND_QA_LLM_MODEL", "GigaChat"),
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
            )
        if response.status_code != 200:
            logger.debug("trend_qa_llm_error", status=response.status_code)
            # При ошибке LLM показываем тренд (fail-open)
            return {"should_show": True, "relevance_score": 0.7, "reasoning": "LLM недоступен"}
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content.strip().strip("```json").strip("```"))
        trend_qa_latency_seconds.labels(outcome="success").observe(time.time() - qa_start)
        return parsed
    except Exception as exc:
        logger.debug("trend_qa_llm_failure", error=str(exc))
        trend_qa_latency_seconds.labels(outcome="error").observe(time.time() - qa_start)
        # При ошибке показываем тренд (fail-open)
        return {"should_show": True, "relevance_score": 0.7, "reasoning": f"Ошибка LLM: {str(exc)}"}


def _load_user_profile(db: Session, user_id: UUID) -> Optional[Dict[str, Any]]:
    """Загрузка профиля пользователя."""
    profile = db.query(UserTrendProfile).filter(UserTrendProfile.user_id == user_id).first()
    if not profile:
        return None
    return {
        "preferred_topics": profile.preferred_topics or [],
        "ignored_topics": profile.ignored_topics or [],
        "preferred_categories": profile.preferred_categories or [],
        "typical_time_windows": profile.typical_time_windows or [],
        "interaction_stats": profile.interaction_stats or {},
    }


async def _filter_trends_with_qa(
    clusters: List[TrendCluster],
    user_id: Optional[UUID],
    db: Session,
    limit: int = 20,
) -> List[TrendCluster]:
    """
    Context7: Фильтрация и ранжирование трендов через QA-агента.
    Возвращает top-K трендов, прошедших проверку качества и релевантности.
    """
    qa_enabled = os.getenv("TREND_QA_ENABLED", "true").lower() == "true"
    if not qa_enabled:
        return clusters[:limit]

    user_profile = None
    if user_id:
        user_profile = _load_user_profile(db, user_id)

    filtered: List[Tuple[TrendCluster, float]] = []
    for cluster in clusters:
        qa_result = await _call_qa_agent(cluster, user_id, user_profile, db)
        if not qa_result or not qa_result.get("should_show", True):
            reason = qa_result.get("reasoning", "unknown") if qa_result else "no_result"
            trend_qa_filtered_total.labels(reason=reason[:50]).inc()
            continue
        relevance_score = qa_result.get("relevance_score", 0.5)
        filtered.append((cluster, relevance_score))

    # Сортировка по relevance_score
    filtered.sort(key=lambda x: x[1], reverse=True)
    return [cluster for cluster, _ in filtered[:limit]]


def _subscription_to_response(subscription: ChatTrendSubscription) -> TrendSubscriptionResponse:
    return TrendSubscriptionResponse(
        chat_id=subscription.chat_id,
        frequency=subscription.frequency,
        topics=subscription.topics or [],
        last_sent_at=subscription.last_sent_at,
        is_active=subscription.is_active,
    )


def _cluster_to_response(
    cluster: TrendCluster,
    metric: Optional[TrendMetrics],
) -> TrendClusterResponse:
    """Формирует DTO для кластера тренда."""
    metrics_payload = TrendMetricsResponse.model_validate(metric) if metric else None
    keywords = cluster.keywords or []
    return TrendClusterResponse(
        id=cluster.id,
        cluster_key=cluster.cluster_key,
        status=cluster.status,
        label=cluster.label,
        summary=cluster.summary,
        keywords=keywords,
        primary_topic=cluster.primary_topic,
        novelty_score=cluster.novelty_score,
        coherence_score=cluster.coherence_score,
        source_diversity=cluster.source_diversity,
        first_detected_at=cluster.first_detected_at,
        last_activity_at=cluster.last_activity_at,
        resolved_trend_id=cluster.resolved_trend_id,
        latest_metrics=metrics_payload,
        card=_build_trend_card(cluster),
        # Context7: Поля для иерархической кластеризации
        parent_cluster_id=cluster.parent_cluster_id,
        cluster_level=cluster.cluster_level,
        subclusters=None,  # Загружается отдельно при необходимости
    )


def _cluster_card_to_trend_response(cluster: TrendCluster) -> TrendResponse:
    card = cluster.card_payload or {}
    stats = card.get("stats") or {}
    example_posts = card.get("example_posts") or []
    title = card.get("title") or cluster.label or cluster.primary_topic or "trend"
    return TrendResponse(
        id=cluster.id,
        trend_keyword=title,
        frequency_count=stats.get("mentions") or cluster.window_mentions or 0,
        growth_rate=stats.get("burst_score") or cluster.burst_score,
        engagement_score=float(stats.get("sources") or cluster.sources_count or 0),
        first_mentioned_at=cluster.first_detected_at,
        last_mentioned_at=cluster.last_activity_at,
        channels_affected=[
            post.get("channel_title") or post.get("channel_id")
            for post in example_posts
            if post.get("channel_title") or post.get("channel_id")
        ],
        posts_sample=example_posts,
        detected_at=cluster.last_activity_at or cluster.first_detected_at or datetime.utcnow(),
        status=cluster.status,
    )


def _cluster_window_info(cluster: TrendCluster) -> Dict[str, Any]:
    start = cluster.window_start or cluster.first_detected_at or datetime.utcnow()
    end = cluster.window_end or cluster.last_activity_at or start
    if end < start:
        end = start
    duration_minutes = max(1, int((end - start).total_seconds() // 60) or 1)
    return {"start": start, "end": end, "duration_minutes": duration_minutes}


def _expected_baseline(freq_baseline: Optional[int], window_minutes: int) -> float:
    if not freq_baseline or freq_baseline <= 0:
        return 1.0
    long_window_minutes = 24 * 60
    buckets = max(1, long_window_minutes // max(1, window_minutes))
    return freq_baseline / buckets


def _cluster_stats_payload(cluster: TrendCluster) -> Dict[str, Any]:
    window = _cluster_window_info(cluster)
    mentions = cluster.window_mentions or 0
    baseline = _expected_baseline(cluster.freq_baseline, window["duration_minutes"])
    stats = {
        "mentions": mentions,
        "baseline": max(1, int(round(baseline))),
        "burst_score": cluster.burst_score,
        "sources": cluster.sources_count or cluster.source_diversity or 0,
        "channels": cluster.channels_count or cluster.source_diversity or 0,
        "coherence": cluster.coherence_score,
        "window_minutes": window["duration_minutes"],
    }
    return stats


def _fetch_cluster_posts(db: Session, cluster_id: UUID, limit: int) -> List[Dict[str, Any]]:
    posts = (
        db.query(TrendClusterPost)
        .filter(TrendClusterPost.cluster_id == cluster_id)
        .order_by(TrendClusterPost.posted_at.desc().nullslast(), TrendClusterPost.created_at.desc())
        .limit(limit)
        .all()
    )
    results: List[Dict[str, Any]] = []
    for post in posts:
        results.append(
            {
                "post_id": str(post.post_id) if post.post_id else None,
                "channel_id": str(post.channel_id) if post.channel_id else None,
                "channel_title": post.channel_title,
                "posted_at": post.posted_at,
                "content_snippet": post.content_snippet,
            }
        )
    return results


async def _call_cluster_llm(prompt_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    api_base = (
        getattr(settings, "openai_api_base", None)
        or os.getenv("OPENAI_API_BASE")
        or os.getenv("GIGACHAT_PROXY_URL")
        or "http://gpt2giga-proxy:8090"
    ).rstrip("/")
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
        "Ты — редактор трендовых сюжетов. Получаешь кластеры постов и их статистику. "
        "Верни JSON с title, summary, why_important и topics (до 5 тегов). Пиши по-русски."
    )
    user_message = (
        "Данные по тренду:\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False)}\n\n"
        "Ответь строго JSON объектом."
    )
    body = {
        "model": os.getenv("TREND_CARD_LLM_MODEL", "GigaChat"),
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            content = choices[0]["message"]["content"]
            return json.loads(content)
    except Exception as exc:
        logger.error("cluster_card_llm_failed", error=str(exc))
        return None


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("/emerging", response_model=TrendClusterListResponse)
async def get_emerging_trends(
    min_sources: int = Query(2, ge=0, description="Минимальное количество источников"),
    min_burst: float = Query(1.0, ge=0.0, description="Минимальный burst score"),
    window: str = Query(
        "3h", description="Окно анализа тренда (формат: 30m, 3h, 7d)", pattern="^[0-9]+[mhd]$"
    ),
    page: int = Query(1, ge=1, description="Номер страницы"),
    page_size: int = Query(20, ge=1, le=100, description="Размер страницы"),
    user_id: Optional[UUID] = Query(None, description="Персонализация по user_id"),
    db: Session = Depends(get_db),
):
    """Получить список emerging кластеров трендов."""
    window_delta = _parse_window_param(window)
    # Context7: Для emerging трендов расширяем окно до 24 часов, если трендов в окне window нет
    cutoff = datetime.now(timezone.utc) - window_delta
    # Context7: Для emerging трендов ослабляем фильтры - quality_score может быть NULL
    min_quality = float(os.getenv("TREND_EMERGING_MIN_QUALITY_SCORE", "0.3"))  # Ослабляем для emerging
    base_query = (
        db.query(TrendCluster)
        .filter(TrendCluster.status == "emerging")
        .filter(TrendCluster.is_generic == False)
        .filter(
            # Quality score может быть NULL или >= min_quality (NULL считается валидным для emerging)
            (TrendCluster.quality_score.is_(None)) | (TrendCluster.quality_score >= min_quality)
        )
        # Summary может быть NULL для emerging трендов (еще не обработаны)
        # .filter(TrendCluster.summary.isnot(None))  # Убираем строгий фильтр для emerging
    )
    # Context7: Применяем фильтры по min_sources и min_burst к кластерам TrendCluster
    if min_sources > 0:
        base_query = base_query.filter(TrendCluster.sources_count >= min_sources)
    if min_burst > 0:
        base_query = base_query.filter(TrendCluster.burst_score >= min_burst)
    
    # Context7: Применяем окно, но расширяем до 7 дней, если в окне window нет трендов
    base_query_window = base_query.filter(TrendCluster.last_activity_at >= cutoff)
    clusters_in_window = base_query_window.count()
    if clusters_in_window == 0:
        # Расширяем окно до 7 дней для emerging трендов, если нет свежих
        cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
        logger.info("No emerging clusters in window, expanding to 7 days", window=window, cutoff=cutoff)
        base_query = base_query.filter(TrendCluster.last_activity_at >= cutoff_7d)
    else:
        base_query = base_query_window
    if user_id:
        try:
            trends_personal_requests_total.labels(endpoint="emerging", outcome="requested").inc()
        except Exception:
            pass
    offset = (page - 1) * page_size
    clusters = (
        base_query.order_by(TrendCluster.last_activity_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    # Context7: Fallback на TrendDetection, если кластеров нет
    if not clusters:
        logger.info("No emerging clusters found, falling back to TrendDetection", window=window)
        cutoff = datetime.now(timezone.utc) - window_delta
        # Context7: Берем больше трендов, чтобы после фильтрации generic осталось достаточно
        trend_detections = (
            db.query(TrendDetection)
            .filter(TrendDetection.status == "active")
            .filter(TrendDetection.detected_at >= cutoff)
            .order_by(TrendDetection.detected_at.desc())
            .limit(page_size * 5)  # Увеличиваем лимит для компенсации фильтрации
            .all()
        )
        if trend_detections:
            # Конвертируем TrendDetection в TrendClusterResponse с фильтрацией
            responses = []
            window_minutes = int(window_delta.total_seconds() / 60)  # Используем window вместо разницы first/last
            for td in trend_detections:
                # Context7: Фильтрация generic-слов
                if _is_generic_trend_keyword(td.trend_keyword):
                    continue
                
                # Context7: Фильтрация по min_sources
                source_count = len(td.channels_affected or [])
                if source_count < min_sources:
                    continue
                
                # Context7: Фильтрация по min_burst (ослабляем для emerging - допускаем 0.0 если нет данных)
                burst = td.growth_rate or 0.0
                if min_burst > 0 and burst > 0 and burst < min_burst:
                    continue
                
                # Создаем минимальную карточку с правильным окном
                end_time = td.last_mentioned_at or td.detected_at or datetime.now(timezone.utc)
                start_time = end_time - window_delta
                
                time_window = TrendTimeWindow(
                    start=start_time,
                    end=end_time,
                    duration_minutes=window_minutes
                )
                
                card = TrendCard(
                    title=td.trend_keyword,
                    summary=f"Тренд обнаружен: {td.trend_keyword}",
                    topics=[td.trend_keyword],
                    keywords=[td.trend_keyword],
                    time_window=time_window,
                    stats=TrendStats(
                        mentions=td.frequency_count,
                        baseline=0,
                        burst_score=burst,
                        sources=source_count,
                        channels=source_count,
                        coherence=None
                    ),
                    why_important=f"Частота упоминаний: {td.frequency_count}, Engagement: {td.engagement_score or 0.0}",
                    example_posts=[]
                )
                # Создаем упрощенный TrendClusterResponse
                import uuid
                cluster_key = str(td.id)[:32]  # Используем первые 32 символа UUID как cluster_key
                responses.append(TrendClusterResponse(
                    id=td.id,
                    cluster_key=cluster_key,
                    status="emerging",
                    label=td.trend_keyword,
                    summary=f"Тренд обнаружен: {td.trend_keyword}",
                    primary_topic=td.trend_keyword,
                    keywords=[td.trend_keyword],
                    topics=[td.trend_keyword],
                    novelty_score=None,
                    coherence_score=None,
                    source_diversity=source_count,
                    first_detected_at=td.first_mentioned_at,
                    last_activity_at=td.last_mentioned_at or td.detected_at,
                    resolved_trend_id=None,
                    latest_metrics=None,
                    card=card
                ))
                if len(responses) >= page_size:
                    break  # Ограничиваем количество после фильтрации
            logger.info("Using TrendDetection fallback for emerging trends", count=len(responses), filtered=len(trend_detections) - len(responses))
            return TrendClusterListResponse(
                clusters=responses,
                total=len(responses),
                page=page,
                page_size=page_size,
                window=window,
            )

    # Context7: Фильтрация через QA-агента перед показом
    # Временно отключаем QA-фильтрацию для диагностики - она может отсеивать все тренды
    qa_enabled = os.getenv("TREND_QA_ENABLED", "true").lower() == "true"
    if qa_enabled and len(clusters) > 0:
        # Пробуем QA-фильтрацию, но если она отсеяла все - используем исходные кластеры
        filtered_clusters = await _filter_trends_with_qa(clusters, user_id, db, limit=page_size * 2)
        if len(filtered_clusters) > 0:
            clusters = filtered_clusters
        else:
            logger.warning(
                "QA filter removed all clusters, using original clusters",
                original_count=len(clusters),
                user_id=str(user_id) if user_id else None
            )
            clusters = clusters[:page_size * 2]  # Ограничиваем без QA-фильтрации
    else:
        clusters = clusters[:page_size * 2]

    metrics_map = _load_latest_metrics_map(db, clusters)
    responses: List[TrendClusterResponse] = []
    for cluster in clusters:
        if min_sources and (cluster.source_diversity or 0) < min_sources:
            continue
        metric = metrics_map.get(cluster.id)
        if min_burst and (not metric or (metric.burst_score or 0.0) < min_burst):
            continue
        # базовая карточка
        base = _cluster_to_response(cluster, metric)
        # персонализация (фильтрация по каналам пользователя)
        card = _apply_personalization(db, cluster, base.card, user_id) if base.card else None
        # Context7: Если персонализация дала пустой результат, показываем базовую карточку
        # Это улучшает UX - пользователь видит тренды, даже если они не из его каналов
        if user_id and card and card.stats.mentions <= 0:
            # Используем базовую карточку вместо персонализированной, если персонализация пуста
            card = base.card
            logger.debug(
                "Using base card for cluster (personalization empty)",
                cluster_id=str(cluster.id),
                user_id=str(user_id)
            )
        responses.append(
            TrendClusterResponse(
                **{**base.model_dump(), "card": card or base.card}
            )
        )
    if user_id:
        try:
            trends_personal_requests_total.labels(endpoint="emerging", outcome="success").inc()
        except Exception:
            pass

    return TrendClusterListResponse(
        clusters=responses,
        total=len(responses),
        page=page,
        page_size=page_size,
        window=window,
    )


@router.get("/clusters", response_model=TrendClusterListResponse)
async def list_trend_clusters(
    status: Optional[str] = Query(None, description="Статус кластера (emerging|stable|archived)"),
    window: Optional[str] = Query(
        None, description="Окно анализа тренда (формат: 30m, 3h, 7d)", pattern="^[0-9]+[mhd]$"
    ),
    min_frequency: Optional[int] = Query(None, ge=0, description="Минимум упоминаний (window_mentions)"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    page_size: int = Query(20, ge=1, le=100, description="Размер страницы"),
    user_id: Optional[UUID] = Query(None, description="Персонализация по user_id"),
    include_subtopics: bool = Query(False, description="Включить подтемы (sub-clusters) в ответ"),
    db: Session = Depends(get_db),
):
    """Получить список кластеров трендов."""
    min_quality = float(os.getenv("TREND_MIN_QUALITY_SCORE", "0.5"))
    query = db.query(TrendCluster)
    if status:
        query = query.filter(TrendCluster.status == status)
    # Quality filters
    query = query.filter(TrendCluster.is_generic == False)
    # Context7: Для stable трендов ослабляем фильтр quality_score - если нет stable, fallback на emerging
    if status == "stable":
        query = query.filter(TrendCluster.quality_score >= min_quality)
        query = query.filter(TrendCluster.summary.isnot(None))
    else:
        # Для emerging и других статусов используем ослабленные фильтры
        min_quality_emerging = float(os.getenv("TREND_EMERGING_MIN_QUALITY_SCORE", "0.3"))
        query = query.filter(
            (TrendCluster.quality_score.is_(None)) | (TrendCluster.quality_score >= min_quality_emerging)
        )
        # Summary может быть NULL для emerging
    # Context7: Фильтр по min_frequency (window_mentions)
    if min_frequency and min_frequency > 0:
        query = query.filter(TrendCluster.window_mentions >= min_frequency)
    # Context7: По умолчанию показываем только основные кластеры (не подтемы)
    if not include_subtopics:
        query = query.filter(TrendCluster.cluster_level == 1)
    requested_window = None
    if window:
        window_delta = _parse_window_param(window)
        requested_window = window
        cutoff = datetime.now(timezone.utc) - window_delta
        query = query.filter(TrendCluster.last_activity_at >= cutoff)

    # Context7: Fallback на emerging кластеры, если stable нет и запрошен status=stable
    offset = (page - 1) * page_size
    if status == "stable":
        stable_count = query.count()
        if stable_count == 0:
            logger.info("No stable clusters found, falling back to emerging clusters", user_id=str(user_id) if user_id else None)
            # Переключаемся на emerging кластеры с ослабленными фильтрами
            fallback_query = db.query(TrendCluster)
            fallback_query = fallback_query.filter(TrendCluster.status == "emerging")
            fallback_query = fallback_query.filter(TrendCluster.is_generic == False)
            # Ослабляем фильтр quality_score для emerging
            min_quality_emerging = float(os.getenv("TREND_EMERGING_MIN_QUALITY_SCORE", "0.3"))
            fallback_query = fallback_query.filter(
                (TrendCluster.quality_score.is_(None)) | (TrendCluster.quality_score >= min_quality_emerging)
            )
            # Summary может быть NULL для emerging
            # fallback_query = fallback_query.filter(TrendCluster.summary.isnot(None))  # Убираем для emerging
            if not include_subtopics:
                fallback_query = fallback_query.filter(TrendCluster.cluster_level == 1)
            if window:
                window_delta = _parse_window_param(window)
                cutoff = datetime.now(timezone.utc) - window_delta
                fallback_query = fallback_query.filter(TrendCluster.last_activity_at >= cutoff)
            # Context7: Применяем фильтр min_frequency к fallback на emerging
            if min_frequency and min_frequency > 0:
                fallback_query = fallback_query.filter(TrendCluster.window_mentions >= min_frequency)
            total = fallback_query.count()
            clusters = (
                fallback_query.order_by(TrendCluster.last_activity_at.desc())
                .offset(offset)
                .limit(page_size)
                .all()
            )
            status = "emerging"  # Обновляем статус для ответа
            if clusters:
                logger.info("Using emerging clusters as fallback for stable", count=len(clusters), user_id=str(user_id) if user_id else None)
        else:
            total = stable_count
            clusters = (
                query.order_by(TrendCluster.last_activity_at.desc())
                .offset(offset)
                .limit(page_size)
                .all()
            )
    else:
        total = query.count()
        clusters = (
            query.order_by(TrendCluster.last_activity_at.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
    
    # Context7: Fallback на TrendDetection, если кластеров нет ДО фильтрации
    if total == 0:
        logger.info("No clusters found, falling back to TrendDetection", status=status, window=requested_window)
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)  # По умолчанию 7 дней
        if requested_window:
            window_delta = _parse_window_param(requested_window)
            cutoff = datetime.now(timezone.utc) - window_delta
        
        trend_detections = (
            db.query(TrendDetection)
            .filter(TrendDetection.status == "active")
            .filter(TrendDetection.detected_at >= cutoff)
            .order_by(TrendDetection.detected_at.desc())
            .limit(page_size * 2)  # Берем больше, чтобы после фильтрации осталось достаточно
            .all()
        )
        if trend_detections:
            # Конвертируем TrendDetection в TrendClusterResponse
            responses = []
            for td in trend_detections:
                # Фильтруем generic-тренды
                if _is_generic_trend_keyword(td.trend_keyword):
                    continue
                # Ограничиваем количество после фильтрации
                if len(responses) >= page_size:
                    break
                # Создаем минимальную карточку
                start_time = td.first_mentioned_at or td.detected_at
                end_time = td.last_mentioned_at or td.detected_at
                duration_minutes = int((end_time - start_time).total_seconds() / 60) if start_time and end_time else 0
                
                time_window = TrendTimeWindow(
                    start=start_time,
                    end=end_time,
                    duration_minutes=duration_minutes
                )
                
                card = TrendCard(
                    title=td.trend_keyword,
                    summary=f"Тренд обнаружен: {td.trend_keyword}",
                    topics=[td.trend_keyword],
                    keywords=[td.trend_keyword],
                    time_window=time_window,
                    stats=TrendStats(
                        mentions=td.frequency_count,
                        baseline=0,
                        burst_score=td.growth_rate or 0.0,
                        sources=len(td.channels_affected or []),
                        channels=len(td.channels_affected or []),
                        coherence=None
                    ),
                    why_important=f"Частота упоминаний: {td.frequency_count}, Engagement: {td.engagement_score or 0.0}",
                    example_posts=[]
                )
                # Создаем упрощенный TrendClusterResponse
                import uuid
                cluster_key = str(td.id)[:32]
                responses.append(TrendClusterResponse(
                    id=td.id,
                    cluster_key=cluster_key,
                    status=status or "stable",
                    label=td.trend_keyword,
                    summary=f"Тренд обнаружен: {td.trend_keyword}",
                    primary_topic=td.trend_keyword,
                    keywords=[td.trend_keyword],
                    topics=[td.trend_keyword],
                    novelty_score=None,
                    coherence_score=None,
                    source_diversity=len(td.channels_affected or []),
                    first_detected_at=td.first_mentioned_at,
                    last_activity_at=td.last_mentioned_at or td.detected_at,
                    resolved_trend_id=None,
                    latest_metrics=None,
                    card=card
                ))
            # Context7: Подсчитываем total после фильтрации generic-трендов
            # Берем все тренды и фильтруем в памяти для точного подсчета
            all_trends = (
                db.query(TrendDetection)
                .filter(TrendDetection.status == "active")
                .filter(TrendDetection.detected_at >= cutoff)
                .all()
            )
            total = sum(1 for td in all_trends if not _is_generic_trend_keyword(td.trend_keyword))
            logger.info("Using TrendDetection fallback for clusters", count=len(responses), total=total, filtered_from=len(all_trends))
            return TrendClusterListResponse(
                clusters=responses,
                total=total,
                page=page,
                page_size=page_size,
                window=requested_window,
            )

    # Context7: Фильтрация через QA-агента перед показом
    # Пробуем QA-фильтрацию, но если она отсеяла все - используем исходные кластеры
    qa_enabled = os.getenv("TREND_QA_ENABLED", "true").lower() == "true"
    if qa_enabled and len(clusters) > 0:
        filtered_clusters = await _filter_trends_with_qa(clusters, user_id, db, limit=page_size)
        if len(filtered_clusters) > 0:
            clusters = filtered_clusters
        else:
            logger.warning(
                "QA filter removed all clusters, using original clusters",
                original_count=len(clusters),
                user_id=str(user_id) if user_id else None,
                status=status
            )
            clusters = clusters[:page_size]  # Ограничиваем без QA-фильтрации
    else:
        clusters = clusters[:page_size]
    
    metrics_map = _load_latest_metrics_map(db, clusters)
    responses: List[TrendClusterResponse] = []
    for cluster in clusters:
        base = _cluster_to_response(cluster, metrics_map.get(cluster.id))
        card = _apply_personalization(db, cluster, base.card, user_id) if base.card else None
        # Context7: Если персонализация дала пустой результат, показываем базовую карточку
        if user_id and card and card.stats.mentions <= 0:
            card = base.card
            logger.debug(
                "Using base card for cluster (personalization empty)",
                cluster_id=str(cluster.id),
                user_id=str(user_id)
            )
        
        response_data = {**base.model_dump(), "card": card or base.card}
        
        # Context7: Загружаем подтемы (sub-clusters), если запрошено
        if include_subtopics:
            subclusters = (
                db.query(TrendCluster)
                .filter(TrendCluster.parent_cluster_id == cluster.id)
                .filter(TrendCluster.status == "active")
                .order_by(TrendCluster.last_activity_at.desc())
                .limit(10)  # Ограничиваем количество подтем
                .all()
            )
            if subclusters:
                subcluster_responses = []
                for subcluster in subclusters:
                    sub_metric = metrics_map.get(subcluster.id)
                    sub_base = _cluster_to_response(subcluster, sub_metric)
                    subcluster_responses.append(sub_base)
                response_data["subclusters"] = subcluster_responses
        
        responses.append(TrendClusterResponse(**response_data))
    if user_id:
        try:
            trends_personal_requests_total.labels(endpoint="clusters", outcome="success").inc()
        except Exception:
            pass

    return TrendClusterListResponse(
        clusters=responses,
        total=total,
        page=page,
        page_size=page_size,
        window=requested_window,
    )


@router.get("/clusters/{cluster_id}", response_model=TrendClusterResponse)
async def get_trend_cluster(
    cluster_id: UUID,
    user_id: Optional[UUID] = Query(None, description="Персонализация по user_id"),
    include_subtopics: bool = Query(False, description="Включить подтемы (sub-clusters) в ответ"),
    db: Session = Depends(get_db),
):
    """Получить информацию о кластере тренда."""
    cluster = db.query(TrendCluster).filter(TrendCluster.id == cluster_id).first()
    if not cluster:
        # Context7: Fallback на TrendDetection, если кластер не найден
        trend_detection = db.query(TrendDetection).filter(TrendDetection.id == cluster_id).first()
        if trend_detection:
            # Конвертируем TrendDetection в TrendClusterResponse
            window_delta = timedelta(hours=3)  # По умолчанию 3 часа для emerging
            window_minutes = int(window_delta.total_seconds() / 60)
            end_time = trend_detection.last_mentioned_at or trend_detection.detected_at or datetime.now(timezone.utc)
            start_time = end_time - window_delta
            
            time_window = TrendTimeWindow(
                start=start_time,
                end=end_time,
                duration_minutes=window_minutes
            )
            
            source_count = len(trend_detection.channels_affected or [])
            burst = trend_detection.growth_rate or 0.0
            
            card = TrendCard(
                title=trend_detection.trend_keyword,
                summary=f"Тренд обнаружен: {trend_detection.trend_keyword}",
                topics=[trend_detection.trend_keyword],
                keywords=[trend_detection.trend_keyword],
                time_window=time_window,
                stats=TrendStats(
                    mentions=trend_detection.frequency_count,
                    baseline=0,
                    burst_score=burst,
                    sources=source_count,
                    channels=source_count,
                    coherence=None
                ),
                why_important=f"Частота упоминаний: {trend_detection.frequency_count}, Engagement: {trend_detection.engagement_score or 0.0}",
                example_posts=[]
            )
            
            cluster_key = str(trend_detection.id)[:32]
            return TrendClusterResponse(
                id=trend_detection.id,
                cluster_key=cluster_key,
                status="emerging",
                label=trend_detection.trend_keyword,
                summary=f"Тренд обнаружен: {trend_detection.trend_keyword}",
                primary_topic=trend_detection.trend_keyword,
                keywords=[trend_detection.trend_keyword],
                topics=[trend_detection.trend_keyword],
                novelty_score=None,
                coherence_score=None,
                source_diversity=source_count,
                first_detected_at=trend_detection.first_mentioned_at,
                last_activity_at=trend_detection.last_mentioned_at or trend_detection.detected_at,
                resolved_trend_id=None,
                latest_metrics=None,
                card=card
            )
        raise HTTPException(status_code=404, detail="Trend cluster not found")
    
    # Context7: Проверка через QA-агента (ослаблена - не блокируем показ)
    user_profile = None
    if user_id:
        user_profile = _load_user_profile(db, user_id)
    qa_enabled = os.getenv("TREND_QA_ENABLED", "true").lower() == "true"
    if qa_enabled:
        qa_result = await _call_qa_agent(cluster, user_id, user_profile, db)
        if not qa_result or not qa_result.get("should_show", True):
            # Context7: Не блокируем показ кластера, только логируем
            logger.debug(
                "QA agent filtered cluster, but showing anyway",
                cluster_id=str(cluster.id),
                user_id=str(user_id) if user_id else None,
                reason=qa_result.get("reasoning", "unknown") if qa_result else "no_result"
            )
    
    metric = (
        db.query(TrendMetrics)
        .filter(TrendMetrics.cluster_id == cluster.id)
        .order_by(TrendMetrics.metrics_at.desc())
        .first()
    )
    base = _cluster_to_response(cluster, metric)
    card = _apply_personalization(db, cluster, base.card, user_id) if base.card else None
    # Context7: Если персонализация дала пустой результат, показываем базовую карточку
    if user_id and card and card.stats.mentions <= 0:
        card = base.card
        logger.debug(
            "Using base card for cluster (personalization empty)",
            cluster_id=str(cluster.id),
            user_id=str(user_id)
        )
    
    response_data = {**base.model_dump(), "card": card or base.card}
    
    # Context7: Загружаем подтемы (sub-clusters), если запрошено
    if include_subtopics:
        subclusters = (
            db.query(TrendCluster)
            .filter(TrendCluster.parent_cluster_id == cluster.id)
            .filter(TrendCluster.status == "active")
            .order_by(TrendCluster.last_activity_at.desc())
            .all()
        )
        if subclusters:
            subcluster_responses = []
            for subcluster in subclusters:
                sub_metric = (
                    db.query(TrendMetrics)
                    .filter(TrendMetrics.cluster_id == subcluster.id)
                    .order_by(TrendMetrics.metrics_at.desc())
                    .first()
                )
                sub_base = _cluster_to_response(subcluster, sub_metric)
                subcluster_responses.append(sub_base)
            response_data["subclusters"] = subcluster_responses
    
    return TrendClusterResponse(**response_data)


@router.get("/", response_model=TrendListResponse)
async def get_trends(
    min_frequency: int = Query(5, ge=1, description="Минимум упоминаний за окно"),
    min_growth: float = Query(0.0, ge=0.0, description="Минимальный burst score"),
    min_sources: int = Query(0, ge=0, description="Минимальное число источников"),
    status: Optional[str] = Query("stable", description="Статус кластера (emerging|stable|archived)"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    page_size: int = Query(20, ge=1, le=100, description="Размер страницы"),
    db: Session = Depends(get_db)
):
    """Получить устойчивые тренды на основе кластеров."""
    try:
        cluster_status = status or "stable"
        min_quality = float(os.getenv("TREND_MIN_QUALITY_SCORE", "0.5"))
        query = db.query(TrendCluster)
        if cluster_status:
            query = query.filter(TrendCluster.status == cluster_status)
        # Quality filters
        query = query.filter(TrendCluster.is_generic == False)
        query = query.filter(TrendCluster.quality_score >= min_quality)
        query = query.filter(TrendCluster.summary.isnot(None))
        if min_frequency:
            query = query.filter(TrendCluster.window_mentions >= min_frequency)
        if min_growth > 0:
            query = query.filter(TrendCluster.burst_score >= min_growth)
        if min_sources > 0:
            query = query.filter(TrendCluster.sources_count >= min_sources)

        total = query.count()
        offset = (page - 1) * page_size
        clusters = (
            query.order_by(TrendCluster.last_activity_at.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        if not clusters and cluster_status == "stable":
            fallback_query = (
                db.query(TrendCluster)
                .filter(TrendCluster.status == "emerging")
                .filter(TrendCluster.is_generic == False)
                .filter(TrendCluster.quality_score >= min_quality)
                .filter(TrendCluster.summary.isnot(None))
            )
            fallback_total = fallback_query.count()
            clusters = (
                fallback_query.order_by(TrendCluster.last_activity_at.desc())
                .offset(0)
                .limit(page_size)
                .all()
            )
            if clusters:
                total = fallback_total
                cluster_status = "emerging"
        
        # Context7: Fallback на TrendDetection, если кластеров нет
        if not clusters:
            logger.info("No clusters found, falling back to TrendDetection")
            from datetime import datetime, timedelta, timezone
            cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
            # Context7: Берем больше трендов для компенсации фильтрации generic
            trend_detections = (
                db.query(TrendDetection)
                .filter(TrendDetection.status == "active")
                .filter(TrendDetection.detected_at >= cutoff_7d)
                .order_by(TrendDetection.detected_at.desc())
                .limit(page_size * 3)  # Увеличиваем лимит для компенсации фильтрации
                .all()
            )
            if trend_detections:
                # Конвертируем TrendDetection в TrendResponse с фильтрацией generic-трендов
                trends_response = []
                for td in trend_detections:
                    # Фильтруем generic-тренды
                    if _is_generic_trend_keyword(td.trend_keyword):
                        continue
                    # Ограничиваем количество после фильтрации
                    if len(trends_response) >= page_size:
                        break
                    trends_response.append(TrendResponse(
                        id=td.id,
                        trend_keyword=td.trend_keyword,
                        frequency_count=td.frequency_count,
                        growth_rate=td.growth_rate,
                        engagement_score=td.engagement_score,
                        first_mentioned_at=td.first_mentioned_at,
                        last_mentioned_at=td.last_mentioned_at,
                        channels_affected=td.channels_affected or [],
                        posts_sample=td.posts_sample or [],
                        detected_at=td.detected_at,
                        status=td.status
                    ))
                # Context7: Подсчитываем total после фильтрации generic-трендов
                all_trends = (
                    db.query(TrendDetection)
                    .filter(TrendDetection.status == "active")
                    .filter(TrendDetection.detected_at >= cutoff_7d)
                    .all()
                )
                total = sum(1 for td in all_trends if not _is_generic_trend_keyword(td.trend_keyword))
                logger.info("Using TrendDetection fallback for trends", count=len(trends_response), total=total, filtered_from=len(all_trends))
                return TrendListResponse(
                    trends=trends_response,
                    total=total,
                    page=page,
                    page_size=page_size,
                )
        
        trends_response = [_cluster_card_to_trend_response(cluster) for cluster in clusters]
        
        logger.info(
            "Cluster trends retrieved",
            total=total,
            page=page,
            filters={
                "min_frequency": min_frequency,
                "min_growth": min_growth,
                "min_sources": min_sources,
                "status": cluster_status,
            },
        )
        
        return TrendListResponse(
            trends=trends_response,
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as e:
        logger.error("Error retrieving cluster trends", error=str(e))
        raise HTTPException(status_code=500, detail="Ошибка получения трендов")


@router.post("/summarize_cluster", response_model=TrendClusterResponse)
async def summarize_cluster_card(
    request: SummarizeClusterRequest,
    user_id: Optional[UUID] = Query(None, description="Персонализация по user_id"),
    db: Session = Depends(get_db)
):
    """Принудительно обновить карточку тренда через LLM."""
    cluster = db.query(TrendCluster).filter(TrendCluster.id == request.cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Trend cluster not found")

    if user_id:
        user_channels = _load_user_channel_ids(db, user_id)
        sample_posts = _personalize_cluster_sample_posts(db, cluster.id, user_channels, request.max_posts)
    else:
        sample_posts = _fetch_cluster_posts(db, cluster.id, request.max_posts)
    stats = _cluster_stats_payload(cluster)
    window = _cluster_window_info(cluster)
    existing_card = _json_dict(cluster.card_payload)
    need_refresh = request.force or not existing_card.get("summary")

    if need_refresh:
        prompt_payload = {
            "title": cluster.label or cluster.primary_topic or "Тренд",
            "keywords": cluster.keywords or existing_card.get("keywords") or [],
            "topics": cluster.topics or existing_card.get("topics") or [],
            "window_minutes": stats.get("window_minutes"),
            "mentions": stats.get("mentions"),
            "baseline": stats.get("baseline"),
            "sources": stats.get("sources"),
            "sample_posts": [
                {
                    "source": post.get("channel_title") or post.get("channel_id") or "Источник",
                    "snippet": post.get("content_snippet"),
                    "posted_at": post.get("posted_at").isoformat() if post.get("posted_at") else None,
                }
                for post in sample_posts[:5]
                if post.get("content_snippet")
            ],
        }
        llm_card = await _call_cluster_llm(prompt_payload)
    else:
        llm_card = None

    card_payload = {
        "id": cluster.cluster_key,
        "title": existing_card.get("title") or cluster.label or cluster.primary_topic or "Тренд",
        "summary": existing_card.get("summary"),
        "why_important": existing_card.get("why_important"),
        "keywords": existing_card.get("keywords") or cluster.keywords or [],
        "topics": existing_card.get("topics") or cluster.topics or [],
        "time_window": {
            "from": window["start"].isoformat(),
            "to": window["end"].isoformat(),
            "duration_minutes": window["duration_minutes"],
        },
        "stats": {
            "mentions": stats.get("mentions"),
            "baseline": stats.get("baseline"),
            "burst_score": stats.get("burst_score"),
            "sources": stats.get("sources"),
            "channels": stats.get("channels"),
            "coherence": stats.get("coherence"),
        },
        "example_posts": serialize_example_posts(sample_posts),
    }

    if llm_card:
        card_payload["title"] = llm_card.get("title") or card_payload["title"]
        card_payload["summary"] = llm_card.get("summary") or card_payload["summary"]
        card_payload["why_important"] = llm_card.get("why_important") or card_payload["why_important"]
        if llm_card.get("topics"):
            card_payload["topics"] = _normalize_topics(llm_card["topics"])
    else:
        if not card_payload.get("summary"):
            card_payload["summary"] = fallback_summary_from_posts(sample_posts)
        if not card_payload.get("why_important"):
            card_payload["why_important"] = fallback_why_from_stats(stats)

    cluster.card_payload = card_payload
    cluster.summary = card_payload.get("summary")
    cluster.why_important = card_payload.get("why_important")
    db.commit()
    db.refresh(cluster)
    metric = (
        db.query(TrendMetrics)
        .filter(TrendMetrics.cluster_id == cluster.id)
        .order_by(TrendMetrics.metrics_at.desc())
        .first()
    )
    return _cluster_to_response(cluster, metric)


@router.get("/{trend_id}", response_model=TrendResponse)
async def get_trend(trend_id: UUID, db: Session = Depends(get_db)):
    """Получить информацию о конкретном тренде."""
    trend = db.query(TrendDetection).filter(TrendDetection.id == trend_id).first()
    
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    
    return TrendResponse(
        id=trend.id,
        trend_keyword=trend.trend_keyword,
        frequency_count=trend.frequency_count,
        growth_rate=trend.growth_rate,
        engagement_score=trend.engagement_score,
        first_mentioned_at=trend.first_mentioned_at,
        last_mentioned_at=trend.last_mentioned_at,
        channels_affected=trend.channels_affected,
        posts_sample=trend.posts_sample,
        detected_at=trend.detected_at,
        status=trend.status
    )


@router.post("/{trend_id}/archive")
async def archive_trend(trend_id: UUID, db: Session = Depends(get_db)):
    """Архивировать тренд."""
    trend = db.query(TrendDetection).filter(TrendDetection.id == trend_id).first()
    
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    
    trend.status = "archived"
    db.commit()
    db.refresh(trend)
    
    logger.info("Trend archived", trend_id=str(trend_id))
    
    return {"message": "Trend archived", "trend_id": str(trend_id)}


@router.post("/detect")
async def detect_trends_now(
    days: int = Query(7, ge=1, le=30, description="Количество дней для анализа"),
    min_frequency: int = Query(10, ge=1, description="Минимальная частота упоминаний"),
    min_growth: float = Query(0.2, ge=0.0, description="Минимальный рост"),
    min_engagement: float = Query(5.0, ge=0.0, description="Минимальный engagement score"),
    db: Session = Depends(get_db)
):
    """
    Запустить обнаружение трендов немедленно.
    
    Context7: Использует TrendDetectionService для анализа всех постов.
    """
    try:
        trend_service = get_trend_detection_service()
        
        trends = await trend_service.detect_trends(
            days=days,
            min_frequency=min_frequency,
            min_growth=min_growth,
            min_engagement=min_engagement,
            db=db
        )
        
        logger.info(
            "Trend detection completed",
            trends_count=len(trends),
            days=days
        )
        
        return {
            "message": "Trend detection completed",
            "trends_count": len(trends),
            "trends": [
                {
                    "trend_id": str(t.trend_id),
                    "keyword": t.keyword,
                    "frequency": t.frequency,
                    "engagement_score": t.engagement_score
                }
                for t in trends
            ]
        }
    
    except Exception as e:
        logger.error("Error detecting trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка обнаружения трендов: {str(e)}")


@router.post("/subscriptions", response_model=TrendSubscriptionResponse)
async def upsert_trend_subscription(
    payload: TrendSubscriptionRequest,
    db: Session = Depends(get_db),
):
    """Создать или обновить подписку на тренд-дайджест."""
    if payload.frequency not in ALLOWED_SUBSCRIPTION_FREQUENCIES:
        raise HTTPException(status_code=422, detail="Unsupported frequency")
    topics = _normalize_topics(payload.topics)
    subscription = (
        db.query(ChatTrendSubscription)
        .filter(
            ChatTrendSubscription.chat_id == payload.chat_id,
            ChatTrendSubscription.frequency == payload.frequency,
        )
        .first()
    )
    if subscription:
        subscription.topics = topics
        subscription.is_active = True
        subscription.last_sent_at = None
    else:
        subscription = ChatTrendSubscription(
            chat_id=payload.chat_id,
            frequency=payload.frequency,
            topics=topics,
            is_active=True,
        )
        db.add(subscription)
    db.commit()
    db.refresh(subscription)
    logger.info(
        "Trend subscription upserted",
        chat_id=payload.chat_id,
        frequency=payload.frequency,
        topics=topics,
    )
    return _subscription_to_response(subscription)


@router.get("/subscriptions/{chat_id}", response_model=TrendSubscriptionListResponse)
async def get_trend_subscriptions(chat_id: int, db: Session = Depends(get_db)):
    """Получить список подписок чата."""
    subscriptions = (
        db.query(ChatTrendSubscription)
        .filter(ChatTrendSubscription.chat_id == chat_id)
        .order_by(ChatTrendSubscription.frequency.asc())
        .all()
    )
    return TrendSubscriptionListResponse(
        subscriptions=[_subscription_to_response(sub) for sub in subscriptions]
    )


@router.delete("/subscriptions/{chat_id}/{frequency}", response_model=TrendSubscriptionResponse)
async def disable_trend_subscription(chat_id: int, frequency: str, db: Session = Depends(get_db)):
    """Отключить подписку."""
    subscription = (
        db.query(ChatTrendSubscription)
        .filter(
            ChatTrendSubscription.chat_id == chat_id,
            ChatTrendSubscription.frequency == frequency,
        )
        .first()
    )
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")
    subscription.is_active = False
    db.commit()
    db.refresh(subscription)
    logger.info(
        "Trend subscription disabled",
        chat_id=chat_id,
        frequency=frequency,
    )
    return _subscription_to_response(subscription)


# ============================================================================
# TREND INTERACTIONS ENDPOINTS
# ============================================================================

class TrendInteractionRequest(BaseModel):
    """Запрос на запись взаимодействия с трендом."""
    user_id: UUID
    cluster_id: UUID
    interaction_type: str = Field(..., pattern="^(view|click_details|dismiss|save)$")


@router.post("/interactions", response_model=Dict[str, Any])
async def record_trend_interaction(
    request: TrendInteractionRequest,
    db: Session = Depends(get_db),
):
    """
    Запись взаимодействия пользователя с трендом.
    
    Context7: Идемпотентная запись взаимодействия для построения профилей интересов.
    """
    from services.user_trend_profile_service import get_user_trend_profile_service
    
    # Проверяем существование кластера
    cluster = db.query(TrendCluster).filter(TrendCluster.id == request.cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Trend cluster not found")
    
    # Сохраняем взаимодействие
    interaction = TrendInteraction(
        user_id=request.user_id,
        cluster_id=request.cluster_id,
        interaction_type=request.interaction_type,
    )
    db.add(interaction)
    db.commit()
    db.refresh(interaction)
    
    # Обновляем профиль (асинхронно, не блокируем ответ)
    try:
        profile_service = get_user_trend_profile_service(db)
        profile_service.update_profile_from_interaction(
            request.user_id,
            request.cluster_id,
            request.interaction_type,
        )
    except Exception as exc:
        logger.warning("trend_interaction_profile_update_failed", error=str(exc))
    
    logger.info(
        "Trend interaction recorded",
        user_id=str(request.user_id),
        cluster_id=str(request.cluster_id),
        interaction_type=request.interaction_type,
    )
    
    return {
        "id": str(interaction.id),
        "user_id": str(interaction.user_id),
        "cluster_id": str(interaction.cluster_id),
        "interaction_type": interaction.interaction_type,
        "created_at": interaction.created_at.isoformat(),
    }

# ============================================================================
# VECTOR SEARCH ENDPOINTS
# ============================================================================

@router.get("/{trend_id}/similar", response_model=List[dict])
async def get_similar_trends(
    trend_id: UUID,
    limit: int = Query(10, ge=1, le=50, description="Максимальное количество результатов"),
    threshold: float = Query(0.7, ge=0.0, le=1.0, description="Минимальная similarity"),
    db: Session = Depends(get_db)
):
    """
    Получить похожие тренды по embedding.
    
    Context7: Использует векторный поиск через pgvector с cosine distance.
    """
    try:
        trend_service = get_trend_detection_service()
        
        similar = await trend_service.find_similar_trends(
            trend_id=trend_id,
            limit=limit,
            threshold=threshold,
            db=db
        )
        
        logger.info(
            "Similar trends retrieved",
            trend_id=str(trend_id),
            count=len(similar)
        )
        
        return similar
    
    except Exception as e:
        logger.error("Error retrieving similar trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка поиска похожих трендов: {str(e)}")


class DeduplicateRequest(BaseModel):
    """Запрос на дедупликацию трендов."""
    threshold: float = Field(0.85, ge=0.0, le=1.0, description="Минимальная similarity для дубликатов")


@router.post("/deduplicate")
async def deduplicate_trends(
    request: DeduplicateRequest = Body(...),
    db: Session = Depends(get_db)
):
    """
    Дедупликация трендов по смыслу.
    
    Context7: Находит группы похожих трендов и помечает дубликаты как archived.
    """
    try:
        trend_service = get_trend_detection_service()
        
        result = await trend_service.deduplicate_trends(
            threshold=request.threshold,
            db=db
        )
        
        logger.info(
            "Trend deduplication completed",
            duplicates_found=result.get('duplicates_found', 0),
            trends_archived=result.get('trends_archived', 0)
        )
        
        return result
    
    except Exception as e:
        logger.error("Error deduplicating trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка дедупликации трендов: {str(e)}")


class GroupRequest(BaseModel):
    """Запрос на группировку трендов."""
    trend_ids: List[UUID] = Field(..., description="Список ID трендов для группировки")
    similarity_threshold: float = Field(0.6, ge=0.0, le=1.0, description="Минимальная similarity для включения в группу")


@router.post("/group")
async def group_related_trends(
    request: GroupRequest = Body(...),
    db: Session = Depends(get_db)
):
    """
    Группировка связанных трендов через векторный поиск и Neo4j.
    
    Context7: Использует векторный поиск и Neo4j для анализа связей.
    """
    try:
        trend_service = get_trend_detection_service()
        
        groups = await trend_service.group_related_trends(
            trend_ids=request.trend_ids,
            similarity_threshold=request.similarity_threshold,
            db=db
        )
        
        logger.info(
            "Related trends grouped",
            input_count=len(request.trend_ids),
            groups_count=len(groups)
        )
        
        return {
            "groups": groups,
            "total_groups": len(groups)
        }
    
    except Exception as e:
        logger.error("Error grouping trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка группировки трендов: {str(e)}")


class ClusterRequest(BaseModel):
    """Запрос на кластеризацию трендов."""
    n_clusters: int = Field(10, ge=1, le=50, description="Желаемое количество кластеров")
    min_similarity: float = Field(0.6, ge=0.0, le=1.0, description="Минимальная similarity для включения в кластер")


@router.post("/cluster")
async def cluster_trends(
    request: ClusterRequest = Body(...),
    db: Session = Depends(get_db)
):
    """
    Кластеризация трендов по embedding.
    
    Context7: Использует векторный поиск для формирования кластеров.
    """
    try:
        trend_service = get_trend_detection_service()
        
        clusters = await trend_service.cluster_trends(
            n_clusters=request.n_clusters,
            min_similarity=request.min_similarity,
            db=db
        )
        
        logger.info(
            "Trend clustering completed",
            clusters_count=len(clusters)
        )
        
        return {
            "clusters": clusters,
            "total_clusters": len(clusters)
        }
    
    except Exception as e:
        logger.error("Error clustering trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка кластеризации трендов: {str(e)}")

