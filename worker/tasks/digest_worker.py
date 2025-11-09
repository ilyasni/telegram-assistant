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
from api.models.database import SessionLocal, DigestHistory, User  # type: ignore # noqa: E402
from api.services.digest_service import get_digest_service  # type: ignore # noqa: E402
from api.utils.telegram_formatter import markdown_to_telegram_chunks  # type: ignore # noqa: E402
import bot.webhook as webhook_module  # type: ignore # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # type: ignore # noqa: E402

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
            if not user or not user.telegram_id:
                raise NonRetryableDigestError("User missing telegram_id for digest delivery")

            digest_result = await self._generate_digest(digest_event, session)
            history.content = digest_result.content
            history.posts_count = digest_result.posts_count
            history.topics = digest_result.topics
            session.commit()

            await self._send_digest(user, history, digest_result.content, digest_result.posts_count, session)

            digest_jobs_processed_total.labels(stage="complete", status="success").inc()
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
            digest_jobs_processed_total.labels(stage="complete", status="non_retryable").inc()
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
            digest_jobs_processed_total.labels(stage="complete", status="circuit_open").inc()
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
            digest_jobs_processed_total.labels(stage="complete", status="error").inc()
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

        digest_service = self._get_digest_service()

        @self._generation_retry
        async def _run_generation():
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
            digest_worker_generation_seconds.labels(status="success").observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(stage="generate", status="success").inc()
            return result
        except Exception as e:
            digest_worker_generation_seconds.labels(status="failed").observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(stage="generate", status="failed").inc()
            if isinstance(e, ValueError):
                raise NonRetryableDigestError(str(e)) from e
            raise

    async def _send_digest(
        self,
        user: User,
        history: DigestHistory,
        content: str,
        posts_count: int,
        session,
    ) -> None:
        """Отправка дайджеста пользователю через Telegram."""
        if not content:
            raise NonRetryableDigestError("Digest content is empty, nothing to send")

        bot = await self._ensure_bot()
        if not bot:
            raise NonRetryableDigestError("Telegram bot is not initialized")

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
            digest_worker_send_seconds.labels(status="success").observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(stage="send", status="success").inc()

            history.status = "sent"
            history.sent_at = datetime.now(timezone.utc)
            session.commit()
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            digest_worker_send_seconds.labels(status="telegram_error").observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(stage="send", status="telegram_error").inc()

            history.status = "failed"
            history.sent_at = None
            session.commit()
            raise NonRetryableDigestError(str(e)) from e
        except Exception as e:
            digest_worker_send_seconds.labels(status="failed").observe(time.perf_counter() - start)
            digest_jobs_processed_total.labels(stage="send", status="failed").inc()
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


async def create_digest_worker_task() -> DigestWorker:
    """Фабрика для supervisor."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    worker = DigestWorker(redis_url=redis_url)
    await worker.start()
    return worker

