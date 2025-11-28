"""
Indexing Task - Consumer для posts.enriched событий
Context7 best practice: индексация в Qdrant и Neo4j с обновлением indexing_status

Обрабатывает события posts.enriched → создание эмбеддингов → индексация → публикация posts.indexed
"""

import asyncio
import os
import re
import time
import structlog
import psycopg2
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

from event_bus import EventConsumer, RedisStreamsClient, EventPublisher
from integrations.qdrant_client import QdrantClient
from integrations.neo4j_client import Neo4jClient
from ai_providers.embedding_service import EmbeddingService

logger = structlog.get_logger()

# Метрики Prometheus
indexing_processed_total = Counter(
    'indexing_processed_total',
    'Total posts indexed',
    ['status']
)

# Context7: Метрики для мониторинга очереди и операций очистки
indexing_queue_size = Gauge(
    'indexing_queue_size',
    'Current size of posts.enriched stream',
    ['stream']
)

indexing_trim_operations_total = Counter(
    'indexing_trim_operations_total',
    'Total number of XTRIM operations performed',
    ['stream', 'status']
)

indexing_processing_duration_seconds = Histogram(
    'indexing_processing_duration_seconds',
    'Time taken to process a single post for indexing',
    ['status']
)

# Context7: Метрика lag по consumer groups (из XINFO GROUPS)
indexing_consumer_lag = Gauge(
    'indexing_consumer_lag',
    'Consumer group lag (messages not yet delivered to any consumer)',
    ['stream', 'group']
)

# Context7: Метрика pending сообщений в PEL
indexing_pending_messages = Gauge(
    'indexing_pending_messages',
    'Number of pending messages in PEL (Pending Entry List)',
    ['stream', 'group']
)

# Context7: Метрика операций XAUTOCLAIM
indexing_autoclaim_operations_total = Counter(
    'indexing_autoclaim_operations_total',
    'Total number of XAUTOCLAIM operations performed',
    ['stream', 'status']
)

# Context7: Метрика количества сообщений, возвращённых XAUTOCLAIM
indexing_autoclaim_messages_total = Counter(
    'indexing_autoclaim_messages_total',
    'Total number of messages claimed via XAUTOCLAIM',
    ['stream']
)


class TenantResolutionError(ValueError):
    """Ошибка разрешения tenant_id для поста при индексации."""
    pass


class ChannelIsolationError(ValueError):
    """Ошибка соответствия канала целевому арендатору."""
    pass

