"""
Digest Worker — обработка событий digests.generate.

Context7: вынос генерации и доставки дайджеста в отдельный воркер с поддержкой
retry, circuit breaker и DLQ.
"""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

import structlog
from prometheus_client import Counter, Histogram
from pathlib import Path
import importlib

logger = structlog.get_logger(__name__)

# -----------------------------------------------------------------------------
# sys.path setup
# -----------------------------------------------------------------------------
WORKER_ROOT = Path(__file__).resolve().parent.parent  # /app/..
PROJECT_ROOT = Path("/opt/telegram-assistant")
PROJECT_API = PROJECT_ROOT / "api"
PROJECT_SHARED = PROJECT_ROOT / "shared" / "python"
FALLBACK_PROJECT = Path("/opt/project")
FALLBACK_API = FALLBACK_PROJECT / "api"
FALLBACK_SHARED = FALLBACK_PROJECT / "shared" / "python"
LOCAL_API = WORKER_ROOT / "api"
LOCAL_SHARED = WORKER_ROOT / "shared" / "python"

preferred_paths = [
    str(WORKER_ROOT),
    str(LOCAL_SHARED),
    str(PROJECT_ROOT),
    str(PROJECT_SHARED),
    str(PROJECT_API),
    str(FALLBACK_PROJECT),
    str(FALLBACK_SHARED),
    str(FALLBACK_API),
    str(LOCAL_API),
]

for path_str in preferred_paths:
    if not path_str:
        continue
    try:
        p = Path(path_str)
        if not p.exists():
            continue
    except Exception:
        pass
    if path_str in sys.path:
        sys.path.remove(path_str)
    sys.path.insert(0, path_str)

# -----------------------------------------------------------------------------
# Ensure API packages accessible under short names
# -----------------------------------------------------------------------------
worker_config = importlib.import_module("config")
api_config = importlib.import_module("api.config")


class _CompositeSettings:
    """Объединяет worker.settings и api.settings, приоритет — worker."""

    __slots__ = ("_primary", "_fallback")

    def __init__(self, primary, fallback):
        object.__setattr__(self, "_primary", primary)
        object.__setattr__(self, "_fallback", fallback)

    def __getattr__(self, name):
        if hasattr(self._primary, name):
            return getattr(self._primary, name)
        if hasattr(self._fallback, name):
            return getattr(self._fallback, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if hasattr(self._primary, name) or not hasattr(self._fallback, name):
            setattr(self._primary, name, value)
        else:
            setattr(self._fallback, name, value)


worker_config.settings = _CompositeSettings(worker_config.settings, api_config.settings)

worker_services_pkg = None
api_services_pkg = None

try:
    worker_services_pkg = importlib.import_module("services")
except ModuleNotFoundError:
    worker_services_pkg = None

try:
    api_services_pkg = importlib.import_module("api.services")
except ModuleNotFoundError as exc:
    api_services_pkg = None
    logger.warning("api.services import failed", error=str(exc))

if worker_services_pkg and api_services_pkg:
    for submodule in ("rag_service",):
        try:
            api_submodule = importlib.import_module(f"api.services.{submodule}")
            sys.modules[f"services.{submodule}"] = api_submodule
            setattr(worker_services_pkg, submodule, api_submodule)
        except ModuleNotFoundError as exc:
            logger.warning("api.services submodule import failed", submodule=submodule, error=str(exc))
elif api_services_pkg and not worker_services_pkg:
    sys.modules["services"] = api_services_pkg

try:
    importlib.import_module("models")
except ModuleNotFoundError:
    try:
        sys.modules["models"] = importlib.import_module("api.models")
    except ModuleNotFoundError as exc:
        logger.warning("api.models import failed", error=str(exc))

# -----------------------------------------------------------------------------
# Imports после настройки путей
# -----------------------------------------------------------------------------
from event_bus import (  # type: ignore # noqa: E402
    ConsumerConfig,
    DigestGenerateEvent,
    DigestContextPreparedEvent,
    EventConsumer,
    EventPublisher,
    RedisStreamsClient,
)
from services.retry_policy import (  # type: ignore # noqa: E402
    DEFAULT_RETRY_CONFIG,
    create_retry_decorator,
)
from shared.utils.circuit_breaker import (  # type: ignore # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpenError,
)
from api.middleware.rls_middleware import set_tenant_id_in_session  # type: ignore # noqa: E402
from api.models.database import SessionLocal, DigestHistory, GroupDigest, GroupConversationWindow, User  # type: ignore # noqa: E402
from api.services.digest_service import get_digest_service  # type: ignore # noqa: E402
from api.services.group_digest_service import (  # type: ignore # noqa: E402
    get_group_digest_service,
    GroupDigestContent,
)
from api.utils.telegram_formatter import markdown_to_telegram_chunks  # type: ignore # noqa: E402
import bot.webhook as webhook_module  # type: ignore # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # type: ignore # noqa: E402
# Context7: Импорт универсальной функции санитизации для Prometheus labels
try:
    from worker.tasks.group_digest_agent import _sanitize_prometheus_label  # type: ignore # noqa: E402
except ImportError:
    # Context7: Fallback - создаем аналогичную функцию для консистентности
    import re
    def _sanitize_prometheus_label(value: Any) -> str:
        """Санитизация значения label для Prometheus (универсальная функция)."""
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

FEATURE_RLS_ENABLED = os.getenv("FEATURE_RLS_ENABLED", "false").lower() == "true"


# -----------------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------------
digest_jobs_processed_total = Counter(
    "digest_jobs_processed_total",
    "Количество обработанных событий digests.generate",
    ["stage", "status"],
)

digest_worker_generation_seconds = Histogram(
    "digest_worker_generation_seconds",
    "Длительность генерации дайджеста в воркере",
    ["status"],
)

digest_worker_send_seconds = Histogram(
    "digest_worker_send_seconds",
    "Длительность отправки дайджеста через Telegram",
    ["status"],
)

group_digest_quality_scores = Histogram(
    "group_digest_evaluation_score",
    "Автоматические оценки качества групповых дайджестов",
    ["metric"],
)


class NonRetryableDigestError(Exception):
    """Исключение для ошибок, по которым нет смысла повторять обработку."""


class DigestWorker:
    """Обработчик событий digests.generate."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.redis_client: Optional[RedisStreamsClient] = None
        self.publisher: Optional[EventPublisher] = None
        self.consumer: Optional[EventConsumer] = None
        self._bot_initialized = False
        self._digest_service = None
        self._group_digest_service = None

        # Retry + circuit breaker
        self._generation_retry = create_retry_decorator(
            config=DEFAULT_RETRY_CONFIG,
            operation_name="digest_generation_worker",
        )
        self._send_retry = create_retry_decorator(
            config=DEFAULT_RETRY_CONFIG,
            operation_name="digest_send_worker",
        )
        self._generation_cb = CircuitBreaker(
            name="digest_generation",
            failure_threshold=3,
            recovery_timeout=90,
        )
        self._send_cb = CircuitBreaker(
            name="digest_send",
            failure_threshold=3,
            recovery_timeout=120,
        )

    async def start(self):
        """Запуск воркера и бесконечное потребление очереди."""
        self.redis_client = RedisStreamsClient(self.redis_url)
        await self.redis_client.connect()
        self.publisher = EventPublisher(self.redis_client)

        consumer_name = f"digest-worker-{os.getpid()}"
        config = ConsumerConfig(
            group_name="digest-workers",
            consumer_name=consumer_name,
            batch_size=5,
            block_time=1000,
            max_retries=3,
            retry_delay=5,
        )
        self.consumer = EventConsumer(self.redis_client, config)

        logger.info(
            "DigestWorker started",
            redis_url=self.redis_url,
            consumer_name=consumer_name,
        )

        await self.consumer.consume_forever("digests.generate", self._handle_event)

    async def _handle_event(self, event_data: Dict[str, Any]) -> None:
        """Основной обработчик события digests.generate."""
        payload = event_data.get("payload") or event_data
        try:
            digest_event = DigestGenerateEvent(**payload)
        except Exception as e:
            logger.error("Failed to parse digest event payload", error=str(e), payload=payload)
            raise

        session = SessionLocal()
        history: Optional[DigestHistory] = None
        start_ts = time.perf_counter()

        try:
            tenant_id = str(digest_event.tenant_id)

            if FEATURE_RLS_ENABLED:
                set_tenant_id_in_session(session, tenant_id)

            if digest_event.history_id:
                try:
                    history = (
                        session.query(DigestHistory)
                        .filter(DigestHistory.id == UUID(digest_event.history_id))
                        .first()
                    )
                except Exception:
                    history = None

            if history is None:
                history = DigestHistory(
                    user_id=UUID(digest_event.user_id),
                    tenant_id=UUID(digest_event.tenant_id),
                    digest_date=digest_event.digest_date,
                    content="",
                    posts_count=0,
                    topics=[],
                    status="pending",
                )
                session.add(history)
                session.commit()
                session.refresh(history)
                logger.info(
                    "Digest history placeholder created by worker",
                    history_id=str(history.id),
                    user_id=digest_event.user_id,
                )

            # Состояние pending уже означает «в очереди / обрабатывается», дополнительное значение не требуется
            session.commit()

            user = session.query(User).filter(User.id == history.user_id).first()
            if not user:
                raise NonRetryableDigestError(f"User not found: {digest_event.user_id}")
            
            if not user.telegram_id:
                logger.warning(
                    "User missing telegram_id for digest delivery",
                    user_id=digest_event.user_id,
                    history_id=str(history.id)
                )
                history.status = "failed"
                history.sent_at = None
                session.commit()
                raise NonRetryableDigestError(f"User {digest_event.user_id} missing telegram_id for digest delivery")

            digest_result = await self._generate_digest(digest_event, session)
            history.content = digest_result.content

            posts_count = getattr(digest_result, "posts_count", None)
            if posts_count is None:
                posts_count = getattr(digest_result, "message_count", 0)
            history.posts_count = posts_count

            topics_value = getattr(digest_result, "topics", [])
            if topics_value and isinstance(topics_value[0], dict):
                history.topics = [topic.get("topic") for topic in topics_value if topic.get("topic")]
            else:
                history.topics = topics_value

            evaluation_scores = getattr(digest_result, "evaluation", {})
            if isinstance(evaluation_scores, dict):
                # Context7: Валидация и санитизация имен метрик для Prometheus
                # Используем универсальную функцию _sanitize_prometheus_label для консистентности
                for metric_name, metric_value in evaluation_scores.items():
                    if isinstance(metric_value, (int, float)):
                        try:
                            # Context7: Санитизация имени метрики для Prometheus labels
                            safe_name = _sanitize_prometheus_label(metric_name)
                            group_digest_quality_scores.labels(metric=safe_name).observe(float(metric_value))
                        except Exception as e:
                            # Context7: Детальное логирование ошибок Prometheus метрик с traceback
                            logger.error(
                                "Failed to record evaluation metric",
                                metric_name=metric_name,
                                error=str(e),
                                error_type=type(e).__name__,
                                traceback=traceback.format_exc(),
                                history_id=str(history.id),
                            )
                logger.info(
                    "Group digest auto-evaluation",
                    history_id=str(history.id),
                    scores=evaluation_scores,
                )
            session.commit()

            await self._send_digest(
                user,
                history,
                digest_result.content,
                posts_count,
                session,
                group_digest_id=getattr(digest_result, "digest_id", None),
                delivery_channel=digest_event.delivery_channel or "telegram",
            )

            # Context7: Санитизация значений метрик для Prometheus labels
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("complete"), 
                status=_sanitize_prometheus_label("success")
            ).inc()
            logger.info(
                "Digest processed successfully",
                history_id=str(history.id),
                user_id=digest_event.user_id,
                tenant_id=tenant_id,
            )

        except NonRetryableDigestError as e:
            if history:
                history.status = "failed"
                history.sent_at = None
                session.commit()
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("complete"), 
                status=_sanitize_prometheus_label("non_retryable")
            ).inc()
            logger.warning(
                "Digest processing failed (non-retryable)",
                user_id=digest_event.user_id,
                tenant_id=digest_event.tenant_id,
                error=str(e),
            )
        except CircuitBreakerOpenError as e:
            if history:
                history.status = "failed"
                session.commit()
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("complete"), 
                status=_sanitize_prometheus_label("circuit_open")
            ).inc()
            if self.publisher:
                await self.publisher.to_dlq(
                    base_event_name="digests.generate",
                    payload=payload,
                    reason="circuit_breaker_open",
                    details=str(e),
                    retry_count=0,
                )
            logger.error(
                "Circuit breaker open during digest processing",
                user_id=digest_event.user_id,
                tenant_id=digest_event.tenant_id,
            )
        except Exception as e:
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("complete"), 
                status=_sanitize_prometheus_label("error")
            ).inc()
            logger.error(
                "Digest processing failed",
                user_id=digest_event.user_id,
                tenant_id=digest_event.tenant_id,
                error=str(e),
            )
            raise
        finally:
            processing_duration = time.perf_counter() - start_ts
            logger.info(
                "Digest event processed",
                user_id=digest_event.user_id,
                tenant_id=digest_event.tenant_id,
                duration_seconds=processing_duration,
            )
            session.close()

    async def _generate_digest(self, digest_event: DigestGenerateEvent, session) -> Any:
        """Генерация дайджеста с retry и circuit breaker."""

        is_group_context = digest_event.context == "group" or digest_event.group_window_id is not None
        digest_service = self._get_digest_service()
        group_digest_service = self._get_group_digest_service() if is_group_context else None

        # Context7: Для групповых дайджестов существующий дайджест будет использован как baseline
        # для сравнения при генерации нового. Это позволяет создать секцию "По сравнению с прошлым окном"
        # Вся логика загрузки baseline перенесена в group_digest_service.generate()

        @self._generation_retry
        async def _run_generation():
            if is_group_context:
                if not digest_event.group_window_id:
                    raise NonRetryableDigestError("group_window_id обязателен для группового дайджеста")
                return await self._generation_cb.call_async(
                    group_digest_service.generate,
                    tenant_id=str(digest_event.tenant_id),
                    group_window_id=UUID(digest_event.group_window_id),
                    db=session,
                    requested_by_user_id=UUID(digest_event.user_id),
                    delivery_channel=digest_event.delivery_channel or "telegram",
                    delivery_format=digest_event.delivery_format or "markdown",
                )

            return await self._generation_cb.call_async(
                digest_service.generate,
                user_id=UUID(digest_event.user_id),
                tenant_id=str(digest_event.tenant_id),
                db=session,
                digest_date=digest_event.digest_date,
            )

        start = time.perf_counter()
        try:
            result = await _run_generation()
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_worker_generation_seconds.labels(status=_sanitize_prometheus_label("success")).observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("generate"), 
                status=_sanitize_prometheus_label("success")
            ).inc()
            if is_group_context:
                await self._publish_context_prepared_event(digest_event, result)
            return result
        except Exception as e:
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_worker_generation_seconds.labels(status=_sanitize_prometheus_label("failed")).observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("generate"), 
                status=_sanitize_prometheus_label("failed")
            ).inc()
            if isinstance(e, ValueError):
                raise NonRetryableDigestError(str(e)) from e
            raise

    async def _publish_context_prepared_event(
        self,
        digest_event: DigestGenerateEvent,
        digest_result: GroupDigestContent,
    ) -> None:
        """Публикация события digest.context.prepared для observability."""
        if not self.publisher:
            return

        stats = getattr(digest_result, "context_stats", {}) or {}
        if not stats:
            return

        window_id = getattr(digest_result, "window_id", None)
        group_id = getattr(digest_result, "group_id", None)
        if not window_id or not group_id:
            return

        try:
            event = DigestContextPreparedEvent(
                idempotency_key=f"context:{window_id}",
                tenant_id=str(digest_event.tenant_id),
                group_id=str(group_id),
                window_id=str(window_id),
                message_total=int(stats.get("original_messages", stats.get("message_total", 0))),
                deduplicated_messages=int(stats.get("deduplicated_messages", stats.get("message_total", 0))),
                duplicates_removed=int(stats.get("duplicates_removed", 0)),
                trimmed_for_max=int(stats.get("trimmed_for_max", 0)),
                historical_matches=int(stats.get("historical_matches", 0)),
                top_ranked=int(stats.get("top_ranked", len(getattr(digest_result, "context_ranking", []) or []))),
                stats=stats,
                sample_messages=(getattr(digest_result, "context_ranking", []) or [])[:5],
            )
            await self.publisher.publish_event("digest.context.prepared", event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "digest_context_event_publish_failed",
                error=str(exc),
                tenant_id=digest_event.tenant_id,
                group_id=group_id,
                window_id=window_id,
            )

    async def _send_digest(
        self,
        user: User,
        history: DigestHistory,
        content: str,
        posts_count: int,
        session,
        *,
        group_digest_id: Optional[str] = None,
        delivery_channel: str = "telegram",
    ) -> None:
        """Отправка дайджеста пользователю через Telegram."""
        if delivery_channel != "telegram":
            raise NonRetryableDigestError(f"Delivery channel '{delivery_channel}' is not supported yet")
        if not content:
            raise NonRetryableDigestError("Digest content is empty, nothing to send")

        bot = await self._ensure_bot()
        if not bot:
            raise NonRetryableDigestError("Telegram bot is not initialized")

        # Context7: Для групповых дайджестов content уже содержит HTML (summary_html),
        # не нужно конвертировать из Markdown. Используем split_for_telegram напрямую.
        if group_digest_id:
            # Групповой дайджест: content уже HTML, используем split_for_telegram
            from utils.telegram_formatter import split_for_telegram
            digest_chunks = split_for_telegram(content, limit=4096)
        else:
            # Обычный дайджест: content в Markdown, конвертируем в HTML
            digest_chunks = markdown_to_telegram_chunks(content)

        async def _do_send():
            for idx, chunk in enumerate(digest_chunks):
                prefix = (
                    f"📰 <b>Ваш дайджест за {history.digest_date}</b>\n\n"
                    if idx == 0
                    else ""
                )
                suffix = (
                    f"\n\n📊 <i>Найдено постов: {posts_count}</i>"
                    if idx == len(digest_chunks) - 1 and posts_count > 0
                    else ""
                )
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=prefix + chunk + suffix,
                    parse_mode="HTML",
                )

        @self._send_retry
        async def _send_with_cb():
            return await self._send_cb.call_async(_do_send)

        start = time.perf_counter()
        try:
            await _send_with_cb()
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_worker_send_seconds.labels(status=_sanitize_prometheus_label("success")).observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("send"), 
                status=_sanitize_prometheus_label("success")
            ).inc()

            history.status = "sent"
            history.sent_at = datetime.now(timezone.utc)
            session.commit()

            if group_digest_id:
                self._mark_group_digest_sent(session, group_digest_id, delivery_channel, status="sent")
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_worker_send_seconds.labels(status=_sanitize_prometheus_label("telegram_error")).observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("send"), 
                status=_sanitize_prometheus_label("telegram_error")
            ).inc()

            history.status = "failed"
            history.sent_at = None
            session.commit()
            if group_digest_id:
                self._mark_group_digest_sent(session, group_digest_id, delivery_channel, status="failed", error=str(e))
            raise NonRetryableDigestError(str(e)) from e
        except Exception as e:
            # Context7: Санитизация значений метрик для Prometheus labels
            digest_worker_send_seconds.labels(status=_sanitize_prometheus_label("failed")).observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(
                stage=_sanitize_prometheus_label("send"), 
                status=_sanitize_prometheus_label("failed")
            ).inc()
            if group_digest_id:
                self._mark_group_digest_sent(session, group_digest_id, delivery_channel, status="failed", error=str(e))
            raise

    async def _ensure_bot(self):
        """Ленивая инициализация aiogram бота."""
        if self._bot_initialized and webhook_module.bot:
            return webhook_module.bot

        webhook_module.init_bot()
        bot = webhook_module.bot

        if bot:
            self._bot_initialized = True
            return bot

        raise NonRetryableDigestError("Failed to initialize Telegram bot")

    def _get_digest_service(self):
        """Ленивая инициализация DigestService."""
        if self._digest_service is None:
            self._digest_service = get_digest_service()
        return self._digest_service

    def _get_group_digest_service(self):
        """Ленивая инициализация GroupDigestService."""
        if self._group_digest_service is None:
            self._group_digest_service = get_group_digest_service()
        return self._group_digest_service

    def _mark_group_digest_sent(
        self,
        session,
        digest_id: str,
        channel: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Обновление статуса доставки группового дайджеста."""
        try:
            digest = session.query(GroupDigest).filter(GroupDigest.id == UUID(digest_id)).first()
            if not digest:
                return

            digest.delivery_status = status
            metadata = dict(digest.delivery_metadata or {})
            metadata.update(
                {
                    "channel": channel,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            if error:
                metadata["error"] = error
            digest.delivery_metadata = metadata

            if status == "sent":
                digest.delivered_at = datetime.now(timezone.utc)
            else:
                digest.delivered_at = None

            session.commit()
        except Exception as exc:
            logger.warning(
                "Failed to update group digest delivery status",
                digest_id=digest_id,
                error=str(exc),
            )


async def create_digest_worker_task() -> DigestWorker:
    """Фабрика для supervisor."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    worker = DigestWorker(redis_url=redis_url)
    await worker.start()
    return worker

