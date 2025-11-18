"""
Tag Persistence Task - Consumer для posts.tagged событий
[C7-ID: WORKER-TAG-PERSISTENCE-001]

Сохраняет теги в post_enrichment с идемпотентностью по хешу
"""

import json
import asyncio
import time
import uuid
from typing import Dict, Any, List
from datetime import datetime, timezone
import asyncpg
import structlog
from prometheus_client import Counter, Histogram, Gauge
from redis.asyncio import Redis

from events.schemas.posts_tagged_v1 import PostTaggedEventV1
from event_bus import EventPublisher

logger = structlog.get_logger()

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

tags_persisted_total = Counter(
    'tags_persisted_total',
    'Total tags persisted to DB',
    ['status']
)

tags_persist_latency_seconds = Histogram(
    'tags_persist_latency_seconds',
    'Tag persistence latency'
)

tags_persist_conflicts_total = Counter(
    'tags_persist_conflicts_total',
    'UPSERT conflicts (no change)'
)

tags_hash_mismatches_total = Counter(
    'tags_hash_mismatches_total',
    'Tag hash changes'
)

# Конфигурация PEL recovery
import os
MAX_PEL_ROUNDS_PER_ITERATION = int(os.getenv("TAG_PERSIST_MAX_PEL_ROUNDS", "5"))
PEL_MIN_IDLE_MS = int(os.getenv("TAG_PERSIST_PEL_MIN_IDLE_MS", "60000"))
PEL_BATCH_SIZE = int(os.getenv("TAG_PERSIST_PEL_BATCH_SIZE", "10"))

# Метрики по фазам
tags_persist_phase_total = Counter(
    'tags_persist_phase_total',
    'Tag persistence by phase',
    ['phase', 'status']  # phase=pending|new, status=ok|fail|skip
)

tags_persist_phase_latency_seconds = Histogram(
    'tags_persist_phase_latency_seconds',
    'Tag persistence latency by phase',
    ['phase']
)

tags_persist_pel_backlog_current = Gauge(
    'tags_persist_pel_backlog_current',
    'Pending entries in PEL'
)

# ============================================================================
# TAG PERSISTENCE TASK
# ============================================================================

