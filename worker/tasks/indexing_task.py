"""
Indexing Task - Consumer для posts.enriched событий
Context7 best practice: индексация в Qdrant и Neo4j с обновлением indexing_status

Обрабатывает события posts.enriched → создание эмбеддингов → индексация → публикация posts.indexed
"""

import asyncio
import os
import time
import structlog
import psycopg2
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from prometheus_client import Counter

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
            consumer_config = ConsumerConfig(
                group_name="indexing_workers",
                consumer_name="indexing_worker_1"
            )
            self.event_consumer = EventConsumer(self.redis_client, consumer_config)
            
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
            
            # Context7 best practice: обработка backlog при старте
            # Перечитываем сообщения с начала stream для обработки необработанных событий
            backlog_processed = await self._process_backlog_once("posts.enriched")
            if backlog_processed > 0:
                logger.info(f"Processed {backlog_processed} backlog messages from stream")
            
            # Запуск потребления событий
            await self.event_consumer.start_consuming("posts.enriched", self._process_single_message)
            
        except Exception as e:
            logger.error("Failed to start IndexingTask", error=str(e))
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
                            logger.debug(f"Skipping already processed post", post_id=post_id, status=row[0])
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
            
            # Получение данных поста
            post_data = await self._get_post_data(post_id)
            if not post_data:
                # Context7: [C7-ID: indexing-graceful-001] Graceful degradation для удалённых постов
                # Посты, удалённые после публикации события, помечаем как skipped, а не failed
                logger.info("Post not found, skipping indexing", 
                          post_id=post_id,
                          reason="post_deleted_or_race_condition")
                await self._update_indexing_status(
                    post_id=post_id,
                    embedding_status='skipped',
                    graph_status='skipped',
                    error_message='Post not found - likely deleted after event publication'
                )
                # Context7: Помечаем пост как обработанный для избежания повторных попыток
                await self._update_post_processed(post_id)
                indexing_processed_total.labels(status='skipped').inc()
                return
            
            # [C7-ID: dev-mode-016] Context7 best practice: проверка текста перед индексацией
            # Посты без текста (только медиа, стикеры) пропускаем с статусом skipped, а не failed
            text = post_data.get('text', '')
            if not text or not text.strip():
                logger.info("Post text is empty, skipping indexing", 
                          post_id=post_id,
                          has_media=post_data.get('has_media', False))
                await self._update_indexing_status(
                    post_id=post_id,
                    embedding_status='skipped',
                    graph_status='skipped',
                    error_message='Post text is empty - no content to index'
                )
                # Context7: Помечаем пост как обработанный даже при пропуске
                await self._update_post_processed(post_id)
                indexing_processed_total.labels(status='skipped').inc()
                return
            
            # Context7: [C7-ID: retry-indexing-001] Генерация эмбеддинга с retry через EmbeddingService
            # Retry логика уже встроена в EmbeddingService
            embedding = await self._generate_embedding(post_data)
            
            # Индексация в Qdrant
            vector_id = await self._index_to_qdrant(post_id, post_data, embedding)
            
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
            await self.publisher.publish_event("posts.indexed", {
                "post_id": post_id,
                "vector_id": vector_id,
                "indexed_at": datetime.now(timezone.utc).isoformat()
            })
            
            indexing_processed_total.labels(status='success').inc()
            logger.info("Post indexed successfully", post_id=post_id, vector_id=vector_id)
            
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
            # Для retryable ошибок оставляем возможность ретрая через process_pending_indexing
            await self._update_indexing_status(
                post_id=post_id,
                embedding_status='failed',
                graph_status='failed',
                error_message=f"[{error_category.value}] {error_str}"
            )
            
            # Context7: Для retryable ошибок не пробрасываем исключение дальше,
            # чтобы сообщение осталось в stream для последующего ретрая
            # Для non-retryable ошибок также не пробрасываем, чтобы не блокировать обработку других сообщений
    
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
            cursor.execute("""
                SELECT 
                    p.id,
                    p.channel_id,
                    p.content as text,
                    p.telegram_message_id,
                    p.created_at,
                    p.tenant_id,
                    p.user_id,
                    pe_vision.data as vision_data,
                    pe_crawl.data as crawl_data,
                    pe_tags.data as tags_data
                FROM posts p
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
                for key in ['vision_data', 'crawl_data', 'tags_data']:
                    value = result.get(key)
                    if value and isinstance(value, str):
                        try:
                            result[key] = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            result[key] = None
                    elif value is None:
                        result[key] = None
                
                return result
            return None
            
        except Exception as e:
            logger.error("Failed to get post data", post_id=post_id, error=str(e))
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
            # Базовый текст поста (приоритет 1)
            post_text = post_data.get('text', '')[:2000]  # Лимит 2000 символов
            text_parts = [post_text] if post_text.strip() else []
            
            # Vision enrichment данные (приоритет 2)
            vision_data = post_data.get('vision_data')
            if vision_data and isinstance(vision_data, dict):
                # Vision description (caption)
                vision_desc = vision_data.get('description', '')
                if vision_desc and len(vision_desc.strip()) >= 5:
                    text_parts.append(vision_desc[:500])  # Лимит 500 символов
                
                # Vision OCR text
                vision_ocr = vision_data.get('ocr')
                if vision_ocr:
                    if isinstance(vision_ocr, dict):
                        ocr_text = vision_ocr.get('text', '')
                    else:
                        ocr_text = str(vision_ocr)
                    
                    if ocr_text and ocr_text.strip():
                        text_parts.append(ocr_text[:300])  # Лимит 300 символов
            
            # Crawl enrichment данные (приоритет 3)
            crawl_data = post_data.get('crawl_data')
            if crawl_data and isinstance(crawl_data, dict):
                # Используем md_excerpt если доступен (первые ~1-2k символов), иначе полный markdown
                crawl_md = crawl_data.get('md_excerpt') or crawl_data.get('markdown') or crawl_data.get('crawl_md', '')
                if crawl_md and crawl_md.strip():
                    # Ограничиваем до 1500 символов
                    crawl_text = crawl_md[:1500]
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
                        text_parts.append(ocr_text[:300])  # Лимит 300 символов
            
            # Объединение всех частей с дедупликацией
            # Простая дедупликация: убираем дубликаты (точные совпадения)
            seen = set()
            unique_parts = []
            for part in text_parts:
                part_normalized = part.strip().lower()
                if part_normalized and part_normalized not in seen:
                    seen.add(part_normalized)
                    unique_parts.append(part.strip())
            
            # Финальный текст для эмбеддинга
            final_text = '\n\n'.join(unique_parts) if unique_parts else post_text
            
            # Защита на случай если проверка пропущена
            if not final_text or not final_text.strip():
                raise ValueError("Post text is empty after enrichment composition - should be checked before calling this method")
            
            # Context7: Используем EmbeddingService для генерации эмбеддинга
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
    
    async def _index_to_qdrant(self, post_id: str, post_data: Dict[str, Any], embedding: list) -> str:
        """
        Индексация поста в Qdrant с расширенным payload для фильтрации и фасетирования.
        
        Context7 best practice: расширенный payload с enrichment данными для фильтрации:
        - tags, vision.is_meme, vision.labels, vision.scene, vision.nsfw_score, vision.aesthetic_score
        - crawl.has_crawl, crawl.html_key, crawl.word_count
        
        В payload храним только фасеты/флаги (< 64KB), полные тексты (md/html) в S3.
        """
        try:
            from config import settings
            
            vector_id = f"{post_id}"
            
            # Базовый payload
            payload = {
                "post_id": post_id,
                "channel_id": post_data.get('channel_id'),
                "text_short": post_data.get('text', '')[:500],  # Превью для быстрого доступа
                "telegram_message_id": post_data.get('telegram_message_id'),
                "created_at": post_data.get('created_at').isoformat() if post_data.get('created_at') else None
            }
            
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
            
            await self.qdrant_client.upsert_vector(
                collection_name=settings.qdrant_collection,
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
    
    async def _index_to_neo4j(self, post_id: str, post_data: Dict[str, Any]):
        """Индексация поста в Neo4j граф."""
        try:
            channel_id = post_data.get('channel_id')
            if not channel_id:
                logger.warning("No channel_id, skipping Neo4j indexing", post_id=post_id)
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
            
            # Context7: Вызов метода create_post_node с enrichment данными
            success = await self.neo4j_client.create_post_node(
                post_id=post_id,
                user_id=post_data.get('user_id', 'system'),  # Fallback для совместимости
                tenant_id=post_data.get('tenant_id', 'default'),  # Fallback для совместимости
                channel_id=channel_id,
                expires_at=expires_at,
                enrichment_data=enrichment_data if enrichment_data else None,
                indexed_at=datetime.now(timezone.utc).isoformat()
            )
            
            if not success:
                raise Exception("create_post_node returned False")
            
            # Context7: Создание Tag relationships
            tags_data = post_data.get('tags_data')
            if tags_data and isinstance(tags_data, dict):
                tags_list = tags_data.get('tags', [])
                if tags_list:
                    # Преобразуем список строк в список dict для create_tag_relationships
                    tags_dicts = [
                        {'name': tag, 'category': 'general', 'confidence': 1.0}
                        if isinstance(tag, str) else tag
                        for tag in tags_list
                    ]
                    await self.neo4j_client.create_tag_relationships(post_id, tags_dicts)
            
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
                    
                    await self.neo4j_client.create_image_content_node(
                        post_id=post_id,
                        sha256=sha256 or 'unknown',  # Fallback если нет sha256
                        s3_key=image_key,
                        mime_type=None,  # TODO: извлечь из media metadata
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
            
            logger.debug("Updated posts.is_processed", post_id=post_id)
            
        except Exception as e:
            logger.warning("Failed to update posts.is_processed", 
                         post_id=post_id, 
                         error=str(e))
            # Не пробрасываем ошибку - это не критично для работы пайплайна
    
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
            logger.error("Error stopping IndexingTask", error=str(e))
    
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
            logger.error("Error in health check", error=str(e))
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
