"""
Tagging Task - Consumer для posts.parsed событий
[C7-ID: WORKER-TAGGING-002]

Обрабатывает события posts.parsed → тегирование через GigaChain → публикация posts.tagged
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import structlog
from prometheus_client import Counter, Histogram, Gauge

from metrics import posts_processed_total
from ai_providers.gigachain_adapter import tagging_requests_total, tagging_latency_seconds

from ai_providers.gigachain_adapter import GigaChainAdapter, create_gigachain_adapter
from event_bus import EventConsumer, RedisStreamsClient, EventPublisher, DLQ_STREAMS
from events.schemas import PostParsedEventV1, PostTaggedEventV1
from feature_flags import feature_flags

logger = structlog.get_logger()

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

# [C7-ID: WORKER-TAGGING-002] - Метрики тегирования (уникальные для tagging_task)
tagging_processed_total = Counter(
    'tagging_processed_total',
    'Total posts processed for tagging',
    ['status']
)

# [C7-ID: WORKER-DLQ-001] - Dead-letter метрики
tagging_dlq_total = Counter(
    'tagging_dlq_total',
    'Total events moved to DLQ',
    ['reason']
)

# [C7-ID: WORKER-TAGGING-003] - Кеш и дедупликация метрики
tagging_cache_size = Gauge(
    'tagging_cache_size',
    'Idempotency cache size'
)

tagging_cache_evictions_total = Counter(
    'tagging_cache_evictions_total',
    'Cache evictions'
)

tagging_redis_dedup_hits_total = Counter(
    'tagging_redis_dedup_hits_total',
    'Redis dedup hits'
)

# ============================================================================
# TAGGING TASK
# ============================================================================

class TaggingTask:
    """
    Consumer для обработки posts.parsed событий.
    
    Поддерживает:
    - Идемпотентность по post_id + content_hash
    - Dead-letter queue при невалидном JSON от LLM
    - Батчевое тегирование для оптимизации
    - Метрики и мониторинг
    """
    
    def __init__(
        self,
        redis_url: str = "redis://redis:6379",
        consumer_group: str = "tagging_workers",
        consumer_name: str = "tagging_worker_1"
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

        # Кеши пользовательских тем и маппинга telegram_id → user_uuid
        self._topics_cache: dict[str, tuple[float, List[str]]] = {}
        self._user_uuid_cache: dict[str, tuple[float, Optional[str]]] = {}
        self._topics_cache_ttl = int(os.getenv("TAGGING_TOPICS_CACHE_TTL", "900"))
        self._user_uuid_cache_ttl = int(os.getenv("TAGGING_USER_UUID_CACHE_TTL", "900"))
        
        logger.info(f"TaggingTask initialized (group={consumer_group}, consumer={consumer_name})")
    
    def _cache_cleanup(self):
        """Очистка кеша по TTL и размеру."""
        now = time.time()
        # TTL eviction
        to_del = []
        for k, ts in self._processed_cache.items():
            if now - ts > self._cache_ttl_sec:
                to_del.append(k)
            else:
                break  # OrderedDict по порядку вставки
        for k in to_del:
            self._processed_cache.pop(k, None)
        
        # Size eviction
        while len(self._processed_cache) > self._cache_max_size:
            self._processed_cache.popitem(last=False)
            tagging_cache_evictions_total.inc()
        
        # Метрика
        tagging_cache_size.set(len(self._processed_cache))
    
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
        # Publisher для публикации событий
        self.publisher = EventPublisher(self.redis_client)
        
        # Инициализация AI адаптера
        self.ai_adapter = await create_gigachain_adapter()
        
        logger.info("TaggingTask initialized successfully")

    async def start(self):
        """Запуск tagging task с неблокирующей обработкой."""
        try:
            # Инициализация
            await self._initialize()
            
            # Создание consumer group
            await self.event_consumer._ensure_consumer_group("posts.parsed")
            
            logger.info("TaggingTask started successfully")
            
            # Context7: Используем consume_forever для правильного паттерна pending → новые
            await self.event_consumer.consume_forever(
                "posts.parsed", 
                self._process_single_message
            )
                    
        except Exception as e:
            logger.error("Failed to start TaggingTask", extra={"error": str(e)})
            raise
    
    async def stop(self):
        """Остановка TaggingTask."""
        try:
            if hasattr(self, 'event_consumer') and self.event_consumer:
                self.event_consumer.running = False
                
            if hasattr(self, 'ai_adapter') and self.ai_adapter:
                await self.ai_adapter.close()
                
            if hasattr(self, 'redis_client') and self.redis_client:
                await self.redis_client.disconnect()
                
            logger.info("TaggingTask stopped")
            
        except Exception as e:
            logger.error(f"Error stopping TaggingTask: {e}")
    
    async def _process_messages(self):
        """Обработка сообщений из стрима."""
        try:
            # Используем start_consuming для обработки сообщений
            await self.event_consumer.start_consuming("posts.parsed", self._process_single_message)
            
        except Exception as e:
            logger.error(f"Error in message processing: {e}")
            # Ошибки логируются в gigachain_adapter
    
    async def _process_single_message(self, message: Dict[str, Any]):
        """Обработка одного сообщения с инкрементацией метрик."""
        try:
            # Парсинг события
            # Redis Stream возвращает данные в поле 'data', 'payload' или напрямую
            if 'payload' in message:
                # Новый формат: {'payload': {...}, 'headers': {}}
                event_data = message['payload']
            elif 'data' in message:
                # Старый формат: {'data': json_bytes}
                event_data = json.loads(message['data'])
            else:
                # Прямой формат
                event_data = message
            
            # Context7: Нормализация полей, которые могут прийти как строки JSON (перед валидацией!)
            # urls нормализация
            urls = event_data.get('urls')
            if isinstance(urls, str):
                try:
                    loaded = json.loads(urls)
                    if isinstance(loaded, list):
                        event_data['urls'] = [u for u in loaded if isinstance(u, str) and u.strip()]
                    elif urls.strip():
                        event_data['urls'] = [urls]
                    else:
                        event_data['urls'] = []
                except json.JSONDecodeError:
                    event_data['urls'] = [urls] if urls.strip() else []
            
            # media_sha256_list нормализация (может прийти как строка JSON)
            media_sha256_list = event_data.get('media_sha256_list')
            if isinstance(media_sha256_list, str):
                # Обработка пустой строки или строки '[]'
                if not media_sha256_list.strip() or media_sha256_list.strip() == '[]':
                    event_data['media_sha256_list'] = []
                else:
                    try:
                        loaded = json.loads(media_sha256_list)
                        if isinstance(loaded, list):
                            event_data['media_sha256_list'] = [m for m in loaded if isinstance(m, str) and m.strip()]
                        else:
                            event_data['media_sha256_list'] = []
                    except json.JSONDecodeError:
                        # Если не JSON, пытаемся как обычную строку (не должно быть, но на всякий случай)
                        event_data['media_sha256_list'] = [media_sha256_list] if media_sha256_list.strip() else []
            elif media_sha256_list is None:
                event_data['media_sha256_list'] = []
            
            # Валидация входного события (после нормализации)
            parsed_event = PostParsedEventV1(**event_data)
            
            # МЕТРИКА: попытка обработки
            posts_processed_total.labels(stage='tagging', success='attempt').inc()
            
            # Проверка длины текста (частая причина пропуска!)
            # Context7: НЕ пропускаем посты с медиа, даже если текст короткий
            # Vision анализ может обогатить пост описанием и OCR текстом
            MIN_CHARS = int(os.getenv("TAGGING_MIN_CHARS", "80"))
            has_media = parsed_event.has_media and parsed_event.media_sha256_list
            text_too_short = len(parsed_event.text) < MIN_CHARS
            
            if text_too_short and not has_media:
                # Пропускаем только если текст короткий И нет медиа
                logger.debug(
                    f"Text too short and no media: {len(parsed_event.text)} < {MIN_CHARS}",
                    post_id=parsed_event.post_id
                )
                tagging_requests_total.labels(provider="precheck", model="none", success="skip").inc()
                # Публикуем событие с пустыми тегами (не блокируем пайплайн!)
                await self._publish_tagged_event(parsed_event, [], 0, None)
                posts_processed_total.labels(stage='tagging', success='skip_short').inc()
                return
            elif text_too_short and has_media:
                # Текст короткий, но есть медиа - продолжим обработку
                # Vision может обогатить пост описанием/OCR для улучшения тегов
                logger.debug(
                    "Text short but has media - will use Vision enrichment if available",
                    post_id=parsed_event.post_id,
                    text_length=len(parsed_event.text),
                    media_count=len(parsed_event.media_sha256_list)
                )
            
            # Проверка идемпотентности
            idempotency_key = f"{parsed_event.post_id}:{parsed_event.content_hash}"
            if await self._check_idempotency(idempotency_key):
                logger.debug(f"Message already processed (post_id={parsed_event.post_id}, idem={idempotency_key})")
                tagging_requests_total.labels(provider="precheck", model="dedup", success="skip").inc()
                posts_processed_total.labels(stage='tagging', success='skip_dedup').inc()
                return
            
            # Тегирование
            start_time = time.time()
            tagging_result = await self._tag_post(parsed_event)
            processing_time = time.time() - start_time
            
            # Грациозная деградация: публикуем даже при отсутствии тегов
            tags_list = []
            language = None
            if tagging_result:
                try:
                    tags_list = list(tagging_result.tags or [])
                except Exception:
                    tags_list = []
                try:
                    language = getattr(tagging_result, 'language', None)
                except Exception:
                    language = None

            # Публикация события posts.tagged (даже с пустыми тегами)
            await self._publish_tagged_event(parsed_event, tags_list, processing_time, language)
            
            # Отметка как обработанное
            await self._mark_processed(idempotency_key)
            
            # МЕТРИКА: успешная обработка
            posts_processed_total.labels(stage='tagging', success='true').inc()
            # Латентность логируется в gigachain_adapter
            
            logger.info(f"Post tagged successfully post_id={parsed_event.post_id} tags_count={len(tags_list)} took={processing_time:.3f}s")
        except json.JSONDecodeError as e:
            # Невалидный JSON - перемещение в DLQ
            logger.error(f"Invalid JSON in message {message.get('id')}: {e}")
            await self._move_to_dlq(message, "invalid_json", str(e))
            tagging_dlq_total.labels(reason='invalid_json').inc()
            posts_processed_total.labels(stage='tagging', success='error').inc()
            
        except Exception as e:
            logger.error(f"Unexpected error processing message {message.get('id')}: {e}")
            posts_processed_total.labels(stage='tagging', success='error').inc()
            # Не гасим ошибку: пусть consumer применит retry/DLQ
            raise
    
    async def _get_vision_enrichment(self, post_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение Vision результатов из БД для обогащения текста при тегировании.
        
        Context7: Гибридный подход - если Vision уже готов, используем его для улучшения тегов.
        Если Vision ещё не готов, тегируем только текст (не блокируем пайплайн).
        """
        try:
            import asyncpg
            import os
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = await asyncpg.connect(db_url)
            
            # Context7: Проверяем наличие Vision результатов в post_enrichment
            row = await conn.fetchrow("""
                SELECT 
                    data->>'description' as vision_description,
                    data->>'ocr_text' as vision_ocr_text,
                    vision_description as legacy_vision_description,
                    vision_ocr_text as legacy_vision_ocr_text
                FROM post_enrichment
                WHERE post_id = $1 AND kind = 'vision'
                LIMIT 1
            """, post_id)
            
            await conn.close()
            
            if not row:
                return None
            
            # Используем новые поля из data или legacy поля
            vision_description = row.get('vision_description') or row.get('legacy_vision_description')
            vision_ocr_text = row.get('vision_ocr_text') or row.get('legacy_vision_ocr_text')
            
            if not vision_description and not vision_ocr_text:
                return None
            
            return {
                'description': vision_description,
                'ocr_text': vision_ocr_text
            }
            
        except Exception as e:
            logger.debug(
                "Failed to get vision enrichment",
                post_id=post_id,
                error=str(e)
            )
            return None

    async def _get_user_topics(self, user_id: Optional[str]) -> List[str]:
        """Получить активные темы пользователя для обогащения события."""
        if not user_id:
            return []

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
        cached_topics = self._topics_cache.get(cache_key)
        if cached_topics and (now - cached_topics[0]) < self._topics_cache_ttl:
            return cached_topics[1]

        topics: List[str] = []
        try:
            import asyncpg

            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = await asyncpg.connect(db_url)
            try:
                if resolved_user_id is None:
                    resolved_user_id = await self._resolve_user_uuid(user_key, conn)
                    self._user_uuid_cache[user_key] = (now, resolved_user_id)
                    cache_key = resolved_user_id or user_key

                row = None
                if resolved_user_id:
                    row = await conn.fetchrow(
                        """
                        SELECT base_topics, dialog_topics
                        FROM user_crawl_triggers
                        WHERE user_id = $1
                        """,
                        resolved_user_id,
                    )
                if row:
                    base_topics = row.get("base_topics")
                    dialog_topics = row.get("dialog_topics")
                    topics = self._normalize_topics_payload(base_topics) or []
                    extra = self._normalize_topics_payload(dialog_topics)
                    if extra:
                        topics.extend(extra)
                elif resolved_user_id:
                    fallback = await conn.fetchrow(
                        "SELECT topics FROM digest_settings WHERE user_id = $1",
                        resolved_user_id,
                    )
                    if fallback:
                        topics = self._normalize_topics_payload(fallback.get("topics"))
            finally:
                await conn.close()

        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load user topics", user_id=user_id, error=str(exc))
            topics = []

        normalized = [topic for topic in (self._prepare_topic(t) for t in topics) if topic]
        self._topics_cache[cache_key] = (now, normalized)
        return normalized

    async def _resolve_user_uuid(self, user_identifier: str, conn) -> Optional[str]:
        """
        Определить UUID пользователя по telegram_id или вернуть исходный UUID.
        """
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
        """Преобразовать json/jsonb payload в список строк."""
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                if isinstance(decoded, list):
                    return [str(item) for item in decoded if item]
            except json.JSONDecodeError:
                return [value]
        return []

    @staticmethod
    def _prepare_topic(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _looks_like_uuid(candidate: str) -> bool:
        try:
            uuid.UUID(str(candidate))
            return True
        except (ValueError, TypeError, AttributeError):
            return False
    
    async def _tag_post(self, parsed_event: PostParsedEventV1) -> Optional[Any]:
        """
        Тегирование поста через AI адаптер с обогащением Vision результатами.
        
        Context7: Гибридный подход (Вариант C):
        - Тегируем текст сразу
        - Если Vision уже готов - добавляем его к тексту для улучшения тегов
        - Если Vision не готов - тегируем только текст (не блокируем пайплайн)
        """
        start_time = time.time()
        try:
            if not self.ai_adapter:
                logger.error("AI adapter not initialized")
                tagging_requests_total.labels(provider="gigachain", model="gigachat", success="error").inc()
                return None
            
            # Context7: Проверяем наличие Vision результатов для обогащения
            text_for_tagging = parsed_event.text
            vision_enrichment = None
            
            if parsed_event.has_media and parsed_event.media_sha256_list:
                # Только если есть медиа - проверяем Vision результаты
                vision_enrichment = await self._get_vision_enrichment(parsed_event.post_id)
                
                if vision_enrichment:
                    # Обогащаем текст Vision данными
                    enrichment_parts = []
                    
                    if vision_enrichment.get('description'):
                        enrichment_parts.append(f"[Изображение: {vision_enrichment['description']}]")
                    
                    if vision_enrichment.get('ocr_text'):
                        enrichment_parts.append(f"[Текст с изображения: {vision_enrichment['ocr_text']}]")
                    
                    if enrichment_parts:
                        text_for_tagging = f"{parsed_event.text}\n\n{' '.join(enrichment_parts)}"
                        logger.debug(
                            "Tagging with vision enrichment",
                            post_id=parsed_event.post_id,
                            original_length=len(parsed_event.text),
                            enriched_length=len(text_for_tagging),
                            has_description=bool(vision_enrichment.get('description')),
                            has_ocr=bool(vision_enrichment.get('ocr_text'))
                        )
            
            # Тегирование (с обогащенным текстом, если Vision готов)
            results = await self.ai_adapter.generate_tags_batch(
                [text_for_tagging],
                force_immediate=True
            )
            
            processing_time = time.time() - start_time
            tagging_latency_seconds.labels(provider="gigachain", model="gigachat").observe(processing_time)
            
            if results and len(results) > 0:
                tagging_requests_total.labels(provider="gigachain", model="gigachat", success="success").inc()
                if vision_enrichment:
                    logger.info(
                        "Post tagged with vision enrichment",
                        post_id=parsed_event.post_id,
                        used_vision=True
                    )
                return results[0]
            else:
                logger.warning(f"No tagging results returned post_id={parsed_event.post_id}")
                tagging_requests_total.labels(provider="gigachain", model="gigachat", success="error").inc()
                return None
                
        except Exception as e:
            logger.error(f"Error in AI tagging for post {parsed_event.post_id}: {e}")
            tagging_requests_total.labels(provider="gigachain", model="gigachat", success="false").inc()
            return None
    
    async def _publish_tagged_event(
        self,
        parsed_event: PostParsedEventV1,
        tags_list: list,
        processing_time: float,
        language: Optional[str] = None
    ):
        """Публикация события posts.tagged."""
        try:
            # Подготовка данных события (упрощённая схема)
            metadata = {"model": "GigaChat:latest"}
            if language:
                metadata["language"] = language
            if not tags_list:
                metadata["reason"] = "no_tags"

            topics = await self._get_user_topics(parsed_event.user_id)

            tagged_event = PostTaggedEventV1(
                idempotency_key=f"{parsed_event.post_id}:tagged:v1",
                post_id=parsed_event.post_id,
                tenant_id=parsed_event.tenant_id,
                user_id=parsed_event.user_id,
                channel_id=parsed_event.channel_id,
                tags=tags_list,
                tags_hash=PostTaggedEventV1.compute_hash(tags_list),
                provider="gigachat",
                latency_ms=int(processing_time * 1000),
                metadata=metadata,
                topics=topics
            )
            
            # Публикация в Redis Streams
            await self.publisher.publish_event(
                "posts.tagged",
                tagged_event
            )
            
            logger.debug(f"Published posts.tagged event post_id={parsed_event.post_id} tags_count={len(tagged_event.tags)}")
            
        except Exception as e:
            logger.error(f"Error publishing tagged event for post {parsed_event.post_id}: {e}")
            raise
    
    async def _move_to_dlq(self, message: Dict[str, Any], reason: str, error_details: str):
        """Перемещение сообщения в Dead Letter Queue."""
        try:
            # Подготовка данных для DLQ (строки/JSON)
            dlq_data = {
                "reason": reason,
                "error": error_details,
                "moved_at": datetime.now(timezone.utc).isoformat(),
                "task": "tagging",
                "original_message": json.dumps(message, ensure_ascii=False) if not isinstance(message, str) else message
            }
            # Публикация напрямую в DLQ стрим
            dlq_stream = DLQ_STREAMS['posts.parsed']
            await self.redis_client.client.xadd(dlq_stream, dlq_data, maxlen=10000)
            logger.warning(f"Message moved to DLQ id={message.get('id')} reason={reason}")
        except Exception as e:
            logger.error(f"Error moving message to DLQ: {e}")
    
    async def _check_idempotency(self, idempotency_key: str) -> bool:
        """Проверка идемпотентности с TTL-LRU кешем и Redis."""
        # 1. Локальный кеш с TTL
        if idempotency_key in self._processed_cache:
            ts = self._processed_cache[idempotency_key]
            if (time.time() - ts) < self._cache_ttl_sec:
                return True
            else:
                self._processed_cache.pop(idempotency_key, None)
        
        # 2. Redis дедупликация
        redis_key = f"tagging:processed:{idempotency_key}"
        exists = await self.redis_client.client.exists(redis_key)
        if exists:
            tagging_redis_dedup_hits_total.inc()
            return True
        
        return False
    
    async def _mark_processed(self, idempotency_key: str):
        """Отметка сообщения как обработанного."""
        # Локальный кеш
        self._processed_cache[idempotency_key] = time.time()
        self._processed_counter += 1
        
        # Периодическая очистка
        if (self._processed_counter % 1000) == 0:
            self._cache_cleanup()
        
        # Redis с TTL
        redis_key = f"tagging:processed:{idempotency_key}"
        await self.redis_client.client.setex(redis_key, self._cache_ttl_sec, "1")
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики tagging task."""
        return {
            'processed_cache_size': len(self._processed_cache),
            'redis_connected': self.redis_client is not None,
            'ai_adapter_available': self.ai_adapter is not None,
            'feature_flags': {
                'gigachat_enabled': feature_flags.gigachat_enabled,
                'openrouter_enabled': feature_flags.openrouter_enabled,
                'available_providers': feature_flags.get_available_ai_providers()
            }
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check для tagging task."""
        try:
            # Проверка Redis
            redis_healthy = False
            if self.redis_client:
                await self.redis_client.ping()
                redis_healthy = True
            
            # Проверка AI адаптера
            ai_healthy = self.ai_adapter is not None
            
            return {
                'status': 'healthy' if (redis_healthy and ai_healthy) else 'unhealthy',
                'redis': 'connected' if redis_healthy else 'disconnected',
                'ai_adapter': 'available' if ai_healthy else 'unavailable',
                'stats': await self.get_stats()
            }
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                'status': 'unhealthy',
                'error': str(e),
                'stats': await self.get_stats()
            }

# ============================================================================
# MAIN FUNCTION
# ============================================================================

async def main():
    """Основная функция запуска tagging task."""
    logger.info("Starting TaggingTask...")
    
    # Конфигурация
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    
    # Создание task
    task = TaggingTask(redis_url)
    
    try:
        # Запуск
        await task.start()
    except KeyboardInterrupt:
        logger.info("TaggingTask stopped by user")
    except Exception as e:
        logger.error(f"TaggingTask failed: {e}")
        raise
    finally:
        if hasattr(task, 'stop'):
            await task.stop()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())