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
                logger.warning("Post not found", post_id=post_id)
                await self._update_indexing_status(
                    post_id=post_id,
                    embedding_status='failed',
                    graph_status='failed',
                    error_message='Post not found'
                )
                return
            
            # Генерация эмбеддинга
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
            
            # Публикация события posts.indexed
            await self.publisher.publish_event("posts.indexed", {
                "post_id": post_id,
                "vector_id": vector_id,
                "indexed_at": datetime.now(timezone.utc).isoformat()
            })
            
            indexing_processed_total.labels(status='success').inc()
            logger.info("Post indexed successfully", post_id=post_id, vector_id=vector_id)
            
        except Exception as e:
            logger.error("Failed to process post",
                        post_id=post_id,
                        error=str(e))
            indexing_processed_total.labels(status='error').inc()
            
            # Context7: Обновляем статус failed при ошибке
            await self._update_indexing_status(
                post_id=post_id,
                embedding_status='failed',
                graph_status='failed',
                error_message=str(e)
            )
    
    async def _get_post_data(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Получение данных поста из БД."""
        try:
            from psycopg2.extras import RealDictCursor
            
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT id, channel_id, content as text, telegram_message_id, created_at
                FROM posts
                WHERE id = %s
            """, (post_id,))
            
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if row:
                return dict(row)
            return None
            
        except Exception as e:
            logger.error("Failed to get post data", post_id=post_id, error=str(e))
            return None
    
    async def _generate_embedding(self, post_data: Dict[str, Any]) -> list:
        """Генерация эмбеддинга для поста."""
        try:
            text = post_data.get('text', '')
            if not text:
                raise ValueError("Post text is empty")
            
            # Context7: Используем EmbeddingService для генерации эмбеддинга
            embedding = await self.embedding_service.generate_embedding(text)
            return embedding
            
        except Exception as e:
            logger.error("Failed to generate embedding",
                        post_id=post_data.get('id'),
                        error=str(e))
            raise
    
    async def _index_to_qdrant(self, post_id: str, post_data: Dict[str, Any], embedding: list) -> str:
        """Индексация поста в Qdrant."""
        try:
            from config import settings
            
            vector_id = f"{post_id}"
            await self.qdrant_client.upsert_vector(
                collection_name=settings.qdrant_collection,
                vector_id=vector_id,
                vector=embedding,
                payload={
                    "post_id": post_id,
                    "channel_id": post_data.get('channel_id'),
                    "text": post_data.get('text', '')[:500],  # text уже алиас для content из SELECT
                    "telegram_message_id": post_data.get('telegram_message_id'),
                    "created_at": post_data.get('created_at').isoformat() if post_data.get('created_at') else None
                }
            )
            
            logger.debug("Indexed to Qdrant", post_id=post_id, vector_id=vector_id)
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
            
            # Context7: Вызов метода create_post_node с правильными параметрами
            success = await self.neo4j_client.create_post_node(
                post_id=post_id,
                user_id=post_data.get('user_id', 'system'),  # Fallback для совместимости
                tenant_id=post_data.get('tenant_id', 'default'),  # Fallback для совместимости
                channel_id=channel_id,
                expires_at=expires_at,
                enrichment_data=None,  # Может быть обогащено позже
                indexed_at=datetime.now(timezone.utc).isoformat()
            )
            
            if success:
                logger.debug("Indexed to Neo4j", post_id=post_id, channel_id=channel_id)
            else:
                raise Exception("create_post_node returned False")
            
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
            embedding_status: Статус эмбеддинга (pending/processing/completed/failed)
            graph_status: Статус графа (pending/processing/completed/failed)
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
