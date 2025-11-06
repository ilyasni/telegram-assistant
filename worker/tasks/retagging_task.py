"""
Retagging Task - Consumer для posts.vision.analyzed событий
Context7 best practice: автоматический ретеггинг после Vision с версионированием и анти-петлями

Обрабатывает события posts.vision.analyzed → проверка версий → ретеггинг → публикация posts.tagged
"""

import asyncio
import hashlib
import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import structlog
from prometheus_client import Counter, Histogram, Gauge

from metrics import posts_processed_total
from ai_providers.gigachain_adapter import tagging_requests_total, tagging_latency_seconds
from ai_providers.gigachain_adapter import GigaChainAdapter, create_gigachain_adapter
from event_bus import EventConsumer, RedisStreamsClient, EventPublisher, DLQ_STREAMS
from events.schemas import PostTaggedEventV1
from events.schemas.posts_vision_v1 import VisionAnalyzedEventV1, VisionSkippedEventV1
from feature_flags import feature_flags

logger = structlog.get_logger()

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

# Context7: Метрики ретеггинга с контролем кардинальности
retagging_processed_total = Counter(
    'retagging_processed_total',
    'Total posts processed for retagging',
    ['changed', 'outcome']  # changed: true|false, outcome: ok|err
)

retagging_duration_seconds = Histogram(
    'retagging_duration_seconds',
    'Retagging processing duration',
    ['changed']  # changed: true|false
)

retagging_dlq_total = Counter(
    'retagging_dlq_total',
    'Total retagging events moved to DLQ',
    ['reason']
)

retagging_skipped_total = Counter(
    'retagging_skipped_total',
    'Total retagging attempts skipped',
    ['reason']  # no_tags_version, vision_not_newer, no_changes
)

# ============================================================================
# RETAGGING TASK
# ============================================================================

