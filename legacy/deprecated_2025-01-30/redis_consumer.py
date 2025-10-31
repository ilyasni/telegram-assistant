#!/usr/bin/env python3
"""
[DEPRECATED] Redis Streams Consumer для обработки событий.
Простая реализация для восстановления пайплайна.

ИСПОЛЬЗУЙТЕ: tagging_task.py + tag_persistence_task.py вместо этого consumer
"""

import asyncio
import json
import logging
import os
import socket
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

import redis.asyncio as redis
import structlog
import psycopg2
from psycopg2.extras import RealDictCursor
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# [DEPRECATED] Проверка feature flag
from worker.feature_flags import feature_flags

logger = logging.getLogger(__name__)

if not feature_flags.legacy_redis_consumer_enabled:
    logger.warning("[DEPRECATED] redis_consumer disabled by LEGACY_REDIS_CONSUMER_ENABLED=false")
    logger.warning("[DEPRECATED] Use tagging_task.py + tag_persistence_task.py instead")
    raise SystemExit(0)

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Prometheus метрики
worker_events_processed_total = Counter(
    'worker_events_processed_total',
    'Total events processed by worker',
    ['status']
)

worker_stream_lag = Gauge(
    'worker_stream_lag',
    'Current lag in Redis Stream',
    ['stream_name']
)

worker_processing_duration_seconds = Histogram(
    'worker_processing_duration_seconds',
    'Event processing duration',
    ['stage']
)

worker_embeddings_generated_total = Counter(
    'worker_embeddings_generated_total',
    'Total embeddings generated',
    ['provider']
)

worker_qdrant_upserts_total = Counter(
    'worker_qdrant_upserts_total',
    'Total Qdrant upserts',
    ['status']
)

worker_neo4j_operations_total = Counter(
    'worker_neo4j_operations_total',
    'Total Neo4j operations',
    ['operation', 'status']
)

