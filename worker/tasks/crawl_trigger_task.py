"""
Crawl Trigger Task - Producer для posts.crawl
[C7-ID: WORKER-CRAWL-TRIGGER-001]

Читает posts.tagged и публикует в posts.crawl при наличии триггерных тегов.
"""
import asyncio
import json
import time
from typing import Dict, Any, Optional
import structlog
from redis.asyncio import Redis
from prometheus_client import Counter, Gauge, Histogram

logger = structlog.get_logger()

# Метрики с правильным неймингом
crawl_triggers_total = Counter(
    'crawl_triggers_total',
    'Total crawl triggers',
    ['reason']
)

crawl_trigger_queue_depth_current = Gauge(
    'crawl_trigger_queue_depth_current',
    'Current queue depth for posts.tagged'
)

crawl_trigger_processing_latency_seconds = Histogram(
    'crawl_trigger_processing_latency_seconds',
    'Processing latency for trigger task'
)

crawl_trigger_idempotency_hits_total = Counter(
    'crawl_trigger_idempotency_hits_total',
    'Idempotency cache hits'
)

crawl_trigger_policy_skips_total = Counter(
    'crawl_trigger_policy_skips_total',
    'Policy skips by reason',
    ['reason']  # фиксированные: no_trigger_tags, no_urls
)

class CrawlTriggerTask:
    """Producer для posts.crawl на основе триггерных тегов."""
    
    def __init__(
        self,
        redis_url: str,
        trigger_tags: list,
        consumer_group: str = "crawl_trigger_workers",
        consumer_name: str = "crawl_trigger_worker_1"
    ):
        self.redis_url = redis_url
        self.trigger_tags = trigger_tags
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.stream_in = "stream:posts:tagged"
        self.stream_out = "stream:posts:crawl"
        self.redis: Optional[Redis] = None
        
        logger.info("CrawlTriggerTask initialized",
                   trigger_tags=trigger_tags)
    
    async def _initialize(self):
        """Инициализация."""
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        
        # Создание consumer group
        try:
            await self.redis.xgroup_create(
                self.stream_in,
                self.consumer_group,
                id="0",
                mkstream=True
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.warning("Failed to create consumer group", error=str(e))
    
    async def start(self):
        """Запуск producer."""
        await self._initialize()
        logger.info("CrawlTriggerTask started")
        
        while True:
            try:
                # Обновляем метрику глубины очереди
                queue_length = await self.redis.xlen(self.stream_in)
                crawl_trigger_queue_depth_current.set(queue_length)
                
                # Читаем сообщения (сначала с начала, потом только новые)
                logger.debug("Reading from stream", stream=self.stream_in, group=self.consumer_group)
                messages = await self.redis.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    {self.stream_in: ">"},  # Читаем только новые сообщения
                    count=50,
                    block=1000
                )
                
                logger.debug("xreadgroup result", messages=messages, type=type(messages), len=len(messages) if messages else 0)
                
                # Обработка сообщений
                if messages:
                    logger.debug("CrawlTriggerTask received messages", count=len(messages))
                    for stream_name, entries in messages:
                        logger.debug("Processing stream", stream=stream_name, entries_count=len(entries))
                        if not entries:
                            logger.debug("Stream has zero entries", stream=stream_name)
                            continue
                        for msg_id, fields in entries:
                            logger.debug("Processing message", msg_id=msg_id, fields_keys=list(fields.keys()) if isinstance(fields, dict) else "not_dict")
                            try:
                                # Измеряем время обработки
                                start_time = time.time()
                                await self._process_tagged_event(msg_id, fields)
                                processing_time = time.time() - start_time
                                crawl_trigger_processing_latency_seconds.observe(processing_time)
                                
                                await self.redis.xack(self.stream_in, self.consumer_group, msg_id)
                            except Exception as e:
                                logger.error("Error processing tagged event",
                                           msg_id=msg_id, error=str(e))
                else:
                    await asyncio.sleep(0.1)
                    continue
                
                            
            except Exception as e:
                logger.error("Error in CrawlTriggerTask loop", error=str(e))
                await asyncio.sleep(5)
    
    async def _process_tagged_event(self, msg_id: str, fields: Dict[str, Any]):
        """Проверка триггеров и публикация в posts.crawl."""
        logger.debug("Processing tagged event", msg_id=msg_id, fields=str(fields)[:512])
        payload = fields.get("data") or fields.get("payload") or fields
        if isinstance(payload, str):
            payload = json.loads(payload)
        
        post_id = payload.get('post_id')
        tags = payload.get('tags', [])
        # Парсим tags если это строка JSON
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tags JSON", tags=tags)
                tags = []
        logger.debug("Extracted payload", post_id=post_id, tags=tags)
        urls = payload.get('urls', [])
        # Парсим urls если это строка JSON
        if isinstance(urls, str):
            try:
                urls = json.loads(urls)
            except json.JSONDecodeError:
                logger.warning("Failed to parse urls JSON", urls=urls)
                urls = []
        trace_id = payload.get('trace_id', msg_id)
        
        # Context7: Проверка trigger - обрабатываем все события, включая vision_retag
        trigger = payload.get('trigger')
        is_retagging = (trigger == "vision_retag")
        
        # Проверка триггерных тегов
        has_trigger = any(tag in self.trigger_tags for tag in tags)
        
        # Context7: Логируем если это retagging для observability
        if is_retagging:
            logger.debug(
                "Processing retagging event in CrawlTriggerTask",
                post_id=post_id,
                has_trigger_tags=has_trigger,
                trace_id=trace_id
            )
        
        if has_trigger:
            # Если нет URLs, генерируем заглушку для тестирования
            if not urls:
                urls = [f"https://example.com/post/{post_id}"]
                crawl_trigger_policy_skips_total.labels(reason='no_urls').inc()
            
            # Публикация в posts.crawl
            crawl_request = {
                'post_id': post_id,
                'urls': urls,
                'tags': tags,
                'trigger_reason': 'trigger_tag',
                'trace_id': trace_id,
                'metadata': payload.get('metadata', {})
            }
            
            await self.redis.xadd(
                self.stream_out,
                {'data': json.dumps(crawl_request)}
            )
            
            crawl_triggers_total.labels(reason='triggered').inc()
            logger.info("Crawl triggered",
                       post_id=post_id,
                       tags=tags,
                       urls_count=len(urls),
                       trace_id=trace_id)
        else:
            if not has_trigger:
                crawl_trigger_policy_skips_total.labels(reason='no_trigger_tags').inc()
                logger.debug("No trigger tags", post_id=post_id, tags=tags)
            if not urls:
                crawl_trigger_policy_skips_total.labels(reason='no_urls').inc()
                logger.debug("No URLs", post_id=post_id)