class RetaggingTask:
    """
    Consumer для обработки posts.vision.analyzed событий.
    
    Поддерживает:
    - Версионирование: ретеггинг только если vision_version > tags_version
    - Анти-петля: игнорирует события posts.tagged с trigger=vision_retag
    - Идемпотентность через tags_hash
    - Retry с экспоненциальной задержкой
    - DLQ для failed events
    """
    
    def __init__(
        self,
        redis_url: str = "redis://redis:6379",
        consumer_group: str = "retagging_workers",
        consumer_name: str = "retagging_worker_1"
    ):
        self.redis_url = redis_url
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        
        # Redis клиенты
        self.redis_client: Optional[RedisStreamsClient] = None
        self.event_consumer: Optional[EventConsumer] = None
        self.publisher: Optional[EventPublisher] = None
        
        # AI адаптер
        self.ai_adapter: Optional[GigaChainAdapter] = None
        
        # TTL-LRU кеш для идемпотентности
        self._processed_cache: OrderedDict[str, float] = OrderedDict()
        self._cache_max_size = 10_000
        self._cache_ttl_sec = 24 * 3600  # 24 часа
        self._processed_counter = 0
        
        logger.info(f"RetaggingTask initialized (group={consumer_group}, consumer={consumer_name})")
    
    def _cache_cleanup(self):
        """Очистка кеша по TTL и размеру."""
        now = time.time()
        # TTL eviction
        to_del = []
        for k, ts in self._processed_cache.items():
            if now - ts > self._cache_ttl_sec:
                to_del.append(k)
            else:
                break
        for k in to_del:
            self._processed_cache.pop(k, None)
        
        # Size eviction
        while len(self._processed_cache) > self._cache_max_size:
            self._processed_cache.popitem(last=False)
        
        # Метрика
        if hasattr(self, '_cache_gauge'):
            self._cache_gauge.set(len(self._processed_cache))
    
    async def _initialize(self):
        """Инициализация компонентов task."""
        # Подключение к Redis
        self.redis_client = RedisStreamsClient(self.redis_url)
        await self.redis_client.connect()
        
        # Инициализация EventConsumer
        from event_bus import ConsumerConfig
        consumer_config = ConsumerConfig(
            group_name=self.consumer_group,
            consumer_name=self.consumer_name,
            batch_size=50,
            block_time=1000,
            retry_delay=5,
            idle_timeout=300
        )
        self.event_consumer = EventConsumer(self.redis_client, consumer_config)
        self.publisher = EventPublisher(self.redis_client)
        
        # Инициализация AI адаптера
        self.ai_adapter = await create_gigachain_adapter()
        
        logger.info("RetaggingTask initialized successfully")
    
    async def start(self):
        """Запуск retagging task."""
        stream_name = "posts.vision.analyzed"
        
        logger.info("=== RetaggingTask.start() CALLED ===", extra={"stream_name": stream_name})
        
        try:
            logger.info("Starting RetaggingTask.start() method - about to call _initialize()")
            await self._initialize()
            logger.info("RetaggingTask._initialize() completed successfully")
            
            # Context7: Проверка, что stream существует в STREAMS перед использованием
            logger.info("Importing STREAMS from event_bus", extra={"stream_name": stream_name})
            from event_bus import STREAMS
            
            logger.info(
                "STREAMS imported successfully",
                extra={
                    "stream_name": stream_name,
                    "total_streams": len(STREAMS),
                    "sample_streams": list(STREAMS.keys())[:5]
                }
            )
            
            if stream_name not in STREAMS:
                error_msg = f"Stream '{stream_name}' not found in STREAMS. Available: {list(STREAMS.keys())}"
                logger.error(error_msg)
                raise KeyError(error_msg)
            
            logger.info(
                "Stream found in STREAMS, calling consume_forever",
                extra={
                    "stream_name": stream_name,
                    "stream_key": STREAMS[stream_name]
                }
            )
            
            # Context7: consume_forever сам вызовет _ensure_consumer_group, не нужно дублировать
            # Это соответствует паттерну из других tasks (TaggingTask, EnrichmentWorker)
            logger.info("About to call event_consumer.consume_forever()")
            await self.event_consumer.consume_forever(
                stream_name,
                self._process_single_message
            )
            logger.info("consume_forever returned (should not happen normally)")
                    
        except KeyError as ke:
            logger.error(
                "KeyError in RetaggingTask.start()",
                extra={
                    "stream_name": stream_name,
                    "error": str(ke),
                    "error_repr": repr(ke),
                    "error_args": getattr(ke, 'args', None)
                },
                exc_info=True
            )
            raise
        except Exception as e:
            logger.error(
                "Unexpected exception in RetaggingTask.start()",
                extra={
                    "stream_name": stream_name,
                    "error": str(e),
                    "error_repr": repr(e),
                    "error_type": type(e).__name__,
                    "error_args": getattr(e, 'args', None)
                },
                exc_info=True
            )
            raise
    
    async def stop(self):
        """Остановка RetaggingTask."""
        try:
            if hasattr(self, 'event_consumer') and self.event_consumer:
                self.event_consumer.running = False
                
            if hasattr(self, 'ai_adapter') and self.ai_adapter:
                await self.ai_adapter.close()
                
            if hasattr(self, 'redis_client') and self.redis_client:
                await self.redis_client.disconnect()
                
            logger.info("RetaggingTask stopped")
            
        except Exception as e:
            logger.error(f"Error stopping RetaggingTask: {e}")
    
    async def _process_single_message(self, message: Dict[str, Any]):
        """Обработка одного события posts.vision.analyzed."""
        try:
            # Парсинг события
            if 'payload' in message:
                event_data = message['payload']
            elif 'data' in message:
                event_data = json.loads(message['data'])
            else:
                event_data = message
            
            # Context7: Проверка типа события (analyzed vs skipped)
            event_type = event_data.get('event_type', '')
            if event_type == 'posts.vision.skipped':
                # Обработка skipped события - просто логируем
                skipped_event = VisionSkippedEventV1(**event_data)
                logger.info(
                    "Vision skipped event received, no retagging needed",
                    post_id=skipped_event.post_id,
                    trace_id=skipped_event.trace_id,
                    reason_count=len(skipped_event.reasons)
                )
                retagging_skipped_total.labels(reason="vision_skipped").inc()
                return
            
            # Валидация входного события
            analyzed_event = VisionAnalyzedEventV1(**event_data)
            
            post_id = analyzed_event.post_id
            trace_id = analyzed_event.trace_id
            
            # Context7: Проверка версионирования - нужен ли ретеггинг?
            should_retag = await self._should_retag(post_id, analyzed_event)
            
            if not should_retag:
                retagging_skipped_total.labels(reason="version_check_failed").inc()
                logger.debug(
                    "Retagging skipped",
                    post_id=post_id,
                    reason="version_check_failed",
                    trace_id=trace_id
                )
                return
            
            # Получение данных поста для ретеггинга
            post_data = await self._get_post_data(post_id)
            if not post_data:
                logger.warning("Post not found for retagging", post_id=post_id, trace_id=trace_id)
                retagging_skipped_total.labels(reason="post_not_found").inc()
                return
            
            # Получение Vision результатов для обогащения
            vision_enrichment = await self._get_vision_enrichment(post_id)
            
            # Ретеггинг с Vision обогащением
            start_time = time.time()
            retag_result = await self._retag_post(post_data, vision_enrichment, analyzed_event)
            duration = time.time() - start_time
            
            if not retag_result:
                retagging_processed_total.labels(changed="false", outcome="err").inc()
                logger.warning("Retagging failed", post_id=post_id, trace_id=trace_id)
                return
            
            # Проверка изменений тегов
            old_tags_hash = post_data.get('tags_hash')
            new_tags_hash = retag_result.get('tags_hash')
            tags_changed = old_tags_hash != new_tags_hash
            
            if tags_changed:
                # Сохранение новых тегов
                await self._save_new_tags(post_id, retag_result, analyzed_event.vision_version)
                
                # Публикация события posts.tagged с trigger=vision_retag
                await self._publish_tagged_event(
                    post_id=post_id,
                    tags_list=retag_result.get('tags', []),
                    processing_time=duration,
                    vision_version=analyzed_event.vision_version,
                    trace_id=trace_id
                )
                
                retagging_processed_total.labels(changed="true", outcome="ok").inc()
                retagging_duration_seconds.labels(changed="true").observe(duration)
                
                logger.info(
                    "Post retagged successfully",
                    post_id=post_id,
                    old_tags_hash=old_tags_hash[:8] if old_tags_hash else None,
                    new_tags_hash=new_tags_hash[:8],
                    trace_id=trace_id
                )
            else:
                retagging_processed_total.labels(changed="false", outcome="ok").inc()
                retagging_duration_seconds.labels(changed="false").observe(duration)
                retagging_skipped_total.labels(reason="no_changes").inc()
                
                logger.debug(
                    "Retagging: tags unchanged",
                    post_id=post_id,
                    tags_hash=new_tags_hash[:8] if new_tags_hash else None,
                    trace_id=trace_id
                )
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message {message.get('id')}: {e}")
            await self._move_to_dlq(message, "invalid_json", str(e))
            retagging_dlq_total.labels(reason='invalid_json').inc()
            
        except Exception as e:
            logger.error(f"Unexpected error processing message {message.get('id')}: {e}")
            retagging_processed_total.labels(changed="false", outcome="err").inc()
            # Не гасим ошибку: пусть consumer применит retry/DLQ
            raise
    
    async def _should_retag(self, post_id: str, analyzed_event: VisionAnalyzedEventV1) -> bool:
        """
        Context7: Проверка, нужен ли ретеггинг на основе версионирования.
        
        Ретеггинг нужен если:
        - tags_version отсутствует (тегирование еще не было с Vision)
        - vision_version > tags_version (новая версия Vision)
        - features_hash изменился (признаки Vision изменились)
        """
        try:
            import asyncpg
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = await asyncpg.connect(db_url)
            
            try:
                # Проверяем версии в post_enrichment
                row = await conn.fetchrow("""
                    SELECT 
                        metadata->>'tags_version' as tags_version,
                        metadata->>'vision_version' as vision_version,
                        metadata->>'vision_features_hash' as vision_features_hash
                    FROM post_enrichment
                    WHERE post_id = $1 AND kind = 'tags'
                    LIMIT 1
                """, post_id)
                
                if not row:
                    # Тегов нет - ретеггинг нужен
                    return True
                
                tags_version = row.get('tags_version')
                stored_vision_version = row.get('vision_version')
                stored_features_hash = row.get('vision_features_hash')
                
                # Получаем версию Vision из события
                vision_version = analyzed_event.vision_version
                features_hash = analyzed_event.features_hash
                
                # Context7: Сравнение версий
                if not tags_version:
                    # Теги были без версии - ретеггинг нужен
                    return True
                
                if not vision_version:
                    # Vision без версии - используем features_hash
                    if features_hash and features_hash != stored_features_hash:
                        return True
                    return False
                
                # Сравнение версий (упрощенное - если vision_version отличается)
                if vision_version != stored_vision_version:
                    # Новая версия Vision - ретеггинг нужен
                    return True
                
                # Проверка features_hash
                if features_hash and features_hash != stored_features_hash:
                    return True
                
                return False
                
            finally:
                await conn.close()
                
        except Exception as e:
            logger.warning(
                "Failed to check retagging version",
                post_id=post_id,
                error=str(e)
            )
            # При ошибке - пропускаем ретеггинг (graceful degradation)
            return False
    
    async def _get_post_data(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Получение данных поста из БД."""
        try:
            import asyncpg
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = await asyncpg.connect(db_url)
            
            try:
                row = await conn.fetchrow("""
                    SELECT 
                        p.id,
                        p.content,
                        p.has_media,
                        pe.tags,
                        pe.metadata->>'tags_version' as tags_version
                    FROM posts p
                    LEFT JOIN post_enrichment pe ON pe.post_id = p.id AND pe.kind = 'tags'
                    WHERE p.id = $1
                    LIMIT 1
                """, post_id)
                
                if not row:
                    return None
                
                # Context7: Вычисляем tags_hash в Python, а не в SQL
                # PostgreSQL не знает о Python функциях
                tags = row.get('tags') or []
                if isinstance(tags, str):
                    import json
                    tags = json.loads(tags)
                elif hasattr(tags, 'tolist'):
                    tags = tags.tolist()
                
                tags_hash = PostTaggedEventV1.compute_hash([str(t) for t in tags if t])
                
                return {
                    'post_id': str(row['id']),
                    'content': row.get('content') or '',
                    'has_media': row.get('has_media', False),
                    'tags': [str(t) for t in tags if t],
                    'tags_version': row.get('tags_version'),
                    'tags_hash': tags_hash
                }
                
            finally:
                await conn.close()
                
        except Exception as e:
            logger.error(f"Failed to get post data: {e}")
            return None
    
    async def _get_vision_enrichment(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Получение Vision результатов для обогащения текста."""
        try:
            import asyncpg
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = await asyncpg.connect(db_url)
            
            try:
                row = await conn.fetchrow("""
                    SELECT 
                        data->>'description' as vision_description,
                        data->>'ocr_text' as vision_ocr_text,
                        data->'ocr'->>'text' as ocr_text_from_obj
                    FROM post_enrichment
                    WHERE post_id = $1 AND kind = 'vision'
                    LIMIT 1
                """, post_id)
                
                if not row:
                    return None
                
                vision_description = row.get('vision_description')
                vision_ocr_text = row.get('vision_ocr_text') or row.get('ocr_text_from_obj')
                
                if not vision_description and not vision_ocr_text:
                    return None
                
                return {
                    'description': vision_description,
                    'ocr_text': vision_ocr_text
                }
                
            finally:
                await conn.close()
                
        except Exception as e:
            logger.debug(f"Failed to get vision enrichment: {e}")
            return None
    
    async def _retag_post(
        self,
        post_data: Dict[str, Any],
        vision_enrichment: Optional[Dict[str, Any]],
        analyzed_event: VisionAnalyzedEventV1
    ) -> Optional[Dict[str, Any]]:
        """Ретеггинг поста с Vision обогащением."""
        try:
            if not self.ai_adapter:
                logger.error("AI adapter not initialized")
                return None
            
            # Context7: Обогащение текста Vision данными
            text_for_tagging = post_data.get('content', '')
            
            if vision_enrichment:
                enrichment_parts = []
                
                if vision_enrichment.get('description'):
                    enrichment_parts.append(f"[Изображение: {vision_enrichment['description']}]")
                
                if vision_enrichment.get('ocr_text'):
                    enrichment_parts.append(f"[Текст с изображения: {vision_enrichment['ocr_text']}]")
                
                if enrichment_parts:
                    text_for_tagging = f"{text_for_tagging}\n\n{' '.join(enrichment_parts)}"
            
            # Тегирование
            results = await self.ai_adapter.generate_tags_batch(
                [text_for_tagging],
                force_immediate=True
            )
            
            if results and len(results) > 0:
                tags_list = [tag.name for tag in results[0].tags if tag.name]
                tags_hash = PostTaggedEventV1.compute_hash(tags_list)
                
                return {
                    'tags': tags_list,
                    'tags_hash': tags_hash,
                    'provider': results[0].provider,
                    'latency_ms': results[0].processing_time_ms
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error in retagging post {post_data.get('post_id')}: {e}")
            return None
    
    async def _save_new_tags(
        self,
        post_id: str,
        retag_result: Dict[str, Any],
        vision_version: Optional[str]
    ):
        """Сохранение новых тегов в БД с версионированием."""
        try:
            import asyncpg
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = await asyncpg.connect(db_url)
            
            try:
                await conn.execute("""
                    INSERT INTO post_enrichment (
                        post_id, kind, provider, data, status
                    ) VALUES (
                        $1, 'tags', $2, $3::jsonb, 'ok'
                    )
                    ON CONFLICT (post_id, kind) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        data = jsonb_build_object(
                            'tags', $4::text[],
                            'tags_version', $5,
                            'retagged_at', NOW(),
                            'retagged_from_vision_version', $5
                        ),
                        updated_at = NOW()
                """,
                    post_id,
                    retag_result.get('provider', 'gigachat'),
                    json.dumps({'tags': retag_result.get('tags', [])}, ensure_ascii=False),
                    retag_result.get('tags', []),
                    vision_version
                )
                
            finally:
                await conn.close()
                
        except Exception as e:
            logger.error(f"Failed to save new tags: {e}")
            raise
    
    async def _publish_tagged_event(
        self,
        post_id: str,
        tags_list: list,
        processing_time: float,
        vision_version: Optional[str],
        trace_id: str
    ):
        """Публикация события posts.tagged с trigger=vision_retag."""
        try:
            tagged_event = PostTaggedEventV1(
                idempotency_key=f"{post_id}:tagged:v1",
                post_id=post_id,
                tags=tags_list,
                tags_hash=PostTaggedEventV1.compute_hash(tags_list),
                provider="gigachat",
                latency_ms=int(processing_time * 1000),
                metadata={
                    "model": "GigaChat:latest",
                    "retagged": True
                },
                trigger="vision_retag",  # Context7: Анти-петля - RetaggingTask игнорирует такие события
                vision_version=vision_version
            )
            
            # Публикация в Redis Streams
            await self.publisher.publish_event(
                "posts.tagged",
                tagged_event
            )
            
            logger.debug(
                "Published retagged event",
                post_id=post_id,
                tags_count=len(tags_list),
                trigger="vision_retag",
                trace_id=trace_id
            )
            
        except Exception as e:
            logger.error(f"Error publishing retagged event: {e}")
            raise
    
    async def _move_to_dlq(self, message: Dict[str, Any], reason: str, error_details: str):
        """Перемещение сообщения в Dead Letter Queue."""
        try:
            dlq_data = {
                "reason": reason,
                "error": error_details,
                "moved_at": datetime.now(timezone.utc).isoformat(),
                "task": "retagging",
                "original_message": json.dumps(message, ensure_ascii=False) if not isinstance(message, str) else message
            }
            dlq_stream = DLQ_STREAMS.get('posts.vision.analyzed', 'stream:dlq:retagging')
            await self.redis_client.client.xadd(dlq_stream, dlq_data, maxlen=10000)
            logger.warning(f"Message moved to DLQ id={message.get('id')} reason={reason}")
        except Exception as e:
            logger.error(f"Error moving message to DLQ: {e}")

# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def main():
    """Основная функция запуска retagging task."""
    logger.info("Starting RetaggingTask...")
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    
    task = RetaggingTask(redis_url)
    
    try:
        await task.start()
    except KeyboardInterrupt:
        logger.info("RetaggingTask stopped by user")
    except Exception as e:
        logger.error(f"RetaggingTask failed: {e}")
        raise
    finally:
        if hasattr(task, 'stop'):
            await task.stop()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

