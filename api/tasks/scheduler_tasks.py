"""
Scheduled tasks для периодической генерации дайджестов и анализа трендов.
Context7: Используем APScheduler для планирования задач
"""

import asyncio
import os
from datetime import datetime, date, time, timezone, timedelta
from typing import List, Optional, Dict, Any
import json
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from prometheus_client import Counter, Histogram, REGISTRY
from sqlalchemy.orm import Session

from models.database import (
    get_db,
    DigestSettings,
    User,
    TrendDetection,
    TrendCluster,
    TrendMetrics,
    DigestHistory,
    UserInterest,
    Group,
    GroupConversationWindow,
    GroupMessage,
    ChatTrendSubscription,
    UserTrendProfile,
    Tenant,
)
from sqlalchemy import and_
from services.trend_detection_service import get_trend_detection_service
from services.user_interest_service import get_user_interest_service
from services.graph_service import get_graph_service
from services.user_trend_profile_service import get_user_trend_profile_service
import sys
from pathlib import Path
from config import settings
from middleware.rls_middleware import set_tenant_id_in_session
from worker.event_bus import EventPublisher, RedisStreamsClient, DigestGenerateEvent
from uuid import UUID

logger = structlog.get_logger()

# ============================================================================
# PROMETHEUS METRICS для scheduled tasks
# ============================================================================

# Context7: Метрики для trends_stable_task - проверяем, что не дублируются с trends_worker
# Используем префикс "trends_stable_" чтобы избежать конфликтов
# Проверяем существование метрик перед созданием, чтобы избежать дублирования при повторном импорте
def _get_or_create_metric(metric_type, name, *args, **kwargs):
    """Создает метрику только если она еще не существует в REGISTRY."""
    try:
        # Пытаемся получить существующую метрику
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError):
        pass
    
    # Создаем новую метрику
    return metric_type(name, *args, **kwargs)

trends_stable_task_runs_total = _get_or_create_metric(
    Counter,
    "trends_stable_task_runs_total",
    "Total number of trends_stable_task executions",
    ["outcome"],  # outcome: success|error
)

trends_stable_clusters_checked_total = _get_or_create_metric(
    Counter,
    "trends_stable_clusters_checked_total",
    "Total number of clusters checked by trends_stable_task",
)

trends_stable_clusters_promoted_total = _get_or_create_metric(
    Counter,
    "trends_stable_clusters_promoted_total",
    "Total number of clusters promoted to stable status",
)

trends_stable_clusters_skipped_total = _get_or_create_metric(
    Counter,
    "trends_stable_clusters_skipped_total",
    "Total number of clusters skipped by trends_stable_task",
    ["reason"],  # reason: no_metrics|freq_too_low|sources_too_low|burst_too_low|is_generic
)

