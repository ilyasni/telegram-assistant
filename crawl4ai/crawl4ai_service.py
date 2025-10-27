"""
Crawl4AI Enrichment Service
[C7-ID: CRAWL4AI-SERVICE-002]
"""
import asyncio
import os
import json
from typing import Dict, Any, Optional
import structlog
import asyncpg
from redis.asyncio import Redis
from enrichment_engine import EnrichmentEngine
from prometheus_client import Counter, Histogram, Gauge

logger = structlog.get_logger()

# Метрики с правильным неймингом
crawl_requests_total = Counter(
    'crawl_requests_total',
    'Total crawl requests',
    ['status', 'reason']  # reason только для status=skipped
)

crawl_processing_seconds = Histogram(
    'crawl_processing_seconds',
    'Crawl processing time'
)

crawl_queue_backlog_current = Gauge(
    'crawl_queue_backlog_current',
    'Crawl queue backlog'
)

crawl_pel_backlog_current = Gauge(
    'crawl_pel_backlog_current',
    'PEL backlog for posts.crawl',
    ['group']  # crawl4ai_workers
)

crawl_idempotency_hits_total = Counter(
    'crawl_idempotency_hits_total',
    'URL hash deduplication hits'
)

crawl_domain_ratelimit_inflight = Gauge(
    'crawl_domain_ratelimit_inflight',
    'Crawl domain rate limit in-flight',
    ['domain']
)

