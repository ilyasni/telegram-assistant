"""
Graph Writer Service - синхронизация forwards/replies данных в Neo4j (Context7 P2).

Сервис читает события из Redis Streams (или батчи из PostgreSQL) и мапит их в Neo4j:
- Узлы: (:Post), (:ForwardSource), (:Author), (:Channel), (:Topic)
- Связи: [:FORWARDED_FROM], [:REPLIES_TO], [:AUTHOR_OF], [:IN_CHANNEL], [:HAS_TOPIC]

Context7 best practices:
- Event-driven: читает события из Redis Streams
- Decoupling: не зависит от Telethon напрямую
- Идемпотентность через MERGE
- Batch processing для эффективности
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import structlog
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text
from prometheus_client import Counter, Gauge, Histogram

from worker.integrations.neo4j_client import Neo4jClient
from services.retry_policy import DLQService

logger = structlog.get_logger()

# Context7 P2: Prometheus метрики для GraphWriter
graph_writer_processed_total = Counter(
    'graph_writer_processed_total',
    'Total events processed by GraphWriter',
    ['operation_type', 'status']  # operation_type: forward|reply|author, status: ok|error
)

graph_writer_errors_total = Counter(
    'graph_writer_errors_total',
    'Total errors in GraphWriter',
    ['error_type']  # error_type: parse_error|neo4j_error|redis_error|processing_error
)

graph_writer_pel_size = Gauge(
    'graph_writer_pel_size',
    'Number of pending messages in PEL (Pending Entry List)',
    ['stream', 'consumer_group']
)

graph_writer_pending_older_than_seconds = Gauge(
    'graph_writer_pending_older_than_seconds',
    'Age of oldest pending message in seconds',
    ['stream', 'consumer_group']
)

graph_writer_autoclaim_operations_total = Counter(
    'graph_writer_autoclaim_operations_total',
    'Total number of XAUTOCLAIM operations performed',
    ['stream', 'status']
)

graph_writer_autoclaim_messages_total = Counter(
    'graph_writer_autoclaim_messages_total',
    'Total number of messages claimed via XAUTOCLAIM',
    ['stream']
)

graph_writer_operation_duration_seconds = Histogram(
    'graph_writer_operation_duration_seconds',
    'GraphWriter operation duration',
    ['operation_type'],  # operation_type: forward|reply|author|batch
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

graph_writer_batch_size = Histogram(
    'graph_writer_batch_size',
    'GraphWriter batch size',
    ['stream']
)

# Redis Stream keys
STREAM_POSTS_PARSED = "stream:posts:parsed"
STREAM_POSTS_ENRICHED = "stream:posts:enriched"  # Опционально: для обновления после enrichment
STREAM_PERSONA_MESSAGES_INGESTED = "stream:persona:messages:ingested"  # Context7 P3: Persona messages


class GraphWriter:
    """
    Graph Writer для синхронизации данных в Neo4j (Context7 P2).
    
    Читает события из Redis Streams и создаёт графовые связи в Neo4j.
    Не зависит от Telethon напрямую - работает через события.
    """
    
    def __init__(
        self,
        neo4j_client: Neo4jClient,
        redis_client: redis.Redis,
        db_session: Optional[AsyncSession] = None,
        dlq_service: Optional[DLQService] = None,
        consumer_group: str = "graph_writer",
        batch_size: int = 100,
        pel_min_idle_ms: int = 60000,  # 60 секунд для XAUTOCLAIM
        max_retries: int = 10,  # Context7: Максимальное количество retry перед отправкой в DLQ
        pel_batch_size: int = 10,  # Размер батча для pending messages
        pel_reclaim_interval: int = 30  # Интервал обработки PEL (секунды)
    ):
        """
        Инициализация GraphWriter.
        
        Args:
            neo4j_client: Neo4j клиент для записи в граф
            redis_client: Redis клиент для чтения событий
            db_session: Опциональная DB сессия для чтения данных из PostgreSQL
            consumer_group: Имя consumer group для Redis Streams
            batch_size: Размер батча для обработки событий
            pel_min_idle_ms: Минимальное время простоя для XAUTOCLAIM (мс)
            pel_batch_size: Размер батча для pending messages
            pel_reclaim_interval: Интервал обработки PEL (секунды)
        """
        self.neo4j_client = neo4j_client
        self.redis_client = redis_client
        self.db_session = db_session
        self.dlq_service = dlq_service
        self.max_retries = max_retries
        self.consumer_group = consumer_group
        self.batch_size = batch_size
        self.pel_min_idle_ms = pel_min_idle_ms
        self.pel_batch_size = pel_batch_size
        self.pel_reclaim_interval = pel_reclaim_interval
        self._running = False
        self._last_pel_reclaim_time = 0.0
        self._retry_count = {}  # Счётчик retry для каждого сообщения
        self._last_metrics_update_time = 0.0
        self._metrics_update_interval = 30.0  # Обновление метрик каждые 30 секунд
        
        logger.info("GraphWriter initialized",
                   consumer_group=consumer_group,
                   batch_size=batch_size,
                   pel_min_idle_ms=pel_min_idle_ms,
                   dlq_enabled=dlq_service is not None,
                   max_retries=max_retries)
    
    async def start_consuming(self, stream_key: str = STREAM_POSTS_PARSED):
        """
        Запуск consumption событий из Redis Streams (Context7 P2).
        
        Args:
            stream_key: Имя Redis Stream для чтения
        """
        if self._running:
            logger.warning("GraphWriter already running")
            return
        
        self._running = True
        
        try:
            # Context7: Создаём consumer group если не существует
            try:
                await self.redis_client.xgroup_create(
                    stream_key,
                    self.consumer_group,
                    id="0",
                    mkstream=True
                )
                logger.info("Created consumer group",
                           stream_key=stream_key,
                           consumer_group=self.consumer_group)
            except redis.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    # Consumer group уже существует - это нормально
                    logger.debug("Consumer group already exists",
                               stream_key=stream_key,
                               consumer_group=self.consumer_group)
                else:
                    raise
            
            consumer_name = f"{self.consumer_group}_worker_{id(self)}"
            
            logger.info("Starting GraphWriter consumption",
                       stream_key=stream_key,
                       consumer_group=self.consumer_group,
                       consumer_name=consumer_name)
            
            while self._running:
                try:
                    # Context7 P2: Периодическая обработка pending messages через XAUTOCLAIM
                    current_time = time.time()
                    if current_time - self._last_pel_reclaim_time >= self.pel_reclaim_interval:
                        pending_count = await self._process_pending_messages(stream_key, consumer_name)
                        if pending_count > 0:
                            logger.info("Processed pending messages via XAUTOCLAIM",
                                       stream_key=stream_key,
                                       count=pending_count)
                            graph_writer_autoclaim_messages_total.labels(stream=stream_key).inc(pending_count)
                        graph_writer_autoclaim_operations_total.labels(stream=stream_key, status='ok').inc()
                        self._last_pel_reclaim_time = current_time
                    
                    # Context7 P2: Периодическое обновление метрик PEL
                    if current_time - self._last_metrics_update_time >= self._metrics_update_interval:
                        await self._update_pel_metrics(stream_key)
                        self._last_metrics_update_time = current_time
                    
                    # Context7: Читаем события из Stream с блокировкой
                    messages = await self.redis_client.xreadgroup(
                        self.consumer_group,
                        consumer_name,
                        {stream_key: ">"},  # Читаем новые сообщения
                        count=self.batch_size,
                        block=5000  # Блокировка 5 секунд
                    )
                    
                    if not messages:
                        continue
                    
                    # Обработка батча событий
                    for stream, stream_messages in messages:
                        # Context7 P3: Определяем тип stream и обрабатываем соответственно
                        if stream_key == STREAM_PERSONA_MESSAGES_INGESTED:
                            await self._process_persona_batch(stream_messages, stream_key, consumer_name)
                        else:
                            await self._process_batch(stream_messages, stream_key, consumer_name)
                        
                except Exception as e:
                    logger.error("Error consuming events",
                               stream_key=stream_key,
                               error=str(e),
                               exc_info=True)
                    # Context7: Exponential backoff при ошибке
                    await asyncio.sleep(min(60, 2 ** min(self._retry_count.get('consume_error', 0), 6)))
                    self._retry_count['consume_error'] = self._retry_count.get('consume_error', 0) + 1
                    
        except Exception as e:
            logger.error("GraphWriter consumption failed",
                        stream_key=stream_key,
                        error=str(e),
                        exc_info=True)
            self._running = False
            raise
    
    async def _process_pending_messages(
        self,
        stream_key: str,
        consumer_name: str
    ) -> int:
        """
        Context7 P2: Обработка pending сообщений через XAUTOCLAIM.
        
        Args:
            stream_key: Имя stream для чтения
            consumer_name: Имя consumer для claim
            
        Returns:
            Количество обработанных pending сообщений
        """
        try:
            # Context7: XAUTOCLAIM для получения зависших сообщений из PEL
            result = await self.redis_client.xautoclaim(
                name=stream_key,
                groupname=self.consumer_group,
                consumername=consumer_name,
                min_idle_time=self.pel_min_idle_ms,
                start_id="0-0",
                count=self.pel_batch_size,
                justid=False
            )
            
            # xautoclaim возвращает [next_id, messages] или [next_id, messages, other_data]
            if isinstance(result, (list, tuple)) and len(result) >= 2:
                next_id, messages = result[0], result[1]
            else:
                messages = result if isinstance(result, list) else []
                next_id = None
            
            if not messages:
                return 0
            
            processed = 0
            for msg_id, fields in messages:
                try:
                    # Десериализация события
                    event_data = self._parse_stream_message(fields)
                    
                    if not event_data:
                        logger.warning("Failed to parse pending message",
                                     message_id=msg_id,
                                     stream_key=stream_key)
                        continue
                    
                    # Обработка события
                    success = await self._process_post_parsed_event(event_data)
                    
                    if success:
                        # ACK сообщения после успешной обработки
                        await self.redis_client.xack(stream_key, self.consumer_group, msg_id)
                        processed += 1
                        # Сброс retry count при успехе
                        if msg_id in self._retry_count:
                            del self._retry_count[msg_id]
                    else:
                        # Не ACK - оставляем в PEL для повторной обработки
                        retry_count = self._retry_count.get(msg_id, 0) + 1
                        self._retry_count[msg_id] = retry_count
                        
                        if retry_count > self.max_retries:
                            logger.error("Message exceeded max retries, sending to DLQ",
                                       message_id=msg_id,
                                       retry_count=retry_count,
                                       max_retries=self.max_retries,
                                       post_id=event_data.get('post_id'))
                            
                            # Context7: Отправка в DLQ если превышен лимит retry
                            await self._send_to_dlq(
                                message_id=msg_id,
                                event_data=event_data,
                                retry_count=retry_count,
                                error=Exception(f"Message exceeded max retries ({retry_count}/{self.max_retries})")
                            )
                            
                            # ACK сообщение после отправки в DLQ (чтобы оно не обрабатывалось снова)
                            await self.redis_client.xack(stream_key, self.consumer_group, msg_id)
                            
                            # Удаляем из retry count
                            if msg_id in self._retry_count:
                                del self._retry_count[msg_id]
                        
                except Exception as e:
                    logger.error("Error processing pending message",
                               message_id=msg_id,
                               error=str(e),
                               exc_info=True)
                    # Не ACK - оставляем в PEL для повторной обработки
            
            if processed > 0:
                logger.debug("Processed pending messages",
                           stream_key=stream_key,
                           processed=processed,
                           total=len(messages))
                graph_writer_autoclaim_messages_total.labels(stream=stream_key).inc(processed)
            
            return processed
            
        except Exception as e:
            logger.error("Error in _process_pending_messages",
                        stream_key=stream_key,
                        error=str(e),
                        exc_info=True)
            graph_writer_autoclaim_operations_total.labels(stream=stream_key, status='error').inc()
            graph_writer_errors_total.labels(error_type='redis_error').inc()
            return 0
    
    async def _process_batch(
        self,
        stream_messages: List[tuple],
        stream_key: str,
        consumer_name: str
    ):
        """
        Обработка батча событий из Redis Streams.
        
        Args:
            stream_messages: Список сообщений из Redis Stream (message_id, fields_dict)
            stream_key: Имя stream для логирования
        """
        processed = 0
        failed = 0
        
        for message_id, fields in stream_messages:
            try:
                # Context7: Десериализация события из Redis Stream
                event_data = self._parse_stream_message(fields)
                
                if not event_data:
                    logger.warning("Failed to parse stream message",
                                 message_id=message_id,
                                 stream_key=stream_key)
                    graph_writer_errors_total.labels(error_type='parse_error').inc()
                    failed += 1
                    continue
                
                # Обработка события и создание графовых связей
                success = await self._process_post_parsed_event(event_data)
                
                if success:
                    # Context7: ACK сообщения после успешной обработки
                    await self.redis_client.xack(stream_key, self.consumer_group, message_id)
                    processed += 1
                    # Сброс retry count при успехе
                    if message_id in self._retry_count:
                        del self._retry_count[message_id]
                else:
                    failed += 1
                    retry_count = self._retry_count.get(message_id, 0) + 1
                    self._retry_count[message_id] = retry_count
                    
                    logger.warning("Failed to process event",
                                 message_id=message_id,
                                 post_id=event_data.get('post_id'),
                                 retry_count=retry_count,
                                 max_retries=self.max_retries)
                    
                    # Context7: Проверка превышения лимита retry
                    if retry_count > self.max_retries:
                        logger.error("Message exceeded max retries in batch processing, sending to DLQ",
                                   message_id=message_id,
                                   retry_count=retry_count,
                                   max_retries=self.max_retries,
                                   post_id=event_data.get('post_id'))
                        
                        # Отправка в DLQ
                        await self._send_to_dlq(
                            message_id=message_id,
                            event_data=event_data,
                            retry_count=retry_count,
                            error=Exception(f"Message exceeded max retries ({retry_count}/{self.max_retries})")
                        )
                        
                        # ACK сообщение после отправки в DLQ
                        await self.redis_client.xack(stream_key, self.consumer_group, message_id)
                        
                        # Удаляем из retry count
                        if message_id in self._retry_count:
                            del self._retry_count[message_id]
                    else:
                        # Не ACK - оставляем в PEL для повторной обработки через XAUTOCLAIM
                        pass
                    
            except Exception as e:
                logger.error("Error processing stream message",
                           message_id=message_id,
                           error=str(e),
                           exc_info=True)
                failed += 1
                # Не ACK - оставляем в PEL для повторной обработки через XAUTOCLAIM
        
        # Context7 P2: Обновление метрик
        graph_writer_batch_size.labels(stream=stream_key).observe(len(stream_messages))
        
        if processed > 0:
            graph_writer_processed_total.labels(operation_type='batch', status='ok').inc(processed)
        if failed > 0:
            graph_writer_processed_total.labels(operation_type='batch', status='error').inc(failed)
            graph_writer_errors_total.labels(error_type='processing_error').inc(failed)
        
        logger.info("Batch processed",
                   stream_key=stream_key,
                   processed=processed,
                   failed=failed,
                   total=len(stream_messages))
    
    def _parse_stream_message(self, fields: Dict[str, bytes]) -> Optional[Dict[str, Any]]:
        """
        Парсинг сообщения из Redis Stream в событие.
        
        Args:
            fields: Словарь полей из Redis Stream
            
        Returns:
            Распарсенное событие или None при ошибке
        """
        try:
            # Context7: Redis Stream может хранить данные в разных форматах
            # Проверяем наличие поля 'data' (JSON bytes) или прямое хранение полей
            if b'data' in fields:
                # Формат: {"data": json_bytes}
                data_bytes = fields[b'data']
                if isinstance(data_bytes, bytes):
                    data_str = data_bytes.decode('utf-8')
                    event_data = json.loads(data_str)
                else:
                    event_data = json.loads(data_bytes)
            else:
                # Формат: прямые поля как строки
                event_data = {}
                for key, value in fields.items():
                    key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                    value_str = value.decode('utf-8') if isinstance(value, bytes) else value
                    
                    # Пытаемся распарсить JSON для сложных полей
                    try:
                        event_data[key_str] = json.loads(value_str)
                    except (json.JSONDecodeError, TypeError):
                        event_data[key_str] = value_str
            
            return event_data
            
        except Exception as e:
            logger.error("Failed to parse stream message",
                        error=str(e),
                        fields_keys=list(fields.keys())[:5] if fields else [])
            return None
    
    async def _process_post_parsed_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Обработка события post.parsed и создание графовых связей (Context7 P2).
        
        Создаёт:
        1. Пост узел (если ещё не создан через indexing_task)
        2. Forward связи (если есть forward_from_peer_id)
        3. Reply связи (если есть reply_to_message_id)
        4. Author связи (если есть author информация)
        
        Args:
            event_data: Данные события post.parsed
            
        Returns:
            True если успешно, False при ошибке
        """
        start_time = time.perf_counter()
        try:
            post_id = event_data.get('post_id')
            if not post_id:
                logger.warning("Event missing post_id", event_data=event_data)
                return False
            
            # Context7 P2: Получаем дополнительные данные из PostgreSQL если доступна сессия
            # (forwards/replies могут быть в БД, но не в событии)
            post_metadata = await self._fetch_post_metadata(post_id) if self.db_session else {}
            
            # Объединяем данные события и метаданные из БД
            merged_data = {**event_data, **post_metadata}
            
            # Context7 P2: Создание forward связей
            if merged_data.get('forward_from_peer_id') or merged_data.get('forward_from_chat_id'):
                forward_start = time.perf_counter()
                try:
                    await self.neo4j_client.create_forward_relationship(
                        post_id=post_id,
                        forward_from_peer_id=merged_data.get('forward_from_peer_id'),
                        forward_from_chat_id=merged_data.get('forward_from_chat_id'),
                        forward_from_message_id=merged_data.get('forward_from_message_id'),
                        forward_date=merged_data.get('forward_date'),
                        forward_from_name=merged_data.get('forward_from_name')
                    )
                    graph_writer_processed_total.labels(operation_type='forward', status='ok').inc()
                    graph_writer_operation_duration_seconds.labels(operation_type='forward').observe(time.perf_counter() - forward_start)
                except Exception as e:
                    graph_writer_processed_total.labels(operation_type='forward', status='error').inc()
                    graph_writer_errors_total.labels(error_type='neo4j_error').inc()
                    logger.error("Error creating forward relationship", post_id=post_id, error=str(e))
            
            # Context7 P2: Создание reply связей
            if merged_data.get('reply_to_message_id'):
                reply_start = time.perf_counter()
                try:
                    await self.neo4j_client.create_reply_relationship(
                        post_id=post_id,
                        reply_to_message_id=merged_data.get('reply_to_message_id'),
                        reply_to_chat_id=merged_data.get('reply_to_chat_id'),
                        thread_id=merged_data.get('thread_id')
                    )
                    graph_writer_processed_total.labels(operation_type='reply', status='ok').inc()
                    graph_writer_operation_duration_seconds.labels(operation_type='reply').observe(time.perf_counter() - reply_start)
                except Exception as e:
                    graph_writer_processed_total.labels(operation_type='reply', status='error').inc()
                    graph_writer_errors_total.labels(error_type='neo4j_error').inc()
                    logger.error("Error creating reply relationship", post_id=post_id, error=str(e))
            
            # Context7 P2: Создание author связей (если есть информация об авторе)
            # Автор может быть в post_author или в forward_from_name
            author_peer_id = merged_data.get('author_peer_id')
            author_name = merged_data.get('post_author') or merged_data.get('forward_from_name')
            
            if author_peer_id or author_name:
                author_start = time.perf_counter()
                try:
                    await self.neo4j_client.create_author_relationship(
                        post_id=post_id,
                        author_peer_id=author_peer_id,
                        author_name=author_name,
                        author_type=merged_data.get('author_type')
                    )
                    graph_writer_processed_total.labels(operation_type='author', status='ok').inc()
                    graph_writer_operation_duration_seconds.labels(operation_type='author').observe(time.perf_counter() - author_start)
                except Exception as e:
                    graph_writer_processed_total.labels(operation_type='author', status='error').inc()
                    graph_writer_errors_total.labels(error_type='neo4j_error').inc()
                    logger.error("Error creating author relationship", post_id=post_id, error=str(e))
            
            elapsed = time.perf_counter() - start_time
            graph_writer_operation_duration_seconds.labels(operation_type='batch').observe(elapsed)
            
            logger.debug("Processed post parsed event",
                        post_id=post_id,
                        has_forward=bool(merged_data.get('forward_from_peer_id')),
                        has_reply=bool(merged_data.get('reply_to_message_id')),
                        has_author=bool(author_peer_id or author_name),
                        duration_seconds=elapsed)
            
            return True
            
        except Exception as e:
            graph_writer_errors_total.labels(error_type='processing_error').inc()
            logger.error("Error processing post parsed event",
                        post_id=event_data.get('post_id'),
                        error=str(e),
                        exc_info=True)
            return False
    
    async def _fetch_post_metadata(self, post_id: str) -> Dict[str, Any]:
        """
        Получение метаданных поста из PostgreSQL (Context7 P2).
        
        Извлекает forwards/replies данные из таблиц post_forwards и post_replies.
        
        Args:
            post_id: ID поста
            
        Returns:
            Словарь с метаданными (forward_from_peer_id, reply_to_message_id и т.д.)
        """
        if not self.db_session:
            return {}
        
        try:
            metadata = {}
            
            # Получаем данные о forwards
            forwards_result = await self.db_session.execute(
                text("""
                    SELECT 
                        forward_from_peer_id,
                        forward_from_chat_id,
                        forward_from_message_id,
                        forward_date,
                        forward_from_name
                    FROM posts
                    WHERE id = :post_id
                """),
                {"post_id": post_id}
            )
            forwards_row = forwards_result.fetchone()
            
            if forwards_row:
                if forwards_row.forward_from_peer_id:
                    metadata['forward_from_peer_id'] = forwards_row.forward_from_peer_id
                if forwards_row.forward_from_chat_id:
                    metadata['forward_from_chat_id'] = forwards_row.forward_from_chat_id
                if forwards_row.forward_from_message_id:
                    metadata['forward_from_message_id'] = forwards_row.forward_from_message_id
                if forwards_row.forward_date:
                    metadata['forward_date'] = forwards_row.forward_date.isoformat() if hasattr(forwards_row.forward_date, 'isoformat') else str(forwards_row.forward_date)
                if forwards_row.forward_from_name:
                    metadata['forward_from_name'] = forwards_row.forward_from_name
            
            # Получаем данные о replies
            replies_result = await self.db_session.execute(
                text("""
                    SELECT 
                        reply_to_message_id,
                        reply_to_chat_id,
                        thread_id
                    FROM posts
                    WHERE id = :post_id
                """),
                {"post_id": post_id}
            )
            replies_row = replies_result.fetchone()
            
            if replies_row:
                if replies_row.reply_to_message_id:
                    metadata['reply_to_message_id'] = replies_row.reply_to_message_id
                if replies_row.reply_to_chat_id:
                    metadata['reply_to_chat_id'] = replies_row.reply_to_chat_id
                if replies_row.thread_id:
                    metadata['thread_id'] = replies_row.thread_id
            
            return metadata
            
        except Exception as e:
            logger.warning("Failed to fetch post metadata from DB",
                         post_id=post_id,
                         error=str(e))
            return {}
    
    async def process_batch_from_postgres(
        self,
        post_ids: List[str],
        batch_size: int = 100
    ) -> Dict[str, int]:
        """
        Обработка батча постов из PostgreSQL (Context7 P2).
        
        Полезно для backfilling графа из существующих данных.
        
        Args:
            post_ids: Список ID постов для обработки
            batch_size: Размер батча
            
        Returns:
            Словарь со статистикой обработки
        """
        if not self.db_session:
            logger.error("DB session not available for batch processing")
            return {'processed': 0, 'failed': len(post_ids)}
        
        processed = 0
        failed = 0
        
        for i in range(0, len(post_ids), batch_size):
            batch = post_ids[i:i + batch_size]
            
            try:
                # Получаем данные постов из БД
                result = await self.db_session.execute(
                    text("""
                        SELECT 
                            id as post_id,
                            channel_id,
                            forward_from_peer_id,
                            forward_from_chat_id,
                            forward_from_message_id,
                            forward_date,
                            forward_from_name,
                            reply_to_message_id,
                            reply_to_chat_id,
                            thread_id,
                            post_author
                        FROM posts
                        WHERE id = ANY(:post_ids)
                    """),
                    {"post_ids": batch}
                )
                
                posts = result.fetchall()
                
                for post in posts:
                    try:
                        # Создаём событие-подобную структуру из данных БД
                        event_data = {
                            'post_id': str(post.post_id),
                            'channel_id': str(post.channel_id),
                            'forward_from_peer_id': post.forward_from_peer_id,
                            'forward_from_chat_id': post.forward_from_chat_id,
                            'forward_from_message_id': post.forward_from_message_id,
                            'forward_date': post.forward_date.isoformat() if post.forward_date and hasattr(post.forward_date, 'isoformat') else str(post.forward_date) if post.forward_date else None,
                            'forward_from_name': post.forward_from_name,
                            'reply_to_message_id': post.reply_to_message_id,
                            'reply_to_chat_id': post.reply_to_chat_id,
                            'thread_id': post.thread_id,
                            'post_author': post.post_author
                        }
                        
                        success = await self._process_post_parsed_event(event_data)
                        if success:
                            processed += 1
                        else:
                            failed += 1
                            
                    except Exception as e:
                        logger.error("Error processing post from batch",
                                   post_id=str(post.post_id),
                                   error=str(e))
                        failed += 1
                        
            except Exception as e:
                logger.error("Error fetching batch from PostgreSQL",
                           batch_start=i,
                           batch_size=len(batch),
                           error=str(e))
                failed += len(batch)
        
        logger.info("Batch processing from PostgreSQL completed",
                   total=len(post_ids),
                   processed=processed,
                   failed=failed)
        
        return {
            'processed': processed,
            'failed': failed,
            'total': len(post_ids)
        }
    
    async def _update_pel_metrics(self, stream_key: str):
        """
        Context7 P2: Обновление метрик PEL для мониторинга.
        
        Args:
            stream_key: Имя stream для обновления метрик
        """
        try:
            # Получение информации о PEL через XPENDING
            pending_info = await self.redis_client.xpending(stream_key, self.consumer_group)
            
            if pending_info and isinstance(pending_info, dict):
                pel_size = pending_info.get('pending', 0)
                graph_writer_pel_size.labels(stream=stream_key, consumer_group=self.consumer_group).set(pel_size)
                
                # Получение возраста самого старого pending сообщения
                if pel_size > 0:
                    try:
                        # Получаем самые старые pending сообщения
                        pending_messages = await self.redis_client.xpending_range(
                            stream_key,
                            self.consumer_group,
                            min="-",
                            max="+",
                            count=1
                        )
                        
                        if pending_messages:
                            # Возраст в миллисекундах
                            oldest_age_ms = pending_messages[0].get('time_since_delivered', 0) if isinstance(pending_messages[0], dict) else 0
                            oldest_age_seconds = oldest_age_ms / 1000.0
                            graph_writer_pending_older_than_seconds.labels(
                                stream=stream_key,
                                consumer_group=self.consumer_group
                            ).set(oldest_age_seconds)
                    except Exception as e:
                        logger.debug("Failed to get pending message age", error=str(e))
                else:
                    # Нет pending сообщений
                    graph_writer_pending_older_than_seconds.labels(
                        stream=stream_key,
                        consumer_group=self.consumer_group
                    ).set(0)
        except Exception as e:
            logger.debug("Failed to update PEL metrics", stream_key=stream_key, error=str(e))
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        Context7 P2: Получение статистики GraphWriter для health checks.
        
        Returns:
            Словарь со статистикой (pel_size, retry_count, etc.)
        """
        try:
            stats = {
                'running': self._running,
                'retry_count': len(self._retry_count),
                'pel_size': 0,
                'pending_older_than_seconds': 0
            }
            
            # Получаем PEL статистику
            if self._running:
                try:
                    # Используем первый stream_key (STREAM_POSTS_PARSED)
                    stream_key = STREAM_POSTS_PARSED
                    pending_info = await self.redis_client.xpending(stream_key, self.consumer_group)
                    
                    if pending_info and isinstance(pending_info, dict):
                        stats['pel_size'] = pending_info.get('pending', 0)
                        
                        # Возраст самого старого pending сообщения
                        if stats['pel_size'] > 0:
                            try:
                                pending_messages = await self.redis_client.xpending_range(
                                    stream_key,
                                    self.consumer_group,
                                    min="-",
                                    max="+",
                                    count=1
                                )
                                if pending_messages:
                                    oldest_age_ms = pending_messages[0].get('time_since_delivered', 0) if isinstance(pending_messages[0], dict) else 0
                                    stats['pending_older_than_seconds'] = oldest_age_ms / 1000.0
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug("Failed to get PEL stats", error=str(e))
            
            return stats
        except Exception as e:
            logger.error("Error getting GraphWriter stats", error=str(e))
            return {'running': self._running, 'error': str(e)}
    
    # ============================================================================
    # P3: PERSONA MESSAGES PROCESSING (Context7 P3: Sideloading)
    # ============================================================================
    
    async def _process_persona_batch(
        self,
        stream_messages: List[tuple],
        stream_key: str,
        consumer_name: str
    ):
        """
        Обработка батча persona событий из Redis Streams (Context7 P3).
        
        Args:
            stream_messages: Список сообщений из Redis Stream
            stream_key: Имя stream
            consumer_name: Имя consumer
        """
        processed = 0
        failed = 0
        
        for msg_id, fields in stream_messages:
            try:
                # Десериализация события
                event_data = self._parse_stream_message(fields)
                
                if not event_data:
                    logger.warning("Failed to parse persona message",
                                 message_id=msg_id,
                                 stream_key=stream_key)
                    failed += 1
                    continue
                
                # Обработка persona события
                success = await self._process_persona_message_event(event_data)
                
                if success:
                    # ACK сообщения после успешной обработки
                    await self.redis_client.xack(stream_key, self.consumer_group, msg_id)
                    processed += 1
                    graph_writer_processed_total.labels(operation_type='persona', status='ok').inc()
                else:
                    failed += 1
                    graph_writer_processed_total.labels(operation_type='persona', status='error').inc()
                    # Не ACK - оставляем в PEL для повторной обработки
                    
            except Exception as e:
                logger.error("Error processing persona message",
                           message_id=msg_id,
                           error=str(e),
                           exc_info=True)
                failed += 1
                graph_writer_errors_total.labels(error_type='processing_error').inc()
        
        if processed > 0 or failed > 0:
            logger.info("Processed persona batch",
                       stream_key=stream_key,
                       processed=processed,
                       failed=failed,
                       total=len(stream_messages))
            graph_writer_batch_size.labels(stream=stream_key).observe(len(stream_messages))
    
    async def _process_persona_message_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Обработка события persona_message_ingested и создание графовых связей (Context7 P3).
        
        Создаёт:
        1. Узел (:Persona) для пользователя (если не существует)
        2. Узел (:Dialogue) для диалога (если не существует)
        3. Связь (:Persona)-[:HAS_DIALOGUE]->(:Dialogue)
        4. Связь (:Post)-[:IN_DIALOGUE]->(:Dialogue)
        5. Связь (:Persona)-[:SENT_MESSAGE]->(:Post) (если пользователь - отправитель)
        
        Args:
            event_data: Данные события persona_message_ingested
            
        Returns:
            True если успешно, False при ошибке
        """
        start_time = time.perf_counter()
        try:
            user_id = event_data.get('user_id')
            tenant_id = event_data.get('tenant_id')
            message_id = event_data.get('message_id')
            telegram_message_id = event_data.get('telegram_message_id')
            dialog_type = event_data.get('dialog_type', 'dm')
            peer_id = event_data.get('sender_tg_id')  # Используем sender_tg_id как peer_id для DM
            
            if not user_id or not tenant_id:
                logger.warning("Event missing user_id or tenant_id", event_data=event_data)
                return False
            
            # Context7 P3: Получаем telegram_id пользователя из БД
            telegram_id = None
            if self.db_session:
                try:
                    result = await self.db_session.execute(
                        text("SELECT telegram_id FROM users WHERE id = :user_id::uuid"),
                        {"user_id": user_id}
                    )
                    row = result.fetchone()
                    if row:
                        telegram_id = row.telegram_id
                except Exception as e:
                    logger.warning("Failed to fetch user telegram_id", user_id=user_id, error=str(e))
            
            if not telegram_id:
                # Используем user_id как fallback (предполагаем, что user_id - это telegram_id)
                try:
                    telegram_id = int(user_id)
                except (ValueError, TypeError):
                    logger.warning("Cannot determine telegram_id for persona", user_id=user_id)
                    return False
            
            # Context7 P3: Создаём узел Persona
            persona_start = time.perf_counter()
            try:
                await self.neo4j_client.create_persona_node(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    telegram_id=telegram_id,
                    persona_name=None,  # Можно расширить в будущем
                    persona_metadata=None
                )
                graph_writer_operation_duration_seconds.labels(operation_type='persona').observe(time.perf_counter() - persona_start)
            except Exception as e:
                logger.error("Error creating persona node", user_id=user_id, error=str(e))
                return False
            
            # Context7 P3: Определяем dialogue_id и peer_id
            # Для DM: dialogue_id = peer_id (telegram_id собеседника)
            # Для групп: dialogue_id = peer_id (telegram_id группы)
            dialogue_id = str(peer_id) if peer_id else f"dialogue_{user_id}_{dialog_type}"
            
            # Если нет peer_id, используем dialogue_id из события или создаём на основе типа
            if not peer_id:
                # Для DM нужно получить peer_id из channel_id (виртуальный канал с отрицательным ID)
                if message_id and self.db_session and dialog_type == 'dm':
                    try:
                        result = await self.db_session.execute(
                            text("""
                                SELECT c.tg_channel_id, c.title 
                                FROM posts p
                                JOIN channels c ON p.channel_id = c.id
                                WHERE p.id = :message_id::uuid AND p.source = 'dm'
                            """),
                            {"message_id": message_id}
                        )
                        row = result.fetchone()
                        if row:
                            # tg_channel_id для DM отрицательный, берём модуль
                            peer_id = abs(row.tg_channel_id)
                            dialogue_id = str(peer_id)
                    except Exception as e:
                        logger.warning("Failed to fetch DM peer_id", message_id=message_id, error=str(e))
            
            # Context7 P3: Создаём узел Dialogue
            dialogue_start = time.perf_counter()
            try:
                peer_name = event_data.get('sender_username') or event_data.get('sender_name')
                await self.neo4j_client.create_dialogue_node(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    dialogue_id=dialogue_id,
                    dialogue_type=dialog_type,
                    peer_id=int(peer_id) if peer_id else 0,
                    peer_name=peer_name,
                    dialogue_metadata=None
                )
                graph_writer_operation_duration_seconds.labels(operation_type='dialogue').observe(time.perf_counter() - dialogue_start)
            except Exception as e:
                logger.error("Error creating dialogue node",
                           dialogue_id=dialogue_id,
                           dialogue_type=dialog_type,
                           error=str(e))
                return False
            
            # Context7 P3: Создаём связи между Post и Dialogue
            # Для DM: message_id - это post_id в таблице posts
            # Для групп: message_id - это group_message_id в таблице group_messages
            if message_id:
                # Для DM используем message_id как post_id
                if dialog_type == 'dm':
                    post_id = message_id
                else:
                    # Для групп нужно получить post_id из group_messages или создать связь по-другому
                    # Временно пропускаем для групп (можно расширить позже)
                    logger.debug("Skipping group message link (not implemented yet)",
                               message_id=message_id,
                               dialog_type=dialog_type)
                    post_id = None
                
                if post_id:
                    relationship_start = time.perf_counter()
                    try:
                        await self.neo4j_client.create_persona_message_relationship(
                            post_id=post_id,
                            user_id=user_id,
                            tenant_id=tenant_id,
                            dialogue_id=dialogue_id,
                            dialogue_type=dialog_type,
                            peer_id=int(peer_id) if peer_id else 0
                        )
                        graph_writer_operation_duration_seconds.labels(operation_type='persona_relationship').observe(time.perf_counter() - relationship_start)
                    except Exception as e:
                        logger.error("Error creating persona message relationship",
                                   post_id=post_id,
                                   dialogue_id=dialogue_id,
                                   error=str(e))
                        # Не критично - продолжаем
            
            elapsed = time.perf_counter() - start_time
            graph_writer_operation_duration_seconds.labels(operation_type='batch').observe(elapsed)
            
            logger.debug("Processed persona message event",
                        user_id=user_id,
                        dialogue_id=dialogue_id,
                        dialog_type=dialog_type,
                        duration_seconds=elapsed)
            
            return True
            
        except Exception as e:
            graph_writer_errors_total.labels(error_type='processing_error').inc()
            logger.error("Error processing persona message event",
                        user_id=event_data.get('user_id'),
                        error=str(e),
                        exc_info=True)
            return False
    
    async def start_consuming_persona(self):
        """
        Запуск consumption persona событий из Redis Streams (Context7 P3).
        
        Отдельный метод для persona stream, можно запускать параллельно с основным.
        """
        await self.start_consuming(STREAM_PERSONA_MESSAGES_INGESTED)
    
    async def _send_to_dlq(
        self,
        message_id: str,
        event_data: Dict[str, Any],
        retry_count: int,
        error: Exception
    ) -> Optional[str]:
        """
        Отправка сообщения в DLQ при превышении лимита retry (Context7).
        
        Args:
            message_id: ID сообщения из Redis Stream
            event_data: Данные события
            retry_count: Количество попыток retry
            error: Ошибка, вызвавшая попадание в DLQ
            
        Returns:
            Message ID в DLQ stream или None при ошибке
        """
        try:
            trace_id = event_data.get('trace_id') or event_data.get('correlation_id') or str(message_id)
            tenant_id = event_data.get('tenant_id')
            post_id = event_data.get('post_id')
            
            # Определяем тип события
            base_event_type = "posts.parsed"  # По умолчанию для post.parsed
            if 'dialog_type' in event_data:
                base_event_type = "persona.messages.ingested"
            
            # Context7: Используем DLQService если доступен
            if self.dlq_service:
                dlq_message_id = await self.dlq_service.send_to_dlq(
                    base_event_type=base_event_type,
                    payload={
                        **event_data,
                        "original_message_id": message_id.decode() if isinstance(message_id, bytes) else message_id,
                        "post_id": post_id
                    },
                    error=error,
                    retry_count=retry_count,
                    trace_id=trace_id,
                    tenant_id=tenant_id
                )
                
                logger.info("Message sent to DLQ via DLQService",
                           message_id=message_id,
                           dlq_message_id=dlq_message_id,
                           base_event_type=base_event_type,
                           retry_count=retry_count,
                           post_id=post_id)
                
                graph_writer_processed_total.labels(operation_type='dlq', status='ok').inc()
                return dlq_message_id
            else:
                # Fallback: прямая отправка в DLQ stream
                dlq_stream = f"stream:{base_event_type.replace('.', ':')}:dlq"
                import json
                
                dlq_message_id = await self.redis_client.xadd(
                    dlq_stream,
                    {
                        "original_stream": STREAM_POSTS_PARSED if base_event_type == "posts.parsed" else STREAM_PERSONA_MESSAGES_INGESTED,
                        "original_message_id": message_id.decode() if isinstance(message_id, bytes) else message_id,
                        "error": str(error)[:500],
                        "retry_count": str(retry_count),
                        "max_retries": str(self.max_retries),
                        "trace_id": trace_id,
                        "tenant_id": tenant_id or "",
                        "post_id": post_id or "",
                        "data": json.dumps(event_data, default=str)
                    },
                    maxlen=10000
                )
                
                logger.warning("Message sent to DLQ via fallback (DLQService not available)",
                             message_id=message_id,
                             dlq_message_id=dlq_message_id,
                             dlq_stream=dlq_stream,
                             retry_count=retry_count,
                             post_id=post_id)
                
                graph_writer_processed_total.labels(operation_type='dlq', status='ok').inc()
                return dlq_message_id
                
        except Exception as e:
            graph_writer_errors_total.labels(error_type='dlq_error').inc()
            logger.error("Failed to send message to DLQ",
                        message_id=message_id,
                        error=str(e),
                        exc_info=True)
            return None
    
    async def stop(self):
        """Остановка GraphWriter."""
        self._running = False
        logger.info("GraphWriter stopped")