class IndexingTask:
    """
    Consumer для обработки posts.enriched событий.
    
    Поддерживает:
    - Индексацию эмбеддингов в Qdrant
    - Создание графа в Neo4j
    - Обновление indexing_status в БД
    - Публикацию posts.indexed событий
    """
    
    def __init__(
        self,
        redis_url: str,
        qdrant_url: str,
        neo4j_url: str
    ):
        self.redis_url = redis_url
        self.qdrant_url = qdrant_url
        self.neo4j_url = neo4j_url
        
        # Клиенты
        self.redis_client: Optional[RedisStreamsClient] = None
        self.event_consumer: Optional[EventConsumer] = None
        self.qdrant_client: Optional[QdrantClient] = None
        self.neo4j_client: Optional[Neo4jClient] = None
        self.embedding_service: Optional[EmbeddingService] = None
        self.publisher: Optional[EventPublisher] = None
        
        # Context7: Отслеживание последнего обработанного ID для XTRIM
        self.last_processed_id: Optional[str] = None
        self.processed_count_since_trim: int = 0
        self.trim_interval: int = int(os.getenv("INDEXING_TRIM_INTERVAL", "50"))  # Trim каждые N сообщений (уменьшено для более частой очистки)
        
        # Context7: Параллелизм для обработки сообщений
        self.max_concurrent_processing: int = int(os.getenv("INDEXING_CONCURRENCY", "4"))  # Максимум параллельных обработок
        self.processing_semaphore: Optional[asyncio.Semaphore] = None
        
        # Context7: Периодическая обработка подвисших сообщений (PEL) через XAUTOCLAIM
        self.pel_reclaim_interval: int = int(os.getenv("INDEXING_PEL_RECLAIM_INTERVAL", "30"))  # XAUTOCLAIM каждые N секунд
        self.pel_min_idle_ms: int = int(os.getenv("INDEXING_PEL_MIN_IDLE_MS", "60000"))  # Минимальное время простоя для XAUTOCLAIM (60 сек)
        self.last_pel_reclaim_time: float = 0.0
        
        logger.info("IndexingTask initialized",
                   redis_url=redis_url[:50],
                   qdrant_url=qdrant_url,
                   neo4j_url=neo4j_url)
    
    async def start(self):
        """Запуск indexing task."""
        try:
            # Инициализация Redis
            self.redis_client = RedisStreamsClient(self.redis_url)
            await self.redis_client.connect()
            
            # Инициализация EventConsumer
            from event_bus import ConsumerConfig
            # Context7: Увеличиваем batch_size для ускорения обработки
            # Поддержка INDEXING_BATCH_SIZE через env переменную
            batch_size = int(os.getenv("INDEXING_BATCH_SIZE", "50"))
            consumer_config = ConsumerConfig(
                group_name="indexing_workers",
                consumer_name="indexing_worker_1",
                batch_size=batch_size
            )
            self.event_consumer = EventConsumer(self.redis_client, consumer_config)
            logger.info("EventConsumer initialized with batch_size",
                       batch_size=batch_size,
                       group_name=consumer_config.group_name)
            
            # Инициализация Qdrant
            self.qdrant_client = QdrantClient(self.qdrant_url)
            await self.qdrant_client.connect()
            
            # Инициализация Neo4j
            from config import settings
            self.neo4j_client = Neo4jClient(
                uri=self.neo4j_url,
                username=os.getenv("NEO4J_USER", settings.neo4j_username),
                password=os.getenv("NEO4J_PASSWORD", settings.neo4j_password)
            )
            await self.neo4j_client.connect()
            
            # Инициализация EmbeddingService
            from ai_providers.gigachain_adapter import create_gigachain_adapter
            from ai_providers.embedding_service import create_embedding_service
            ai_adapter = await create_gigachain_adapter()
            self.embedding_service = await create_embedding_service(ai_adapter)
            
            # Инициализация Publisher
            self.publisher = EventPublisher(self.redis_client)
            
            # Context7: Инициализация Semaphore для параллельной обработки
            self.processing_semaphore = asyncio.Semaphore(self.max_concurrent_processing)
            logger.info("Processing semaphore initialized",
                       max_concurrent=self.max_concurrent_processing)
            
            # Context7: Создание consumer group перед обработкой backlog
            await self.event_consumer._ensure_consumer_group("posts.enriched")
            
            # Context7: Создание consumer group для выходного потока indexed (для мониторинга через XPENDING)
            # Это позволяет отслеживать лаги и pending сообщения через E2E проверки
            try:
                import redis.asyncio as redis_async
                from event_bus import STREAMS
                indexed_stream = STREAMS.get("posts.indexed", "stream:posts:indexed")
                try:
                    await self.redis_client.client.xgroup_create(
                        indexed_stream,
                        "indexing_monitoring",
                        id='0',
                        mkstream=True
                    )
                    logger.info("Created monitoring consumer group for posts.indexed stream", 
                              stream=indexed_stream, 
                              group="indexing_monitoring")
                except redis_async.ResponseError as e:
                    if "BUSYGROUP" in str(e):
                        logger.debug("Monitoring consumer group for posts.indexed already exists")
                    else:
                        logger.warning("Failed to create monitoring consumer group for posts.indexed", 
                                     error=str(e), 
                                     stream=indexed_stream)
                except Exception as e:
                    logger.warning("Unexpected error creating monitoring consumer group", 
                                 error=str(e), 
                                 error_type=type(e).__name__)
            except Exception as e:
                logger.error("Failed to setup monitoring consumer group for posts.indexed", 
                           error=str(e), 
                           error_type=type(e).__name__)
            
            logger.info("IndexingTask started, consuming posts.enriched events")
            
            # Context7: Устанавливаем running флаг для EventConsumer перед запуском цикла
            # Это необходимо, так как _consume_with_trim проверяет self.event_consumer.running
            self.event_consumer.running = True
            
            # Context7 best practice: обработка backlog при старте
            # Перечитываем сообщения с начала stream для обработки необработанных событий
            backlog_processed = await self._process_backlog_once("posts.enriched")
            if backlog_processed > 0:
                logger.info(f"Processed {backlog_processed} backlog messages from stream")
            
            # Context7: Запуск потребления событий с отслеживанием message_id для XTRIM
            await self._consume_with_trim("posts.enriched", self._process_single_message)
            
        except Exception as e:
            logger.error("Failed to start IndexingTask", extra={"error": str(e)})
            raise
    
    async def _process_backlog_once(self, stream_name: str) -> int:
        """
        Context7 best practice: обработка backlog при старте.
        
        Перечитывает все сообщения из stream через XREADGROUP.
        Работает только если consumer group был пересоздан или stream содержит
        непрочитанные сообщения.
        
        Args:
            stream_name: Имя стрима для обработки
            
        Returns:
            int: Количество обработанных сообщений
        """
        try:
            from event_bus import STREAMS
            
            logger.debug(f"Starting backlog processing for {stream_name}")
            
            if stream_name not in STREAMS:
                logger.error(f"Stream name {stream_name} not found in STREAMS mapping")
                return 0
            
            stream_key = STREAMS[stream_name]
            batch_size = 100
            max_backlog_messages = 500  # Ограничение для безопасности
            processed_count = 0
            
            logger.info(f"Processing backlog for {stream_name} (stream_key: {stream_key})...")
            print(f"[BACKLOG DEBUG] Starting backlog processing for {stream_name}, stream_key={stream_key}", flush=True)
            
            # Проверяем, что redis_client инициализирован
            if not hasattr(self, 'redis_client') or self.redis_client is None:
                error_msg = "redis_client not initialized, cannot process backlog"
                logger.error(error_msg)
                print(f"[BACKLOG DEBUG] ERROR: {error_msg}", flush=True)
                return 0
            
            if not hasattr(self.redis_client, 'client') or self.redis_client.client is None:
                error_msg = "redis_client.client not initialized, cannot process backlog"
                logger.error(error_msg)
                print(f"[BACKLOG DEBUG] ERROR: {error_msg}", flush=True)
                return 0
            
            logger.debug(f"Redis client is ready, proceeding with backlog processing")
            print(f"[BACKLOG DEBUG] Redis client is ready", flush=True)
            
            # Context7 best practice: используем прямое чтение через XRANGE для backlog
            # Это позволяет обработать все сообщения независимо от consumer group состояния
            # Supabase best practice: batch processing с проверкой идемпотентности через БД
            try:
                # Проверяем доступность stream
                logger.debug(f"Getting stream length for {stream_key}...")
                print(f"[BACKLOG DEBUG] Getting stream length for {stream_key}...", flush=True)
                try:
                    stream_length = await self.redis_client.client.xlen(stream_key)
                    logger.info(f"Stream {stream_key} length: {stream_length} messages", 
                               stream_key=stream_key, 
                               length=stream_length)
                    print(f"[BACKLOG DEBUG] Stream length: {stream_length}", flush=True)
                except Exception as e:
                    logger.error(f"Error getting stream length: {e}", 
                               error=str(e), 
                               error_type=type(e).__name__,
                               stream_key=stream_key)
                    import traceback
                    logger.error(traceback.format_exc())
                    return 0
                
                if stream_length == 0:
                    logger.info("Stream is empty, no backlog to process")
                    return 0
                
                # Читаем сообщения напрямую из stream через XRANGE
                # Ограничиваемся последними N сообщениями для безопасности
                logger.info(f"Reading up to {max_backlog_messages} messages from stream {stream_key}...")
                
                # Получаем сообщения через XRANGE (от начала к концу)
                # Используем '-' (начало) до '+' (конец) для чтения всех доступных
                try:
                    messages_data = await self.redis_client.client.xrange(
                        stream_key,
                        min='-',
                        max='+',
                        count=max_backlog_messages
                    )
                    logger.debug(f"XRANGE returned {len(messages_data) if messages_data else 0} messages")
                except Exception as e:
                    logger.error(f"Error calling XRANGE: {e}", error_type=type(e).__name__)
                    import traceback
                    logger.error(traceback.format_exc())
                    return 0
                
                if not messages_data:
                    logger.info("XRANGE returned empty result, no messages to process")
                    return 0
                
                logger.info(f"Found {len(messages_data)} messages in stream, processing...")
                
                # Обрабатываем сообщения (обратный порядок - от новых к старым)
                for message_id, fields in reversed(messages_data):
                    try:
                        # Парсинг события (поля уже декодированы благодаря decode_responses=True)
                        event_data = self.event_consumer._parse_event_data(fields)
                        
                        # Проверяем post_id
                        post_id = event_data.get('post_id') if isinstance(event_data, dict) else event_data.get('payload', {}).get('post_id')
                        if not post_id:
                            logger.debug("Skipping message without post_id", message_id=str(message_id))
                            continue
                        
                        # Supabase best practice: проверка идемпотентности через БД перед обработкой
                        db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
                        conn = psycopg2.connect(db_url)
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT embedding_status FROM indexing_status WHERE post_id = %s", 
                            (post_id,)
                        )
                        row = cursor.fetchone()
                        cursor.close()
                        conn.close()
                        
                        # Пропускаем уже обработанные (идемпотентность)
                        if row and row[0] in ('completed', 'processing'):
                            logger.debug("Skipping already processed post", extra={"post_id": post_id, "status": row[0]})
                            continue
                        
                        # Обработка сообщения
                        await self._process_single_message(event_data)
                        
                        processed_count += 1
                        
                        # Логируем прогресс каждые 20 сообщений
                        if processed_count % 20 == 0:
                            logger.info(f"Backlog progress: {processed_count} messages processed")
                        
                        # Ограничение на количество обработанных сообщений за раз
                        if processed_count >= max_backlog_messages:
                            logger.info(f"Reached max backlog messages limit ({max_backlog_messages}), stopping")
                            break
                            
                    except Exception as e:
                        logger.error(f"Error processing backlog message {message_id}",
                                   error=str(e),
                                   error_type=type(e).__name__,
                                   message_id=str(message_id))
                        # Продолжаем обработку следующих сообщений
                        import traceback
                        logger.debug(traceback.format_exc())
                        continue
                
                logger.info(f"Backlog batch processing completed: {processed_count} new messages processed")
                
            except Exception as e:
                logger.error(f"Error reading backlog: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return 0
            
            if processed_count > 0:
                logger.info(f"Backlog processing completed: {processed_count} messages processed")
            else:
                logger.info("No backlog messages to process or all already processed")
            
            return processed_count
            
        except Exception as e:
            logger.error(f"Error in backlog processing: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return 0
    
    async def _process_single_message(self, message: Dict[str, Any]):
        """
        Обработка одного posts.enriched события.
        
        Context7 best practice: обновление indexing_status на каждом этапе.
        """
        # Парсинг события: EventConsumer передает структуру {'payload': {...}, 'headers': {}}
        if 'payload' in message:
            event_data = message['payload']
        elif 'data' in message:
            # Старый формат: {'data': json_bytes}
            import json
            event_data = json.loads(message['data']) if isinstance(message['data'], (bytes, str)) else message['data']
        else:
            # Прямой формат
            event_data = message
        
        post_id = event_data.get('post_id')
        if not post_id:
            logger.warning("Message without post_id, skipping", message=message, event_data=event_data)
            return
        
        try:
            # Context7: Устанавливаем статус processing в начале обработки
            await self._update_indexing_status(
                post_id=post_id,
                embedding_status='processing',
                graph_status='pending'
            )
            
            # Context7: Получение данных поста с retry для обработки race condition
            # События индексации могут прийти раньше, чем пост сохранен в БД
            post_data = None
            max_retries = 3
            retry_delay = 1  # секунды
            
            for attempt in range(max_retries):
                post_data = await self._get_post_data(post_id)
                if post_data:
                    break
                
                if attempt < max_retries - 1:
                    logger.debug(
                        "Post not found, retrying (race condition)",
                        post_id=post_id,
                        attempt=attempt + 1,
                        max_retries=max_retries
                    )
                    await asyncio.sleep(retry_delay * (attempt + 1))  # Exponential backoff
            
            if not post_data:
                # Context7: [C7-ID: indexing-graceful-001] Graceful degradation для удалённых постов
                # Посты, удалённые после публикации события, помечаем как skipped, а не failed
                logger.info("Post not found after retries, skipping indexing", 
                          post_id=post_id,
                          reason="post_deleted_or_race_condition",
                          retries=max_retries)
                await self._update_indexing_status(
                    post_id=post_id,
                    embedding_status='skipped',
                    graph_status='skipped',
                    error_message='Post not found - likely deleted after event publication or persistent race condition'
                )
                # Context7: Помечаем пост как обработанный для избежания повторных попыток
                await self._update_post_processed(post_id)
                indexing_processed_total.labels(status='skipped').inc()
                return
            
            # Context7: Если tenant_id из post_data это 'default' или None, используем tenant_id из event_data
            # Это позволяет корректно индексировать посты, для которых tenant_id не извлекается из БД
            event_tenant_id = event_data.get('tenant_id')
            if post_data and (not post_data.get('tenant_id') or post_data.get('tenant_id') == 'default'):
                if event_tenant_id and event_tenant_id != 'default':
                    logger.debug(
                        "Using tenant_id from event_data as fallback",
                        post_id=post_id,
                        event_tenant_id=event_tenant_id,
                        post_data_tenant_id=post_data.get('tenant_id')
                    )
                    post_data['tenant_id'] = event_tenant_id
            
            # Context7: [C7-ID: indexing-with-enrichment-001] Проверка текста ПОСЛЕ композиции с enrichment данными
            # Если основной текст пуст, но есть vision/crawl данные - используем их для индексации
            # Это позволяет индексировать посты только с медиа (vision description/OCR)
            text_for_check = post_data.get('text', '')
            has_vision = bool(post_data.get('vision_data'))
            has_crawl = bool(post_data.get('crawl_data'))
            
            # Проверяем, есть ли хотя бы один источник текста для индексации
            if not text_for_check or not text_for_check.strip():
                if not has_vision and not has_crawl:
                    # Нет ни текста, ни enrichment данных - пропускаем
                    logger.info("Post text is empty and no enrichment data, skipping indexing", 
                              post_id=post_id,
                              has_media=post_data.get('has_media', False),
                              has_vision=has_vision,
                              has_crawl=has_crawl)
                    await self._update_indexing_status(
                        post_id=post_id,
                        embedding_status='skipped',
                        graph_status='skipped',
                        error_message='Post text is empty and no enrichment data (vision/crawl) to index'
                    )
                    await self._update_post_processed(post_id)
                    indexing_processed_total.labels(status='skipped').inc()
                    return
                else:
                    # Есть enrichment данные - можем индексировать
                    logger.debug("Post text is empty but has enrichment data, will use enrichment for indexing",
                               post_id=post_id,
                               has_vision=has_vision,
                               has_crawl=has_crawl)
            
            # Context7: [C7-ID: retry-indexing-001] Генерация эмбеддинга с retry через EmbeddingService
            # Retry логика уже встроена в EmbeddingService
            embedding = await self._generate_embedding(post_data)
            
            # Индексация в Qdrant
            # Context7: Передаём event_data для использования tenant_id из события
            vector_id = await self._index_to_qdrant(post_id, post_data, embedding, event_data)
            
            # Индексация в Neo4j
            await self._index_to_neo4j(post_id, post_data)
            
            # Context7: Обновляем статус completed после успешной индексации
            await self._update_indexing_status(
                post_id=post_id,
                embedding_status='completed',
                graph_status='completed',
                vector_id=vector_id
            )
            
            # Context7: Обновляем is_processed в таблице posts после успешной индексации
            await self._update_post_processed(post_id)
            
            # Публикация события posts.indexed
            # Context7: Добавляем tenant_id для multi-tenant изоляции
            # Context7: Используем tenant_id из post_data (уже обновлен через event_data fallback выше)
            # Если tenant_id все еще None или 'default', пытаемся получить из event_data как последний fallback
            tenant_id = post_data.get('tenant_id')
            if not tenant_id or tenant_id == 'default':
                # Приоритет 1: tenant_id из event_data (если передан)
                if event_data and event_data.get('tenant_id') and event_data.get('tenant_id') != 'default':
                    tenant_id = event_data.get('tenant_id')
                    logger.debug(
                        "Using tenant_id from event_data in posts.indexed",
                        post_id=post_id,
                        tenant_id=tenant_id
                    )
                else:
                    # Приоритет 2: запрос к БД
                    tenant_id_db = await self._get_tenant_id_from_post(post_id)
                    if tenant_id_db and tenant_id_db != 'default':
                        tenant_id = tenant_id_db
                        logger.debug(
                            "Using tenant_id from DB in posts.indexed",
                            post_id=post_id,
                            tenant_id=tenant_id
                        )
            
            # Context7: Если все еще не найден, используем fallback на 'default', но логируем предупреждение
            # Context7: КРИТИЧНО: tenant_id должен быть строкой, не None, иначе EventPublisher удалит его из JSON
            if not tenant_id or tenant_id == 'default':
                logger.warning(
                    "tenant_id not found or is 'default' for posts.indexed, using 'default'",
                    post_id=post_id,
                    tenant_id_from_post_data=post_data.get('tenant_id'),
                    tenant_id_from_event_data=event_data.get('tenant_id') if event_data else None,
                    channel_id=post_data.get('channel_id')
                )
                tenant_id = tenant_id or 'default'
            
            # Context7: Гарантируем, что tenant_id всегда строка (не None), иначе EventPublisher._to_json_bytes удалит его
            tenant_id_str = str(tenant_id) if tenant_id else 'default'
            
            await self.publisher.publish_event("posts.indexed", {
                "post_id": post_id,
                "tenant_id": tenant_id_str,  # Context7: Обязательно строка, не None
                "vector_id": vector_id,
                "indexed_at": datetime.now(timezone.utc).isoformat()
            })
            
            indexing_processed_total.labels(status='success').inc()
            logger.info("Post indexed successfully", extra={"post_id": post_id, "vector_id": vector_id})
            
        except Exception as e:
            error_str = str(e)
            error_type = type(e).__name__
            
            # Context7: [C7-ID: retry-indexing-002] Классификация ошибок для определения retry стратегии
            from services.retry_policy import classify_error, ErrorCategory, should_retry
            
            error_category = classify_error(e)
            is_retryable = should_retry(error_category)
            
            logger.error("Failed to process post",
                        post_id=post_id,
                        error=error_str,
                        error_type=error_type,
                        error_category=error_category.value,
                        is_retryable=is_retryable)
            
            indexing_processed_total.labels(status='error').inc()
            
            # Context7: Обновляем статус failed при ошибке
            await self._update_indexing_status(
                post_id=post_id,
                embedding_status='failed',
                graph_status='failed',
                error_message=f"[{error_category.value}] {error_str}"
            )
            
            # Context7: Правильная обработка ошибок для ACK/DLQ flow
            # Для retryable ошибок: пробрасываем исключение, чтобы EventConsumer НЕ ACK'ил сообщение
            # Сообщение останется в PEL для повторной обработки через XAUTOCLAIM
            # Для non-retryable ошибок: НЕ пробрасываем исключение, чтобы EventConsumer ACK'ил
            # EventConsumer сам отправит в DLQ если настроено через _handle_failed_message
            
            if is_retryable:
                # Retryable ошибки - пробрасываем исключение для предотвращения ACK
                # Сообщение останется в PEL для повторной обработки
                logger.warning("Retryable error, message will remain in PEL for retry",
                             post_id=post_id,
                             error_category=error_category.value,
                             error=error_str)
                raise  # Пробрасываем для предотвращения ACK в EventConsumer
            else:
                # Non-retryable ошибки - не пробрасываем, сообщение ACK'ится и вручную отправляется в DLQ
                logger.error("Non-retryable error, message will be ACKed and sent to DLQ",
                           post_id=post_id,
                           error_category=error_category.value,
                           error=error_str)
                if self.publisher:
                    try:
                        await self.publisher.to_dlq(
                            "posts.indexed",
                            {
                                "post_id": post_id,
                                "channel_id": post_data.get('channel_id'),
                                "tenant_id": post_data.get('tenant_id'),
                                "error_type": error_type,
                            },
                            reason=error_category.value,
                            details=error_str
                        )
                    except Exception as dlq_publish_error:
                        logger.warning(
                            "Failed to publish indexing error to DLQ",
                            post_id=post_id,
                            dlq_error=str(dlq_publish_error)
                        )
                # Не пробрасываем исключение - EventConsumer обработает через _handle_failed_message
    
    async def _get_post_data(self, post_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение данных поста из БД с загрузкой enrichment данных из post_enrichment.
        
        Context7 best practice: плоский агрегат всех enrichment данных для downstream-задач
        (эмбеддинги, Qdrant, Neo4j) без необходимости знать о БД-структуре.
        
        Returns:
            Dict с полями: id, channel_id, text, telegram_message_id, created_at,
            vision_data (dict или None), crawl_data (dict или None), tags_data (dict или None)
        """
        try:
            from psycopg2.extras import RealDictCursor
            import json
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Context7: JOIN с post_enrichment для загрузки всех enrichment данных
            # Context7: tenant_id получаем из users через user_channel (приоритет 1), затем из tags_data (приоритет 2), затем из channels.settings (приоритет 3)
            # Context7: Явное приведение типа для tenant_id через CAST для избежания ошибки "COALESCE types text[] and jsonb cannot be matched"
            # Context7: users.tenant_id имеет тип UUID, приводим к text для COALESCE
            # Context7: Добавляем channel_title для Neo4j индексации
            cursor.execute("""
                   SELECT 
                       p.id,
                       p.channel_id,
                       p.content as text,
                       p.telegram_message_id,
                       p.created_at,
                       c.title as channel_title,
                       COALESCE(
                           (SELECT u.tenant_id::text FROM users u 
                            JOIN user_channel uc ON uc.user_id = u.id 
                            WHERE uc.channel_id = c.id 
                            LIMIT 1),
                           CAST(pe_tags.data->>'tenant_id' AS text),
                           CAST(c.settings->>'tenant_id' AS text)
                       ) as tenant_id,
                    NULL as user_id,
                    pe_vision.data as vision_data,
                    pe_crawl.data as crawl_data,
                    pe_tags.data as tags_data
                FROM posts p
                JOIN channels c ON p.channel_id = c.id
                LEFT JOIN post_enrichment pe_vision 
                    ON pe_vision.post_id = p.id AND pe_vision.kind = 'vision'
                LEFT JOIN post_enrichment pe_crawl 
                    ON pe_crawl.post_id = p.id AND pe_crawl.kind = 'crawl'
                LEFT JOIN post_enrichment pe_tags 
                    ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
                WHERE p.id = %s
            """, (post_id,))
            
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if row:
                result = dict(row)
                
                # Context7: Парсинг JSONB полей data из post_enrichment
                # Если данные уже dict (psycopg2 может автоматически парсить JSONB), оставляем как есть
                # Если это строки, парсим через json.loads
                # Context7: Также обрабатываем случаи, когда data это JSONB dict напрямую
                for key in ['vision_data', 'crawl_data', 'tags_data']:
                    value = result.get(key)
                    if value is None:
                        result[key] = None
                    elif isinstance(value, str):
                        try:
                            result[key] = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            result[key] = None
                    elif isinstance(value, dict):
                        # Уже dict (psycopg2 автоматически парсит JSONB)
                        result[key] = value
                    else:
                        # Неожиданный тип - логируем и устанавливаем None
                        logger.warning(f"Unexpected type for {key}", 
                                     post_id=post_id,
                                     value_type=type(value).__name__)
                        result[key] = None
                
                return result
            return None
            
        except Exception as e:
            logger.error("Failed to get post data", extra={"post_id": post_id, "error": str(e)})
            return None
    
    async def _generate_embedding(self, post_data: Dict[str, Any]) -> list:
        """
        Генерация эмбеддинга для поста с включением enrichment данных.
        
        Context7 best practice: композиция текста с приоритетом и лимитами:
        - post.text (2000 символов)
        - vision.description (500)
        - vision.ocr.text (300)
        - crawl.md (1500, заголовки+аннотация)
        - crawl.ocr (300)
        
        Дедуп и нормализация для предотвращения раздувания токенов.
        
        [C7-ID: dev-mode-016] Предполагается, что проверка на пустой текст уже выполнена в вызывающем коде.
        """
        try:
            # Context7: Импорт normalize_text один раз для всех нормализаций
            from ai_providers.embedding_service import normalize_text
            
            # Базовый текст поста (приоритет 1)
            post_text_raw = post_data.get('text', '')
            if post_text_raw and post_text_raw.strip():
                # Context7: Нормализация основного текста для консистентности
                post_text = normalize_text(post_text_raw)[:2000]  # Лимит 2000 символов
                text_parts = [post_text]
            else:
                post_text = ''
                text_parts = []
            
            # Vision enrichment данные (приоритет 2)
            vision_data = post_data.get('vision_data')
            if vision_data and isinstance(vision_data, dict):
                # Vision description (caption)
                vision_desc = vision_data.get('description', '')
                if vision_desc and len(vision_desc.strip()) >= 5:
                    # Context7: Нормализация текста перед добавлением
                    vision_desc_normalized = normalize_text(vision_desc)
                    text_parts.append(vision_desc_normalized[:500])  # Лимит 500 символов
                
                # Vision OCR text
                # Context7: Используем text_enhanced если доступен (приоритет), иначе text
                vision_ocr = vision_data.get('ocr')
                if vision_ocr:
                    if isinstance(vision_ocr, dict):
                        # Приоритет: text_enhanced > text (fallback на оригинал если enhanced отсутствует)
                        ocr_text = vision_ocr.get('text_enhanced') or vision_ocr.get('text', '')
                    else:
                        ocr_text = str(vision_ocr)
                    
                    if ocr_text and ocr_text.strip():
                        # Context7: [C7-ID: ocr-text-normalization-001] Нормализация OCR текста перед использованием
                        # OCR текст часто содержит множественные переносы строк и плохое форматирование
                        # Нормализация удаляет избыточные пробелы и переносы строк для улучшения качества эмбеддингов
                        # text_enhanced уже нормализован, но дополнительная нормализация для консистентности
                        ocr_text_normalized = normalize_text(ocr_text)
                        text_parts.append(ocr_text_normalized[:300])  # Лимит 300 символов
                        
                        # Context7: Логирование метрик качества (coverage, если есть corrections)
                        if isinstance(vision_ocr, dict) and vision_ocr.get('corrections'):
                            corrections_count = len(vision_ocr.get('corrections', []))
                            original_length = len(vision_ocr.get('text', ''))
                            enhanced_length = len(ocr_text)
                            coverage = corrections_count / max(1, len(re.findall(r'\b\w+\b', vision_ocr.get('text', ''))))
                            logger.debug(
                                "Using enhanced OCR text for embedding",
                                post_id=post_data.get('id'),
                                original_length=original_length,
                                enhanced_length=enhanced_length,
                                corrections_count=corrections_count,
                                coverage=round(coverage, 3)
                            )
            
            # Crawl enrichment данные (приоритет 3)
            crawl_data = post_data.get('crawl_data')
            if crawl_data and isinstance(crawl_data, dict):
                # Используем md_excerpt если доступен (первые ~1-2k символов), иначе полный markdown
                crawl_md = crawl_data.get('md_excerpt') or crawl_data.get('markdown') or crawl_data.get('crawl_md', '')
                if crawl_md and crawl_md.strip():
                    # Context7: Нормализация markdown текста перед добавлением
                    crawl_text = normalize_text(crawl_md)[:1500]
                    text_parts.append(crawl_text)
                
                # Crawl OCR (если есть)
                crawl_ocr_texts = crawl_data.get('ocr_texts', [])
                if crawl_ocr_texts and isinstance(crawl_ocr_texts, list):
                    # Берём первый OCR текст (если несколько)
                    if crawl_ocr_texts and isinstance(crawl_ocr_texts[0], dict):
                        ocr_text = crawl_ocr_texts[0].get('text', '')
                    else:
                        ocr_text = str(crawl_ocr_texts[0]) if crawl_ocr_texts else ''
                    
                    if ocr_text and ocr_text.strip():
                        # Context7: Нормализация OCR текста из crawl данных
                        ocr_text_normalized = normalize_text(ocr_text)
                        text_parts.append(ocr_text_normalized[:300])  # Лимит 300 символов
            
            # Объединение всех частей с дедупликацией
            # Context7: [C7-ID: text-deduplication-001] Дедупликация с нормализацией для сравнения
            # Все части уже нормализованы через normalize_text(), но дополнительно нормализуем для сравнения
            seen = set()
            unique_parts = []
            for part in text_parts:
                # Дополнительная нормализация для дедупликации (убираем регистр и лишние пробелы)
                part_normalized = normalize_text(part).lower()
                if part_normalized and part_normalized not in seen:
                    seen.add(part_normalized)
                    # Сохраняем оригинальную нормализованную версию (уже нормализована выше)
                    unique_parts.append(part)
            
            # Финальный текст для эмбеддинга
            # Context7: Объединяем через пробел (не двойной перенос строки) для компактности
            # Все части уже нормализованы через normalize_text()
            final_text = ' '.join(unique_parts) if unique_parts else (post_text if post_text else '')
            
            # Context7: Финальная нормализация для гарантии консистентности
            # (на случай если объединение добавило какие-то проблемы)
            final_text = normalize_text(final_text)
            
            # Защита на случай если проверка пропущена
            if not final_text or not final_text.strip():
                raise ValueError("Post text is empty after enrichment composition - should be checked before calling this method")
            
            # Context7: Используем EmbeddingService для генерации эмбеддинга
            # EmbeddingService также нормализует текст внутри себя, но нормализация здесь гарантирует консистентность
            embedding = await self.embedding_service.generate_embedding(final_text)
            
            logger.debug("Generated embedding with enrichment",
                        post_id=post_data.get('id'),
                        text_length=len(final_text),
                        parts_count=len(unique_parts),
                        has_vision=bool(vision_data),
                        has_crawl=bool(crawl_data))
            
            return embedding
            
        except Exception as e:
            logger.error("Failed to generate embedding",
                        post_id=post_data.get('id'),
                        error=str(e))
            raise
    
    async def _index_to_qdrant(self, post_id: str, post_data: Dict[str, Any], embedding: list, event_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Индексация поста в Qdrant с расширенным payload для фильтрации и фасетирования.
        
        Context7 best practice: расширенный payload с enrichment данными для фильтрации:
        - tags, vision.is_meme, vision.labels, vision.scene, vision.nsfw_score, vision.aesthetic_score
        - crawl.has_crawl, crawl.html_key, crawl.word_count
        - album_id (для постов из альбомов)
        
        В payload храним только фасеты/флаги (< 64KB), полные тексты (md/html) в S3.
        """
        try:
            from config import settings
            
            vector_id = f"{post_id}"
            
            # Context7: Получаем album_id если пост принадлежит альбому
            album_id = await self._get_album_id_for_post(post_id)
            
            # Context7: Получаем tenant_id из post_data (обязательно для multi-tenant)
            # Context7: Используем tenant_id из event_data как fallback, если post_data содержит 'default' или None
            tenant_id = post_data.get('tenant_id')
            
            # Context7: Если tenant_id из post_data это 'default' или None, пытаемся получить из event_data
            if not tenant_id or str(tenant_id).lower() in ('default', 'none'):
                # Приоритет 1: tenant_id из event_data (если передан)
                if event_data:
                    event_tenant = event_data.get('tenant_id')
                    if event_tenant and str(event_tenant).lower() not in ('default', 'none'):
                        tenant_id = event_tenant
                        logger.debug(
                            "Using tenant_id from event_data in _index_to_qdrant",
                            post_id=post_id,
                            tenant_id=tenant_id
                        )
                        # Обновляем post_data для дальнейших шагов
                        post_data['tenant_id'] = tenant_id
                # Приоритет 2: запрос к БД
                if not tenant_id or str(tenant_id).lower() in ('default', 'none'):
                    tenant_id_db = await self._get_tenant_id_from_post(post_id)
                    if tenant_id_db:
                        tenant_id = tenant_id_db
                        post_data['tenant_id'] = tenant_id
            
            tenant_id_str = str(tenant_id) if tenant_id else ""
            if not tenant_id_str or tenant_id_str.lower() in ('default', 'none'):
                logger.error(
                    "Tenant id unresolved for indexing",
                    post_id=post_id,
                    original_tenant=post_data.get('tenant_id'),
                    event_tenant=event_data.get('tenant_id') if event_data else None,
                    channel_id=post_data.get('channel_id')
                )
                raise TenantResolutionError(f"tenant_id unresolved for post {post_id}")
            
            tenant_id = tenant_id_str
            post_data['tenant_id'] = tenant_id
            
            channel_id = post_data.get('channel_id')
            if not channel_id:
                logger.error(
                    "Channel id missing in post data during indexing",
                    post_id=post_id,
                    tenant_id=tenant_id
                )
                raise ChannelIsolationError(f"channel_id missing for post {post_id}")
            channel_id_str = str(channel_id)
            if not await self._channel_belongs_to_tenant(channel_id_str, tenant_id):
                logger.error(
                    "Channel does not belong to tenant",
                    post_id=post_id,
                    tenant_id=tenant_id,
                    channel_id=channel_id_str
                )
                raise ChannelIsolationError(f"Channel {channel_id_str} is not linked to tenant {tenant_id}")
            
            # Базовый payload с tenant_id для фильтрации
            payload = {
                "post_id": post_id,
                "tenant_id": str(tenant_id),  # Context7: обязательное поле для multi-tenant изоляции
                "channel_id": channel_id_str,
                "text_short": post_data.get('text', '')[:500],  # Превью для быстрого доступа
                "telegram_message_id": post_data.get('telegram_message_id'),
                "created_at": post_data.get('created_at').isoformat() if post_data.get('created_at') else None
            }
            
            # Context7: Добавляем album_id в payload если пост из альбома
            if album_id:
                payload["album_id"] = album_id
            
            # Context7: Per-tenant коллекция: t{tenant_id}_posts
            collection_name = f"t{tenant_id}_posts"
            
            # Tags enrichment
            tags_data = post_data.get('tags_data')
            if tags_data and isinstance(tags_data, dict):
                tags_list = tags_data.get('tags', [])
                if tags_list:
                    payload["tags"] = tags_list if isinstance(tags_list, list) else []
            
            # Vision enrichment данные
            vision_data = post_data.get('vision_data')
            if vision_data and isinstance(vision_data, dict):
                vision_payload = {}
                
                # Обязательные поля для фильтрации
                if 'is_meme' in vision_data:
                    vision_payload["is_meme"] = bool(vision_data['is_meme'])
                if 'labels' in vision_data:
                    labels = vision_data['labels']
                    if isinstance(labels, list):
                        vision_payload["labels"] = labels[:20]  # Максимум 20 labels
                    else:
                        vision_payload["labels"] = []
                if 'objects' in vision_data:
                    objects = vision_data['objects']
                    if isinstance(objects, list):
                        vision_payload["objects"] = objects[:10]  # Максимум 10 objects
                
                # Опциональные поля
                if 'scene' in vision_data and vision_data['scene']:
                    vision_payload["scene"] = str(vision_data['scene'])
                if 'nsfw_score' in vision_data and vision_data['nsfw_score'] is not None:
                    vision_payload["nsfw_score"] = float(vision_data['nsfw_score'])
                if 'aesthetic_score' in vision_data and vision_data['aesthetic_score'] is not None:
                    vision_payload["aesthetic_score"] = float(vision_data['aesthetic_score'])
                if 'classification' in vision_data:
                    vision_payload["classification"] = str(vision_data['classification'])
                if 'dominant_colors' in vision_data:
                    colors = vision_data['dominant_colors']
                    if isinstance(colors, list):
                        vision_payload["dominant_colors"] = colors[:5]  # Максимум 5 цветов
                
                if vision_payload:
                    payload["vision"] = vision_payload
            
            # Crawl enrichment данные
            crawl_data = post_data.get('crawl_data')
            if crawl_data and isinstance(crawl_data, dict):
                crawl_payload = {
                    "has_crawl": True
                }
                
                # Извлекаем s3_keys для HTML
                s3_keys = crawl_data.get('s3_keys', {})
                if s3_keys:
                    # s3_keys может быть dict {url: {html: '...', md: '...'}} или просто dict с html/md
                    if isinstance(s3_keys, dict):
                        # Если это dict с url в качестве ключей, берём первый URL
                        first_url = next(iter(s3_keys.keys())) if s3_keys else None
                        if first_url and isinstance(s3_keys[first_url], dict):
                            html_key = s3_keys[first_url].get('html')
                            md_key = s3_keys[first_url].get('md')
                        else:
                            # Простой dict с html/md напрямую
                            html_key = s3_keys.get('html')
                            md_key = s3_keys.get('md')
                        
                        if html_key:
                            crawl_payload["html_key"] = str(html_key)
                
                # Word count
                word_count = crawl_data.get('word_count') or crawl_data.get('meta', {}).get('word_count')
                if word_count:
                    crawl_payload["word_count"] = int(word_count)
                elif crawl_data.get('md_excerpt') or crawl_data.get('markdown'):
                    # Оценочный word_count из текста
                    crawl_text = crawl_data.get('md_excerpt') or crawl_data.get('markdown', '')
                    if crawl_text:
                        crawl_payload["word_count"] = len(crawl_text.split())
                
                payload["crawl"] = crawl_payload
            
            # Context7: Валидация payload через Pydantic перед сохранением
            try:
                from shared.schemas.enrichment_validation import validate_qdrant_payload
                # Добавляем обязательные поля для валидации
                payload_for_validation = payload.copy()
                payload_for_validation.setdefault('has_media', post_data.get('has_media', False))
                payload_for_validation.setdefault('content_length', len(post_data.get('text', '')))
                if post_data.get('posted_at'):
                    if isinstance(post_data['posted_at'], datetime):
                        payload_for_validation['posted_at'] = int(post_data['posted_at'].timestamp())
                    elif isinstance(post_data['posted_at'], str):
                        # Парсим ISO timestamp
                        try:
                            dt = datetime.fromisoformat(post_data['posted_at'].replace('Z', '+00:00'))
                            payload_for_validation['posted_at'] = int(dt.timestamp())
                        except:
                            payload_for_validation['posted_at'] = None
                
                validated_payload = validate_qdrant_payload(payload_for_validation)
                # Конвертируем обратно в dict, но используем валидированные данные
                payload = validated_payload.model_dump(exclude_none=False)
                logger.debug(
                    "Qdrant payload validated successfully",
                    post_id=post_id,
                    payload_size=len(str(payload))
                )
            except Exception as validation_error:
                # Context7: Валидация не критична - логируем но продолжаем
                logger.warning(
                    "Qdrant payload validation failed, continuing without validation",
                    post_id=post_id,
                    error=str(validation_error),
                    error_type=type(validation_error).__name__
                )
                # Продолжаем с оригинальными данными
            
            # Context7: Валидация размера payload (< 64KB для Qdrant)
            import json
            payload_json = json.dumps(payload, default=str)
            payload_size_bytes = len(payload_json.encode('utf-8'))
            
            if payload_size_bytes > 64 * 1024:
                original_payload = payload.copy()  # Сохраняем для логирования
                logger.warning(
                    "Qdrant payload exceeds 64KB, truncating enrichment data",
                    post_id=post_id,
                    payload_size_bytes=payload_size_bytes,
                    payload_size_kb=round(payload_size_bytes / 1024, 2),
                    has_vision=bool(vision_data),
                    has_crawl=bool(crawl_data),
                    has_tags=bool(tags_data)
                )
                # Упрощаем payload, оставляя только критичные поля
                payload = {
                    "post_id": post_id,
                    "channel_id": post_data.get('channel_id'),
                    "text_short": post_data.get('text', '')[:500],
                    "telegram_message_id": post_data.get('telegram_message_id'),
                    "created_at": payload["created_at"]
                }
                # Сохраняем album_id даже при усечении
                if album_id:
                    payload["album_id"] = album_id
                # Добавляем только критичные флаги
                if vision_data and isinstance(vision_data, dict):
                    payload["vision"] = {
                        "is_meme": bool(vision_data.get('is_meme', False))
                    }
                if crawl_data:
                    payload["crawl"] = {"has_crawl": True}
                
                # Логируем что было потеряно
                logger.info(
                    "Qdrant payload truncated - enrichment filters may be limited",
                    post_id=post_id,
                    original_size_kb=round(payload_size_bytes / 1024, 2),
                    truncated_size_kb=round(len(json.dumps(payload, default=str).encode('utf-8')) / 1024, 2)
                )
            
            # Context7: Создаём коллекцию если не существует
            await self.qdrant_client.ensure_collection(
                collection_name=collection_name,
                vector_size=len(embedding)
            )
            
            await self.qdrant_client.upsert_vector(
                collection_name=collection_name,
                vector_id=vector_id,
                vector=embedding,
                payload=payload
            )
            
            logger.debug("Indexed to Qdrant with enrichment payload",
                        post_id=post_id,
                        vector_id=vector_id,
                        has_vision=bool(vision_data),
                        has_crawl=bool(crawl_data),
                        has_tags=bool(tags_data))
            
            return vector_id
            
        except Exception as e:
            logger.error("Failed to index to Qdrant",
                        post_id=post_id,
                        error=str(e))
            raise
    
    async def _get_tenant_id_from_post(self, post_id: str) -> Optional[str]:
        """Получает tenant_id для поста из БД."""
        try:
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import text
            import os
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
            engine = create_async_engine(db_url)
            async_session = async_sessionmaker(engine, expire_on_commit=False)
            
            async with async_session() as session:
                result = await session.execute(
                    text("""
                        SELECT COALESCE(
                            (SELECT u.tenant_id::text FROM users u 
                             JOIN user_channel uc ON uc.user_id = u.id 
                             WHERE uc.channel_id = c.id 
                             LIMIT 1),
                            CAST(pe.data->>'tenant_id' AS text),
                            CAST(c.settings->>'tenant_id' AS text),
                            'default'
                        ) as tenant_id
                        FROM posts p
                        JOIN channels c ON p.channel_id = c.id
                        LEFT JOIN post_enrichment pe 
                            ON pe.post_id = p.id AND pe.kind = 'tags'
                        WHERE p.id = :post_id
                        LIMIT 1
                    """),
                    {"post_id": post_id}
                )
                row = result.fetchone()
                if row:
                    tenant_id_value = row[0]
                    if tenant_id_value:
                        tenant_id_str = str(tenant_id_value)
                        if tenant_id_str.lower() not in ('default', 'none'):
                            return tenant_id_str
                        logger.warning(
                            "Resolved tenant_id falls back to default placeholder",
                            post_id=post_id,
                            tenant_id=tenant_id_str
                        )
        except Exception as e:
            logger.debug(
                "Error getting tenant_id for post",
                post_id=post_id,
                error=str(e)
            )
        return None
    
    async def _channel_belongs_to_tenant(self, channel_id: str, tenant_id: str) -> bool:
        """Проверяет, связан ли канал с указанным tenant."""
        try:
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import text
            import os
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
            engine = create_async_engine(db_url)
            async_session = async_sessionmaker(engine, expire_on_commit=False)
            
            async with async_session() as session:
                result = await session.execute(
                    text("""
                        SELECT EXISTS (
                            SELECT 1
                            FROM user_channel uc
                            JOIN users u ON u.id = uc.user_id
                            WHERE uc.channel_id = CAST(:channel_id AS uuid)
                              AND u.tenant_id = CAST(:tenant_id AS uuid)
                        )
                    """),
                    {"channel_id": channel_id, "tenant_id": tenant_id}
                )
                row = result.scalar()
                return bool(row)
        except Exception as e:
            logger.error(
                "Failed to validate channel tenant mapping",
                channel_id=channel_id,
                tenant_id=tenant_id,
                error=str(e)
            )
        return False
    
    async def _get_album_id_for_post(self, post_id: str) -> Optional[int]:
        """Получает album_id для поста из media_group_items."""
        try:
            # Используем прямые SQL запросы через async БД
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
            from sqlalchemy import text
            import os
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            # Преобразуем в asyncpg URL
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
            engine = create_async_engine(db_url)
            async_session = async_sessionmaker(engine, expire_on_commit=False)
            
            async with async_session() as session:
                result = await session.execute(
                    text("""
                        SELECT mg.id as album_id
                        FROM media_group_items mgi
                        JOIN media_groups mg ON mgi.group_id = mg.id
                        WHERE mgi.post_id = :post_id
                        LIMIT 1
                    """),
                    {"post_id": post_id}
                )
                row = result.fetchone()
                if row:
                    return row[0]
        except Exception as e:
            logger.debug(
                "Error getting album_id for post",
                post_id=post_id,
                error=str(e)
            )
        return None
    
    async def _index_to_neo4j(self, post_id: str, post_data: Dict[str, Any]):
        """Индексация поста в Neo4j граф."""
        try:
            channel_id = post_data.get('channel_id')
            
            # Context7: Получаем album_id для создания связей в Neo4j
            album_id = await self._get_album_id_for_post(post_id)
            if not channel_id:
                logger.warning("No channel_id, skipping Neo4j indexing", extra={"post_id": post_id})
                return
            
            # Context7: Используем create_post_node из Neo4jClient
            # Определяем expires_at (например, 30 дней от created_at)
            created_at = post_data.get('created_at')
            if created_at:
                if isinstance(created_at, str):
                    # Парсинг ISO формата строки
                    try:
                        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    except ValueError:
                        # Fallback: используем текущее время
                        created_dt = datetime.now(timezone.utc)
                elif isinstance(created_at, datetime):
                    created_dt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
                else:
                    created_dt = datetime.now(timezone.utc)
                expires_at_dt = created_dt + timedelta(days=30)
                expires_at = expires_at_dt.isoformat()
            else:
                expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            
            # Context7: Агрегируем все enrichment данные для передачи в create_post_node
            enrichment_data = {}
            if post_data.get('vision_data'):
                enrichment_data['vision'] = post_data.get('vision_data')
            if post_data.get('crawl_data'):
                enrichment_data['crawl'] = post_data.get('crawl_data')
            if post_data.get('tags_data'):
                enrichment_data['tags'] = post_data.get('tags_data')
            
            # Context7: Получаем tenant_id с приоритетами (post_data уже обновлен через event_data fallback выше)
            # Context7: Если tenant_id все еще 'default' или None, пытаемся получить из БД как последний fallback
            tenant_id_for_neo4j = post_data.get('tenant_id')
            if not tenant_id_for_neo4j or tenant_id_for_neo4j == 'default':
                # Приоритет: event_data -> БД -> 'default'
                if event_data and event_data.get('tenant_id') and event_data.get('tenant_id') != 'default':
                    tenant_id_for_neo4j = event_data.get('tenant_id')
                else:
                    tenant_id_db = await self._get_tenant_id_from_post(post_id)
                    if tenant_id_db and tenant_id_db != 'default':
                        tenant_id_for_neo4j = tenant_id_db
                    else:
                        tenant_id_for_neo4j = 'default'
                        logger.warning(
                            "Using 'default' tenant_id for Neo4j (no real tenant_id found)",
                            post_id=post_id,
                            tenant_id_from_post_data=post_data.get('tenant_id'),
                            tenant_id_from_event_data=event_data.get('tenant_id') if event_data else None
                        )
            
            # Context7: Валидация данных узла перед созданием в Neo4j
            node_data = {
                'post_id': post_id,
                'tenant_id': tenant_id_for_neo4j,
                'channel_id': channel_id,
                'content': post_data.get('text'),
                'posted_at': post_data.get('created_at').isoformat() if isinstance(post_data.get('created_at'), datetime) else post_data.get('created_at'),
                'expires_at': expires_at,
                'indexed_at': datetime.now(timezone.utc).isoformat(),
                'enrichment_data': enrichment_data if enrichment_data else None
            }
            
            try:
                from shared.schemas.enrichment_validation import validate_neo4j_post_node
                validated_node = validate_neo4j_post_node(node_data)
                # Конвертируем обратно в dict для передачи в create_post_node
                node_data = validated_node.model_dump(exclude_none=False)
                logger.debug(
                    "Neo4j post node data validated successfully",
                    post_id=post_id
                )
            except Exception as validation_error:
                # Context7: Валидация не критична - логируем но продолжаем
                logger.warning(
                    "Neo4j post node validation failed, continuing without validation",
                    post_id=post_id,
                    error=str(validation_error),
                    error_type=type(validation_error).__name__
                )
                # Продолжаем с оригинальными данными
            
            # Context7: Вызов метода create_post_node с enrichment данными
            # Context7 P2: Добавляем telegram_message_id и tg_channel_id для reply связей
            # Context7: Добавляем posted_at для обогащения графа временными данными
            # Context7: Добавляем channel_title для удобства запросов в Neo4j
            success = await self.neo4j_client.create_post_node(
                post_id=node_data['post_id'],
                user_id=post_data.get('user_id', 'system'),  # Fallback для совместимости
                tenant_id=node_data['tenant_id'],
                channel_id=node_data['channel_id'],
                expires_at=node_data['expires_at'],
                enrichment_data=node_data.get('enrichment_data'),
                indexed_at=node_data['indexed_at'],
                content=node_data.get('content'),
                telegram_message_id=post_data.get('telegram_message_id'),
                tg_channel_id=post_data.get('tg_channel_id'),
                posted_at=node_data.get('posted_at'),
                channel_title=post_data.get('channel_title')
            )
            
            # Context7: Создаём узел альбома и связи если пост из альбома
            if album_id and success:
                await self.neo4j_client.create_album_node_and_relationships(
                    album_id=album_id,
                    post_id=post_id,
                    channel_id=channel_id,
                    tenant_id=tenant_id_for_neo4j  # Context7: Используем тот же tenant_id
                )
            
            if not success:
                raise Exception("create_post_node returned False")
            
            # Context7: Создание Tag relationships
            # Context7: tags_data может быть dict с ключом 'tags' или уже списком
            tags_data = post_data.get('tags_data')
            if tags_data:
                tags_list = None
                
                if isinstance(tags_data, dict):
                    # Формат: {"tags": [...], "tags_hash": "...", "provider": "..."}
                    tags_list = tags_data.get('tags', [])
                elif isinstance(tags_data, list):
                    # Прямой список тегов (legacy формат)
                    tags_list = tags_data
                
                if tags_list and isinstance(tags_list, list) and len(tags_list) > 0:
                    # Преобразуем список строк в список dict для create_tag_relationships
                    tags_dicts = [
                        {'name': tag, 'category': 'general', 'confidence': 1.0}
                        if isinstance(tag, str) else tag
                        for tag in tags_list
                    ]
                    try:
                        await self.neo4j_client.create_tag_relationships(post_id, tags_dicts)
                        logger.debug("Tag and Topic relationships created via IndexingTask",
                                   post_id=post_id,
                                   tags_count=len(tags_dicts))
                    except Exception as e:
                        logger.error("Failed to create tag relationships in IndexingTask",
                                   post_id=post_id,
                                   error=str(e),
                                   tags_count=len(tags_dicts))
                        # Не прерываем индексацию из-за ошибки создания тегов
            
            # Context7: Создание ImageContent nodes для Vision
            vision_data = post_data.get('vision_data')
            if vision_data and isinstance(vision_data, dict):
                # Извлекаем s3_keys из vision_data
                s3_keys = vision_data.get('s3_keys', {})
                image_key = None
                sha256 = None
                
                if isinstance(s3_keys, dict):
                    # s3_keys может быть dict {image: '...', thumb: '...'}
                    image_key = s3_keys.get('image')
                elif isinstance(s3_keys, list) and s3_keys:
                    # Если это список, берём первый элемент
                    image_key = s3_keys[0].get('s3_key') if isinstance(s3_keys[0], dict) else None
                
                # Используем legacy s3_keys_list если s3_keys не содержит image_key
                s3_keys_list = None
                if not image_key:
                    s3_keys_list = vision_data.get('s3_keys_list', [])
                    if s3_keys_list and isinstance(s3_keys_list, list) and s3_keys_list:
                        image_key = s3_keys_list[0].get('s3_key')
                        sha256 = s3_keys_list[0].get('sha256')
                
                if image_key or sha256:
                    labels = vision_data.get('labels', [])
                    # Context7: Предупреждение если sha256 отсутствует
                    if not sha256:
                        logger.warning(
                            "SHA256 not found in vision_data for ImageContent node",
                            post_id=post_id,
                            has_s3_key=bool(image_key),
                            vision_data_keys=list(vision_data.keys()),
                            has_s3_keys_list=bool(s3_keys_list)
                        )
                    
                    # Context7: Извлечение mime_type из БД (media_objects) по sha256
                    mime_type = None
                    if sha256:
                        try:
                            # Получаем mime_type из media_objects по sha256
                            # Используем async execute для запроса к БД
                            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
                            if db_url.startswith("postgresql://"):
                                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
                            
                            engine = create_async_engine(db_url)
                            try:
                                async_session = async_sessionmaker(engine, expire_on_commit=False)
                                
                                async with async_session() as session:
                                    result = await session.execute(
                                        text("""
                                            SELECT mime_type 
                                            FROM media_objects 
                                            WHERE file_sha256 = :sha256 
                                            LIMIT 1
                                        """),
                                        {"sha256": sha256}
                                    )
                                    row = result.fetchone()
                                    if row and row[0]:
                                        mime_type = row[0]
                            finally:
                                # Context7: Закрываем engine после использования
                                await engine.dispose()
                        except Exception as e:
                            logger.debug(
                                "Failed to get mime_type from DB for ImageContent node",
                                post_id=post_id,
                                sha256=sha256[:16] + "..." if sha256 and len(sha256) > 16 else sha256,
                                error=str(e)
                            )
                            # Не критично - продолжаем без mime_type
                    
                    await self.neo4j_client.create_image_content_node(
                        post_id=post_id,
                        sha256=sha256 or 'unknown',  # Fallback если нет sha256
                        s3_key=image_key,
                        mime_type=mime_type,
                        vision_classification=vision_data.get('classification'),
                        is_meme=vision_data.get('is_meme', False),
                        labels=labels if isinstance(labels, list) else [],
                        provider=vision_data.get('provider', 'gigachat')
                    )
            
            # Context7: Создание WebPage nodes для Crawl
            crawl_data = post_data.get('crawl_data')
            if crawl_data and isinstance(crawl_data, dict):
                urls_metadata = crawl_data.get('urls', [])
                s3_keys = crawl_data.get('s3_keys', {})
                
                # Обрабатываем каждый URL из crawl_data
                for url_info in urls_metadata if isinstance(urls_metadata, list) else []:
                    if isinstance(url_info, dict):
                        url = url_info.get('url')
                        url_hash = url_info.get('url_hash')
                        content_sha256 = url_info.get('content_sha256')
                        
                        # Извлекаем s3_html_key из s3_keys
                        html_key = None
                        if s3_keys:
                            if isinstance(s3_keys, dict):
                                # s3_keys может быть {url: {html: '...', md: '...'}}
                                if url in s3_keys and isinstance(s3_keys[url], dict):
                                    html_key = s3_keys[url].get('html')
                        
                        if url:
                            await self.neo4j_client.create_webpage_node(
                                post_id=post_id,
                                url=url,
                                s3_html_key=html_key,
                                url_hash=url_hash,
                                content_sha256=content_sha256
                            )
            
            # Context7: Создание Entity nodes из OCR сущностей
            if vision_data and isinstance(vision_data, dict):
                vision_ocr = vision_data.get('ocr')
                if vision_ocr and isinstance(vision_ocr, dict):
                    ocr_entities = vision_ocr.get('entities', [])
                    if ocr_entities and isinstance(ocr_entities, list):
                        # Используем text_enhanced для контекста если доступен
                        ocr_text = vision_ocr.get('text_enhanced') or vision_ocr.get('text', '')
                        await self.neo4j_client.create_ocr_entities(
                            post_id=post_id,
                            entities=ocr_entities,
                            ocr_context=ocr_text
                        )
                        logger.debug(
                            "OCR entities indexed to Neo4j",
                            post_id=post_id,
                            entities_count=len(ocr_entities)
                        )
            
            logger.debug("Indexed to Neo4j with enrichment",
                        post_id=post_id,
                        channel_id=channel_id,
                        has_tags=bool(tags_data),
                        has_vision=bool(vision_data),
                        has_crawl=bool(crawl_data))
            
        except Exception as e:
            logger.error("Failed to index to Neo4j",
                        post_id=post_id,
                        error=str(e))
            raise
    
    async def _update_indexing_status(
        self,
        post_id: str,
        embedding_status: str,
        graph_status: str,
        vector_id: Optional[str] = None,
        error_message: Optional[str] = None
    ):
        """
        Context7 best practice: Обновление indexing_status в БД после индексации.
        
        Supabase best practice: Используем параметризованные запросы для безопасности.
        
        Args:
            post_id: ID поста
            embedding_status: Статус эмбеддинга (pending/processing/completed/failed/skipped)
            graph_status: Статус графа (pending/processing/completed/failed/skipped)
            vector_id: ID вектора в Qdrant
            error_message: Сообщение об ошибке (если есть)
        """
        try:
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor()
            
            # Context7: Supabase best practice - параметризованные запросы, атомарный upsert
            processing_started_at = datetime.now(timezone.utc) if embedding_status == 'processing' else None
            
            cursor.execute("""
                INSERT INTO indexing_status (
                    post_id, 
                    embedding_status, 
                    graph_status, 
                    vector_id, 
                    error_message, 
                    processing_started_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (post_id) DO UPDATE SET
                    embedding_status = EXCLUDED.embedding_status,
                    graph_status = EXCLUDED.graph_status,
                    vector_id = COALESCE(EXCLUDED.vector_id, indexing_status.vector_id),
                    error_message = EXCLUDED.error_message,
                    processing_started_at = COALESCE(
                        indexing_status.processing_started_at, 
                        EXCLUDED.processing_started_at
                    ),
                    processing_completed_at = CASE 
                        WHEN EXCLUDED.embedding_status = 'completed' 
                         AND EXCLUDED.graph_status = 'completed' 
                        THEN NOW() 
                        WHEN EXCLUDED.embedding_status = 'skipped' 
                         AND EXCLUDED.graph_status = 'skipped' 
                        THEN NOW()  -- [C7-ID: dev-mode-016] skipped посты тоже считаются обработанными
                        ELSE indexing_status.processing_completed_at 
                    END
            """, (
                post_id,
                embedding_status,
                graph_status,
                vector_id,
                error_message,
                processing_started_at
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info("Updated indexing_status", 
                       post_id=post_id,
                       embedding_status=embedding_status,
                       graph_status=graph_status,
                       vector_id=vector_id)
            
        except Exception as e:
            logger.error("Failed to update indexing_status", 
                        post_id=post_id, 
                        error=str(e),
                        error_type=type(e).__name__)
            # Не пробрасываем ошибку, чтобы не блокировать основной поток
    
    async def _update_post_processed(self, post_id: str):
        """
        Context7: Обновление is_processed в таблице posts после успешной индексации.
        
        Supabase best practice: параметризованные запросы для безопасности.
        """
        try:
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE posts 
                SET is_processed = true 
                WHERE id = %s
            """, (post_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.debug("Updated posts.is_processed", extra={"post_id": post_id})
            
        except Exception as e:
            logger.warning("Failed to update posts.is_processed", 
                         extra={"post_id": post_id, "error": str(e)})
            # Не пробрасываем ошибку - это не критично для работы пайплайна
    
    async def _trim_processed_messages(self, stream_name: str, last_id: Optional[str] = None) -> int:
        """
        Context7 best practice: периодическая очистка обработанных сообщений через XTRIM.
        
        Redis Streams не удаляют сообщения автоматически после XACK. Нужен периодический XTRIM
        для предотвращения роста стрима до бесконечности.
        
        Args:
            stream_name: Имя стрима для очистки
            last_id: Последний обработанный ID (если None, используется self.last_processed_id)
            
        Returns:
            int: Количество удалённых сообщений
        """
        try:
            from event_bus import STREAMS
            
            if stream_name not in STREAMS:
                logger.warning(f"Stream name '{stream_name}' not found in STREAMS mapping")
                return 0
            
            stream_key = STREAMS[stream_name]
            trim_id = last_id or self.last_processed_id
            
            if not trim_id:
                logger.debug("No last processed ID available for trimming")
                return 0
            
            # Context7: XTRIM с MINID для удаления всех сообщений до указанного ID
            # Context7 best practice: XTRIM MINID удаляет все сообщения с ID < minid (строго меньше, не включительно)
            # Для безопасности используем консервативный подход - используем минимальный гарантированно ACK'нутый ID
            try:
                # Context7: Проверяем текущий размер стрима перед XTRIM
                current_length = await self.redis_client.client.xlen(stream_key)
                
                if current_length == 0:
                    logger.debug("Stream is empty, nothing to trim", stream=stream_name)
                    return 0
                
                # Context7: Проверяем, есть ли сообщения старше trim_id для удаления
                # Используем XRANGE для проверки первого сообщения в стриме
                first_messages = await self.redis_client.client.xrange(stream_key, min='-', max='+', count=1)
                first_id = first_messages[0][0] if first_messages else None
                
                if not first_id:
                    logger.debug("Stream is empty, nothing to trim", stream=stream_name)
                    return 0
                
                # Context7: Сравниваем первый ID с trim_id
                # Если первый ID >= trim_id, значит все сообщения новее и ничего не нужно удалять
                compare_result = self._compare_message_ids(first_id, trim_id)
                if compare_result >= 0:
                    logger.debug("All messages are newer than or equal to trim_id, nothing to trim",
                               stream=stream_name,
                               first_id=first_id,
                               trim_id=trim_id,
                               current_length=current_length)
                    return 0
                
                logger.debug("Before XTRIM",
                            stream=stream_name,
                            current_length=current_length,
                            first_id=first_id,
                            trim_id=trim_id)
                
                # Context7: Безопасный XTRIM - проверяем, что сообщения ACK'нуты
                # Context7 best practice: не триммируем основной стрим "вслепую" по MAXLEN,
                # пока не подтвердили, что записи ACK'нуты всеми группами
                # Используем стратегию "min-id after last-ack-per-group"
                
                # Проверяем pending сообщения в группе перед XTRIM
                groups_info = await self.redis_client.client.xinfo_groups(stream_key)
                min_pending_id = None
                for group_info in groups_info:
                    group_name_bytes = group_info.get(b'name', b'')
                    if group_name_bytes:
                        group_name = group_name_bytes.decode() if isinstance(group_name_bytes, bytes) else str(group_name_bytes)
                        
                        # Context7: Получаем минимальный ID из pending сообщений
                        try:
                            pending_info = await self.redis_client.client.xpending_range(
                                stream_key,
                                group_name,
                                min='-',
                                max='+',
                                count=1
                            )
                            if pending_info:
                                pending_id = pending_info[0].get('message_id')
                                if pending_id:
                                    pending_id_str = pending_id.decode() if isinstance(pending_id, bytes) else str(pending_id)
                                    if not min_pending_id or self._compare_message_ids(pending_id_str, min_pending_id) < 0:
                                        min_pending_id = pending_id_str
                        except Exception as e:
                            logger.debug("Failed to get pending info", group=group_name, error=str(e))
                
                # Context7: Если есть pending сообщения, используем минимальный pending ID вместо last-delivered-id
                # Это гарантирует, что мы не удалим ещё недоставленные сообщения
                safe_trim_id = min_pending_id if min_pending_id and self._compare_message_ids(min_pending_id, trim_id) < 0 else trim_id
                
                if safe_trim_id != trim_id:
                    logger.debug("Using safe trim_id based on pending messages",
                               original_trim_id=trim_id,
                               safe_trim_id=safe_trim_id,
                               min_pending_id=min_pending_id)
                
                # Context7: XTRIM с MINID - удаляет все сообщения с ID < minid (строго меньше)
                # approximate=True для лучшей производительности
                # Используем safe_trim_id - Redis удалит все сообщения до этого ID (не включительно)
                trimmed_count = await self.redis_client.client.xtrim(
                    stream_key,
                    minid=safe_trim_id,
                    approximate=True
                )
                
                # Context7: Проверяем результат после XTRIM
                new_length = await self.redis_client.client.xlen(stream_key)
                
                if trimmed_count > 0:
                    indexing_trim_operations_total.labels(stream=stream_name, status='success').inc()
                    indexing_queue_size.labels(stream=stream_name).set(new_length)
                    logger.info("Trimmed processed messages from stream",
                              stream=stream_name,
                              stream_key=stream_key,
                              trimmed_count=trimmed_count,
                              before_length=current_length,
                              after_length=new_length,
                              last_id=trim_id)
                else:
                    # Context7: Логируем даже если ничего не удалено для диагностики
                    logger.debug("No messages trimmed (possibly all messages are newer than trim_id)",
                               stream=stream_name,
                               trim_id=trim_id,
                               current_length=current_length)
                
                return trimmed_count if trimmed_count else 0
                
            except Exception as e:
                error_str = str(e)
                # Redis может не поддерживать MINID в старых версиях - используем MAXLEN как fallback
                if "MINID" in error_str or "syntax" in error_str.lower():
                    logger.warning("XTRIM MINID not supported, using MAXLEN fallback",
                                 stream=stream_name,
                                 error=error_str)
                    # Fallback: получаем текущий размер и оставляем последние N сообщений
                    current_length = await self.redis_client.client.xlen(stream_key)
                    if current_length > 1000:  # Trim только если больше 1000 сообщений
                        keep_count = 500  # Оставляем последние 500
                        trimmed_count = await self.redis_client.client.xtrim(
                            stream_key,
                            maxlen=keep_count,
                            approximate=True
                        )
                        if trimmed_count > 0:
                            indexing_trim_operations_total.labels(stream=stream_name, status='fallback').inc()
                            indexing_queue_size.labels(stream=stream_name).set(
                                await self.redis_client.client.xlen(stream_key)
                            )
                            logger.info("Trimmed stream using MAXLEN fallback",
                                      stream=stream_name,
                                      trimmed_count=trimmed_count)
                        return trimmed_count
                    return 0
                else:
                    indexing_trim_operations_total.labels(stream=stream_name, status='error').inc()
                    logger.error("Failed to trim processed messages",
                               stream=stream_name,
                               error=error_str,
                               error_type=type(e).__name__)
                    return 0
                    
        except Exception as e:
            logger.error("Error in _trim_processed_messages",
                       stream=stream_name,
                       error=str(e),
                       error_type=type(e).__name__)
            return 0
    
    async def _reclaim_pending_messages(self, stream_name: str) -> int:
        """
        Context7 best practice: периодическая обработка подвисших сообщений (PEL) через XAUTOCLAIM.
        
        XAUTOCLAIM возвращает сообщения, которые были прочитаны, но не ACK'нуты из-за:
        - Падений воркеров
        - Таймаутов обработки
        - Ошибок обработки
        
        Без этого lag не уйдёт, а новые батчи будут отставать.
        
        Args:
            stream_name: Имя стрима для обработки
            
        Returns:
            int: Количество сообщений, возвращённых XAUTOCLAIM
        """
        try:
            from event_bus import STREAMS
            
            if stream_name not in STREAMS:
                logger.warning(f"Stream name '{stream_name}' not found in STREAMS mapping")
                return 0
            
            stream_key = STREAMS[stream_name]
            
            # Context7: XAUTOCLAIM с JUSTID для лучшей производительности
            # Используем min_idle_time для фильтрации только старых сообщений
            try:
                result = await self.redis_client.client.xautoclaim(
                    stream_key,
                    self.event_consumer.config.group_name,
                    self.event_consumer.config.consumer_name,
                    min_idle_time=self.pel_min_idle_ms,
                    start_id="0-0",
                    count=self.event_consumer.config.batch_size,
                    justid=False  # Нужны данные для обработки
                )
                
                # Context7: XAUTOCLAIM возвращает [next_id, messages] или [next_id, messages, deleted_ids]
                if not result or len(result) < 2:
                    indexing_autoclaim_operations_total.labels(stream=stream_name, status='no_messages').inc()
                    return 0
                
                messages = result[1] if result[1] else []
                
                if not messages:
                    indexing_autoclaim_operations_total.labels(stream=stream_name, status='no_messages').inc()
                    return 0
                
                # Context7: Обрабатываем возвращённые сообщения
                reclaimed_count = len(messages)
                indexing_autoclaim_operations_total.labels(stream=stream_name, status='success').inc()
                indexing_autoclaim_messages_total.labels(stream=stream_name).inc(reclaimed_count)
                
                logger.debug("XAUTOCLAIM returned messages",
                           stream=stream_name,
                           count=reclaimed_count,
                           min_idle_ms=self.pel_min_idle_ms)
                
                # Context7: Обрабатываем сообщения через основной handler
                # Используем параллельную обработку, если доступна
                for msg_id, fields in messages:
                    try:
                        # Парсинг события
                        event_data = self.event_consumer._parse_event_data(fields)
                        
                        # Обработка события
                        await self._process_single_message(event_data)
                        
                        # ACK сообщения
                        await self.redis_client.client.xack(
                            stream_key,
                            self.event_consumer.config.group_name,
                            msg_id
                        )
                        
                        logger.debug("Reclaimed and processed pending message",
                                   stream=stream_name,
                                   message_id=msg_id)
                        
                    except Exception as e:
                        logger.warning("Failed to process reclaimed message",
                                     stream=stream_name,
                                     message_id=msg_id,
                                     error=str(e))
                        # Не ACK'им - сообщение останется в PEL для повторной обработки
                
                return reclaimed_count
                
            except Exception as e:
                error_str = str(e)
                indexing_autoclaim_operations_total.labels(stream=stream_name, status='error').inc()
                logger.error("XAUTOCLAIM failed",
                           stream=stream_name,
                           error=error_str,
                           error_type=type(e).__name__)
                return 0
                
        except Exception as e:
            logger.error("Error in _reclaim_pending_messages",
                       stream=stream_name,
                       error=str(e),
                       error_type=type(e).__name__)
            return 0
    
    def _compare_message_ids(self, id1: str, id2: str) -> int:
        """
        Context7: Сравнение двух message ID для определения какая новее.
        
        Returns:
            -1 если id1 < id2, 0 если равны, 1 если id1 > id2
        """
        try:
            # Формат ID: timestamp-counter
            parts1 = id1.split('-')
            parts2 = id2.split('-')
            
            if len(parts1) != 2 or len(parts2) != 2:
                return 0
            
            ts1, cnt1 = int(parts1[0]), int(parts1[1])
            ts2, cnt2 = int(parts2[0]), int(parts2[1])
            
            if ts1 < ts2:
                return -1
            elif ts1 > ts2:
                return 1
            else:
                # Если timestamp равны, сравниваем counter
                if cnt1 < cnt2:
                    return -1
                elif cnt1 > cnt2:
                    return 1
                else:
                    return 0
        except (ValueError, IndexError):
            return 0
    
    async def _consume_with_trim(self, stream_name: str, handler_func):
        """
        Context7 best practice: потребление событий с периодической очисткой через XTRIM.
        
        Обёртка над EventConsumer.consume_forever с отслеживанием последнего обработанного ID
        и периодическим вызовом XTRIM для уменьшения размера стрима.
        
        Args:
            stream_name: Имя стрима для потребления
            handler_func: Функция-обработчик события
        """
        from event_bus import STREAMS
        
        if stream_name not in STREAMS:
            raise KeyError(f"Stream name '{stream_name}' not found in STREAMS")
        
        stream_key = STREAMS[stream_name]
        
        # Context7: Обновляем метрики очереди, lag и pending
        async def update_queue_metrics():
            try:
                queue_size = await self.redis_client.client.xlen(stream_key)
                indexing_queue_size.labels(stream=stream_name).set(queue_size)
                
                # Context7: Обновляем метрики lag и pending из XINFO GROUPS
                groups_info = await self.redis_client.client.xinfo_groups(stream_key)
                for group_info in groups_info:
                    group_name_bytes = group_info.get(b'name', b'')
                    if group_name_bytes:
                        group_name = group_name_bytes.decode() if isinstance(group_name_bytes, bytes) else str(group_name_bytes)
                        
                        # Context7: Lag - количество сообщений, не доставленных ни одному consumer
                        lag_bytes = group_info.get(b'lag', 0)
                        lag = lag_bytes if isinstance(lag_bytes, int) else (int(lag_bytes.decode()) if isinstance(lag_bytes, bytes) else 0)
                        indexing_consumer_lag.labels(stream=stream_name, group=group_name).set(lag)
                        
                        # Context7: Pending - количество сообщений в PEL
                        pending_bytes = group_info.get(b'pending', 0)
                        pending = pending_bytes if isinstance(pending_bytes, int) else (int(pending_bytes.decode()) if isinstance(pending_bytes, bytes) else 0)
                        indexing_pending_messages.labels(stream=stream_name, group=group_name).set(pending)
                        
            except Exception as e:
                logger.debug("Failed to update queue metrics", error=str(e))
        
        await update_queue_metrics()
        
        # Context7: Основной цикл потребления с периодической очисткой
        iteration = 0
        while self.event_consumer.running:
            try:
                # Context7: Обрабатываем батч сообщений с параллелизмом
                processed = await self._consume_batch_with_parallelism(stream_name, handler_func)
                logger.debug("Consume batch completed",
                           stream=stream_name,
                           processed_count=processed,
                           processed_count_since_trim=self.processed_count_since_trim,
                           last_processed_id=self.last_processed_id,
                           iteration=iteration)
                
                # Context7: Получаем last-delivered-id из consumer group для XTRIM
                # Context7: processed_count_since_trim уже обновлён в _consume_batch_with_parallelism
                # (там учитываются все ACK'нутые сообщения, включая пропущенные без post_id)
                if processed > 0 or self.processed_count_since_trim > 0:
                    try:
                        groups_info = await self.redis_client.client.xinfo_groups(stream_key)
                        for group_info in groups_info:
                            if group_info.get(b'name', b'').decode() == self.event_consumer.config.group_name:
                                last_id_bytes = group_info.get(b'last-delivered-id', b'0-0')
                                if last_id_bytes:
                                    last_id_str = last_id_bytes.decode() if isinstance(last_id_bytes, bytes) else str(last_id_bytes)
                                    if last_id_str and last_id_str != '0-0':
                                        # Context7: Используем last-delivered-id как fallback, если last_processed_id не установлен
                                        if not self.last_processed_id or self._compare_message_ids(last_id_str, self.last_processed_id) > 0:
                                            self.last_processed_id = last_id_str
                                            logger.debug("Updated last_processed_id from consumer group",
                                                       last_id=self.last_processed_id)
                                    break
                    except Exception as e:
                        logger.debug("Failed to get last-delivered-id", error=str(e))
                    
                    # Context7: Периодическая очистка после обработки (не ждём 10 итераций)
                    # Проверяем каждую итерацию, если достигли интервала
                    if self.processed_count_since_trim >= self.trim_interval and self.last_processed_id:
                        trimmed = await self._trim_processed_messages(stream_name, self.last_processed_id)
                        if trimmed > 0:
                            logger.info("Periodic trim after processing batch",
                                      stream=stream_name,
                                      trimmed_count=trimmed,
                                      processed_since_trim=self.processed_count_since_trim,
                                      last_id=self.last_processed_id)
                        self.processed_count_since_trim = 0
                
                # Context7: Периодическая обработка подвисших сообщений (PEL) через XAUTOCLAIM
                current_time = time.time()
                if current_time - self.last_pel_reclaim_time >= self.pel_reclaim_interval:
                    reclaimed = await self._reclaim_pending_messages(stream_name)
                    if reclaimed > 0:
                        logger.info("Reclaimed pending messages via XAUTOCLAIM",
                                  stream=stream_name,
                                  reclaimed_count=reclaimed)
                    self.last_pel_reclaim_time = current_time
                
                # Обновляем метрики каждые 10 итераций
                iteration += 1
                if iteration % 10 == 0:
                    await update_queue_metrics()
                    
                    # Context7: Дополнительная проверка на очистку каждые 10 итераций
                    # Используем last-delivered-id из consumer group как fallback
                    if not self.last_processed_id:
                        try:
                            groups_info = await self.redis_client.client.xinfo_groups(stream_key)
                            for group_info in groups_info:
                                if group_info.get(b'name', b'').decode() == self.event_consumer.config.group_name:
                                    last_id_bytes = group_info.get(b'last-delivered-id', b'0-0')
                                    if last_id_bytes:
                                        last_id_str = last_id_bytes.decode() if isinstance(last_id_bytes, bytes) else str(last_id_bytes)
                                        if last_id_str and last_id_str != '0-0':
                                            self.last_processed_id = last_id_str
                                            logger.debug("Updated last_processed_id from consumer group (fallback)",
                                                       last_id=self.last_processed_id)
                                            break
                        except Exception as e:
                            logger.debug("Failed to get last-delivered-id (fallback)", error=str(e))
                    
                    # Context7: Периодическая очистка каждые 10 итераций (даже если не достигли интервала)
                    # Это гарантирует, что очистка будет происходить регулярно
                    if self.last_processed_id:
                        trimmed = await self._trim_processed_messages(stream_name, self.last_processed_id)
                        if trimmed > 0:
                            logger.info("Periodic trim on metrics update (every 10 iterations)",
                                      stream=stream_name,
                                      trimmed_count=trimmed,
                                      last_id=self.last_processed_id,
                                      processed_since_trim=self.processed_count_since_trim)
                
                if processed == 0:
                    await asyncio.sleep(0.2)
                    
            except asyncio.CancelledError:
                logger.info("Consume loop cancelled")
                break
            except Exception as e:
                logger.error("Error in consume loop", error=str(e))
                await asyncio.sleep(5)
    
    async def _consume_batch_with_parallelism(self, stream_name: str, handler_func) -> int:
        """
        Context7 best practice: обработка батча сообщений с ограниченным параллелизмом.
        
        Использует Semaphore для ограничения количества одновременных обработок,
        что предотвращает перегрузку внешних сервисов (GigaChat, Neo4j, Qdrant).
        
        Args:
            stream_name: Имя стрима для потребления
            handler_func: Функция-обработчик события
            
        Returns:
            int: Количество обработанных сообщений
        """
        from event_bus import STREAMS
        
        if stream_name not in STREAMS:
            raise KeyError(f"Stream name '{stream_name}' not found in STREAMS")
        
        stream_key = STREAMS[stream_name]
        
        # Context7: Читаем новые сообщения через XREADGROUP
        try:
            messages = await self.redis_client.client.xreadgroup(
                self.event_consumer.config.group_name,
                self.event_consumer.config.consumer_name,
                {stream_key: '>'},
                count=self.event_consumer.config.batch_size,
                block=self.event_consumer.config.block_time
            )
            
            if not messages:
                return 0
            
            # Обрабатываем pending сообщения через XAUTOCLAIM
            try:
                pending_result = await self.redis_client.client.xautoclaim(
                    stream_key,
                    self.event_consumer.config.group_name,
                    self.event_consumer.config.consumer_name,
                    min_idle_time=60000,  # 60 секунд
                    count=self.event_consumer.config.batch_size
                )
                
                pending_messages = []
                if pending_result and len(pending_result) > 1:
                    pending_messages = pending_result[1] if pending_result[1] else []
                
                logger.debug("XAUTOCLAIM result",
                           stream=stream_name,
                           pending_count=len(pending_messages))
                
                # Объединяем pending и новые сообщения
                all_messages = []
                if messages:
                    for stream, stream_messages in messages:
                        if stream == stream_key:
                            all_messages.extend([(msg_id, fields) for msg_id, fields in stream_messages])
                
                if pending_messages:
                    all_messages.extend([(msg_id, fields) for msg_id, fields in pending_messages])
                
                if not all_messages:
                    logger.debug("No messages to process after merging", stream=stream_name)
                    return 0
                
                logger.debug("Total messages to process",
                           stream=stream_name,
                           total_count=len(all_messages),
                           new_messages=len([m for m in all_messages if messages and any(stream == stream_key for stream, _ in messages)]),
                           pending_messages=len(pending_messages))
                
                # Context7: Параллельная обработка с ограничением через Semaphore
                async def process_single_with_semaphore(message_id: str, fields: Dict):
                    """Обработка одного сообщения с контролем параллелизма."""
                    async with self.processing_semaphore:
                        try:
                            # Парсинг события
                            from event_bus import EventConsumer
                            parsed_event = self.event_consumer._parse_event_data(fields)
                            
                            # Context7: Проверяем post_id до обработки
                            # Если post_id отсутствует, ACK'им сообщение и пропускаем
                            event_data = parsed_event.get('payload') if isinstance(parsed_event, dict) and 'payload' in parsed_event else parsed_event
                            post_id = event_data.get('post_id') if isinstance(event_data, dict) else None
                            
                            if not post_id:
                                # Context7: Сообщения без post_id ACK'им, чтобы они не блокировали очередь
                                logger.debug("Skipping message without post_id, ACKing", message_id=message_id)
                                await self.redis_client.client.xack(
                                    stream_key,
                                    self.event_consumer.config.group_name,
                                    message_id
                                )
                                # Обновляем last_processed_id даже для пропущенных сообщений
                                if message_id:
                                    self.last_processed_id = message_id
                                return True  # Считаем как обработанное для счётчика
                            
                            # Context7: _process_single_message ожидает структуру с payload или прямой формат
                            await handler_func(parsed_event)
                            
                            # ACK сообщения только если обработка прошла успешно
                            await self.redis_client.client.xack(
                                stream_key,
                                self.event_consumer.config.group_name,
                                message_id
                            )
                            
                            # Context7: Обновляем last_processed_id для XTRIM
                            if message_id:
                                self.last_processed_id = message_id
                            
                            return True
                        except Exception as e:
                            # Context7: Если исключение проброшено, это retryable ошибка
                            # Не ACK'им сообщение, оно останется в PEL для повторной обработки
                            logger.warning("Retryable error in parallel processing, message will remain in PEL",
                                         message_id=message_id,
                                         error=str(e))
                            return False
                
                # Context7: Параллельная обработка всех сообщений
                tasks = [
                    process_single_with_semaphore(msg_id, fields)
                    for msg_id, fields in all_messages
                ]
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                processed_count = sum(1 for r in results if r is True)
                
                # Context7: Обновляем счётчик для XTRIM - считаем все ACK'нутые сообщения
                # Context7: Все сообщения, которые вернули True (включая пропущенные без post_id), уже ACK'нуты
                acked_count = sum(1 for r in results if r is True)
                self.processed_count_since_trim += acked_count
                
                # Context7: Обновляем last_processed_id на максимальный ID из всех ACK'нутых сообщений
                # Все сообщения до этого ID уже ACK'нуты (успешно обработаны или пропущены)
                if all_messages and acked_count > 0:
                    # Находим максимальный ID из всех ACK'нутых сообщений
                    acked_ids = []
                    for i, result in enumerate(results):
                        if result is True and i < len(all_messages):
                            acked_ids.append(all_messages[i][0])
                    
                    if acked_ids:
                        # Используем максимальный ACK'нутый ID для безопасного XTRIM
                        # Сортируем по timestamp-counter для нахождения максимума
                        acked_ids.sort(key=lambda x: (int(x.split('-')[0]), int(x.split('-')[1])))
                        max_acked_id = acked_ids[-1]
                        
                        # Обновляем last_processed_id только если новый ID больше текущего
                        if not self.last_processed_id or self._compare_message_ids(max_acked_id, self.last_processed_id) > 0:
                            self.last_processed_id = max_acked_id
                            logger.debug("Updated last_processed_id from ACKed batch",
                                       last_id=self.last_processed_id,
                                       acked_count=len(acked_ids),
                                       batch_size=len(all_messages),
                                       processed_count=processed_count)
                
                return processed_count
                
            except Exception as e:
                logger.error("Error in parallel processing",
                           stream=stream_name,
                           error=str(e),
                           error_type=type(e).__name__)
                return 0
                
        except Exception as e:
            logger.error("Error reading messages",
                       stream=stream_name,
                       error=str(e),
                       error_type=type(e).__name__)
            return 0
    
    async def stop(self):
        """Остановка indexing task."""
        try:
            if self.event_consumer:
                await self.event_consumer.stop()
            
            if self.redis_client:
                await self.redis_client.disconnect()
            
            if self.qdrant_client:
                # QdrantClient не имеет метода disconnect, только close если нужно
                pass
            
            if self.neo4j_client:
                await self.neo4j_client.close()
            
            logger.info("IndexingTask stopped")
            
        except Exception as e:
            logger.error("Error stopping IndexingTask", extra={"error": str(e)})
    
    async def health_check(self) -> Dict[str, Any]:
        """Проверка здоровья indexing task."""
        try:
            health = {
                'status': 'healthy',
                'redis_connected': self.redis_client is not None,
                'qdrant_connected': self.qdrant_client is not None,
                'neo4j_connected': self.neo4j_client is not None,
                'embedding_service_available': self.embedding_service is not None
            }
            
            # Проверка подключений
            if self.qdrant_client:
                health['qdrant_healthy'] = await self.qdrant_client.health_check()
            
            if self.neo4j_client:
                health['neo4j_healthy'] = await self.neo4j_client.health_check()
            
            return health
            
        except Exception as e:
            logger.error("Error in health check", extra={"error": str(e)})
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