class TagPersistenceTask:
    """Consumer для сохранения тегов из posts.tagged в post_enrichment."""
    
    def __init__(
        self,
        redis_url: str,
        db_dsn: str,
        stream: str = "posts.tagged",
        consumer_group: str = "tag_persist_workers",
        consumer_name: str = "tag_persistence_worker_1"
    ):
        self.redis_url = redis_url
        self.db_dsn = db_dsn
        self.stream = stream
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.redis = None
        self.pool = None
        self.publisher = None
        
        logger.info("TagPersistenceTask initialized",
                   stream=stream,
                   consumer_group=consumer_group)
    
    @property
    def stream_key(self) -> str:
        """Получение реального ключа стрима из STREAMS."""
        from event_bus import STREAMS
        return STREAMS.get(self.stream, self.stream)
    
    async def _initialize(self):
        """Инициализация компонентов task."""
        # Подключение к Redis
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        
        # Подключение к БД
        self.pool = await asyncpg.create_pool(
            self.db_dsn,
            min_size=2,
            max_size=10
        )
        
        # Инициализация publisher для posts.enriched
        from event_bus import RedisStreamsClient
        redis_streams_client = RedisStreamsClient(self.redis_url)
        await redis_streams_client.connect()
        self.publisher = EventPublisher(redis_streams_client)
        
        # Создание consumer group
        try:
            await self.redis.xgroup_create(
                self.stream_key,
                self.consumer_group,
                id="0",
                mkstream=True
            )
            logger.info("Consumer group created", group=self.consumer_group)
        except Exception as e:
            if "BUSYGROUP" in str(e):
                logger.debug("Consumer group exists", group=self.consumer_group)
            else:
                logger.warning(f"Failed to create consumer group: {e}")
        
        logger.info("TagPersistenceTask initialized successfully")

    async def start(self):
        """Запуск task с двухфазной обработкой: pending → новые + anti-starvation."""
        try:
            await self._initialize()
            logger.info("TagPersistenceTask started successfully")
            
            iteration = 0
            while True:
                try:
                    # Фаза 1: PEL recovery с anti-starvation лимитом
                    pending_round = 0
                    total_pending = 0
                    
                    while pending_round < MAX_PEL_ROUNDS_PER_ITERATION:
                        processed = await self._process_pending_messages()
                        if processed == 0:
                            break
                        total_pending += processed
                        pending_round += 1
                    
                    # Фаза 2: Обработка новых сообщений
                    if total_pending == 0:
                        await self._process_new_messages()
                    else:
                        logger.info("PEL recovery completed",
                                   rounds=pending_round,
                                   total_processed=total_pending)
                        await asyncio.sleep(0.1)
                    
                    # Периодический мониторинг PEL (каждые 10 итераций)
                    iteration += 1
                    # if iteration % 10 == 0:
                    #     await self._monitor_pel_backlog()
                        
                except Exception as e:
                    logger.error("Error in TagPersistenceTask loop", error=str(e))
                    raise
                    
        except Exception as e:
            logger.error("Failed to start TagPersistenceTask", error=str(e))
            raise
    
    async def _process_pending_messages(self) -> int:
        """
        [C7-ID: EVENTBUS-PEL-RECOVERY-001]
        Обработка pending сообщений через XAUTOCLAIM.
        Возвращает количество обработанных сообщений.
        """
        import time
        start_time = time.time()
        
        try:
            # Используем нативный xautoclaim (redis-py >= 4.2)
            result = await self.redis.xautoclaim(
                name=self.stream_key,
                groupname=self.consumer_group,
                consumername=self.consumer_name,
                min_idle_time=PEL_MIN_IDLE_MS,
                start_id="0-0",
                count=PEL_BATCH_SIZE,
                justid=False
            )
            
            # xautoclaim возвращает список [next_id, messages] или [next_id, messages, other_data]
            if isinstance(result, (list, tuple)) and len(result) >= 2:
                next_id, messages = result[0], result[1]
            else:
                messages = result
                next_id = None
            
            if not messages:
                return 0
            
            processed = 0
            for msg_id, fields in messages:
                try:
                    await self._process_single_message(msg_id, fields)
                    await self.redis.xack(self.stream_key, self.consumer_group, msg_id)
                    processed += 1
                    tags_persist_phase_total.labels(phase='pending', status='ok').inc()
                    
                except Exception as e:
                    logger.error("Error processing pending message",
                               msg_id=msg_id, error=str(e))
                    tags_persist_phase_total.labels(phase='pending', status='fail').inc()
                    # Не ACK - оставляем в PEL для повторной обработки
            
            if processed > 0:
                latency = time.time() - start_time
                tags_persist_phase_latency_seconds.labels(phase='pending').observe(latency)
                logger.info("Processed pending messages",
                           count=processed,
                           latency_ms=int(latency * 1000))
            
            return processed
            
        except Exception as e:
            logger.error("Error in _process_pending_messages", error=str(e))
            return 0

    async def _process_new_messages(self):
        """Обработка новых сообщений (id='>')."""
        import time
        start_time = time.time()
        
        # Читаем только новые сообщения (id='>')
        read_id = ">"
        
        messages = await self.redis.xreadgroup(
            self.consumer_group,
            self.consumer_name,
            {self.stream_key: read_id},
            count=50,
            block=1000
        )
        
        if not messages:
            await asyncio.sleep(0.1)
            return
        
        processed = 0
        for stream_name, entries in messages:
            for msg_id, fields in entries:
                try:
                    await self._process_single_message(msg_id, fields)
                    await self.redis.xack(self.stream_key, self.consumer_group, msg_id)
                    processed += 1
                    tags_persist_phase_total.labels(phase='new', status='ok').inc()
                except Exception as e:
                    logger.error("Error processing new message",
                               msg_id=msg_id, error=str(e))
                    tags_persist_phase_total.labels(phase='new', status='fail').inc()
        
        if processed > 0:
            latency = time.time() - start_time
            tags_persist_phase_latency_seconds.labels(phase='new').observe(latency)

    async def _monitor_pel_backlog(self):
        """
        [C7-ID: EVENTBUS-OBSERVABILITY-001]
        Мониторинг PEL backlog через XPENDING.
        """
        # Временно отключено
        pass
    
    async def _process_single_message(self, msg_id: str, fields: Dict[str, Any]):
        """Обработка одного сообщения."""
        # Парсинг payload
        payload = fields.get("data") or fields.get("payload") or fields
        if isinstance(payload, str):
            payload = json.loads(payload)
        
        # Парсинг вложенных JSON полей
        if isinstance(payload.get("tags"), str):
            payload["tags"] = json.loads(payload["tags"])
        if isinstance(payload.get("metadata"), str):
            payload["metadata"] = json.loads(payload["metadata"])
        
        # Валидация через Pydantic с безопасным переводом в DLQ при ошибке
        try:
            event = PostTaggedEventV1(**payload)
        except Exception as e:
            # [C7-ID: EVENTBUS-DLQ-001] Любая ошибка валидации уходит в DLQ и ACK
            try:
                dlq_payload = {
                    "error": str(e),
                    "raw": payload,
                    "msg_id": msg_id,
                }
                await self.publisher.publish_event("posts.tagged.dlq", dlq_payload)
                tags_persist_phase_total.labels(phase='pending', status='fail').inc()
                logger.error("PostTaggedEventV1 validation failed; moved to DLQ",
                             msg_id=msg_id, error=str(e))
            finally:
                # ACK, чтобы сообщение не застревало в PEL
                try:
                    await self.redis.xack(self.stream_key, self.consumer_group, msg_id)
                except Exception:
                    pass
            return
        
        # Context7: Логирование для отладки потери тегов
        trigger = getattr(event, 'trigger', None)
        logger.info("Processing tags event",
                   post_id=event.post_id,
                   tags_count=len(event.tags) if event.tags else 0,
                   tags_sample=event.tags[:3] if event.tags else [],
                   provider=event.provider,
                   trigger=trigger,
                   is_retagging=(trigger == "vision_retag"))
        
        # Context7: Проверка существования поста перед сохранением тегов
        post_exists = await self._check_post_exists(event.post_id)
        if not post_exists:
            logger.warning("Post not found in DB, skipping tag persistence",
                         post_id=event.post_id,
                         trace_id=(event.metadata or {}).get('trace_id') if event.metadata else None)
            tags_persist_phase_total.labels(phase='pending', status='skip').inc()
            tags_persisted_total.labels(status='skip').inc()
            # ACK сообщение, чтобы оно не застревало в PEL
            try:
                await self.redis.xack(self.stream_key, self.consumer_group, msg_id)
            except Exception:
                pass
            return
        
        # Сохранение в БД
        start_time = time.time()
        await self._save_tags_to_db(
            post_id=event.post_id,
            tags=event.tags,
            tags_hash=event.tags_hash,
            metadata={
                "provider": event.provider,
                "latency_ms": event.latency_ms,
                **(event.metadata or {}),
                "tenant_id": event.tenant_id,
                "user_id": event.user_id,
                "channel_id": event.channel_id,
                "topics": event.topics or []
            }
        )
        
        processing_time = time.time() - start_time
        tags_persist_latency_seconds.observe(processing_time)
        tags_persisted_total.labels(status='success').inc()
        
        logger.info("Tags persisted successfully",
                   post_id=event.post_id,
                   tags_count=len(event.tags),
                   processing_time=processing_time)
    
    async def _check_post_exists(self, post_id: str) -> bool:
        """Context7: Проверка существования поста в БД."""
        async with self.pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM posts WHERE id = $1)",
                post_id
            )
            return result is True
    
    async def _save_tags_to_db(
        self,
        post_id: str,
        tags: List[str],
        tags_hash: str,
        metadata: Dict[str, Any]
    ):
        """
        Context7: Сохранение тегов в post_enrichment через EnrichmentRepository.
        Использует единую модель с kind='tags' и структурированное JSONB поле data.
        """
        # Context7: Импорт shared репозитория с правильной обработкой путей
        import sys
        import os
        
        try:
            # Попытка 1: Прямой импорт (если пакет установлен через pip install -e)
            from shared.repositories.enrichment_repository import EnrichmentRepository
        except ImportError:
            # Попытка 2: Добавление пути из worker контейнера
            shared_paths = [
                '/app/shared/python',  # Production путь в контейнере (из Dockerfile)
                os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared', 'python')),  # Dev путь от worker/tasks
                os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python')),  # Альтернативный путь
            ]
            
            for shared_path in shared_paths:
                if os.path.exists(shared_path) and shared_path not in sys.path:
                    sys.path.insert(0, shared_path)
                    logger.debug(f"Added shared path to sys.path: {shared_path}")
            
            try:
                from shared.repositories.enrichment_repository import EnrichmentRepository
            except ImportError as e:
                logger.error(f"Failed to import EnrichmentRepository: {e}", extra={
                    "sys_path": sys.path[:5],
                    "shared_paths_tried": shared_paths
                })
                raise
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Context7: Извлекаем tenant_id из БД (как в enrichment_task и indexing_task)
                # Используем COALESCE с приоритетами: users -> tags_data -> channels.settings
                tenant_id_result = await conn.fetchrow(
                    """
                    SELECT COALESCE(
                        (SELECT u.tenant_id::text FROM users u 
                         JOIN user_channel uc ON uc.user_id = u.id 
                         WHERE uc.channel_id = c.id 
                         LIMIT 1),
                        CAST(pe_tags.data->>'tenant_id' AS text),
                        CAST(c.settings->>'tenant_id' AS text),
                        'default'
                    ) as tenant_id
                    FROM posts p
                    JOIN channels c ON p.channel_id = c.id
                    LEFT JOIN post_enrichment pe_tags 
                        ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
                    WHERE p.id = $1
                    LIMIT 1
                    """,
                    post_id
                )
                
                # Context7: Используем tenant_id из БД, fallback на metadata, затем 'default'
                tenant_id_from_db = tenant_id_result["tenant_id"] if tenant_id_result else None
                tenant_id = metadata.get("tenant_id") or tenant_id_from_db or "default"
                
                # Context7: Проверяем, изменились ли теги (для метрик)
                # Убрана ссылка на несуществующую колонку tags, используем data->'tags'
                existing_tags_result = await conn.fetchrow(
                    "SELECT data->'tags' as tags FROM post_enrichment WHERE post_id = $1 AND kind = 'tags'",
                    post_id
                )
                existing_tags = existing_tags_result["tags"] if existing_tags_result and existing_tags_result.get("tags") else []
                
                # Структурируем данные для JSONB поля data
                tags_data = {
                    "tags": tags or [],
                    "tags_hash": tags_hash,
                    "provider": metadata.get("provider", "gigachat"),
                    "latency_ms": int(metadata.get("latency_ms") or 0),
                    "tenant_id": tenant_id,  # Context7: Используем tenant_id из БД, не из metadata
                    "trace_id": metadata.get("trace_id", str(uuid.uuid4())),
                    "metadata": {k: v for k, v in metadata.items() if k not in ["provider", "latency_ms", "tenant_id", "trace_id"]}
                }
                
                # Context7: Логирование перед сохранением
                logger.info("Saving tags to DB",
                           post_id=post_id,
                           tags_count=len(tags) if tags else 0,
                           tags_sample=tags[:3] if tags else [],
                           tags_data_count=len(tags_data.get('tags', [])),
                           tenant_id=tenant_id,
                           tenant_id_source="db" if tenant_id_from_db and tenant_id_from_db != "default" else ("metadata" if metadata.get("tenant_id") else "default"))
                
                # Используем EnrichmentRepository (принимает asyncpg.Pool)
                repo = EnrichmentRepository(self.pool)
                await repo.upsert_enrichment(
                    post_id=post_id,
                    kind='tags',
                    provider=metadata.get("provider", "gigachat"),
                    data=tags_data,
                    params_hash=None,  # Теги не версионируются по параметрам модели
                    status='ok',
                    error=None,
                    trace_id=metadata.get("trace_id")
                )
                
                # Если теги не изменились — инкрементируем метрику конфликтов
                if existing_tags == (tags or []):
                    tags_persist_conflicts_total.inc()
                
                # КРИТИЧНО: Публикация в posts.enriched (даже если теги пустые!)
                # Context7: Используем tenant_id из БД, не хардкод 'default'
                enriched_event = {
                    "schema": "posts.enriched.v1",
                    "post_id": post_id,
                    "tenant_id": tenant_id,  # Context7: Используем tenant_id из БД
                    "tags": tags or [],
                    "enrichment": {},  # Будет заполнено crawl4ai
                    "trace_id": metadata.get("trace_id", str(uuid.uuid4())),
                    "ts": datetime.now(timezone.utc).isoformat()
                }
                
                await self.publisher.publish_event("posts.enriched", enriched_event)
                logger.info(f"Published posts.enriched event for post_id={post_id}, tenant_id={tenant_id}")
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check."""
        try:
            # Проверка Redis
            redis_healthy = await self.redis.ping()
            
            # Проверка БД
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_healthy = True
            
            return {
                'status': 'healthy' if (redis_healthy and db_healthy) else 'unhealthy',
                'redis': 'connected' if redis_healthy else 'disconnected',
                'database': 'connected' if db_healthy else 'disconnected'
            }
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return {
                'status': 'unhealthy',
                'error': str(e)
            }


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Запуск tag persistence task."""
    import os
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    db_dsn = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/postgres")
    
    task = TagPersistenceTask(
        redis_url=redis_url,
        db_dsn=db_dsn
    )
    
    try:
        await task.start()
    except KeyboardInterrupt:
        logger.info("TagPersistenceTask stopped by user")
    except Exception as e:
        logger.error("TagPersistenceTask failed", error=str(e))
        raise


if __name__ == "__main__":
    asyncio.run(main())
