import os
from typing import Any, Dict, Optional

import structlog
from prometheus_client import Counter, Gauge, Histogram

from event_bus import (  # type: ignore
    ConsumerConfig,
    DigestContextPreparedEvent,
    EventConsumer,
    RedisStreamsClient,
)

logger = structlog.get_logger(__name__)


digest_context_messages = Histogram(
    "digest_context_messages",
    "Количество сообщений на различных этапах подготовки контекста",
    ["metric"],
)

digest_context_duplicates_total = Counter(
    "digest_context_duplicates_total",
    "Количество удалённых дубликатов при сборе контекста",
    ["tenant"],
)

digest_context_history_matches_total = Counter(
    "digest_context_history_matches_total",
    "Количество совпадений с историческими окнами (Context7 Storage)",
    ["tenant"],
)

digest_context_media_total = Gauge(
    "digest_context_media_total",
    "Количество медиа-вложений, попавших в окно контекста",
    ["tenant", "group"],
)

digest_context_media_without_description_total = Gauge(
    "digest_context_media_without_description_total",
    "Количество медиа-вложений без описаний в окне контекста",
    ["tenant", "group"],
)


class DigestContextObserver:
    """Консьюмер событий digest.context.prepared для метрик и аудита."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.redis_client: Optional[RedisStreamsClient] = None
        self.consumer: Optional[EventConsumer] = None

    async def start(self) -> None:
        self.redis_client = RedisStreamsClient(self.redis_url)
        await self.redis_client.connect()

        consumer_name = f"digest-context-observer-{os.getpid()}"
        config = ConsumerConfig(
            group_name="digest-context-observers",
            consumer_name=consumer_name,
            batch_size=20,
            block_time=1_000,
            max_retries=5,
            retry_delay=5,
        )
        self.consumer = EventConsumer(self.redis_client, config)

        logger.info(
            "DigestContextObserver started",
            redis_url=self.redis_url,
            consumer_name=consumer_name,
        )

        await self.consumer.consume_forever("digest.context.prepared", self._handle_event)

    async def _handle_event(self, payload: Dict[str, Any]) -> None:
        data = payload.get("payload") or payload
        try:
            event = DigestContextPreparedEvent(**data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("digest_context_event_parse_failed", error=str(exc), raw=data)
            return

        stats = event.stats or {}
        tenant = str(event.tenant_id)

        original_messages = float(stats.get("original_messages", event.message_total))
        dedup_messages = float(stats.get("deduplicated_messages", event.deduplicated_messages))

        digest_context_messages.labels(metric="original").observe(original_messages)
        digest_context_messages.labels(metric="deduplicated").observe(dedup_messages)

        duplicates_removed = int(stats.get("duplicates_removed", event.duplicates_removed))
        if duplicates_removed:
            digest_context_duplicates_total.labels(tenant=tenant).inc(duplicates_removed)

        historical_matches = int(stats.get("historical_matches", event.historical_matches))
        if historical_matches:
            digest_context_history_matches_total.labels(tenant=tenant).inc(historical_matches)

        media_total = int(stats.get("media_total", 0))
        digest_context_media_total.labels(tenant=tenant, group=str(event.group_id)).set(media_total)

        media_without_description = int(stats.get("media_without_description", 0))
        digest_context_media_without_description_total.labels(
            tenant=tenant, group=str(event.group_id)
        ).set(media_without_description)

        logger.info(
            "digest_context_event_processed",
            tenant_id=tenant,
            group_id=event.group_id,
            window_id=event.window_id,
            message_total=event.message_total,
            deduplicated=event.deduplicated_messages,
            duplicates_removed=duplicates_removed,
            historical_matches=historical_matches,
            media_total=media_total,
            media_without_description=media_without_description,
        )


async def create_digest_context_task() -> DigestContextObserver:
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    observer = DigestContextObserver(redis_url=redis_url)
    await observer.start()
    return observer