class Crawl4AIService:
    """
    Consumer для posts.crawl событий.
    Обрабатывает crawling с модульным сохранением в post_enrichment.
    """
    
    def __init__(
        self,
        redis_url: str,
        database_url: str,
        config_path: str,
        consumer_group: str = "crawl4ai_workers",
        consumer_name: str = "crawl4ai_worker_1"
    ):
        self.redis_url = redis_url
        self.database_url = database_url
        self.config_path = config_path
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.stream = "stream:posts:crawl"
        
        # Компоненты
        self.redis: Optional[Redis] = None
        self.db_pool: Optional[asyncpg.Pool] = None
        self.engine: Optional[EnrichmentEngine] = None
        self.config: Dict[str, Any] = {}
        
        logger.info("Crawl4AIService initialized",
                   consumer_group=consumer_group)
    
    async def _initialize(self):
        """Инициализация компонентов."""
        # Загрузка конфигурации
        import yaml
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Redis
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        
        # Database pool
        self.db_pool = await asyncpg.create_pool(
            self.database_url,
            min_size=2,
            max_size=10
        )
        
        # EnrichmentEngine
        self.engine = EnrichmentEngine(
            redis_url=self.redis_url,
            max_concurrent_crawls=int(os.getenv("MAX_CONCURRENT_CRAWLS", "3")),
            rate_limit_per_host=10,
            cache_ttl=3600
        )
        await self.engine.start()
        
        # Создание consumer group
        try:
            await self.redis.xgroup_create(
                self.stream,
                self.consumer_group,
                id="0",
                mkstream=True
            )
            logger.info("Consumer group created", group=self.consumer_group)
        except Exception as e:
            error_str = str(e)
            if "BUSYGROUP" in error_str:
                logger.debug("Consumer group exists", group=self.consumer_group)
            else:
                logger.warning("Failed to create consumer group", error=error_str)
        
        logger.info("Crawl4AIService initialized successfully")
    
    async def start(self):
        """Запуск сервиса с двухфазной обработкой."""
        try:
            await self._initialize()
            logger.info("Crawl4AIService started")
            
            iteration = 0
            while True:
                try:
                    # Фаза 1: Pending messages
                    pending_count = await self._process_pending_messages()
                    
                    # Фаза 2: New messages (если pending пуст)
                    if pending_count == 0:
                        await self._process_new_messages()
                    else:
                        await asyncio.sleep(0.1)
                    
                    # Периодический мониторинг очереди
                    iteration += 1
                    if iteration % 10 == 0:
                        await self._monitor_queue_backlog()
                        
                except Exception as e:
                    logger.error("Error in Crawl4AIService loop", error=str(e))
                    await asyncio.sleep(5)
                    
        except Exception as e:
            logger.error("Failed to start Crawl4AIService", error=str(e))
            raise
        finally:
            await self._cleanup()
    
    async def _process_pending_messages(self) -> int:
        """Обработка pending сообщений."""
        try:
            # Используем нативный xautoclaim
            result = await self.redis.xautoclaim(
                name=self.stream,
                groupname=self.consumer_group,
                consumername=self.consumer_name,
                min_idle_time=60000,  # 1 минута idle
                start_id="0-0",
                count=5,  # Меньше batch для crawling
                justid=False
            )
            
            # xautoclaim возвращает список [next_id, messages] или [next_id, messages, other_data]
            logger.debug("xautoclaim result type", result_type=type(result), result_len=len(result) if hasattr(result, '__len__') else 'no_len')
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
                    await self._process_crawl_request(msg_id, fields)
                    await self.redis.xack(self.stream, self.consumer_group, msg_id)
                    processed += 1
                    crawl_requests_total.labels(status='success').inc()
                    
                except Exception as e:
                    logger.error("Error processing pending crawl",
                               msg_id=msg_id, error=str(e))
                    crawl_requests_total.labels(status='failed').inc()
            
            if processed > 0:
                logger.info("Processed pending crawl requests", count=processed)
            
            return processed
            
        except Exception as e:
            logger.error("Error in _process_pending_messages", error=str(e))
            return 0
    
    async def _process_new_messages(self):
        """Обработка новых сообщений."""
        try:
            messages = await self.redis.xreadgroup(
                self.consumer_group,
                self.consumer_name,
                {self.stream: ">"},
                count=5,  # Меньше batch для crawling
                block=1000
            )
        except Exception as e:
            error_str = str(e)
            if "NOGROUP" in error_str:
                # Recreate consumer group if missing
                logger.warning("Consumer group not found, recreating", error=error_str)
                try:
                    await self.redis.xgroup_create(
                        self.stream,
                        self.consumer_group,
                        id="0",
                        mkstream=True
                    )
                    logger.info("Consumer group recreated", group=self.consumer_group)
                    return  # Retry on next iteration
                except Exception as create_error:
                    logger.error("Failed to recreate consumer group", error=str(create_error))
            raise
        
        if not messages:
            await asyncio.sleep(0.1)
            return
        
        for stream_name, entries in messages:
            for msg_id, fields in entries:
                try:
                    await self._process_crawl_request(msg_id, fields)
                    await self.redis.xack(self.stream, self.consumer_group, msg_id)
                except Exception as e:
                    logger.error("Error processing crawl request",
                               msg_id=msg_id, error=str(e))
                    crawl_requests_total.labels(status='failed').inc()
    
    async def _process_crawl_request(self, msg_id: str, fields: Dict[str, Any]):
        """Обработка одного crawl запроса."""
        import time
        start_time = time.time()
        
        try:
            # Парсинг payload
            payload = fields.get("data") or fields.get("payload") or fields
            logger.debug("Payload before parsing", payload_type=type(payload), payload=str(payload)[:200])
            if isinstance(payload, str):
                payload = json.loads(payload)
            logger.debug("Payload after parsing", payload_type=type(payload), payload=str(payload)[:200])
            
            post_id = payload.get('post_id')
            urls = payload.get('urls', [])
            trace_id = payload.get('trace_id', msg_id)
            
            if not urls:
                logger.warning("No URLs in crawl request", post_id=post_id)
                crawl_requests_total.labels(status='skipped', reason='no_urls').inc()
                return
            
            # Crawling через EnrichmentEngine
            success, enrichment_data, reason = await self.engine.enrich_post(
                payload,
                self.config
            )
            
            if not success:
                logger.info("Crawl skipped", post_id=post_id, reason=reason)
                crawl_requests_total.labels(status='skipped', reason=reason or 'unknown').inc()
                return
            
            # Модульное сохранение в post_enrichment
            latency_ms = int((time.time() - start_time) * 1000)
            
            for url, url_data in enrichment_data.items():
                # Сохранение crawl_md (kind='crawl')
                if url_data.get('markdown'):
                    await self._save_enrichment(
                        post_id=post_id,
                        kind='crawl',
                        crawl_md=url_data['markdown'],
                        enrichment_latency_ms=latency_ms,
                        metadata={
                            'url': url, 
                            'source': 'crawl4ai',
                            'trace_id': trace_id
                        }
                    )
                
                # Сохранение ocr_text (kind='ocr')
                if url_data.get('ocr_text'):
                    await self._save_enrichment(
                        post_id=post_id,
                        kind='ocr',
                        ocr_text=url_data['ocr_text'],
                        enrichment_latency_ms=latency_ms,
                        metadata={
                            'url': url, 
                            'source': 'crawl4ai',
                            'trace_id': trace_id
                        }
                    )
                
                # Сохранение vision_labels (kind='vision')
                if url_data.get('vision_labels'):
                    await self._save_enrichment(
                        post_id=post_id,
                        kind='vision',
                        vision_labels=url_data['vision_labels'],
                        enrichment_latency_ms=latency_ms,
                        metadata={
                            'url': url, 
                            'source': 'crawl4ai',
                            'trace_id': trace_id
                        }
                    )
            
            crawl_requests_total.labels(status='success').inc()
            crawl_processing_seconds.observe(time.time() - start_time)
            
            logger.info("Crawl completed",
                       post_id=post_id,
                       urls_count=len(enrichment_data),
                       latency_ms=latency_ms,
                       trace_id=trace_id)
            
        except Exception as e:
            logger.error("Error in _process_crawl_request",
                        msg_id=msg_id, error=str(e))
            crawl_requests_total.labels(status='error').inc()
            raise
    
    async def _save_enrichment(
        self,
        post_id: str,
        kind: str,
        crawl_md: Optional[str] = None,
        ocr_text: Optional[str] = None,
        vision_labels: Optional[list] = None,
        enrichment_latency_ms: int = 0,
        metadata: Dict[str, Any] = None
    ):
        """
        Модульное сохранение обогащения в post_enrichment.
        Каждый вид обогащения - отдельная запись с уникальным (post_id, kind).
        """
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO post_enrichment (
                    post_id, kind, crawl_md, ocr_text, vision_labels,
                    enrichment_provider, enrichment_latency_ms, metadata,
                    enriched_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
                ON CONFLICT (post_id, kind)
                DO UPDATE SET
                    crawl_md = COALESCE(EXCLUDED.crawl_md, post_enrichment.crawl_md),
                    ocr_text = COALESCE(EXCLUDED.ocr_text, post_enrichment.ocr_text),
                    vision_labels = COALESCE(EXCLUDED.vision_labels, post_enrichment.vision_labels),
                    enrichment_latency_ms = EXCLUDED.enrichment_latency_ms,
                    metadata = post_enrichment.metadata || EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                post_id,
                kind,
                crawl_md,
                ocr_text,
                vision_labels,
                'crawl4ai',
                enrichment_latency_ms,
                json.dumps(metadata or {})
            )
    
    async def _monitor_queue_backlog(self):
        """Мониторинг очереди crawling и PEL backlog."""
        try:
            # Мониторинг размера очереди
            queue_length = await self.redis.xlen(self.stream)
            crawl_queue_backlog_current.set(queue_length)
            
            # Мониторинг PEL backlog
            try:
                pending_info = await self.redis.xpending(self.stream, self.consumer_group)
                if pending_info and len(pending_info) >= 4:
                    pending_count = pending_info[0]  # количество pending сообщений
                    crawl_pel_backlog_current.labels(group=self.consumer_group).set(pending_count)
                    
                    if pending_count > 10:
                        logger.warning("High PEL backlog",
                                     group=self.consumer_group,
                                     pending_count=pending_count)
            except Exception as pel_error:
                # Игнорируем ошибки мониторинга PEL - не критично
                pass
            
            if queue_length > 50:
                logger.warning("High crawl queue backlog",
                             queue_length=queue_length)
        except Exception as e:
            logger.error("Error monitoring queue backlog", error=str(e))
    
    async def _cleanup(self):
        """Очистка ресурсов."""
        if self.engine:
            await self.engine.stop()
        if self.db_pool:
            await self.db_pool.close()
        if self.redis:
            await self.redis.close()
        logger.info("Crawl4AIService stopped")