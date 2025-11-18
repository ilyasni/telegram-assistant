"""
Crawl Trigger Task - Producer для posts.crawl
[C7-ID: WORKER-CRAWL-TRIGGER-001]

Читает posts.tagged и публикует в posts.crawl при наличии триггерных тегов.
"""
import asyncio
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import asyncpg
import structlog
from redis.asyncio import Redis
from prometheus_client import Counter, Gauge, Histogram

logger = structlog.get_logger()

# Метрики с правильным неймингом
crawl_triggers_total = Counter(
    'crawl_triggers_total',
    'Total crawl triggers',
    ['reason']
)

crawl_trigger_queue_depth_current = Gauge(
    'crawl_trigger_queue_depth_current',
    'Current queue depth for posts.tagged'
)

crawl_trigger_processing_latency_seconds = Histogram(
    'crawl_trigger_processing_latency_seconds',
    'Processing latency for trigger task'
)

crawl_trigger_idempotency_hits_total = Counter(
    'crawl_trigger_idempotency_hits_total',
    'Idempotency cache hits'
)

crawl_trigger_policy_skips_total = Counter(
    'crawl_trigger_policy_skips_total',
    'Policy skips by reason',
    ['reason']  # фиксированные: no_trigger_tags, no_urls
)

crawl_trigger_source_total = Counter(
    'crawl_trigger_source_total',
    'Trigger source usage',
    ['source']  # personal, fallback, topics_payload, missing
)

crawl_trigger_cache_hits_total = Counter(
    'crawl_trigger_cache_hits_total',
    'Cache hits for user triggers',
    ['status']  # hit, miss
)