class RedisStreamConsumer:
    """Consumer для Redis Streams с поддержкой consumer groups."""
    
    def __init__(self, redis_url: str = "redis://redis:6379/0"):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
        self.running = False
        self.consumer_name = f"worker-{socket.gethostname()}"
        self.group_name = "telegram-assistant"
        self.stream_name = "stream:posts:parsed"
        
    async def connect(self):
        """Подключение к Redis."""
        try:
            self.client = redis.from_url(self.redis_url, decode_responses=True)
            await self.client.ping()
            logger.info("Connected to Redis", url=self.redis_url)
        except Exception as e:
            logger.error("Failed to connect to Redis", error=str(e))
            raise
    
    async def disconnect(self):
        """Отключение от Redis."""
        if self.client:
            await self.client.close()
            logger.info("Disconnected from Redis")
    
    async def create_consumer_group(self):
        """Создание consumer group (идемпотентно)."""
        try:
            await self.client.xgroup_create(
                self.stream_name,
                self.group_name,
                id='0',
                mkstream=True
            )
            logger.info("Created consumer group", 
                       stream=self.stream_name, 
                       group=self.group_name)
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info("Consumer group already exists", 
                           stream=self.stream_name, 
                           group=self.group_name)
            else:
                logger.error("Failed to create consumer group", error=str(e))
                raise
    
    async def start_consuming(self, batch_size: int = 50):
        """Запуск потребления событий."""
        print(f"start_consuming called with batch_size={batch_size}", flush=True)
        
        print("Connecting to Redis...", flush=True)
        await self.connect()
        print("Connected to Redis", flush=True)
        
        print("Creating consumer group...", flush=True)
        await self.create_consumer_group()
        print("Consumer group created", flush=True)
        
        self.running = True
        print(f"Started consuming events from {self.stream_name}", flush=True)
        logger.info("Started consuming events", 
                   stream=self.stream_name,
                   group=self.group_name,
                   consumer=self.consumer_name,
                   batch_size=batch_size)
        
        processed_count = 0
        error_count = 0
        
        while self.running:
            try:
                # Чтение сообщений из стрима
                messages = await self.client.xreadgroup(
                    self.group_name,
                    self.consumer_name,
                    {self.stream_name: '>'},
                    count=batch_size,
                    block=2000  # 2 секунды
                )
                
                if messages:
                    logger.info("Received messages batch", count=len(messages))
                    for stream, stream_messages in messages:
                        logger.info("Processing stream messages", 
                                   stream=stream, 
                                   message_count=len(stream_messages))
                        for message_id, fields in stream_messages:
                            try:
                                logger.info("Processing message", 
                                           message_id=message_id,
                                           fields=list(fields.keys()))
                                
                                # Обработка сообщения
                                await self._process_message(message_id, fields)
                                processed_count += 1
                                
                                # ACK после успешной обработки
                                await self.client.xack(
                                    self.stream_name,
                                    self.group_name,
                                    message_id
                                )
                                
                                logger.info("Message processed successfully", 
                                           message_id=message_id,
                                           processed_count=processed_count)
                                
                            except Exception as e:
                                error_count += 1
                                logger.error("Failed to process message", 
                                           message_id=message_id,
                                           error=str(e),
                                           error_count=error_count)
                                
                                # ACK даже при ошибке, чтобы не застрять
                                await self.client.xack(
                                    self.stream_name,
                                    self.group_name,
                                    message_id
                                )
                
                # Логирование прогресса каждые 10 сообщений
                if processed_count > 0 and processed_count % 10 == 0:
                    logger.info("Processing progress", 
                              processed_count=processed_count,
                              error_count=error_count)
                
            except asyncio.CancelledError:
                logger.info("Consumer cancelled")
                break
            except Exception as e:
                logger.error("Consumer error", error=str(e))
                await asyncio.sleep(5)  # Пауза перед повтором
    
    async def _process_message(self, message_id: str, fields: Dict[str, str]):
        """Обработка одного сообщения с идемпотентностью."""
        try:
            # Парсинг данных события
            event_data = self._parse_event_data(fields)
            post_id = event_data.get('post_id')
            
            if not post_id:
                logger.warning("No post_id in event data", message_id=message_id)
                return
            
            # Проверка идемпотентности через Redis
            processing_key = f"processing:{post_id}"
            if not await self._check_idempotency(processing_key):
                logger.debug("Post already being processed", post_id=post_id)
                return
            
            logger.info("Processing event", 
                       message_id=message_id,
                       post_id=post_id,
                       channel_id=event_data.get('channel_id'))
            
            try:
                # 1. Запись в indexing_status (processing)
                await self._update_indexing_status(
                    post_id=post_id,
                    embedding_status='processing',
                    graph_status='processing'
                )
                
                # 2. Генерация эмбеддингов (пока заглушка)
                embedding = await self._generate_embedding(event_data)
                
                # 3. Сохранение в Qdrant (пока заглушка)
                vector_id = await self._index_to_qdrant(event_data, embedding)
                
                # 4. Сохранение в Neo4j (пока заглушка)
                await self._index_to_neo4j(event_data)
                
                # 5. Обогащение данных
                await self._enrich_post(event_data)
                
                # 6. Обновление indexing_status (completed)
                await self._update_indexing_status(
                    post_id=post_id,
                    embedding_status='completed',
                    graph_status='completed',
                    vector_id=vector_id
                )
                
                # Отметить как обработанный
                await self._mark_processed(post_id)
                
                # Обновить метрики
                worker_events_processed_total.labels(status='success').inc()
                
                logger.info("Event processed successfully", 
                           message_id=message_id,
                           post_id=post_id,
                           vector_id=vector_id)
                
            except Exception as e:
                logger.error("Failed to process event", 
                            message_id=message_id,
                            post_id=post_id,
                            error=str(e))
                
                # Обновление indexing_status (failed)
                await self._update_indexing_status(
                    post_id=post_id,
                    embedding_status='failed',
                    graph_status='failed',
                    error_message=str(e)
                )
                
                # Обновить метрики
                worker_events_processed_total.labels(status='error').inc()
                raise
                
        except Exception as e:
            logger.error("Failed to process event", 
                        message_id=message_id,
                        error=str(e))
            raise
        finally:
            # Очистка ключа обработки
            await self._cleanup_processing_key(processing_key)
    
    def _parse_event_data(self, fields: Dict[str, str]) -> Dict[str, Any]:
        """Парсинг данных события из Redis Streams."""
        event_data = {}
        
        # Если есть ключ 'data', парсим его как JSON
        if 'data' in fields:
            try:
                event_data = json.loads(fields['data'])
                print(f"Parsed event data from 'data' key: post_id={event_data.get('post_id')}", flush=True)
                return event_data
            except json.JSONDecodeError as e:
                logger.error("Failed to parse 'data' field as JSON", error=str(e))
        
        # Иначе парсим отдельные ключи
        for key, value in fields.items():
            # Парсинг JSON полей
            if key in ['media_urls']:
                try:
                    event_data[key] = json.loads(value)
                except json.JSONDecodeError:
                    event_data[key] = value
            # Парсинг datetime полей
            elif key in ['created_at', 'posted_at']:
                try:
                    event_data[key] = value  # Оставляем как строку пока
                except ValueError:
                    event_data[key] = value
            else:
                event_data[key] = value
        
        return event_data
    
    async def _update_indexing_status(self, post_id: str, embedding_status: str, 
                                    graph_status: str, vector_id: str = None, 
                                    error_message: str = None):
        """Обновление статуса индексации в базе данных."""
        try:
            # Подключение к базе данных
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor()
            
            # Upsert в indexing_status
            cursor.execute("""
                INSERT INTO indexing_status (post_id, embedding_status, graph_status, vector_id, error_message, processing_started_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (post_id) DO UPDATE SET
                    embedding_status = EXCLUDED.embedding_status,
                    graph_status = EXCLUDED.graph_status,
                    vector_id = EXCLUDED.vector_id,
                    error_message = EXCLUDED.error_message,
                    processing_completed_at = CASE 
                        WHEN EXCLUDED.embedding_status = 'completed' AND EXCLUDED.graph_status = 'completed' 
                        THEN NOW() 
                        ELSE indexing_status.processing_completed_at 
                    END
            """, (post_id, embedding_status, graph_status, vector_id, error_message, datetime.now(timezone.utc)))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.debug("Updated indexing_status", 
                        post_id=post_id, 
                        embedding_status=embedding_status, 
                        graph_status=graph_status)
            
        except Exception as e:
            logger.error("Failed to update indexing_status", 
                        post_id=post_id, 
                        error=str(e))
            raise
    
    async def _generate_embedding(self, event_data: Dict[str, Any]) -> list:
        """Генерация эмбеддинга для текста."""
        try:
            # Получаем текст для эмбеддинга
            content = event_data.get('content', '')
            if not content:
                content = event_data.get('text', '')
            
            if not content:
                logger.warning("No content for embedding", post_id=event_data.get('post_id'))
                # Возвращаем нулевой вектор
                return [0.0] * 1536
            
            # Пока что используем простой hash-based embedding
            # В будущем здесь будет интеграция с GigaChat
            import hashlib
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            
            # Создаем детерминированный вектор из хеша
            embedding = []
            for i in range(0, len(content_hash), 2):
                val = int(content_hash[i:i+2], 16) / 255.0
                embedding.append(val)
            
            # Дополняем до 1536 размерности
            while len(embedding) < 1536:
                embedding.append(0.0)
            
            # Обрезаем до нужного размера
            embedding = embedding[:1536]
            
            # Обновляем метрики
            worker_embeddings_generated_total.labels(provider='hash-based').inc()
            
            logger.debug("Generated embedding", 
                        post_id=event_data.get('post_id'),
                        embedding_dim=len(embedding),
                        content_length=len(content))
            
            return embedding
            
        except Exception as e:
            logger.error("Failed to generate embedding", 
                        post_id=event_data.get('post_id'),
                        error=str(e))
            # Возвращаем нулевой вектор при ошибке
            return [0.0] * 1536
    
    async def _index_to_qdrant(self, event_data: Dict[str, Any], embedding: list) -> str:
        """Индексация в Qdrant."""
        try:
            import requests
            import uuid
            
            post_id = event_data.get('post_id')
            vector_id = str(uuid.uuid4())
            
            # URL для Qdrant
            qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
            collection_name = os.getenv("QDRANT_COLLECTION", "telegram_posts")
            
            # Создаем коллекцию если её нет
            await self._ensure_qdrant_collection(qdrant_url, collection_name)
            
            # Подготавливаем данные для upsert
            payload = {
                "post_id": post_id,
                "channel_id": event_data.get('channel_id'),
                "content": event_data.get('content', ''),
                "created_at": event_data.get('created_at', ''),
                "has_media": event_data.get('has_media', False)
            }
            
            # Upsert в Qdrant
            upsert_data = {
                "points": [{
                    "id": vector_id,
                    "vector": embedding,
                    "payload": payload
                }]
            }
            
            response = requests.put(
                f"{qdrant_url}/collections/{collection_name}/points",
                json=upsert_data,
                timeout=10
            )
            
            if response.status_code == 200:
                # Обновляем метрики
                worker_qdrant_upserts_total.labels(status='success').inc()
                
                logger.debug("Indexed to Qdrant", 
                            post_id=post_id,
                            vector_id=vector_id,
                            collection=collection_name)
                
                return vector_id
            else:
                raise Exception(f"Qdrant upsert failed: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.error("Failed to index to Qdrant", 
                        post_id=event_data.get('post_id'),
                        error=str(e))
            
            # Обновляем метрики
            worker_qdrant_upserts_total.labels(status='error').inc()
            
            # Возвращаем случайный ID при ошибке
            import uuid
            return str(uuid.uuid4())
    
    async def _ensure_qdrant_collection(self, qdrant_url: str, collection_name: str):
        """Создание коллекции в Qdrant если её нет."""
        try:
            import requests
            
            # Проверяем существование коллекции
            response = requests.get(f"{qdrant_url}/collections/{collection_name}", timeout=5)
            
            if response.status_code == 200:
                logger.debug("Collection already exists", collection=collection_name)
                return
            
            # Создаем коллекцию
            collection_config = {
                "vectors": {
                    "size": 1536,
                    "distance": "Cosine"
                }
            }
            
            response = requests.put(
                f"{qdrant_url}/collections/{collection_name}",
                json=collection_config,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info("Created Qdrant collection", collection=collection_name)
            else:
                logger.error("Failed to create collection", 
                            collection=collection_name,
                            status=response.status_code,
                            error=response.text)
                
        except Exception as e:
            logger.error("Failed to ensure Qdrant collection", 
                        collection=collection_name,
                        error=str(e))
    
    async def _index_to_neo4j(self, event_data: Dict[str, Any]):
        """Индексация в Neo4j."""
        try:
            import requests
            
            post_id = event_data.get('post_id')
            channel_id = event_data.get('channel_id')
            content = event_data.get('content', '')
            
            from neo4j import GraphDatabase
            
            # URL для Neo4j
            neo4j_url = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
            neo4j_user = os.getenv("NEO4J_USER", "neo4j")
            neo4j_password = os.getenv("NEO4J_PASSWORD", "neo4j")
            
            # Подключаемся к Neo4j через Bolt
            driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_user, neo4j_password))
            
            with driver.session() as session:
                # Cypher запрос для создания узла Post
                cypher_query = """
                MERGE (p:Post {id: $post_id})
                SET p.channel_id = $channel_id,
                    p.content = $content,
                    p.created_at = datetime(),
                    p.updated_at = datetime()
                RETURN p.id as post_id
                """
                
                # Выполняем запрос
                result = session.run(cypher_query, {
                    "post_id": post_id,
                    "channel_id": channel_id,
                    "content": content
                })
                
                # Получаем результат
                record = result.single()
                if record:
                    # Обновляем метрики
                    worker_neo4j_operations_total.labels(operation='create_post', status='success').inc()
                    
                    logger.debug("Indexed to Neo4j", 
                                post_id=post_id,
                                channel_id=channel_id)
                else:
                    raise Exception("No result returned from Neo4j")
            
            driver.close()
                
        except Exception as e:
            logger.error("Failed to index to Neo4j", 
                        post_id=event_data.get('post_id'),
                        error=str(e))
            
            # Обновляем метрики
            worker_neo4j_operations_total.labels(operation='create_post', status='error').inc()
    
    async def _check_idempotency(self, processing_key: str) -> bool:
        """Проверка идемпотентности через Redis."""
        try:
            # Установить ключ с TTL 1 час, только если не существует
            result = await self.client.set(processing_key, '1', ex=3600, nx=True)
            return result is True
        except Exception as e:
            logger.error("Failed to check idempotency", key=processing_key, error=str(e))
            return False
    
    async def _mark_processed(self, post_id: str):
        """Отметить пост как обработанный."""
        try:
            processed_key = f"processed:{post_id}"
            await self.client.set(processed_key, '1', ex=86400)  # 24 часа
            logger.debug("Marked as processed", post_id=post_id)
        except Exception as e:
            logger.error("Failed to mark as processed", post_id=post_id, error=str(e))
    
    async def _cleanup_processing_key(self, processing_key: str):
        """Очистка ключа обработки."""
        try:
            await self.client.delete(processing_key)
        except Exception as e:
            logger.error("Failed to cleanup processing key", key=processing_key, error=str(e))
    
    async def stop(self):
        """Остановка consumer."""
        self.running = False
        logger.info("Stopping consumer")
        await self.disconnect()

    async def _enrich_post(self, event_data: Dict[str, Any]) -> bool:
        """Обогащение поста данными через GigaChat."""
        try:
            post_id = event_data.get('post_id')
            # Нормализуем контент: используем content -> text -> message
            content = event_data.get('content') or event_data.get('text') or event_data.get('message') or ''
            channel_id = event_data.get('channel_id')
            
            if not post_id:
                logger.warning("No post_id for enrichment")
                return False
            
            # Подключение к базе данных
            db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor()
            
            try:
                start_time = time.time()
                
                # Проверяем достаточность контента для тегирования
                logger.debug("Enrichment content check", post_id=post_id, content_length=len(content))
                if not self._is_content_sufficient_for_tagging(content):
                    logger.info("Insufficient content for tagging, skipping", 
                              post_id=post_id, content_length=len(content))
                    tags = []
                else:
                    # Тегирование через GigaChat (с fallback)
                    try:
                        tags = await self._generate_tags_with_gigachat(content)
                        logger.info("Tags generated via GigaChat", 
                                  post_id=post_id, tags_count=len(tags))
                    except Exception as e:
                        logger.warning("GigaChat unavailable, using fallback tagging", 
                                    error=str(e), post_id=post_id)
                        tags = self._extract_simple_tags(content)
                
                enrichment_latency_ms = int((time.time() - start_time) * 1000)
                
                # Вставка данных обогащения
                cursor.execute("""
                    INSERT INTO post_enrichment (
                        post_id, 
                        tags, 
                        enrichment_provider, 
                        enrichment_latency_ms,
                        metadata
                    ) VALUES (
                        %s, 
                        %s, 
                        %s, 
                        %s,
                        %s
                    ) ON CONFLICT (post_id) DO UPDATE SET
                        tags = EXCLUDED.tags,
                        enrichment_provider = EXCLUDED.enrichment_provider,
                        enrichment_latency_ms = EXCLUDED.enrichment_latency_ms,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                """, (
                    post_id,
                    json.dumps(tags),
                    'gigachat',
                    enrichment_latency_ms,
                    json.dumps({
                        'enriched_at': datetime.now(timezone.utc).isoformat(),
                        'content_length': len(content),
                        'channel_id': channel_id,
                        'provider': 'gigachat'
                    })
                ))
                
                conn.commit()
                
                logger.info("Post enriched successfully with GigaChat", 
                           post_id=post_id,
                           tags_count=len(tags),
                           latency_ms=enrichment_latency_ms)
                return True
                
            except Exception as e:
                conn.rollback()
                logger.error("Failed to enrich post", 
                           post_id=post_id,
                           error=str(e))
                return False
            finally:
                cursor.close()
                conn.close()
                
        except Exception as e:
            logger.error("Enrichment failed", 
                        post_id=event_data.get('post_id'),
                        error=str(e))
            return False

    async def _generate_tags_with_gigachat(self, content: str) -> List[str]:
        """Генерация тегов через GigaChat."""
        try:
            import openai
            
            # Конфигурация GigaChat через gpt2giga-proxy
            api_key = os.getenv("OPENAI_API_KEY", "dummy")
            base_url = os.getenv("OPENAI_API_BASE", "http://gpt2giga-proxy:8090/v1")
            
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url
            )
            
            # Промпт для тегирования (жёсткие правила качества и разнообразия)
            prompt = f"""
Определи 3–7 релевантных тематических тегов для данного текста.

Правила:
- ТОЛЬКО русский язык; каждый тег — 1–3 слова.
- Каждый тег ДОЛЖЕН быть фразой, которая встречается в тексте (как подстрока) или его точной формой (нормализованной по пробелам/регистру).
- Запрещены мета‑теги: "анализ текста", "классификация контента", "теги", "контент", "текст", "пост".
- Избегай чрезмерно общих тегов (например: "экономика", "инвестиции", "новости", "политика", "бизнес", "технологии") — добавляй их ТОЛЬКО если они явно упомянуты и добавляй рядом более конкретные теги из текста (например, названия компаний, отраслей, событий, стран, товаров, сущностей).
- Без дубликатов; приведи к нижнему регистру; нормализуй пробелы.
- Верни ТОЛЬКО JSON‑массив строк без пояснений и markdown.

Формат ответа: ["тег1", "тег2", "тег3"]

Текст: {content}
"""
            
            # Используем модель GigaChat, проксируемую через gpt2giga-proxy
            gigachat_model = os.getenv("GIGACHAT_MODEL", "GigaChat")
            response = await client.chat.completions.create(
                model=gigachat_model,
                messages=[
                    {"role": "system", "content": "Ты эксперт по анализу текста и определению тегов. Отвечай только JSON массивом тегов."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.0
            )
            
            # Парсинг ответа
            content_response = response.choices[0].message.content.strip()
            
            # Извлечение JSON из ответа
            if content_response.startswith('[') and content_response.endswith(']'):
                tags = json.loads(content_response)
            else:
                # Fallback - извлечение тегов из текста
                import re
                tags_match = re.findall(r'\[(.*?)\]', content_response)
                if tags_match:
                    tags = [tag.strip().strip('"\'') for tag in tags_match[0].split(',')]
                else:
                    # Простое извлечение слов
                    words = content_response.lower().split()
                    common_tags = ['технологии', 'программирование', 'ai', 'новости', 'разработка']
                    tags = [word for word in words if word in common_tags]
            
            # Нормализация и валидация тегов
            tags = self._normalize_tags(tags)
            
            logger.info("Tags generated with GigaChat", 
                       tags=tags,
                       content_length=len(content))
            
            return tags
            
        except Exception as e:
            logger.error("Failed to generate tags with GigaChat", 
                        error=str(e),
                        content_length=len(content))
            
            # Fallback - простое извлечение тегов
            return self._extract_simple_tags(content)
    
    def _is_content_sufficient_for_tagging(self, content: str) -> bool:
        """Проверяет, достаточно ли контента для тегирования."""
        if not content or not content.strip():
            return False
        
        # Минимальная длина для осмысленного тегирования
        min_length = 20  # Минимум 20 символов
        
        # Проверяем, что это не просто эмодзи или символы
        text_content = content.strip()
        if len(text_content) < min_length:
            return False
        
        # Проверяем, что есть осмысленные слова (не только символы/эмодзи)
        words = text_content.split()
        meaningful_words = [w for w in words if len(w) > 1 and w.isalpha()]
        
        # Нужно минимум 3 осмысленных слова
        return len(meaningful_words) >= 3

    def _extract_simple_tags(self, content: str) -> List[str]:
        """Простое извлечение тегов как fallback."""
        if not content:
            return []
        
        words = content.lower().split()
        
        # Расширенный список тегов для лучшего покрытия
        common_tags = {
            'технологии': ['технологии', 'технология', 'tech'],
            'программирование': ['программирование', 'код', 'разработка', 'coding'],
            'искусственный интеллект': ['искусственный интеллект', 'ai', 'машинное обучение', 'нейросети'],
            'новости': ['новости', 'новость', 'news'],
            'python': ['python', 'питон'],
            'javascript': ['javascript', 'js'],
            'данные': ['данные', 'анализ данных', 'data'],
            'алгоритмы': ['алгоритмы', 'алгоритм'],
            'приложения': ['приложения', 'приложение', 'app'],
            'веб': ['веб', 'web', 'сайт'],
            'мобильные': ['мобильные', 'мобильный', 'mobile']
        }
        
        tags = []
        content_lower = content.lower()
        
        # Поиск релевантных тегов
        for tag, keywords in common_tags.items():
            for keyword in keywords:
                if keyword in content_lower and tag not in tags:
                    tags.append(tag)
                    break
                if len(tags) >= 5:  # Максимум 5 тегов
                    break
            if len(tags) >= 5:
                break
        
        return self._normalize_tags(tags)

    def _normalize_tags(self, tags: List[str]) -> List[str]:
        """Нормализация тегов: очистка, дедупликация, фильтрация общих мета-тегов."""
        if not tags:
            return []
        import re
        normalized: List[str] = []
        seen = set()
        generic = {
            'анализ текста', 'классификация контента', 'теги', 'контент', 'текст', 'пост',
            'посты', 'статья', 'заметка'
        }
        corrections = {
            'финансоваяаналитика': 'финансовая аналитика'
        }
        for raw in tags:
            if not raw:
                continue
            t = str(raw).lower().strip().strip('"\'')
            # Исправляем известные слитные варианты
            if t in corrections:
                t = corrections[t]
            # Нормализуем пробелы
            t = re.sub(r"\s+", " ", t).strip()
            # Удаляем пунктуацию по краям
            t = t.strip(' ,.;:!?#[](){}')
            # Фильтруем пустые и слишком общие
            if not t or t in generic:
                continue
            # Ограничим длину и число слов
            if len(t) > 40:
                continue
            words = t.split()
            if len(words) == 0 or len(words) > 3:
                continue
            if t not in seen:
                seen.add(t)
                normalized.append(t)
            if len(normalized) >= 7:
                break
        return normalized

async def main():
    """Главная функция."""
    print("=== WORKER STARTING ===", flush=True)
    logger.info("Worker main() called")
    
    # Получение конфигурации из environment
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    batch_size = int(os.getenv("BATCH_SIZE", "50"))
    
    print(f"Redis URL: {redis_url}", flush=True)
    print(f"Batch size: {batch_size}", flush=True)
    
    # Запуск HTTP сервера для метрик
    metrics_port = int(os.getenv("METRICS_PORT", "8001"))
    print(f"Starting metrics server on port {metrics_port}", flush=True)
    try:
        start_http_server(metrics_port)
        print(f"Metrics server started on port {metrics_port}", flush=True)
        logger.info("Metrics server started", port=metrics_port)
    except OSError as e:
        if e.errno == 98:  # Address already in use
            print(f"Metrics server already running on port {metrics_port}", flush=True)
            logger.warning("Metrics server already running", port=metrics_port)
        else:
            print(f"Error starting metrics server: {e}", flush=True)
            raise
    
    print("Creating RedisStreamConsumer", flush=True)
    consumer = RedisStreamConsumer(redis_url)
    print("RedisStreamConsumer created", flush=True)
    
    try:
        await consumer.start_consuming(batch_size)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error("Consumer failed", error=str(e))
        raise
    finally:
        await consumer.stop()

if __name__ == "__main__":
    asyncio.run(main())
# Updated Sat Oct 25 12:31:02 AM MSK 2025
