"""Context7 best practice: PostPersistenceWorker для обработки событий постов.

Цель: Обработка событий из Redis Streams и сохранение в БД.
Архитектура: Redis Streams → PostPersistenceWorker → PostgreSQL (async)
"""

import asyncio
import asyncpg
import structlog
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import hashlib
import json
from typing import Union
from event_bus import RedisStreamsClient, EventConsumer, ConsumerConfig
from prometheus_client import Counter, Histogram, Gauge, REGISTRY
from api.utils.postgres_metrics import measure_postgres_operation, update_connection_metrics

logger = structlog.get_logger()

# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

# Context7: Безопасное создание метрик для избежания дублирования
def _safe_create_metric(metric_class, name, *args, **kwargs):
    """Создание метрики с проверкой на дублирование."""
    try:
        return metric_class(name, *args, **kwargs)
    except ValueError as e:
        if 'Duplicated' in str(e) or 'already registered' in str(e).lower():
            try:
                if hasattr(REGISTRY, '_names_to_collectors'):
                    existing = REGISTRY._names_to_collectors.get(name)
                    if existing:
                        logger.debug(f"Found existing metric {name} in REGISTRY, reusing", metric=name)
                        return existing
            except (AttributeError, KeyError, TypeError):
                pass
            logger.warning(f"Metric {name} exists but could not retrieve from REGISTRY, using mock", metric=name)
            class MockMetric:
                def labels(self, **kwargs):
                    return self
                def inc(self, value=1):
                    pass
                def observe(self, value):
                    pass
                def set(self, value):
                    pass
            return MockMetric()
        raise

# Метрики обработки событий
post_persistence_processed_total = _safe_create_metric(
    Counter,
    'post_persistence_processed_total',
    'Total posts persisted',
    ['status']  # success, error, skipped
)

post_persistence_latency_seconds = _safe_create_metric(
    Histogram,
    'post_persistence_latency_seconds',
    'Post persistence latency',
    ['operation'],  # upsert_post, upsert_channel
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
)

# Метрики БД операций
post_persistence_db_operations_total = _safe_create_metric(
    Counter,
    'post_persistence_db_operations_total',
    'Total DB operations',
    ['operation', 'status']  # upsert_post, upsert_channel, success, error
)

# Метрики PEL
post_persistence_pel_size = _safe_create_metric(
    Gauge,
    'post_persistence_pel_size',
    'Pending Entry List size for post persistence',
    ['consumer_group']
)