class CrawlTriggerTask:
    """Producer для posts.crawl на основе триггерных тегов."""
    
    def __init__(
        self,
        redis_url: str,
        trigger_tags: list,
        db_dsn: Optional[str] = None,
        consumer_group: str = "crawl_trigger_workers",
        consumer_name: str = "crawl_trigger_worker_1"
    ):
        self.redis_url = redis_url
        self.db_dsn = db_dsn or os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
        self.trigger_tags = trigger_tags
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.stream_in = "stream:posts:tagged"
        self.stream_out = "stream:posts:crawl"
        self.redis: Optional[Redis] = None
        self.db_pool: Optional[asyncpg.Pool] = None
        self._triggers_cache: Dict[str, tuple[float, List[str], str]] = {}
        self._user_uuid_cache: Dict[str, tuple[float, Optional[str]]] = {}
        self._triggers_cache_ttl = int(os.getenv("CRAWL_TRIGGER_CACHE_TTL", "900"))
        self._user_uuid_cache_ttl = int(os.getenv("CRAWL_TRIGGER_UUID_CACHE_TTL", "900"))
        self._fallback_trigger_tags = [self._normalize_token(tag) for tag in trigger_tags if isinstance(tag, str)]
        
        logger.info("CrawlTriggerTask initialized",
                   trigger_tags_count=len(self._fallback_trigger_tags))
    
    async def _initialize(self):
        """Инициализация."""
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        try:
            self.db_pool = await asyncpg.create_pool(
                self.db_dsn,
                min_size=1,
                max_size=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to initialize DB pool for CrawlTriggerTask", error=str(exc))
            self.db_pool = None
        
        # Создание consumer group
        try:
            await self.redis.xgroup_create(
                self.stream_in,
                self.consumer_group,
                id="0",
                mkstream=True
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.warning("Failed to create consumer group", error=str(e))
    
    async def start(self):
        """Запуск producer."""
        logger.info("CrawlTriggerTask.start() called")
        try:
            await self._initialize()
            logger.info("CrawlTriggerTask initialized and started")
        except Exception as e:
            logger.error("Failed to initialize CrawlTriggerTask", error=str(e), exc_info=True)
            raise
        
        # Context7: Бесконечный цикл с обработкой всех исключений для предотвращения завершения задачи
        # Context7: Добавлено логирование для диагностики завершения задачи
        iteration = 0
        logger.info("CrawlTriggerTask entering main loop")
        try:
            while True:
                iteration += 1
                if iteration % 100 == 0:
                    logger.debug("CrawlTriggerTask iteration", iteration=iteration)
                try:
                    # Обновляем метрику глубины очереди
                    try:
                        queue_length = await self.redis.xlen(self.stream_in)
                        crawl_trigger_queue_depth_current.set(queue_length)
                    except Exception as e:
                        logger.warning("Failed to get queue length", error=str(e))
                    
                    # Читаем сообщения (сначала с начала, потом только новые)
                    logger.debug("Reading from stream", stream=self.stream_in, group=self.consumer_group)
                    try:
                        messages = await self.redis.xreadgroup(
                            self.consumer_group,
                            self.consumer_name,
                            {self.stream_in: ">"},  # Читаем только новые сообщения
                            count=50,
                            block=1000
                        )
                    except Exception as e:
                        logger.error("Failed to read from stream", stream=self.stream_in, error=str(e), exc_info=True)
                        await asyncio.sleep(5)
                        continue
                    
                    logger.debug("xreadgroup result", messages=messages, type=type(messages), len=len(messages) if messages else 0)
                    
                    # Обработка сообщений
                    if messages:
                        logger.debug("CrawlTriggerTask received messages", count=len(messages))
                        for stream_name, entries in messages:
                            logger.debug("Processing stream", stream=stream_name, entries_count=len(entries))
                            if not entries:
                                logger.debug("Stream has zero entries", stream=stream_name)
                                continue
                            for msg_id, fields in entries:
                                logger.debug("Processing message", msg_id=msg_id, fields_keys=list(fields.keys()) if isinstance(fields, dict) else "not_dict")
                                try:
                                    # Измеряем время обработки
                                    start_time = time.time()
                                    await self._process_tagged_event(msg_id, fields)
                                    processing_time = time.time() - start_time
                                    crawl_trigger_processing_latency_seconds.observe(processing_time)
                                    
                                    await self.redis.xack(self.stream_in, self.consumer_group, msg_id)
                                except Exception as e:
                                    logger.error("Error processing tagged event",
                                               msg_id=msg_id, error=str(e), exc_info=True)
                                    # Context7: Продолжаем обработку следующих сообщений даже при ошибке
                    else:
                        await asyncio.sleep(0.1)
                        continue
                    
                except asyncio.CancelledError:
                    logger.info("CrawlTriggerTask cancelled")
                    raise
                except Exception as e:
                    logger.error("Error in CrawlTriggerTask loop", error=str(e), exc_info=True)
                    # Context7: Продолжаем работу после ошибки с задержкой
                    await asyncio.sleep(5)
                    continue
                except BaseException as e:
                    # Context7: Обрабатываем все исключения, включая SystemExit и KeyboardInterrupt
                    logger.critical("Fatal error in CrawlTriggerTask loop", error=str(e), error_type=type(e).__name__, exc_info=True)
                    # Для критических ошибок делаем паузу перед продолжением
                    await asyncio.sleep(10)
                    continue
        except asyncio.CancelledError:
            logger.info("CrawlTriggerTask main loop cancelled")
            raise
        except Exception as e:
            logger.critical("CrawlTriggerTask main loop exited with exception", error=str(e), error_type=type(e).__name__, exc_info=True)
            raise
        finally:
            logger.warning("CrawlTriggerTask.start() exiting - this should not happen!")
            # Context7: Если мы дошли сюда, значит цикл завершился - это ошибка
            # Не возвращаемся, чтобы supervisor мог перезапустить задачу
    
    async def _process_tagged_event(self, msg_id: str, fields: Dict[str, Any]):
        """Проверка триггеров и публикация в posts.crawl."""
        logger.debug("Processing tagged event", msg_id=msg_id, fields=str(fields)[:512])
        payload = fields.get("data") or fields.get("payload") or fields
        if isinstance(payload, str):
            payload = json.loads(payload)
        
        post_id = payload.get('post_id')
        tags = payload.get('tags', [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tags JSON", tags=tags)
                tags = []
        tags = [self._normalize_token(tag) for tag in tags if tag]

        tenant_id = payload.get('tenant_id')
        user_id = payload.get('user_id')
        channel_id = payload.get('channel_id')
        topics_payload = self._normalize_topics_payload(payload.get('topics'))

        logger.debug("Extracted payload", post_id=post_id, tags=tags, user_id=user_id, tenant_id=tenant_id)
        urls = payload.get('urls', [])
        # Парсим urls если это строка JSON
        if isinstance(urls, str):
            try:
                urls = json.loads(urls)
            except json.JSONDecodeError:
                logger.warning("Failed to parse urls JSON", urls=urls)
                urls = []
        trace_id = payload.get('trace_id', msg_id)
        
        # Context7: Проверка trigger - обрабатываем все события, включая vision_retag
        trigger = payload.get('trigger')
        is_retagging = (trigger == "vision_retag")

        triggers, trigger_source = await self._resolve_triggers(user_id, tenant_id, topics_payload)
        crawl_trigger_source_total.labels(source=trigger_source).inc()

        has_trigger = any(tag in triggers for tag in tags)
        
        # Context7: Логируем если это retagging для observability
        if is_retagging:
            logger.debug(
                "Processing retagging event in CrawlTriggerTask",
                post_id=post_id,
                has_trigger_tags=has_trigger,
                trace_id=trace_id
            )
        
        if has_trigger:
            # Если нет URLs, генерируем заглушку для тестирования
            if not urls:
                urls = [f"https://example.com/post/{post_id}"]
                crawl_trigger_policy_skips_total.labels(reason='no_urls').inc()
            
            metadata = payload.get('metadata') or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}

            metadata.update({
                "tenant_id": tenant_id,
                "user_id": user_id,
                "channel_id": channel_id,
                "event_topics": topics_payload,
                "trigger_source": trigger_source,
                "trigger_tags_used": triggers[:20],
                "is_retagging": is_retagging,
            })

            crawl_request = {
                'post_id': post_id,
                'urls': urls,
                'tags': tags,
                'trigger_reason': 'trigger_tag',
                'trace_id': trace_id,
                'metadata': metadata
            }
            
            await self.redis.xadd(
                self.stream_out,
                {'data': json.dumps(crawl_request)}
            )
            
            crawl_triggers_total.labels(reason='triggered').inc()
            logger.info("Crawl triggered",
                       post_id=post_id,
                       tags=tags,
                       urls_count=len(urls),
                       trace_id=trace_id)
        else:
            if not has_trigger:
                crawl_trigger_policy_skips_total.labels(reason='no_trigger_tags').inc()
                logger.debug("No trigger tags", post_id=post_id, tags=tags)
            if not urls:
                crawl_trigger_policy_skips_total.labels(reason='no_urls').inc()
                logger.debug("No URLs", post_id=post_id)

    async def _resolve_triggers(
        self,
        user_id: Optional[str],
        tenant_id: Optional[str],
        topics_payload: List[str] | None,
    ) -> tuple[List[str], str]:
        """Получить список триггеров для пользователя с кэшированием."""
        if not user_id or not self.db_pool:
            source = "fallback_no_user" if not user_id else "fallback_no_db"
            return list(self._fallback_trigger_tags), source

        user_key = str(user_id)
        now = time.time()

        resolved_user_id: Optional[str] = None
        cached_uuid = self._user_uuid_cache.get(user_key)
        if cached_uuid and (now - cached_uuid[0]) < self._user_uuid_cache_ttl:
            resolved_user_id = cached_uuid[1]
        elif self._looks_like_uuid(user_key):
            resolved_user_id = user_key
            self._user_uuid_cache[user_key] = (now, resolved_user_id)

        cache_key = resolved_user_id or user_key
        cached = self._triggers_cache.get(cache_key)
        if cached and (now - cached[0]) < self._triggers_cache_ttl:
            crawl_trigger_cache_hits_total.labels(status='hit').inc()
            return cached[1], cached[2]

        crawl_trigger_cache_hits_total.labels(status='miss').inc()

        triggers: List[str] = []
        source = "fallback"

        try:
            async with self.db_pool.acquire() as conn:
                if resolved_user_id is None:
                    resolved_user_id = await self._resolve_user_uuid(user_key, conn)
                    self._user_uuid_cache[user_key] = (now, resolved_user_id)
                    cache_key = resolved_user_id or user_key

                row = None
                if resolved_user_id:
                    row = await conn.fetchrow(
                        "SELECT triggers FROM user_crawl_triggers WHERE user_id = $1",
                        resolved_user_id,
                    )
                if row and row.get("triggers"):
                    triggers = self._normalize_topics_payload(row.get("triggers"))
                    source = "personal"
                elif topics_payload:
                    triggers = topics_payload
                    source = "topics_payload"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fetch personal triggers",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(exc)
            )

        if not triggers:
            triggers = list(self._fallback_trigger_tags)
            if source == "personal":
                source = "fallback"

        self._triggers_cache[cache_key] = (now, triggers, source)
        return triggers, source

    async def _resolve_user_uuid(self, user_identifier: str, conn) -> Optional[str]:
        if not user_identifier:
            return None
        if self._looks_like_uuid(user_identifier):
            return user_identifier

        try:
            telegram_id = int(user_identifier)
        except (TypeError, ValueError):
            return None

        row = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id = $1 LIMIT 1",
            telegram_id,
        )
        if row and row.get("id"):
            return str(row["id"])
        return None

    @staticmethod
    def _normalize_topics_payload(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [CrawlTriggerTask._normalize_token(item) for item in value if item]
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                if isinstance(decoded, list):
                    return [CrawlTriggerTask._normalize_token(item) for item in decoded if item]
            except json.JSONDecodeError:
                return [CrawlTriggerTask._normalize_token(value)]
        return []

    @staticmethod
    def _normalize_token(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()

    @staticmethod
    def _looks_like_uuid(candidate: str) -> bool:
        try:
            uuid.UUID(str(candidate))
            return True
        except (ValueError, TypeError, AttributeError):
            return False
