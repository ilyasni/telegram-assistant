"""
Crawl4AI Enrichment Service
[C7-ID: CRAWL4AI-SERVICE-002]
"""
import asyncio
import os
import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone
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
        
        # Context7: Инициализация S3StorageService для долговечного хранения HTML/MD
        s3_service = None
        try:
            # Импорт S3StorageService (cross-service, временное исключение)
            import sys
            parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from api.services.s3_storage import S3StorageService
            
            # Инициализация S3 сервиса из переменных окружения
            s3_endpoint = os.getenv("S3_ENDPOINT_URL")
            s3_bucket = os.getenv("S3_BUCKET_NAME")
            s3_access_key = os.getenv("S3_ACCESS_KEY_ID")
            s3_secret_key = os.getenv("S3_SECRET_ACCESS_KEY")
            
            if s3_endpoint and s3_bucket and s3_access_key and s3_secret_key:
                s3_service = S3StorageService(
                    endpoint_url=s3_endpoint,
                    bucket_name=s3_bucket,
                    access_key_id=s3_access_key,
                    secret_access_key=s3_secret_key
                )
                logger.info("S3StorageService initialized for Crawl4ai", bucket=s3_bucket)
            else:
                logger.warning("S3 credentials not provided, HTML will be cached only in Redis")
        except Exception as e:
            logger.warning("Failed to initialize S3StorageService for Crawl4ai", error=str(e))
        
        # EnrichmentEngine с S3 интеграцией
        self.engine = EnrichmentEngine(
            redis_url=self.redis_url,
            max_concurrent_crawls=int(os.getenv("MAX_CONCURRENT_CRAWLS", "3")),
            rate_limit_per_host=10,
            cache_ttl=3600,
            s3_service=s3_service
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
            tenant_id = payload.get('tenant_id')  # Context7: Извлекаем tenant_id для проверки
            
            if not urls:
                logger.warning("No URLs in crawl request", post_id=post_id)
                crawl_requests_total.labels(status='skipped', reason='no_urls').inc()
                return
            
            # Context7: Проверка наличия tenant_id в payload
            if not tenant_id:
                logger.warning(
                    "tenant_id not found in crawl request payload",
                    post_id=post_id,
                    payload_keys=list(payload.keys()) if isinstance(payload, dict) else []
                )
                # Не прерываем выполнение - fallback будет использован в EnrichmentEngine
            
            # Crawling через EnrichmentEngine
            success, enrichment_data, reason = await self.engine.enrich_post(
                payload,
                self.config
            )
            
            if not success:
                logger.info("Crawl skipped", post_id=post_id, reason=reason)
                crawl_requests_total.labels(status='skipped', reason=reason or 'unknown').inc()
                return
            
            # Context7: Модульное сохранение в post_enrichment через EnrichmentRepository
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Агрегируем все данные от всех URL в единую структуру для kind='crawl'
            crawl_md_parts = []
            ocr_texts = []
            vision_labels_list = []
            urls_data = []
            s3_keys = {}  # Context7: Агрегированные s3_keys для всех URL
            checksums = {}  # Context7: Агрегированные checksums для целостности
            
            for url, url_data in enrichment_data.items():
                if url_data.get('markdown'):
                    crawl_md_parts.append(url_data['markdown'])
                if url_data.get('ocr_text'):
                    ocr_texts.append({
                        'url': url,
                        'text': url_data['ocr_text']
                    })
                if url_data.get('vision_labels'):
                    vision_labels_list.append({
                        'url': url,
                        'labels': url_data['vision_labels']
                    })
                
                # Context7: Агрегируем s3_keys и checksums для каждого URL
                url_s3_keys = url_data.get('s3_keys', {})
                if url_s3_keys:
                    s3_keys[url] = {
                        'html': url_s3_keys.get('html'),
                        'md': url_s3_keys.get('md')
                    }
                
                url_checksums = url_data.get('checksums', {})
                if url_checksums:
                    checksums[url] = {
                        'html_md5': url_checksums.get('html_md5'),
                        'md_md5': url_checksums.get('md_md5')
                    }
                
                urls_data.append({
                    'url': url,
                    'word_count': url_data.get('word_count'),
                    'status': url_data.get('status'),
                    'url_hash': url_data.get('url_hash'),
                    'content_sha256': url_data.get('content_sha256')
                })
            
            # Сохраняем всё в едином обогащении kind='crawl'
            if crawl_md_parts or ocr_texts or vision_labels_list or s3_keys:
                await self._save_enrichment(
                    post_id=post_id,
                    crawl_md='\n\n---\n\n'.join(crawl_md_parts) if crawl_md_parts else None,
                    ocr_texts=ocr_texts if ocr_texts else None,
                    vision_labels=vision_labels_list if vision_labels_list else None,
                    enrichment_latency_ms=latency_ms,
                    metadata={
                        'urls': urls_data,
                        'source': 'crawl4ai',
                        'trace_id': trace_id,
                        's3_keys': s3_keys,  # Context7: s3_keys для всех URL
                        'checksums': checksums  # Context7: checksums для целостности
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
        crawl_md: Optional[str] = None,
        ocr_texts: Optional[list] = None,
        vision_labels: Optional[list] = None,
        enrichment_latency_ms: int = 0,
        metadata: Dict[str, Any] = None
    ):
        """
        Context7: Сохранение crawl обогащения через EnrichmentRepository.
        Использует единую модель с kind='crawl' и структурированное JSONB поле data.
        """
        # Context7: Импорт shared репозитория
        import sys
        import os
        shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared', 'python'))
        if shared_path not in sys.path:
            sys.path.insert(0, shared_path)
        
        from shared.repositories.enrichment_repository import EnrichmentRepository
        
        # Структурируем данные для JSONB поля data с включением s3_keys и checksums
        urls_metadata = metadata.get('urls', []) if metadata else []
        
        # Извлекаем s3_keys и checksums из metadata (передаются из _process_crawl_request)
        s3_keys = metadata.get('s3_keys', {}) if metadata else {}
        checksums = metadata.get('checksums', {}) if metadata else {}
        
        # Формируем md_excerpt (первые ~1-2k символов) для быстрого доступа
        md_excerpt = None
        if crawl_md and len(crawl_md) > 0:
            md_excerpt = crawl_md[:2000] if len(crawl_md) > 2000 else crawl_md
        
        crawl_data = {
            'url': urls_metadata[0].get('url') if urls_metadata else None,
            'url_hash': urls_metadata[0].get('url_hash') if urls_metadata else None,
            'content_sha256': urls_metadata[0].get('content_sha256') if urls_metadata else None,
            's3_keys': s3_keys,  # {url: {'html': '...', 'md': '...'}}
            'md_excerpt': md_excerpt,  # Первые ~1-2k символов для быстрого доступа
            'markdown': crawl_md,  # Полный markdown (или ссылка на S3 если очень большой)
            'urls': urls_metadata,
            'word_count': sum(url.get('word_count', 0) or 0 for url in urls_metadata),
            'ocr_texts': ocr_texts or [],
            'vision_labels': vision_labels or [],
            'latency_ms': enrichment_latency_ms,
            'crawled_at': datetime.now(timezone.utc).isoformat(),
            'source': metadata.get('source', 'crawl4ai') if metadata else 'crawl4ai',
            'trace_id': metadata.get('trace_id') if metadata else None,
            'checksums': checksums,  # {url: {'html_md5': '...', 'md_md5': '...'}}
            'meta': {
                'title': None,  # TODO: извлечь из enrichment_data
                'lang': None,  # TODO: извлечь из enrichment_data
                'word_count': sum(url.get('word_count', 0) or 0 for url in urls_metadata)
            },
            'metadata': {k: v for k, v in (metadata or {}).items() if k not in ['urls', 'source', 'trace_id', 's3_keys', 'checksums']}
        }
        
        # Используем EnrichmentRepository (принимает asyncpg.Pool)
        repo = EnrichmentRepository(self.db_pool)
        await repo.upsert_enrichment(
            post_id=post_id,
            kind='crawl',
            provider='crawl4ai',
            data=crawl_data,
            params_hash=None,  # Crawl не версионируется по параметрам модели
            status='ok',
            error=None,
            trace_id=metadata.get('trace_id') if metadata else None
        )
        
        # Context7: Также сохраняем в legacy поля для обратной совместимости
        # (будет удалено после полной миграции)
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE post_enrichment
                SET crawl_md = $1,
                    ocr_text = $2,
                    vision_labels = $3::jsonb,
                    enrichment_provider = 'crawl4ai',
                    enrichment_latency_ms = $4,
                    metadata = COALESCE(metadata, '{}'::jsonb) || $5::jsonb
                WHERE post_id = $6 AND kind = 'crawl'
                """,
                crawl_md,
                (ocr_texts[0]['text'] if ocr_texts and len(ocr_texts) > 0 else None) if ocr_texts else None,
                json.dumps([vl['labels'] for vl in vision_labels] if vision_labels else []),
                enrichment_latency_ms,
                json.dumps(metadata or {}),
                post_id
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