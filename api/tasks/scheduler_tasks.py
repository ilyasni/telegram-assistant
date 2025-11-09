"""
Scheduled tasks для периодической генерации дайджестов и анализа трендов.
Context7: Используем APScheduler для планирования задач
"""

import asyncio
from datetime import datetime, date, time, timezone
from typing import List, Optional
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from prometheus_client import Counter
from sqlalchemy.orm import Session

from models.database import get_db, DigestSettings, User, TrendDetection, DigestHistory, UserInterest
from sqlalchemy import and_
from services.trend_detection_service import get_trend_detection_service
from services.user_interest_service import get_user_interest_service
from services.graph_service import get_graph_service
from config import settings
from middleware.rls_middleware import set_tenant_id_in_session
from worker.event_bus import EventPublisher, RedisStreamsClient, DigestGenerateEvent
from uuid import UUID

logger = structlog.get_logger()

digest_retry_total = Counter(
    'digest_retry_total',
    'Количество повторных попыток отправки дайджеста',
    ['tenant_id']
)

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
                        digest_retry_total.labels(tenant_id=tenant_id).inc()
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
        
        # Отправка уведомлений о трендах пользователям
        if trends:
            await send_trend_alerts_to_users(trends, db)
        
        db.close()
    
    except Exception as e:
        logger.error("Error in trend detection task", error=str(e))


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
                logger.error("Error syncing user interests to Neo4j", user_id=str(user_id), error=str(e))
                error_count += 1
        
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
    
    logger.info("Scheduled tasks configured", tasks=["process_digests", "detect_trends", "sync_user_interests"])


def start_scheduler():
    """Запуск scheduler."""
    global scheduler
    
    if scheduler is None:
        scheduler = init_scheduler()
    
    if not scheduler.running:
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

