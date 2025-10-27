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
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, Any, Optional

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
            logger.error(f"Failed to start TaggingTask: {e}")
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
            # Валидация входного события
            parsed_event = PostParsedEventV1(**event_data)
            
            # МЕТРИКА: попытка обработки
            posts_processed_total.labels(stage='tagging', success='attempt').inc()
            
            # Проверка длины текста (частая причина пропуска!)
            MIN_CHARS = int(os.getenv("TAGGING_MIN_CHARS", "80"))
            if len(parsed_event.text) < MIN_CHARS:
                logger.debug(f"Text too short: {len(parsed_event.text)} < {MIN_CHARS}")
                tagging_requests_total.labels(provider="precheck", model="none", success="skip").inc()
                # Публикуем событие с пустыми тегами (не блокируем пайплайн!)
                await self._publish_tagged_event(parsed_event, [], 0, None)
                posts_processed_total.labels(stage='tagging', success='skip_short').inc()
                return
            
            # Нормализация urls (иногда могут прийти строкой)
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
    
    async def _tag_post(self, parsed_event: PostParsedEventV1) -> Optional[Any]:
        """Тегирование поста через AI адаптер."""
        start_time = time.time()
        try:
            if not self.ai_adapter:
                logger.error("AI adapter not initialized")
                tagging_requests_total.labels(provider="gigachain", model="gigachat", success="error").inc()
                return None
            
            # Тегирование
            results = await self.ai_adapter.generate_tags_batch(
                [parsed_event.text],
                force_immediate=True
            )
            
            processing_time = time.time() - start_time
            tagging_latency_seconds.labels(provider="gigachain", model="gigachat").observe(processing_time)
            
            if results and len(results) > 0:
                tagging_requests_total.labels(provider="gigachain", model="gigachat", success="success").inc()
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

            tagged_event = PostTaggedEventV1(
                idempotency_key=f"{parsed_event.post_id}:tagged:v1",
                post_id=parsed_event.post_id,
                tags=tags_list,
                tags_hash=PostTaggedEventV1.compute_hash(tags_list),
                provider="gigachat",
                latency_ms=int(processing_time * 1000),
                metadata=metadata
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