trends_stable_task_duration_seconds = _get_or_create_metric(
    Histogram,
    "trends_stable_task_duration_seconds",
    "Duration of trends_stable_task execution",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# Добавляем api/worker в путь для импорта
worker_dir = Path(__file__).resolve().parent.parent / "worker"
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

# Context7: Условный импорт Threshold Tuner Agent (требует asyncpg, который может быть недоступен в API)
try:
    from trends_threshold_tuner import create_threshold_tuner_agent
    THRESHOLD_TUNER_AVAILABLE = True
except (ImportError, ModuleNotFoundError) as e:
    # Используем print для логирования, так как logger может быть еще не определен
    import warnings
    warnings.warn(f"Threshold Tuner Agent not available: {e}", ImportWarning)
    create_threshold_tuner_agent = None
    THRESHOLD_TUNER_AVAILABLE = False

SUBSCRIPTION_FREQUENCY_DELTA = {
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "daily": timedelta(days=1),
}

SUBSCRIPTION_WINDOW_LABEL = {
    "1h": "1h",
    "3h": "3h",
    "daily": "3h",
}

STABLE_WINDOW_DELTA = timedelta(days=7)


def _normalize_tenant_id(tenant_id: Optional[str]) -> Optional[str]:
    """Context7: нормализация tenant_id для сравнения в feature-flagах rollout."""
    if not tenant_id:
        return None
    try:
        return str(UUID(str(tenant_id))).lower()
    except (ValueError, TypeError):
        return str(tenant_id).strip().lower()


def _is_group_digest_enabled_for_tenant(tenant_id: str) -> bool:
    """Контроль feature-flag: глобальный toggle или canary allow-list."""
    if settings.digest_agent_enabled:
        return True
    normalized = _normalize_tenant_id(tenant_id)
    if normalized is None:
        return False
    allow_list = {
        _normalize_tenant_id(t)
        for t in getattr(settings, "digest_agent_canary_tenants", []) or []
    }
    return normalized in allow_list

def _register_digest_retry_counter() -> Counter:
    metric_name = 'api_digest_retry_total'
    existing = REGISTRY._names_to_collectors.get(metric_name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    try:
        return Counter(
            'digest_retry_total',
            'Количество повторных попыток отправки дайджеста',
            ['tenant_id'],
            namespace='api',
        )
    except ValueError as exc:
        if "Duplicated timeseries" in str(exc):
            existing = REGISTRY._names_to_collectors.get(metric_name)
            if existing is not None:
                return existing  # type: ignore[return-value]
        raise


digest_retry_counter = _register_digest_retry_counter()

# Глобальный scheduler
scheduler: AsyncIOScheduler = None

_digest_event_publisher: Optional[EventPublisher] = None
_digest_publisher_lock: asyncio.Lock = asyncio.Lock()


def init_scheduler():
    """Инициализация APScheduler."""
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler()
        logger.info("APScheduler initialized")
    return scheduler


async def _get_digest_event_publisher() -> EventPublisher:
    """
    Ленивое создание публикатора событий для очереди дайджестов.
    
    Context7: один экземпляр на процесс, защищённый asyncio.Lock.
    """
    global _digest_event_publisher
    if _digest_event_publisher is not None:
        return _digest_event_publisher
    
    async with _digest_publisher_lock:
        if _digest_event_publisher is None:
            redis_url = getattr(settings, "redis_url", "redis://redis:6379")
            client = RedisStreamsClient(redis_url)
            await client.connect()
            _digest_event_publisher = EventPublisher(client)
            logger.info("Digest event publisher initialized", redis_url=redis_url)
    return _digest_event_publisher


async def generate_digest_for_user(
    user_id: str,
    tenant_id: str,
    topics: List[str],
    db: Session,
    trigger: str = "scheduler",
    requested_by: Optional[str] = None
) -> Optional[DigestHistory]:
    """
    Планирует генерацию дайджеста для конкретного пользователя через очередь.
    
    Context7: fail-fast при отсутствии tenant_id или тем.
    """
    if not tenant_id:
        logger.warning("Cannot enqueue digest without tenant_id", user_id=user_id)
        return None
    
    if not topics:
        logger.debug("Skipping digest enqueue for user without topics", user_id=user_id)
        return None
    
    try:
        if settings.feature_rls_enabled:
            set_tenant_id_in_session(db, tenant_id)
        
        today = date.today()
        user_uuid = UUID(user_id)
        tenant_uuid = UUID(tenant_id)
        
        existing = db.query(DigestHistory).filter(
            and_(
                DigestHistory.user_id == user_uuid,
                DigestHistory.digest_date == today
            )
        ).order_by(DigestHistory.created_at.desc()).first()
        
        force_new = trigger == "manual"

        if existing:
            if existing.status in {"scheduled", "pending", "processing"}:
                if force_new:
                    # Context7: При manual trigger перезаписываем только если прошло достаточно времени
                    # Предотвращаем дубликаты при быстром двойном нажатии
                    from datetime import timezone
                    age_seconds = (datetime.now(timezone.utc) - existing.created_at).total_seconds()
                    if age_seconds < 30:  # Меньше 30 секунд - игнорируем второй запрос
                        logger.warning(
                            "Duplicate digest request ignored (too recent)",
                            user_id=user_id,
                            digest_id=str(existing.id),
                            age_seconds=age_seconds
                        )
                        return existing
                    
                    logger.warning(
                        "Manual trigger overriding in-flight digest",
                        user_id=user_id,
                        digest_id=str(existing.id),
                        status=existing.status,
                    )
                    existing.status = "failed"
                    existing.sent_at = None
                    db.commit()
                    existing = None
                else:
                    logger.debug(
                        "Digest already scheduled or in progress",
                        user_id=user_id,
                        digest_id=str(existing.id),
                        status=existing.status
                    )
                    return existing

            if existing and existing.status == "sent":
                if not force_new:
                    logger.debug(
                        "Digest already sent for today, returning existing",
                        user_id=user_id,
                        digest_id=str(existing.id),
                    )
                    return existing
                else:
                    logger.info(
                        "Manual trigger: creating fresh digest despite existing sent record",
                        user_id=user_id,
                        previous_digest_id=str(existing.id),
                    )
                    existing = None
        
        if existing and existing.status == "failed":
            digest_history = existing
            digest_history.status = "pending"
            digest_history.content = digest_history.content or ""
            digest_history.posts_count = digest_history.posts_count or 0
            digest_history.topics = topics
            digest_history.tenant_id = tenant_uuid
            db.commit()
            db.refresh(digest_history)
            logger.info(
                "Re-scheduling failed digest",
                user_id=user_id,
                digest_id=str(digest_history.id)
            )
        else:
            digest_history = DigestHistory(
                user_id=user_uuid,
                tenant_id=tenant_uuid,
                digest_date=today,
                content="",
                posts_count=0,
                topics=topics,
                status="pending"
            )
            db.add(digest_history)
            db.commit()
            db.refresh(digest_history)
            logger.info(
                "Digest placeholder created",
                user_id=user_id,
                digest_id=str(digest_history.id)
            )
        
        event = DigestGenerateEvent(
            idempotency_key=f"digest:{user_id}:{today.isoformat()}",
            user_id=user_id,
            tenant_id=tenant_id,
            digest_date=today,
            history_id=str(digest_history.id),
            trigger=trigger,
            requested_by=requested_by
        )
        
        publisher = await _get_digest_event_publisher()
        await publisher.publish_event("digests.generate", event)
        
        digest_history.status = "pending"
        digest_history.topics = topics
        db.commit()
        
        logger.info(
            "Digest generation enqueued",
            user_id=user_id,
            tenant_id=tenant_id,
            digest_id=str(digest_history.id),
            trigger=trigger
        )
        return digest_history
    
    except Exception as e:
        logger.error(
            "Error enqueueing digest generation",
            user_id=user_id,
            tenant_id=tenant_id,
            error=str(e)
        )
        raise


async def enqueue_group_digest(
    tenant_id: str,
    user_id: str,
    group_id: str,
    window_size_hours: int,
    delivery_channel: str = "telegram",
    delivery_format: str = "telegram_html",
    trigger: str = "manual",
    requested_by: Optional[str] = None,
):
    """Публикация события на генерацию дайджеста по группе."""
    if not _is_group_digest_enabled_for_tenant(tenant_id):
        logger.info(
            "Group digest rollout blocked for tenant",
            tenant_id=tenant_id,
            trigger=trigger,
        )
        raise PermissionError("Group digest feature is disabled for this tenant")

    if window_size_hours not in (4, 6, 12, 24):
        raise ValueError("window_size_hours должен быть одним из значений: 4, 6, 12 или 24")

    db = next(get_db())
    try:
        tenant_uuid = UUID(tenant_id)
        group_uuid = UUID(group_id)
        user_uuid = UUID(user_id)
        requested_by_uuid = UUID(requested_by) if requested_by else user_uuid

        group: Optional[Group] = (
            db.query(Group)
            .filter(Group.id == group_uuid, Group.tenant_id == tenant_uuid)
            .first()
        )
        if not group:
            raise ValueError("Группа не найдена или принадлежит другому арендатору")

        now_utc = datetime.now(timezone.utc)
        window_start = now_utc - timedelta(hours=window_size_hours)
        window_end = now_utc

        # Context7: Конвертируем timezone-aware datetime в naive UTC для сравнения
        # с naive datetime в БД (posted_at хранится как naive datetime в UTC)
        window_start_naive = window_start.replace(tzinfo=None)
        window_end_naive = window_end.replace(tzinfo=None)

        # Context7: Диагностический запрос - проверяем общее количество сообщений в группе
        total_messages_in_group = (
            db.query(GroupMessage)
            .filter(GroupMessage.group_id == group_uuid)
            .count()
        )

        # Context7: Проверяем сообщения в окне с правильной фильтрацией по времени
        messages_query = (
            db.query(GroupMessage)
            .filter(
                GroupMessage.group_id == group_uuid,
                GroupMessage.posted_at >= window_start_naive,
                GroupMessage.posted_at <= window_end_naive,
            )
        )
        message_count = messages_query.count()
        
        # Context7: Дополнительная диагностика - проверяем сообщения без верхней границы
        messages_after_start = (
            db.query(GroupMessage)
            .filter(
                GroupMessage.group_id == group_uuid,
                GroupMessage.posted_at >= window_start_naive,
            )
            .count()
        )

        participant_count = (
            messages_query.distinct(GroupMessage.sender_tg_id)
            .count()
        )

        # Context7: Детальное логирование для диагностики проблемы с подсчетом
        logger.info(
            "Group digest message count calculation",
            tenant_id=tenant_id,
            group_id=group_id,
            window_size_hours=window_size_hours,
            window_start=window_start_naive.isoformat(),
            window_end=window_end_naive.isoformat(),
            total_messages_in_group=total_messages_in_group,
            messages_after_start=messages_after_start,
            message_count_in_window=message_count,
            participant_count=participant_count,
        )

        # Context7: Предупреждение, если сообщения есть в группе, но не попадают в окно
        if total_messages_in_group > 0 and message_count == 0:
            # Проверяем последнее сообщение в группе для диагностики
            last_message = (
                db.query(GroupMessage)
                .filter(GroupMessage.group_id == group_uuid)
                .order_by(GroupMessage.posted_at.desc())
                .first()
            )
            if last_message:
                last_message_age_hours = (window_end_naive - last_message.posted_at).total_seconds() / 3600.0
                logger.warning(
                    "Messages exist in group but none in window - last message is too old",
                    tenant_id=tenant_id,
                    group_id=group_id,
                    window_size_hours=window_size_hours,
                    window_start=window_start_naive.isoformat(),
                    window_end=window_end_naive.isoformat(),
                    last_message_posted_at=last_message.posted_at.isoformat() if last_message.posted_at else None,
                    last_message_age_hours=round(last_message_age_hours, 2),
                    total_messages_in_group=total_messages_in_group,
                    suggestion=f"Last message is {round(last_message_age_hours, 1)} hours old. Try increasing window_size_hours or check if new messages are being ingested.",
                )

        window = GroupConversationWindow(
            group_id=group_uuid,
            tenant_id=tenant_uuid,
            window_size_hours=window_size_hours,
            window_start=window_start,
            window_end=window_end,
            message_count=message_count,
            participant_count=participant_count,
            status="queued",
        )
        db.add(window)
        db.commit()
        db.refresh(window)

        digest_history = DigestHistory(
            user_id=user_uuid,
            tenant_id=tenant_uuid,
            digest_date=window_end.date(),
            content="",
            posts_count=0,
            topics=[],
            status="pending",
        )
        db.add(digest_history)
        db.commit()
        db.refresh(digest_history)

        event = DigestGenerateEvent(
            idempotency_key=f"group-digest:{group_id}:{window.id}",
            user_id=user_id,
            tenant_id=tenant_id,
            digest_date=window_end.date(),
            history_id=str(digest_history.id),
            trigger=trigger,
            requested_by=str(requested_by_uuid),
            context="group",
            group_id=group_id,
            group_window_id=str(window.id),
            window_size_hours=window_size_hours,
            delivery_channel=delivery_channel,
            delivery_format=delivery_format,
        )

        publisher = await _get_digest_event_publisher()
        await publisher.publish_event("digests.generate", event)

        logger.info(
            "Group digest generation enqueued",
            tenant_id=tenant_id,
            user_id=user_id,
            group_id=group_id,
            window_id=str(window.id),
            history_id=str(digest_history.id),
            trigger=trigger,
        )

        return {
            "history_id": str(digest_history.id),
            "group_window_id": str(window.id),
            "message_count": message_count,
            "participant_count": participant_count,
        }

    finally:
        db.close()


async def process_digests_task():
    """
    Периодическая задача для генерации дайджестов.
    
    Context7: Проверяет всех пользователей с включенными дайджестами и topics,
    вычисляет локальное время по schedule_tz и генерирует дайджесты.
    """
    try:
        from pytz import timezone as pytz_timezone
        
        db = next(get_db())
        
        # Получаем всех пользователей с включенными дайджестами
        digest_settings = db.query(DigestSettings).filter(
            DigestSettings.enabled == True
        ).all()
        
        current_utc = datetime.now(timezone.utc)
        
        for setting in digest_settings:
            try:
                # Проверяем наличие topics
                if not setting.topics or len(setting.topics) == 0:
                    logger.debug("Skipping user without topics", user_id=str(setting.user_id))
                    continue
                
                # Получаем пользователя для tenant_id
                user = db.query(User).filter(User.id == setting.user_id).first()
                if not user:
                    continue
                
                if not user.tenant_id:
                    logger.warning(
                        "Skipping digest scheduling due to missing tenant_id",
                        user_id=str(setting.user_id)
                    )
                    continue

                tenant_id = str(user.tenant_id)
                
                if settings.feature_rls_enabled:
                    set_tenant_id_in_session(db, tenant_id)
                
                # Вычисляем локальное время пользователя
                user_tz = pytz_timezone(setting.schedule_tz)
                local_time = current_utc.astimezone(user_tz).time()
                
                # Проверяем, нужно ли генерировать дайджест сейчас
                schedule_time = setting.schedule_time
                if isinstance(schedule_time, str):
                    schedule_time = time.fromisoformat(schedule_time)
                
                # Проверяем, соответствует ли текущее время расписанию
                # (тик каждые 15 минут, проверяем окно ±5 минут)
                time_diff = abs(
                    (local_time.hour * 60 + local_time.minute) -
                    (schedule_time.hour * 60 + schedule_time.minute)
                )
                
                if time_diff <= 5:  # В пределах 5 минут от расписания
                    # Проверяем, не был ли уже сгенерирован дайджест сегодня
                    from datetime import date
                    today = date.today()
                    existing = db.query(DigestHistory).filter(
                        and_(
                            DigestHistory.user_id == setting.user_id,
                            DigestHistory.digest_date == today
                        )
                    ).order_by(DigestHistory.created_at.desc()).first()

                    retry_cooldown = getattr(settings, "digest_retry_cooldown_min", 15)
                    
                    if existing:
                        if existing.status == "sent":
                            continue

                        if existing.status in {"scheduled", "pending", "processing"}:
                            logger.debug(
                                "Digest already queued, skipping duplicate",
                                user_id=str(setting.user_id),
                                digest_id=str(existing.id),
                                status=existing.status
                            )
                            continue

                        if existing.created_at:
                            created_at = existing.created_at
                            if created_at.tzinfo is None:
                                created_at = created_at.replace(tzinfo=timezone.utc)
                            age_minutes = (current_utc - created_at).total_seconds() / 60.0
                        else:
                            age_minutes = float("inf")

                        if age_minutes < retry_cooldown:
                            logger.debug(
                                "Skip digest retry due to cooldown",
                                user_id=str(setting.user_id),
                                status=existing.status,
                                age_minutes=age_minutes
                            )
                            continue

                        logger.info(
                            "Re-enqueueing failed digest generation",
                            user_id=str(setting.user_id),
                            digest_id=str(existing.id),
                            status=existing.status
                        )
                        digest_retry_counter.labels(tenant_id=tenant_id).inc()
                        await generate_digest_for_user(
                            user_id=str(setting.user_id),
                            tenant_id=tenant_id,
                            topics=setting.topics,
                            db=db,
                            trigger="scheduler_retry"
                        )
                    else:
                        # Генерируем дайджест
                        await generate_digest_for_user(
                            user_id=str(setting.user_id),
                            tenant_id=tenant_id,
                            topics=setting.topics,
                            db=db,
                            trigger="scheduler"
                        )
            
            except Exception as e:
                logger.error("Error processing digest for user", user_id=str(setting.user_id), error=str(e))
                continue
        
        db.close()
        logger.info("Digest processing task completed")
    
    except Exception as e:
        logger.error("Error in digest processing task", error=str(e))


async def detect_trends_task():
    """
    Периодическая задача для анализа трендов.
    
    Context7: Ежедневный батч для обнаружения трендов из всех постов.
    """
    try:
        db = next(get_db())
        
        trend_service = get_trend_detection_service()
        
        trends = await trend_service.detect_trends(
            days=7,
            min_frequency=10,
            min_growth=0.2,
            min_engagement=5.0,
            db=db
        )
        
        logger.info(
            "Trend detection task completed",
            trends_count=len(trends)
        )
        
        # Context7: Отправка уведомлений о трендах пользователям (управляется через TREND_ALERTS_ENABLED)
        alerts_enabled = os.getenv("TREND_ALERTS_ENABLED", "false").lower() == "true"
        if trends and alerts_enabled:
            await send_trend_alerts_to_users(trends, db)
        elif trends and not alerts_enabled:
            logger.debug(
                "Trend alerts disabled via TREND_ALERTS_ENABLED",
                trends_count=len(trends)
            )
        
        db.close()
    
    except Exception as e:
        logger.error("Error in trend detection task", error=str(e))


async def trends_stable_task():
    """
    Почасовая агрегирующая задача: подтверждение стабильных трендов.
    
    Context7: Использует trend_metrics для вычисления baseline и переносит
    emerging кластеры в таблицу TrendDetection.
    """
    import time
    task_start = time.time()
    db = None
    try:
        db = next(get_db())
        min_freq = getattr(settings, "trend_stable_min_freq", 30)
        min_sources = getattr(settings, "trend_stable_min_sources", 3)
        min_burst = getattr(settings, "trend_stable_min_burst", 1.5)

        clusters = db.query(TrendCluster).filter(
            TrendCluster.status.in_(["emerging", "stable"]),
            TrendCluster.is_generic == False
        ).all()

        promoted = 0
        updated = 0
        skipped_no_metrics = 0
        skipped_freq = 0
        skipped_sources = 0
        skipped_burst = 0
        skipped_generic = 0

        for cluster in clusters:
            trends_stable_clusters_checked_total.inc()
            
            metrics = (
                db.query(TrendMetrics)
                .filter(TrendMetrics.cluster_id == cluster.id)
                .order_by(TrendMetrics.metrics_at.desc())
                .first()
            )
            if not metrics:
                skipped_no_metrics += 1
                trends_stable_clusters_skipped_total.labels(reason="no_metrics").inc()
                # Context7: Логируем причину пропуска для диагностики
                logger.debug(
                    "trends_stable_task_skipped",
                    cluster_id=str(cluster.id),
                    reason="no_metrics",
                    label=cluster.label,
                )
                continue

            # Context7: Проверяем пороги и логируем причины пропуска
            if (metrics.freq_long or 0) < min_freq:
                skipped_freq += 1
                trends_stable_clusters_skipped_total.labels(reason="freq_too_low").inc()
                logger.debug(
                    "trends_stable_task_skipped",
                    cluster_id=str(cluster.id),
                    reason="freq_too_low",
                    freq_long=metrics.freq_long,
                    min_freq=min_freq,
                    label=cluster.label,
                )
                continue
            if (metrics.source_diversity or 0) < min_sources:
                skipped_sources += 1
                trends_stable_clusters_skipped_total.labels(reason="sources_too_low").inc()
                logger.debug(
                    "trends_stable_task_skipped",
                    cluster_id=str(cluster.id),
                    reason="sources_too_low",
                    source_diversity=metrics.source_diversity,
                    min_sources=min_sources,
                    label=cluster.label,
                )
                continue
            if (metrics.burst_score or 0) < min_burst:
                skipped_burst += 1
                trends_stable_clusters_skipped_total.labels(reason="burst_too_low").inc()
                logger.debug(
                    "trends_stable_task_skipped",
                    cluster_id=str(cluster.id),
                    reason="burst_too_low",
                    burst_score=metrics.burst_score,
                    min_burst=min_burst,
                    label=cluster.label,
                )
                continue
            if cluster.is_generic:
                skipped_generic += 1
                trends_stable_clusters_skipped_total.labels(reason="is_generic").inc()
                logger.debug(
                    "trends_stable_task_skipped",
                    cluster_id=str(cluster.id),
                    reason="is_generic",
                    label=cluster.label,
                )
                continue

            if cluster.status != "stable":
                cluster.status = "stable"
                updated += 1

            if not cluster.resolved_trend_id:
                trend = TrendDetection(
                    trend_keyword=cluster.label or cluster.primary_topic or "trend",
                    trend_embedding=cluster.trend_embedding,
                    frequency_count=metrics.freq_long or metrics.freq_short,
                    growth_rate=metrics.rate_of_change or metrics.burst_score,
                    engagement_score=None,
                    first_mentioned_at=cluster.first_detected_at,
                    last_mentioned_at=cluster.last_activity_at,
                    channels_affected=[],
                    posts_sample=[],
                    status="active",
                )
                db.add(trend)
                db.flush()
                cluster.resolved_trend_id = trend.id
                promoted += 1
                trends_stable_clusters_promoted_total.inc()

        if promoted or updated:
            db.commit()
        else:
            db.rollback()

        task_duration = time.time() - task_start
        trends_stable_task_duration_seconds.observe(task_duration)
        trends_stable_task_runs_total.labels(outcome="success").inc()

        logger.info(
            "Trends stable task completed",
            clusters_checked=len(clusters),
            clusters_promoted=promoted,
            clusters_updated=updated,
            skipped={
                "no_metrics": skipped_no_metrics,
                "freq_too_low": skipped_freq,
                "sources_too_low": skipped_sources,
                "burst_too_low": skipped_burst,
                "is_generic": skipped_generic,
            },
            thresholds={
                "min_freq": min_freq,
                "min_sources": min_sources,
                "min_burst": min_burst,
            },
            duration_seconds=round(task_duration, 2),
        )
    except Exception as e:
        trends_stable_task_runs_total.labels(outcome="error").inc()
        task_duration = time.time() - task_start
        trends_stable_task_duration_seconds.observe(task_duration)
        if db:
            db.rollback()
        logger.error("Error in trends_stable_task", error=str(e), exc_info=True)


async def update_user_trend_profiles_task():
    """
    Ежедневная задача для обновления профилей интересов пользователей.
    
    Context7: Анализ взаимодействий за последние 30 дней через LLM для построения профилей.
    """
    db = None
    try:
        db = next(get_db())
        personalizer_enabled = os.getenv("TREND_PERSONALIZER_ENABLED", "true").lower() == "true"
        if not personalizer_enabled:
            logger.debug("Trend personalizer disabled, skipping profile update")
            return

        # Получаем пользователей с взаимодействиями за последние 30 дней
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        from models.database import TrendInteraction
        users_with_interactions = (
            db.query(TrendInteraction.user_id)
            .filter(TrendInteraction.created_at >= cutoff)
            .distinct()
            .all()
        )

        updated = 0
        failed = 0
        profile_service = get_user_trend_profile_service(db)

        for (user_id,) in users_with_interactions:
            try:
                profile = await profile_service.build_profile_agent(user_id, days=30)
                profile_service.save_profile(user_id, profile)
                updated += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "trend_profile_update_failed",
                    user_id=str(user_id),
                    error=str(exc),
                )

        logger.info(
            "User trend profiles updated",
            total_users=len(users_with_interactions),
            updated=updated,
            failed=failed,
        )
    except Exception as e:
        if db:
            db.rollback()
        logger.error("Error in update_user_trend_profiles_task", error=str(e), exc_info=True)
    finally:
        if db:
            db.close()


async def analyze_trend_thresholds_task():
    """
    Еженедельная задача для анализа и оптимизации порогов трендов.
    
    Context7: Offline-анализ эффективности порогов, предложения для ручного review.
    """
    tuner = None
    try:
        tuner_enabled = os.getenv("TREND_THRESHOLD_TUNER_ENABLED", "true").lower() == "true"
        if not tuner_enabled:
            logger.debug("Threshold tuner disabled, skipping analysis")
            return

        if not THRESHOLD_TUNER_AVAILABLE or create_threshold_tuner_agent is None:
            logger.warning("Threshold Tuner Agent not available (asyncpg missing), skipping")
            return

        tuner = await create_threshold_tuner_agent()
        suggestions = await tuner.analyze_all_thresholds(period_days=30)

        logger.info(
            "Trend thresholds analyzed",
            suggestions_count=len(suggestions),
            thresholds_analyzed=[s.get("threshold_name") for s in suggestions],
        )
    except Exception as e:
        logger.error("Error in analyze_trend_thresholds_task", error=str(e), exc_info=True)
    finally:
        if tuner:
            await tuner.close()

def _cluster_card_payload(cluster: TrendCluster) -> Dict[str, Any]:
    payload = cluster.card_payload or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    stats = payload.get("stats") or {}
    stats.setdefault("mentions", cluster.window_mentions or 0)
    stats.setdefault("baseline", cluster.freq_baseline or 0)
    stats.setdefault("burst_score", cluster.burst_score)
    stats.setdefault("sources", cluster.sources_count or cluster.source_diversity or 0)
    stats.setdefault("channels", cluster.channels_count or cluster.source_diversity or 0)
    payload["stats"] = stats
    time_window = payload.get("time_window")
    if not time_window and cluster.window_start and cluster.window_end:
        duration_minutes = max(
            1, int((cluster.window_end - cluster.window_start).total_seconds() // 60)
        )
        time_window = {
            "from": cluster.window_start.isoformat(),
            "to": cluster.window_end.isoformat(),
            "duration_minutes": duration_minutes,
        }
    payload["time_window"] = time_window or {}
    payload.setdefault("title", cluster.label or cluster.primary_topic or "Без названия")
    payload.setdefault("summary", cluster.summary)
    payload.setdefault("why_important", cluster.why_important)
    payload.setdefault("keywords", cluster.keywords or [])
    payload.setdefault("topics", cluster.topics or [])
    return payload


def _matches_topics(candidates: List[str], topics_filter: List[str]) -> bool:
    if not topics_filter:
        return True
    haystack = " ".join(candidates).lower()
    return any(topic in haystack for topic in topics_filter)


def _fetch_emerging_cards(
    db: Session,
    now_utc: datetime,
    window_delta: timedelta,
    topics_filter: List[str],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    cutoff = now_utc - window_delta
    clusters = (
        db.query(TrendCluster)
        .filter(TrendCluster.status == "emerging")
        .filter(TrendCluster.last_activity_at >= cutoff)
        .order_by(TrendCluster.burst_score.desc().nullslast(), TrendCluster.last_activity_at.desc())
        .limit(limit * 2)
        .all()
    )
    cards: List[Dict[str, Any]] = []
    for cluster in clusters:
        card = _cluster_card_payload(cluster)
        topics = card.get("topics", []) + card.get("keywords", [])
        if _matches_topics(topics, topics_filter):
            cards.append(card)
        if len(cards) >= limit:
            break
    return cards


def _fetch_stable_trends(
    db: Session,
    now_utc: datetime,
    topics_filter: List[str],
    limit: int = 5,
) -> List[TrendDetection]:
    trends = (
        db.query(TrendDetection)
        .filter(TrendDetection.detected_at >= now_utc - STABLE_WINDOW_DELTA)
        .order_by(
            TrendDetection.engagement_score.desc().nullslast(),
            TrendDetection.detected_at.desc(),
        )
        .limit(limit * 2)
        .all()
    )
    if not topics_filter:
        return trends[:limit]
    filtered: List[TrendDetection] = []
    for trend in trends:
        haystack = " ".join([trend.trend_keyword or ""] + (trend.channels_affected or [])).lower()
        if _matches_topics([haystack], topics_filter):
            filtered.append(trend)
        if len(filtered) >= limit:
            break
    return filtered


def _format_emerging_section(cards: List[Dict[str, Any]], window_label: str) -> str:
    lines = [f"🔥 <b>Горячие тренды за {window_label}</b>"]
    if not cards:
        lines.append("— Нет всплесков")
        return "\n".join(lines)
    for card in cards:
        stats = card.get("stats", {})
        mentions = stats.get("mentions", "—")
        baseline = stats.get("baseline", "—")
        burst = stats.get("burst_score")
        burst_text = f"{burst:.1f}×" if isinstance(burst, (int, float)) else "—"
        lines.append(
            f"<b>{card.get('title')}</b>\n"
            f"• {card.get('why_important') or card.get('summary') or '—'}\n"
            f"• ⏱ {mentions} vs {baseline} | ⚡ {burst_text} | 🗞 {stats.get('sources', '—')}"
        )
    return "\n".join(lines)


def _format_stable_section(trends: List[TrendDetection]) -> str:
    lines = ["🧊 <b>Устойчивые тренды за 7 дней</b>"]
    if not trends:
        lines.append("— Нет устойчивых трендов")
        return "\n".join(lines)
    for trend in trends:
        growth = trend.growth_rate
        growth_text = f"{growth:.1%}" if isinstance(growth, (int, float)) else "—"
        lines.append(
            f"<b>{trend.trend_keyword}</b>\n"
            f"• Частота: {trend.frequency_count} | Рост: {growth_text}"
        )
    return "\n".join(lines)


def _build_trend_digest_message(
    subscription: ChatTrendSubscription,
    db: Session,
    now_utc: datetime,
) -> Optional[str]:
    topics_filter = [topic.lower() for topic in (subscription.topics or [])]
    window_label = SUBSCRIPTION_WINDOW_LABEL.get(subscription.frequency, "3h")
    window_delta = SUBSCRIPTION_FREQUENCY_DELTA.get(
        subscription.frequency, timedelta(hours=3)
    )
    emerging_cards = _fetch_emerging_cards(db, now_utc, window_delta, topics_filter, limit=3)
    stable_trends = _fetch_stable_trends(db, now_utc, topics_filter, limit=3)
    if not emerging_cards and not stable_trends:
        return None
    header = f"📡 <b>Trend Digest · {subscription.frequency}</b>"
    sections = [
        header,
        "",
        _format_emerging_section(emerging_cards, window_label),
        "",
        _format_stable_section(stable_trends),
    ]
    return "\n".join(sections)


async def send_trend_alerts_to_users(trends: List, db: Session):
    """
    Отправка уведомлений о трендах пользователям через Telegram.
    
    Context7: Проверяет, какие пользователи подписаны на каналы/темы, связанные с трендами,
    и отправляет уведомления через бота.
    """
    try:
        from models.database import User, TrendAlert, UserChannel, Channel
        from bot.webhook import bot
        from datetime import timezone
        
        if not bot:
            logger.warning("Bot not initialized, cannot send trend alerts")
            return
        
        alerts_sent = 0
        
        for trend_result in trends:
            trend_id = trend_result.trend_id
            trend_keyword = trend_result.keyword
            channels_affected = trend_result.channels_affected
            
            # Находим пользователей, подписанных на затронутые каналы
            if channels_affected:
                # Получаем channel_id из channels_affected (список UUID строк)
                from uuid import UUID
                channel_ids = [UUID(cid) for cid in channels_affected if cid]
                
                # Находим пользователей, подписанных на эти каналы
                subscribed_users = db.query(User).join(UserChannel).filter(
                    UserChannel.channel_id.in_(channel_ids)
                ).distinct().all()
                
                for user in subscribed_users:
                    if not user.telegram_id:
                        continue
                    
                    # Проверяем, не было ли уже отправлено уведомление
                    existing_alert = db.query(TrendAlert).filter(
                        TrendAlert.user_id == user.id,
                        TrendAlert.trend_id == trend_id
                    ).first()
                    
                    if existing_alert:
                        continue  # Уже отправлено
                    
                    try:
                        # Форматируем сообщение о тренде
                        trend_message = f"📈 <b>Новый тренд обнаружен!</b>\n\n"
                        trend_message += f"<b>Тема:</b> {trend_keyword}\n"
                        trend_message += f"<b>Частота упоминаний:</b> {trend_result.frequency}\n"
                        
                        if trend_result.engagement_score:
                            trend_message += f"<b>Engagement:</b> {trend_result.engagement_score:.1f}\n"
                        
                        if channels_affected:
                            # Получаем названия каналов
                            channels = db.query(Channel).filter(
                                Channel.id.in_(channel_ids[:5])  # Показываем до 5 каналов
                            ).all()
                            if channels:
                                trend_message += f"\n<b>Затронутые каналы:</b>\n"
                                for channel in channels[:5]:
                                    trend_message += f"• {channel.title}\n"
                        
                        # Отправляем уведомление
                        await bot.send_message(
                            chat_id=user.telegram_id,
                            text=trend_message,
                            parse_mode="HTML"
                        )
                        
                        # Сохраняем в БД
                        trend_alert = TrendAlert(
                            user_id=user.id,
                            trend_id=trend_id
                        )
                        db.add(trend_alert)
                        alerts_sent += 1
                        
                        logger.debug(
                            "Trend alert sent",
                            user_id=str(user.id),
                            trend_id=str(trend_id),
                            trend_keyword=trend_keyword
                        )
                    
                    except Exception as e:
                        logger.error(
                            "Error sending trend alert",
                            user_id=str(user.id),
                            trend_id=str(trend_id),
                            error=str(e)
                        )
                        continue
        
        if alerts_sent > 0:
            db.commit()
            logger.info("Trend alerts sent to users", alerts_count=alerts_sent, trends_count=len(trends))
    
    except Exception as e:
        logger.error("Error in send_trend_alerts_to_users", error=str(e))
        if 'db' in locals():
            db.rollback()


async def send_trend_digest_subscriptions_task():
    """Рассылка тренд-дайджестов подписанным чатам."""
    db = None
    try:
        db = next(get_db())
        now_utc = datetime.now(timezone.utc)
        subscriptions = (
            db.query(ChatTrendSubscription)
            .filter(ChatTrendSubscription.is_active.is_(True))
            .all()
        )
        if not subscriptions:
            return
        due: List[ChatTrendSubscription] = []
        for subscription in subscriptions:
            delta = SUBSCRIPTION_FREQUENCY_DELTA.get(
                subscription.frequency, timedelta(hours=3)
            )
            last_sent = subscription.last_sent_at
            if not last_sent or now_utc - last_sent >= delta:
                due.append(subscription)
        if not due:
            return
        from bot.webhook import bot

        if not bot:
            logger.warning("Bot not initialized, cannot send trend digests")
            return

        sent = 0
        for subscription in due:
            message = _build_trend_digest_message(subscription, db, now_utc)
            if not message:
                continue
            try:
                await bot.send_message(subscription.chat_id, message, parse_mode="HTML")
                subscription.last_sent_at = now_utc
                db.commit()
                sent += 1
            except Exception as exc:
                db.rollback()
                logger.error(
                    "Failed to send trend digest",
                    chat_id=subscription.chat_id,
                    frequency=subscription.frequency,
                    error=str(exc),
                )
        if sent:
            logger.info("Trend digests sent", subscriptions=sent)
    except Exception as exc:
        logger.error("Error in send_trend_digest_subscriptions_task", error=str(exc), exc_info=True)
        if db:
            db.rollback()
    finally:
        if db:
            db.close()

async def sync_user_interests_to_neo4j_task():
    """
    Context7: Периодическая синхронизация интересов PostgreSQL → Neo4j.
    
    Читает интересы из PostgreSQL (source of truth) и Redis (pending updates),
    синхронизирует в Neo4j (MERGE операция) с идемпотентностью.
    """
    try:
        db = next(get_db())
        graph_service = get_graph_service()
        user_interest_service = get_user_interest_service()
        
        # Context7: Health check перед синхронизацией
        if not await graph_service.health_check():
            logger.warning("Neo4j unavailable, skipping interests sync")
            return
        
        # Получаем всех пользователей с интересами
        users_with_interests = db.query(UserInterest.user_id).distinct().all()
        
        synced_count = 0
        error_count = 0
        
        for (user_id,) in users_with_interests:
            try:
                # Получаем интересы из PostgreSQL
                interests = await user_interest_service.get_user_interests(user_id, limit=50, db=db)
                
                if not interests:
                    continue
                
                # Синхронизируем каждый интерес в Neo4j
                for interest in interests:
                    topic = interest.get('topic')
                    weight = interest.get('weight', 0.0)
                    
                    if topic and weight > 0:
                        success = await graph_service.update_user_interest(
                            user_id=str(user_id),
                            topic=topic,
                            weight=weight
                        )
                        
                        if success:
                            synced_count += 1
                        else:
                            error_count += 1
                
                logger.debug("User interests synced to Neo4j", user_id=str(user_id), count=len(interests))
                
            except Exception as e:
                logger.error("Error syncing user interests to Neo4j", user_id=str(user_id), error=str(e), exc_info=True)
                error_count += 1
                
                # Context7: DLQ для failed синхронизации интересов - отправка в Redis Stream при ошибках
                try:
                    import redis.asyncio as redis
                    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
                    
                    # Отправляем failed синхронизацию в DLQ stream с детальной информацией
                    await redis_client.xadd(
                        "stream:user_interests.sync.failed",
                        {
                            "user_id": str(user_id),
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "interests_count": str(len(interests) if 'interests' in locals() else 0)
                        },
                        maxlen=10000  # Ограничение размера stream
                    )
                    
                    logger.debug(
                        "Failed sync sent to DLQ",
                        user_id=str(user_id),
                        dlq_stream="stream:user_interests.sync.failed"
                    )
                except Exception as dlq_error:
                    logger.warning(
                        "Failed to send to DLQ",
                        user_id=str(user_id),
                        error=str(dlq_error)
                    )
        
        logger.info(
            "Interests sync completed",
            users_count=len(users_with_interests),
            synced_count=synced_count,
            error_count=error_count
        )
        
        # Context7: Обработка ошибок с retry и DLQ для failed синхронизаций
        if error_count > 0:
            try:
                import redis.asyncio as redis
                redis_client = redis.from_url(settings.redis_url, decode_responses=True)
                
                # Отправляем failed синхронизации в DLQ stream
                for _ in range(error_count):
                    await redis_client.xadd(
                        "stream:user_interests.sync.failed",
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "error_count": str(error_count)
                        },
                        maxlen=1000  # Ограничение размера stream
                    )
                
                logger.info("Failed sync operations sent to DLQ", error_count=error_count)
            except Exception as dlq_error:
                logger.warning("Failed to send to DLQ", error=str(dlq_error))
        
    except Exception as e:
        logger.error("Error in sync_user_interests_to_neo4j_task", error=str(e))
        # Context7: DLQ для failed синхронизаций
        try:
            import redis.asyncio as redis
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            await redis_client.xadd(
                "stream:user_interests.sync.failed",
                {
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                },
                maxlen=1000
            )
        except Exception:
            pass  # Не критично, если DLQ недоступен


async def calculate_tenant_storage_usage_task():
    """
    Периодическая задача для расчета использования storage по tenant из S3.
    
    Context7: Сканирует S3 bucket для расчета использования по tenant_id и обновляет БД.
    Выполняется каждые 6 часов для синхронизации использования.
    """
    try:
        import os
        import asyncpg
        
        # Context7: Импорт worker версии StorageQuotaService для async методов
        # Добавляем путь к worker для импорта
        worker_services_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'worker', 'services'
        ))
        if worker_services_path not in sys.path:
            sys.path.insert(0, worker_services_path)
        
        try:
            from worker.services.storage_quota import StorageQuotaService as WorkerStorageQuotaService
            from api.services.s3_storage import S3StorageService
        except ImportError:
            # Fallback: пробуем через разные пути
            try:
                from services.s3_storage import S3StorageService
            except ImportError:
                logger.error("Failed to import S3StorageService")
                return
            
            try:
                # Добавляем путь к worker
                worker_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'worker'))
                if worker_dir not in sys.path:
                    sys.path.insert(0, worker_dir)
                from worker.services.storage_quota import StorageQuotaService as WorkerStorageQuotaService
            except ImportError as e:
                logger.warning(
                    "Worker StorageQuotaService not available, skipping tenant storage usage calculation",
                    error=str(e)
                )
                return
        
        logger.info("Starting tenant storage usage calculation task")
        
        # Context7: Инициализация S3 Storage Service
        secret_key_value = getattr(settings, 's3_secret_access_key', None)
        if secret_key_value and hasattr(secret_key_value, 'get_secret_value'):
            secret_key_value = secret_key_value.get_secret_value()
        elif not secret_key_value:
            secret_key_value = os.getenv('S3_SECRET_ACCESS_KEY', '')
        
        s3_service = S3StorageService(
            endpoint_url=getattr(settings, 's3_endpoint_url', os.getenv('S3_ENDPOINT_URL', 'https://s3.cloud.ru')),
            access_key_id=getattr(settings, 's3_access_key_id', os.getenv('S3_ACCESS_KEY_ID', '')),
            secret_access_key=secret_key_value,
            bucket_name=getattr(settings, 's3_bucket_name', os.getenv('S3_BUCKET_NAME', 'test-467940')),
            region=getattr(settings, 's3_region', os.getenv('S3_REGION', 'ru-central-1')),
            use_compression=getattr(settings, 's3_use_compression', os.getenv('S3_USE_COMPRESSION', 'true').lower() == 'true')
        )
        
        # Context7: Создание asyncpg pool для StorageQuotaService
        db_pool = None
        try:
            database_url = getattr(settings, 'database_url', os.getenv('DATABASE_URL', ''))
            if not database_url:
                logger.warning("DATABASE_URL not configured, skipping tenant storage usage calculation")
                return
            
            # Конвертируем SQLAlchemy URL в asyncpg DSN
            dsn = database_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql://", "postgresql://")
            db_pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=5,
                command_timeout=30
            )
            logger.debug("AsyncPG pool created for tenant storage usage calculation")
        except Exception as e:
            logger.warning(
                "Failed to create asyncpg pool for tenant storage usage calculation",
                error=str(e)
            )
            return  # Без db_pool нельзя обновить БД
        
        # Context7: Инициализация StorageQuotaService (worker версия)
        quota_service = WorkerStorageQuotaService(
            s3_service=s3_service,
            db_pool=db_pool,
            limits={
                "total_gb": 15.0,
                "emergency_threshold_gb": 14.0,
                "per_tenant_max_gb": 2.0
            }
        )
        
        # Получаем список всех tenant_id из БД
        db = next(get_db())
        try:
            tenants = db.query(Tenant).all()
            tenant_ids = [str(tenant.id) for tenant in tenants]
            
            if not tenant_ids:
                logger.info("No tenants found, skipping storage usage calculation")
                return
            
            logger.info(
                "Calculating storage usage for tenants",
                tenant_count=len(tenant_ids)
            )
            
            # Расчет использования для каждого tenant и типа контента
            content_types = ["media", "vision", "crawl"]
            total_processed = 0
            total_errors = 0
            
            for tenant_id in tenant_ids:
                for content_type in content_types:
                    try:
                        # Context7: Используем метод calculate_and_update_tenant_usage для расчета из S3
                        if hasattr(quota_service, 'calculate_and_update_tenant_usage'):
                            result = await quota_service.calculate_and_update_tenant_usage(
                                tenant_id=tenant_id,
                                content_type=content_type
                            )
                        else:
                            # Fallback для sync версии (не поддерживает calculate_and_update_tenant_usage)
                            logger.debug(
                                "StorageQuotaService does not support calculate_and_update_tenant_usage, skipping",
                                tenant_id=tenant_id,
                                content_type=content_type
                            )
                            continue
                        
                        if result and not result.get("error"):
                            total_processed += 1
                            logger.debug(
                                "Tenant storage usage calculated",
                                tenant_id=tenant_id,
                                content_type=content_type,
                                total_gb=result.get("total_gb", 0.0),
                                objects_count=result.get("objects_count", 0)
                            )
                        else:
                            total_errors += 1
                            logger.warning(
                                "Failed to calculate tenant storage usage",
                                tenant_id=tenant_id,
                                content_type=content_type,
                                error=result.get("error") if result else "Unknown error"
                            )
                    
                    except Exception as e:
                        total_errors += 1
                        logger.error(
                            "Error calculating tenant storage usage",
                            tenant_id=tenant_id,
                            content_type=content_type,
                            error=str(e),
                            error_type=type(e).__name__,
                            exc_info=True
                        )
                        continue
            
            logger.info(
                "Tenant storage usage calculation completed",
                total_processed=total_processed,
                total_errors=total_errors,
                tenant_count=len(tenant_ids)
            )
            
        finally:
            db.close()
            if db_pool:
                await db_pool.close()
    
    except Exception as e:
        logger.error(
            "Failed to run tenant storage usage calculation task",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )


def setup_scheduled_tasks():
    """
    Настройка периодических задач.
    
    Context7:
    - Дайджесты: каждые 15 минут (проверка расписания пользователей)
    - Тренды: ежедневно в 00:00 UTC
    - Синхронизация интересов: каждые N минут (из настроек)
    """
    global scheduler
    
    if scheduler is None:
        scheduler = init_scheduler()
    
    # Дайджесты: каждые 15 минут
    scheduler.add_job(
        process_digests_task,
        trigger=CronTrigger(minute="*/15"),  # Каждые 15 минут
        id="process_digests",
        name="Process user digests",
        replace_existing=True
    )
    
    # Тренды: ежедневно в 00:00 UTC
    scheduler.add_job(
        detect_trends_task,
        trigger=CronTrigger(hour=0, minute=0),  # Полночь UTC
        id="detect_trends",
        name="Detect trends from all posts",
        replace_existing=True
    )
    
    # Context7: Синхронизация интересов PostgreSQL → Neo4j каждые N минут
    sync_interval = getattr(settings, 'neo4j_interest_sync_interval_min', 15)
    scheduler.add_job(
        sync_user_interests_to_neo4j_task,
        trigger=CronTrigger(minute=f'*/{sync_interval}'),
        id="sync_user_interests",
        name="Sync user interests to Neo4j",
        replace_existing=True
    )
    
    scheduler.add_job(
        trends_stable_task,
        trigger=CronTrigger(minute=0),  # каждый час
        id="trends_stable",
        name="Promote stable trends",
        replace_existing=True
    )

    # Context7: Обновление профилей интересов пользователей ежедневно в 02:00 UTC
    scheduler.add_job(
        update_user_trend_profiles_task,
        trigger=CronTrigger(hour=2, minute=0),  # 02:00 UTC
        id="update_user_trend_profiles",
        name="Update user trend profiles",
        replace_existing=True
    )

    # Context7: Анализ порогов трендов еженедельно в воскресенье в 03:00 UTC
    scheduler.add_job(
        analyze_trend_thresholds_task,
        trigger=CronTrigger(day_of_week=6, hour=3, minute=0),  # Воскресенье 03:00 UTC
        id="analyze_trend_thresholds",
        name="Analyze trend thresholds",
        replace_existing=True
    )

    scheduler.add_job(
        send_trend_digest_subscriptions_task,
        trigger=CronTrigger(minute="*/5"),
        id="trend_digest_subscriptions",
        name="Send trend digest subscriptions",
        replace_existing=True,
    )
    
    # Context7: Расчет использования storage по tenant каждые 6 часов
    scheduler.add_job(
        calculate_tenant_storage_usage_task,
        trigger=CronTrigger(hour="*/6"),  # Каждые 6 часов
        id="calculate_tenant_storage_usage",
        name="Calculate tenant storage usage from S3",
        replace_existing=True
    )
    
    logger.info(
        "Scheduled tasks configured",
        tasks=[
            "process_digests",
            "detect_trends",
            "sync_user_interests",
            "trends_stable",
            "trend_digest_subscriptions",
            "calculate_tenant_storage_usage",
        ],
    )


async def start_scheduler():
    """Запуск scheduler (async для работы с AsyncIOScheduler)."""
    global scheduler
    
    if scheduler is None:
        scheduler = init_scheduler()
    
    if not scheduler.running:
        # Context7: AsyncIOScheduler требует запущенного event loop
        # Запускаем scheduler в фоне через asyncio.create_task
        scheduler.start()
        setup_scheduled_tasks()
        logger.info("Scheduler started")
    else:
        logger.warning("Scheduler already running")


def stop_scheduler():
    """Остановка scheduler."""
    global scheduler
    
    if scheduler and scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")