class PostPersistenceWorker:
    """Context7: Worker для персистентности постов через async БД."""
    
    def __init__(self, redis_url: str, database_url: str):
        self.redis_url = redis_url
        self.database_url = database_url
        self.db_pool: Optional[asyncpg.Pool] = None
        # Логическое имя стрима для EventBus
        self.stream_name = "posts.parsed"
        self.consumer_group = "post_persist_workers"
        self.consumer_name = f"post-persist-{int(asyncio.get_event_loop().time()*1000)}"
        self.is_running = False
        self.redis_streams_client: Optional[RedisStreamsClient] = None
        self.event_consumer: Optional[EventConsumer] = None
        self._last_pel_update_time = 0.0
        self._pel_update_interval = 30.0  # Обновление метрик PEL каждые 30 секунд
        self._connection_metrics_update_interval = 60.0  # Обновление метрик подключений каждые 60 секунд
        self._last_connection_metrics_update = 0.0
        
    async def initialize(self):
        """Context7: Инициализация подключений."""
        try:
            # Redis Streams клиент через EventBus
            self.redis_streams_client = RedisStreamsClient(self.redis_url)
            await self.redis_streams_client.connect()

            # Инициализация EventConsumer (единая обвязка чтения/ACK/DLQ)
            consumer_config = ConsumerConfig(
                group_name=self.consumer_group,
                consumer_name=self.consumer_name,
                retry_delay=1,
                idle_timeout=300
            )
            self.event_consumer = EventConsumer(self.redis_streams_client, consumer_config)

            # Async DB pool (убираем +asyncpg из DSN для asyncpg)
            dsn = self.database_url.replace('postgresql+asyncpg://', 'postgresql://')
            self.db_pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=10,
                command_timeout=30
            )
            
            logger.info("PostPersistenceWorker initialized successfully")
            
        except Exception as e:
            logger.error("Failed to initialize PostPersistenceWorker", extra={"error": str(e)})
            raise
    
    async def _ensure_consumer_group(self):
        """Создание группы через EventConsumer (идемпотентно)."""
        if self.event_consumer:
            await self.event_consumer._ensure_consumer_group(self.stream_name)
    
    async def start(self):
        """Context7: Запуск worker с обработкой событий."""
        self.is_running = True
        logger.info("PostPersistenceWorker started", consumer=self.consumer_name)
        
        try:
            if not self.event_consumer:
                raise RuntimeError("EventConsumer not initialized")
            
            # Context7: Периодическое обновление метрик PEL в фоне
            import asyncio
            pel_update_task = asyncio.create_task(self._periodic_pel_update())
            
            try:
                # Context7: правильный цикл pending→new
                await self.event_consumer.consume_forever(
                    stream_name=self.stream_name,
                    handler_func=self._handle_post_parsed
                )
            finally:
                pel_update_task.cancel()
                try:
                    await pel_update_task
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            logger.error("PostPersistenceWorker error", extra={"error": str(e)})
            raise
    
    async def _periodic_pel_update(self):
        """Context7: Периодическое обновление метрик PEL и подключений."""
        while self.is_running:
            try:
                await asyncio.sleep(self._pel_update_interval)
                await self._update_pel_metrics()
                
                # Обновление метрик подключений
                current_time = time.time()
                if current_time - self._last_connection_metrics_update >= self._connection_metrics_update_interval:
                    if self.db_pool:
                        await update_connection_metrics(self.db_pool, database_name="telegram_assistant")
                    self._last_connection_metrics_update = current_time
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Failed to update metrics", error=str(e))
    
    async def _update_pel_metrics(self):
        """Context7: Обновление метрик PEL."""
        try:
            if not self.redis_streams_client or not self.redis_streams_client.client:
                return
            
            from event_bus import STREAMS
            stream_key = STREAMS.get(self.stream_name, self.stream_name)
            
            # Получение информации о PEL через XPENDING
            pending_info = await self.redis_streams_client.client.xpending(
                stream_key, 
                self.consumer_group
            )
            
            if pending_info and isinstance(pending_info, dict):
                pel_size = pending_info.get('pending', 0) if isinstance(pending_info.get('pending'), int) else 0
                post_persistence_pel_size.labels(consumer_group=self.consumer_group).set(pel_size)
        except Exception as e:
            logger.debug("Failed to update PEL metrics", error=str(e))
    
    async def _handle_post_parsed(self, event_data: Dict[str, Any]):
        """Обработчик одного события posts.parsed с UPSERT в posts."""
        start_time = time.time()
        try:
            # Если пришёл JSON в поле data — распарсим
            if 'data' in event_data and isinstance(event_data['data'], str):
                try:
                    parsed = json.loads(event_data['data'])
                except Exception:
                    parsed = event_data
            else:
                parsed = event_data

            post_data = self._map_flat_parsed_to_post(parsed)
            async with self.db_pool.acquire() as conn:
                await self._upsert_post(conn, post_data)
            
            # Context7: Обновление метрик
            post_persistence_processed_total.labels(status='success').inc()
            # Метрики БД операций обновляются через декоратор @measure_postgres_operation
            
            logger.info("post_persist_ok", extra={"post_id": post_data.get('id')})
        except Exception as e:
            # Context7: Обновление метрик ошибок
            post_persistence_processed_total.labels(status='error').inc()
            # Метрики БД операций обновляются через декоратор @measure_postgres_operation
            logger.error("post_persist_failed", extra={"error": str(e)})
    
    async def _process_batch(self, messages: List):
        """Context7: Обработка batch с идемпотентностью."""
        if not messages:
            return
        
        processed_count = 0
        failed_count = 0
        
        try:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    for message_id, fields in messages:
                        try:
                            # Context7: Поддержка двух форматов событий
                            event_data = None
                            event_type = fields.get('event_type')

                            # Формат 1: JSON в поле 'data'
                            if 'data' in fields:
                                try:
                                    event_data = json.loads(fields.get('data') or '{}')
                                except json.JSONDecodeError:
                                    event_data = None

                            # Формат 2: Плоские поля stream:posts:parsed v1
                            if event_data is None and 'post_id' in fields and 'telegram_message_id' in fields:
                                event_data = self._map_flat_parsed_to_post(fields)
                                event_type = 'post.created'

                            if event_type == 'post.created':
                                await self._upsert_post(conn, event_data)
                            elif event_type == 'post.updated':
                                await self._upsert_post(conn, event_data)
                            elif event_type == 'channel.updated':
                                await self._upsert_channel(conn, event_data)
                            
                            # Context7: ACK после успешной обработки
                            await self.redis_client.xack(
                                self.stream_name,
                                self.consumer_group,
                                message_id
                            )
                            
                            processed_count += 1
                            
                        except Exception as e:
                            logger.error(
                                "Failed to process message",
                                message_id=message_id,
                                error=str(e)
                            )
                            failed_count += 1
                            
                            # Context7: DLQ для failed сообщений
                            await self._send_to_dlq(message_id, fields, str(e))
            
            logger.info(
                "Batch processed",
                processed=processed_count,
                failed=failed_count,
                total=len(messages)
            )
            
        except Exception as e:
            logger.error("Failed to process batch", extra={"error": str(e)})
    
    @measure_postgres_operation('upsert_post')
    async def _upsert_post(self, conn: asyncpg.Connection, post_data: Dict[str, Any]):
        """Context7: UPSERT поста с идемпотентностью."""
        try:
            # Context7: Конвертируем входные даты в UTC-дататайп с fallback на текущее время
            created_at = self._parse_iso_dt_utc(post_data.get('created_at'))
            if created_at is None:
                # Context7 best practice: используем CURRENT_TIMESTAMP как fallback
                created_at = datetime.now(timezone.utc)
            
            posted_at = self._parse_iso_dt_utc(post_data.get('posted_at'))
            media_urls_json = json.dumps(post_data.get('media_urls') or [])
            await conn.execute(
                """
                INSERT INTO posts (
                    id,
                    channel_id,
                    telegram_message_id,
                    content,
                    media_urls,
                    created_at,
                    posted_at,
                    url,
                    telegram_post_url,
                    has_media,
                    is_edited,
                    views_count,
                    forwards_count,
                    reactions_count
                ) VALUES (
                    $1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12, $13, $14
                )
                ON CONFLICT (channel_id, telegram_message_id)
                DO UPDATE SET
                    content = EXCLUDED.content,
                    media_urls = COALESCE(EXCLUDED.media_urls, posts.media_urls),
                    posted_at = COALESCE(EXCLUDED.posted_at, posts.posted_at),
                    url = COALESCE(EXCLUDED.url, posts.url),
                    telegram_post_url = COALESCE(EXCLUDED.telegram_post_url, posts.telegram_post_url),
                    has_media = COALESCE(EXCLUDED.has_media, posts.has_media),
                    is_edited = COALESCE(EXCLUDED.is_edited, posts.is_edited),
                    views_count = GREATEST(posts.views_count, EXCLUDED.views_count),
                    forwards_count = GREATEST(posts.forwards_count, EXCLUDED.forwards_count),
                    reactions_count = GREATEST(posts.reactions_count, EXCLUDED.reactions_count)
                """,
                post_data['id'],
                post_data['channel_id'],
                post_data['telegram_message_id'],
                post_data.get('content'),
                media_urls_json,
                created_at,
                posted_at,
                post_data.get('url'),
                post_data.get('telegram_post_url'),
                post_data.get('has_media', False),
                post_data.get('is_edited', False),
                int(post_data.get('views_count') or 0),
                int(post_data.get('forwards_count') or 0),
                int(post_data.get('reactions_count') or 0),
            )
            
            # Context7: Метрика длительности операции уже обновляется в _handle_post_parsed
            
        except Exception as e:
            logger.error("Failed to upsert post", extra={"post_id": post_data.get('id'), "error": str(e)})
            raise

    def _map_flat_parsed_to_post(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Преобразование плоских полей stream:posts:parsed v1 в структуру post_data."""
        # В плоских полях все строки — приводим типы и имена
        def to_bool(val: Any) -> bool:
            return str(val).lower() in ("true", "1", "yes")

        def to_int(val: Any) -> int:
            try:
                return int(val)
            except Exception:
                return 0

        post_id = fields.get('post_id')
        content = fields.get('text') or fields.get('content') or ''
        urls_raw = fields.get('urls') or '[]'
        try:
            media_urls = json.loads(urls_raw) if isinstance(urls_raw, str) else urls_raw
        except Exception:
            media_urls = []

        # Таймштампы — всегда сохраняем в UTC; отображение в MSK на уровне клиента
        created_at = self._parse_iso_dt_utc(fields.get('occurred_at') or fields.get('created_at'))
        posted_at = self._parse_iso_dt_utc(fields.get('posted_at'))

        return {
            'id': post_id,
            'channel_id': fields.get('channel_id'),
            'telegram_message_id': to_int(fields.get('telegram_message_id') or fields.get('tg_message_id') or 0),
            'content': content,
            'media_urls': media_urls,
            'created_at': created_at,
            'posted_at': posted_at,
            'url': fields.get('url'),
            'telegram_post_url': fields.get('telegram_post_url'),
            'has_media': to_bool(fields.get('has_media')),
            'is_edited': to_bool(fields.get('is_edited')),
            'views_count': to_int(fields.get('views_count')),
            'forwards_count': to_int(fields.get('forwards_count')),
            'reactions_count': to_int(fields.get('reactions_count')),
        }

    def _parse_iso_dt_utc(self, value: Optional[Union[str, datetime]]) -> Optional[datetime]:
        """Парсит ISO8601 строку в datetime с tz=UTC. Если уже datetime — нормализует к UTC.

        Best practice (Context7): всегда хранить timestamptz в UTC, локаль MSK применять на чтении.
        """
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            # Поддержка форматов: 2025-10-29T20:26:25Z, 2025-10-29T20:26:25+00:00
            s = str(value).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            # В крайнем случае — текущий UTC
            return datetime.now(timezone.utc)
    
    @measure_postgres_operation('upsert_channel')
    async def _upsert_channel(self, conn: asyncpg.Connection, channel_data: Dict[str, Any]):
        """Context7: UPSERT канала."""
        try:
            await conn.execute("""
                INSERT INTO channels (
                    id, telegram_channel_id, title, username, 
                    description, member_count, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, NOW()
                ) ON CONFLICT (id) 
                DO UPDATE SET
                    telegram_channel_id = EXCLUDED.telegram_channel_id,
                    title = EXCLUDED.title,
                    username = EXCLUDED.username,
                    description = EXCLUDED.description,
                    member_count = EXCLUDED.member_count,
                    updated_at = NOW()
            """,
                channel_data['id'],
                channel_data['telegram_channel_id'],
                channel_data['title'],
                channel_data.get('username'),
                channel_data.get('description'),
                channel_data.get('member_count')
            )
            
            # Context7: Метрики обновляются через декоратор @measure_postgres_operation
            
        except Exception as e:
            logger.error("Failed to upsert channel", extra={"channel_id": channel_data.get('id'), "error": str(e)})
            raise
    
    async def _send_to_dlq(self, message_id: str, fields: Dict, error: str):
        """Context7: Отправка в Dead Letter Queue."""
        try:
            dlq_data = {
                "original_message_id": message_id,
                "original_fields": fields,
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            await self.redis_client.xadd(
                "stream:dlq:post_persistence",
                {"data": json.dumps(dlq_data, ensure_ascii=False)},
                maxlen=1000
            )
            
            logger.warning("Message sent to DLQ", message_id=message_id)
            
        except Exception as e:
            logger.error("Failed to send to DLQ", extra={"message_id": message_id, "error": str(e)})
    
    async def stop(self):
        """Context7: Остановка worker."""
        self.is_running = False
        
        if self.redis_client:
            await self.redis_client.close()
        
        if self.db_pool:
            await self.db_pool.close()
        
        logger.info("PostPersistenceWorker stopped")
    
    async def health_check(self) -> Dict[str, Any]:
        """Context7: Health check для мониторинга."""
        try:
            # Проверка Redis
            redis_ok = await self.redis_client.ping()
            
            # Проверка DB
            db_ok = False
            if self.db_pool:
                async with self.db_pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                    db_ok = True
            
            return {
                "status": "healthy" if (redis_ok and db_ok) else "unhealthy",
                "redis_connected": redis_ok,
                "db_connected": db_ok,
                "is_running": self.is_running,
                "consumer_name": self.consumer_name,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error("Health check failed", extra={"error": str(e)})
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }