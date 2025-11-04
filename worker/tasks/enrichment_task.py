"""
Enrichment Task - Consumer для posts.tagged → enrichment через crawl4ai
Поддерживает политики enrichment, лимиты, кеширование и метрики
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse

import yaml
from crawl4ai import AsyncWebCrawler
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from sqlalchemy import text

from event_bus import EventConsumer, ConsumerConfig, PostEnrichedEvent, get_event_publisher
from metrics import (
    enrichment_requests_total,
    enrichment_latency_seconds,
    enrichment_skipped_total,
    posts_processed_total
)

# ============================================================================
# ID SANITIZER (DEV FIX)
# ============================================================================

def normalize_post_id(raw: str) -> uuid.UUID:
    """Детерминированное приведение post_id к UUID для DEV режима."""
    try:
        return uuid.UUID(raw)
    except Exception:
        # Детерминированное приведение: UUID5 в namespace проекта
        NAMESPACE = uuid.UUID("11111111-1111-1111-1111-111111111111")  # зафиксировано в .env/константах
        return uuid.uuid5(NAMESPACE, raw)

def sanitize_event_post_id(event: Dict[str, Any]) -> Dict[str, Any]:
    """Санитизация post_id в событии для DEV режима."""
    if os.getenv("FEATURE_ALLOW_NON_UUID_IDS", "false").lower() == "true":
        raw_id = event.get("post_id")
        if raw_id:
            post_uuid = normalize_post_id(raw_id)
            event["post_id"] = str(post_uuid)
            logger.debug(f"Sanitized post_id: {raw_id} -> {post_uuid}")
    return event

logger = logging.getLogger(__name__)

# ============================================================================
# ENRICHMENT WORKER
# ============================================================================

class EnrichmentWorker:
    """Worker для обогащения постов через crawl4ai."""
    
    def __init__(
        self,
        consumer: EventConsumer,
        db_session,
        redis_client,
        enrichment_config: Dict[str, Any],
        ai_adapter=None,
        publisher=None
    ):
        self.consumer = consumer
        self.db_session = db_session
        self.redis_client = redis_client
        self.config = enrichment_config
        self.ai_adapter = ai_adapter
        self.embedding_service = None
        self.publisher = publisher or get_event_publisher()  # Получаем publisher из event_bus
        
        # Crawl4AI клиент
        self.crawler = AsyncWebCrawler(
            headless=True,
            browser_type="chromium",
            verbose=True
        )
        
        # Статистика
        self.stats = {
            'posts_processed': 0,
            'posts_skipped': 0,
            'urls_crawled': 0,
            'urls_failed': 0,
            'cache_hits': 0,
            'errors': 0
        }
        
        logger.info("Enrichment worker initialized")
    
    async def _initialize(self):
        """Инициализация компонентов task."""
        # Создание consumer group
        await self.consumer._ensure_consumer_group("posts.tagged")
        
        # Инициализация EmbeddingService (если ai_adapter доступен)
        if self.ai_adapter:
            from ai_providers.embedding_service import create_embedding_service
            self.embedding_service = await create_embedding_service(self.ai_adapter)
            logger.info("embedding_service_initialized", extra={"dimension": self.embedding_service.get_dimension()})
        
        logger.info("EnrichmentWorker initialized successfully")

    async def start_processing(self):
        """Запуск обработки событий posts.tagged с неблокирующей обработкой."""
        logger.info("Starting enrichment worker")
        
        # Инициализация
        await self._initialize()
        
        # Context7: Используем consume_forever для бесконечного цикла
        # consume_forever принимает только stream_name и handler_func
        await self.consumer.consume_forever(
            stream_name="posts.tagged",
            handler_func=self._handle_post_tagged
        )
    
    def _sanitize_for_publish(self, obj):
        """Санитайз helper для сериализации."""
        # [C7-ID: ENRICH-DBG-002]
        import uuid, datetime
        if obj is None:
            return None  # на верхнем уровне удалим
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, (uuid.UUID,)):
            return str(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)):
            # всегда ISO-строка
            return obj.isoformat()
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                sv = self._sanitize_for_publish(v)
                # ВАЖНО: None из payload лучше выбрасывать вовсе для Redis/JSON
                if sv is not None:
                    out[k] = sv
            return out
        if isinstance(obj, (list, tuple, set)):
            return [x for x in (self._sanitize_for_publish(v) for v in obj) if x is not None]
        # на всякий случай — строки
        return str(obj)

    def to_json_bytes(self, payload: dict | list) -> bytes:
        """Сериализация перед publish."""
        # [C7-ID: ENRICH-PUB-001] Сериализация перед publish
        import json, uuid, datetime
        if not isinstance(payload, (dict, list)):
            raise TypeError("payload must be dict or list")

        def _norm(o):
            if o is None:
                return None  # отфильтруем на уровне словаря/списка
            if isinstance(o, (str, int, float, bool)):
                return o
            if isinstance(o, uuid.UUID):
                return str(o)
            if isinstance(o, (datetime.datetime, datetime.date)):
                return o.isoformat()
            if isinstance(o, dict):
                out = {}
                for k, v in o.items():
                    nv = _norm(v)
                    if nv is not None:
                        out[k] = nv
                return out
            if isinstance(o, (list, tuple, set)):
                out = [_norm(v) for v in o]
                return [x for x in out if x is not None]
            return str(o)

        normed = _norm(payload)
        if not normed:  # пустой после очистки
            raise ValueError("normalized payload is empty")
        s = json.dumps(normed, ensure_ascii=False)
        return s.encode("utf-8")

    async def _handle_post_tagged(self, event_data: Dict[str, Any]):
        """Обработчик события post.tagged."""
        try:
            # [C7-ID: ENRICH-DEV-FIX-001] Санитизация post_id для DEV режима
            event_data = sanitize_event_post_id(event_data)
            
            # [C7-ID: ENRICH-TRACE-HANDLER-001] Логирование в _handle_post_tagged (самый верх)
            logger.info("enrich_handler_enter", extra={
                "post_id": str(event_data.get("post_id")),
                "keys": list(event_data.keys())
            })
            
            # [C7-ID: ENRICH-DBG-010] Ступенчатые логи
            logger.info("enrich_msg_received", extra={
                "event_type": str(type(event_data)),
                "keys": (list(event_data.keys()) if isinstance(event_data, dict) else None)
            })

            # Парсинг события из Redis Streams
            if 'post_id' in event_data:
                # Прямой формат
                post_id = event_data['post_id']
                tags = event_data.get('tags', [])
                logger.info("enrich_event_parsed", extra={
                    "keys": list(event_data.keys()),
                    "sample": str(event_data)[:200]
                })
            elif 'payload' in event_data:
                # Формат из Redis Streams - используем payload
                payload = event_data['payload']
                post_id = payload.get('post_id')
                tags = payload.get('tags', [])
                # Обновляем event_data с данными из payload
                event_data.update(payload)
                logger.info("enrich_event_parsed_from_payload", extra={
                    "keys": list(event_data.keys()),
                    "post_id": post_id,
                    "tags_count": len(tags)
                })
            elif 'data' in event_data:
                # Формат из Redis Streams - парсим JSON из data
                try:
                    import json
                    parsed_data = json.loads(event_data['data'])
                    post_id = parsed_data.get('post_id')
                    tags = parsed_data.get('tags', [])
                    # Обновляем event_data с распарсенными данными
                    event_data.update(parsed_data)
                    logger.info("enrich_event_parsed_from_stream", extra={
                        "keys": list(event_data.keys()),
                        "post_id": post_id,
                        "tags_count": len(tags)
                    })
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"Failed to parse stream data: {e}")
                    return
            else:
                # Формат из Redis Streams - нужно найти payload
                logger.error(f"enrichment_event_missing_post_id: keys={list(event_data.keys())}")
                return
            
            logger.debug(f"Processing post {post_id} for enrichment")
            
            # МЕТРИКА: попытка обработки
            posts_processed_total.labels(stage='enrichment', success='attempt').inc()
            
            # Проверка триггеров enrichment
            should_enrich, skip_reason = await self._should_enrich_post(post_id, tags)
            
            if not should_enrich:
                logger.info(f"Post {post_id} skipped from enrichment: {skip_reason}")
                await self._publish_skipped_enrichment(event_data, skip_reason)
                self.stats['posts_skipped'] += 1
                enrichment_skipped_total.labels(reason=skip_reason).inc()
                # МЕТРИКА: пропуск обогащения
                posts_processed_total.labels(stage='enrichment', success='skip').inc()
                return
            
            # [C7-ID: ENRICH-URLS-001] Источники URLs: event.urls → regex из text → []
            urls = list(event_data.get('urls', []) or [])
            if not urls:
                # Парсинг URLs из текста
                text = event_data.get('text', '') or ''
                urls = self._extract_urls_from_text(text)
            
            logger.info(f"Post {post_id} URLs: {len(urls)} from event/parsing")
            
            # [C7-ID: ENRICH-DEV-FIX-002] Санитизация post_id перед запросом к БД
            sanitized_post_id = str(normalize_post_id(post_id)) if os.getenv("FEATURE_ALLOW_NON_UUID_IDS", "false").lower() == "true" else post_id
            
            logger.info("enrich_before_db_query", extra={"post_id": post_id, "sanitized_post_id": sanitized_post_id})
            
            # Получение контекста из БД (без URLs)
            post_context = await self._get_post_with_urls(sanitized_post_id)
            
            logger.info("enrich_after_db_query", extra={"post_id": post_id, "post_context": bool(post_context)})
            
            # Context7: Проверка существования поста перед обогащением
            if not post_context:
                logger.warning("Post not found in DB, skipping enrichment", extra={"post_id": post_id, "trace_id": event_data.get('trace_id')})
                posts_processed_total.labels(stage='enrichment', success='skip').inc()
                return
            
            # [C7-ID: ENRICH-DBG-011] Построение enriched_event с логами «между шагами»
            logger.info("enrich_build_start", extra={"post_id": post_id})
            
            # Формирование минимального enrichment_data
            enrichment_data = {
                "kind": "enrichment",
                "source": "enrichment_task", 
                "version": "v1",
                "tags": tags,          # используем теги из входящего события
                "entities": [],
                "embedding": None,     # индексатор дожмет (см. флаг INDEXER_EMBED_IF_MISSING)
                "urls": urls,
            }
            
            # Построение enriched_event
            enriched_event = {
                "idempotency_key": event_data.get('idempotency_key', f"{post_id}:enriched:v1"),
                "post_id": post_id,               # UUID-строка!
                "channel_id": post_context.get("channel_id", "") if post_context else "",
                "text": post_context.get("content", "") if post_context else "",
                "telegram_post_url": post_context.get("telegram_post_url", "") if post_context else "",
                "posted_at": post_context.get("posted_at", datetime.now(timezone.utc)) if post_context else datetime.now(timezone.utc),
                "enrichment_data": enrichment_data,
            }
            
            logger.info("enrich_build_done", extra={
                "out_keys": list(enriched_event.keys()),
                "enrich_keys": list(enrichment_data.keys())
            })
            
            # [C7-ID: ENRICH-DBG-004] Dry-run сериализации (ловим «Invalid input …» до публикации)
            try:
                body = self.to_json_bytes(enriched_event)  # dict → bytes
                logger.info("enrich_serialize_ok", extra={"post_id": post_id, "bytes": len(body)})
            except Exception as e:
                logger.exception("enrich_serialize_fail", extra={
                    "error": str(e),
                    "post_id": post_id
                })
                # DLQ, чтобы не стопорить CG:
                await self.publisher.to_dlq("posts.tagged", event_data, reason="serialize_error", details=str(e))
                # МЕТРИКА: ошибка сериализации
                posts_processed_total.labels(stage='enrichment', success='error').inc()
                return
            
            # Context7: Проверка trigger для observability
            trigger = event_data.get('trigger')
            is_retagging = (trigger == "vision_retag")
            if is_retagging:
                logger.debug(
                    "Processing retagging event in EnrichmentTask",
                    post_id=post_id,
                    trace_id=event_data.get('trace_id')
                )
            
            # [C7-ID: ENRICH-DEV-FIX-003] Санитизация post_id перед сохранением в БД
            sanitized_post_id_for_save = str(normalize_post_id(post_id)) if os.getenv("FEATURE_ALLOW_NON_UUID_IDS", "false").lower() == "true" else post_id
            
            # Сохранение enrichment данных
            await self._save_enrichment_data(sanitized_post_id_for_save, enrichment_data)
            
            # [C7-ID: ENRICH-PUB-002] Публикация enriched
            try:
                logger.info("enrich_publish_start", extra={"post_id": post_id, "size": len(body)})
                # [C7-ID: ENRICH-FORMAT-FIX-002] Публикуем enriched_event напрямую для indexing_task
                await self.publisher.publish_event("posts.enriched", enriched_event)
                logger.info("enrich_publish_ok", extra={"post_id": post_id})
            except Exception as e:
                logger.exception("enrich_publish_fail", extra={
                    "error": str(e),
                    "post_id": post_id
                })  # exception → стектрейс
                # DLQ вместо break-падения:
                await self.publisher.to_dlq("posts.tagged", {"data": body if 'body' in locals() else b""}, reason="publish_fail", details=str(e))
                # МЕТРИКА: ошибка публикации
                posts_processed_total.labels(stage='enrichment', success='error').inc()
                return
            
            self.stats['posts_processed'] += 1
            
            # Context7: Метрики для успешного enrichment
            enrichment_requests_total.labels(
                provider='enrichment_task',
                operation='enrich',
                success=True
            ).inc()
            
            # МЕТРИКА: успешная обработка
            posts_processed_total.labels(stage='enrichment', success='true').inc()
            
            logger.info(f"Post {post_id} enriched successfully with {len(urls)} URLs")
                
        except Exception as e:
            import traceback
            logger.error("Error processing post", extra={"post_id": event_data.get('post_id', 'unknown'), "error": str(e)})
            logger.error("Traceback", extra={"traceback": traceback.format_exc()})
            
            # Context7: Метрики для ошибок enrichment
            enrichment_requests_total.labels(
                provider='enrichment_task',
                operation='enrich',
                success=False
            ).inc()
            # МЕТРИКА: ошибка обработки
            posts_processed_total.labels(stage='enrichment', success='error').inc()
            self.stats['errors'] += 1
    
    async def _should_enrich_post(self, post_id: str, tags: List[Dict[str, Any]]) -> tuple[bool, str]:
        """Проверка, нужно ли обогащать пост."""
        # Проверка включения enrichment
        if not self.config.get('enrichment', {}).get('enabled', True):
            return False, 'enrichment_disabled'
        
        # [C7-ID: ENRICH-LIMITS-002] Feature flags для обхода лимитов
        skip_limits_env = os.getenv("ENRICHMENT_SKIP_LIMITS", "false")
        skip_limits = skip_limits_env.lower() == "true"
        logger.info("enrich_limits_check", extra={"env": skip_limits_env, "skip": skip_limits, "post_id": post_id})
        
        if skip_limits:
            logger.warning("enrich_limits_skipped: feature_flag enabled", extra={"post_id": post_id})
            # Пропускаем проверку лимитов, возвращаем True для продолжения обогащения
            return True, None
        else:
            # Проверка лимитов пользователя
            if not await self._check_user_limits(post_id):
                logger.warning("enrich_limits_exceeded", extra={"post_id": post_id})
                return False, 'user_limits_exceeded'
        
        # Проверка триггерных тегов
        trigger_tags = self.config.get('crawl4ai', {}).get('trigger_tags', [])
        if trigger_tags:
            post_tags = [tag.get('name', '').lower() for tag in tags]
            if not any(tag in post_tags for tag in trigger_tags):
                return False, 'no_trigger_tags'
        
        # Проверка минимального количества слов
        min_word_count = self.config.get('crawl4ai', {}).get('min_word_count', 500)
        post = await self._get_post_content(post_id)
        if post and len(post.get('content', '').split()) < min_word_count:
            return False, 'below_word_count'
        
        return True, None
    
    def _should_skip_limits(self, event) -> bool:
        """Проверка feature flags для обхода лимитов."""
        # [C7-ID: ENRICH-LIMITS-001] Feature flags
        skip_limits = os.getenv("ENRICHMENT_SKIP_LIMITS", "false").lower() == "true"
        if skip_limits:
            logger.warning("enrich_limits_skipped", extra={"reason": "feature_flag"})
            return True
        
        # Мягкое включение через проценты (canary/sampling)
        sampling = int(os.getenv("ENRICHMENT_LIMITS_SAMPLING", "100"))
        if sampling < 100:
            # N% событий проходят мимо лимитов
            if (hash(event.idempotency_key) % 100) >= sampling:
                logger.info("enrich_limits_bypassed_sampling", extra={"sampling": sampling})
                return True
        
        return False

    def _extract_urls_from_text(self, text: str) -> list[str]:
        """Извлечение URLs из текста через regex."""
        # [C7-ID: ENRICH-URLS-001] Парсинг URLs из текста
        import re
        if not text:
            return []
        pattern = r"https?://[^\s)]+"
        return re.findall(pattern, text)

    def _minimal_enrichment(self, event) -> dict:
        """Минимальный payload для разблокировки пайплайна."""
        # [C7-ID: ENRICH-LIMITS-003]
        return {
            "idempotency_key": event.idempotency_key,
            "post_id": event.post_id,
            "channel_id": event.channel_id,
            "telegram_post_url": event.telegram_post_url,
            "text": event.text or "",
            "posted_at": event.posted_at,
            "kind": "enrichment",
            "tags": [],
            "entities": [],
            "embedding": None,
            "source": "enrichment_task",
            "version": "v1",
        }

    async def _check_user_limits(self, post_id: str) -> bool:
        """Проверка лимитов пользователя."""
        try:
            # [C7-ID: WORKER-ENRICHMENT-001] SQLAlchemy 2.x совместимость + диагностика
            # Context7: tenant_id получаем из channels через JOIN, так как posts больше не содержит tenant_id
            sql = """
                SELECT c.tenant_id 
                FROM posts p
                JOIN channels c ON p.channel_id = c.id
                WHERE p.id = :post_id 
                LIMIT 1
            """
            t0 = time.perf_counter()
            result = await self.db_session.execute(
                text(sql),
                {"post_id": post_id}
            )
            row = result.fetchone()
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("enrich_sql_ok", extra={"ms": elapsed, "row_found": bool(row)})
            if not row:
                return False
            
            tenant_id = row.tenant_id
            
            # Проверка дневного лимита enrichment
            max_per_day = self.config.get('limits', {}).get('per_user', {}).get('max_enrichment_per_day', 100)
            
            today = datetime.now(timezone.utc).date()
            # Context7: tenant_id получаем из channels через JOIN
            result = await self.db_session.execute(
                text("""
                SELECT COUNT(*) as count
                FROM post_enrichment pe
                JOIN posts p ON p.id = pe.post_id
                JOIN channels c ON p.channel_id = c.id
                WHERE c.tenant_id = :tenant_id
                AND DATE(pe.enriched_at) = :today
                AND pe.crawl_md IS NOT NULL
                """),
                {"tenant_id": tenant_id, "today": today}
            )
            
            count = result.fetchone().count
            return count < max_per_day
            
        except Exception as e:
            logger.error(f"Failed to check user limits: {e}")
            return False
    
    async def _get_post_with_urls(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Получение поста с URL."""
        # [C7-ID: ENRICH-URLS-001] Убираем несуществующие поля, берем только реальные
        logger.info("enrich_db_query", extra={"post_id": post_id})
        
        result = await self.db_session.execute(
            text("""
            SELECT p.id, p.content, p.channel_id, p.telegram_post_url, p.posted_at, p.telegram_message_id
            FROM posts p
            WHERE p.id = :post_id
            """),
            {"post_id": post_id}
        )
        
        row = result.fetchone()
        if row:
            post_data = dict(row._mapping)
            logger.info("enrich_db_result", extra={
                "post_id": post_id,
                "has_content": bool(post_data.get('content')),
                "content_length": len(post_data.get('content', '')),
                "content_preview": post_data.get('content', '')[:100]
            })
            
            # Парсинг JSON URLs
            if post_data.get('urls'):
                try:
                    post_data['urls'] = json.loads(post_data['urls']) if isinstance(post_data['urls'], str) else post_data['urls']
                except json.JSONDecodeError:
                    post_data['urls'] = []
            return post_data
        
        logger.warning("enrich_db_not_found", extra={"post_id": post_id})
        return None
    
    async def _get_post_content(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Получение контента поста."""
        sql = "SELECT content FROM posts WHERE id = :post_id LIMIT 1"
        t0 = time.perf_counter()
        try:
            result = await self.db_session.execute(
                text(sql),
                {"post_id": post_id}
            )
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("enrich_sql_ok", extra={"ms": elapsed, "row_found": bool(result.fetchone())})
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.error("enrich_sql_error", extra={"err": str(e), "ms": elapsed, "sql_preview": sql.strip()[:120]})
            return None
        
        row = result.fetchone()
        return dict(row._mapping) if row else None
    
    async def _enrich_post_urls(self, post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Обогащение поста через crawl4ai."""
        urls = post.get('urls', [])
        if not urls:
            return None
        
        enrichment_data = {
            'source_urls': urls,
            'crawl_results': [],
            'total_word_count': 0,
            'crawled_at': datetime.now(timezone.utc).isoformat()
        }
        
        for url in urls:
            try:
                # Проверка кеша
                cached_result = await self._get_cached_crawl(url)
                if cached_result:
                    enrichment_data['crawl_results'].append(cached_result)
                    self.stats['cache_hits'] += 1
                    continue
                
                # Crawl URL
                crawl_result = await self._crawl_url(url)
                if crawl_result:
                    enrichment_data['crawl_results'].append(crawl_result)
                    enrichment_data['total_word_count'] += crawl_result.get('word_count', 0)
                    
                    # Кеширование результата
                    await self._cache_crawl_result(url, crawl_result)
                    
                    self.stats['urls_crawled'] += 1
                else:
                    self.stats['urls_failed'] += 1
                    
            except Exception as e:
                logger.error(f"Failed to crawl URL {url}: {e}")
                self.stats['urls_failed'] += 1
        
        return enrichment_data if enrichment_data['crawl_results'] else None
    
    async def _get_cached_crawl(self, url: str) -> Optional[Dict[str, Any]]:
        """Получение кешированного результата crawl."""
        if not self.config.get('crawl4ai', {}).get('caching', {}).get('enabled', True):
            return None
        
        cache_key = f"crawl:{hashlib.sha256(url.encode()).hexdigest()}"
        cached_data = await self.redis_client.get(cache_key)
        
        if cached_data:
            try:
                return json.loads(cached_data)
            except json.JSONDecodeError:
                return None
        
        return None
    
    async def _cache_crawl_result(self, url: str, result: Dict[str, Any]):
        """Кеширование результата crawl."""
        if not self.config.get('crawl4ai', {}).get('caching', {}).get('enabled', True):
            return
        
        cache_key = f"crawl:{hashlib.sha256(url.encode()).hexdigest()}"
        ttl_days = self.config.get('crawl4ai', {}).get('caching', {}).get('ttl_days', 7)
        ttl_seconds = ttl_days * 24 * 3600
        
        await self.redis_client.setex(
            cache_key,
            ttl_seconds,
            json.dumps(result)
        )
    
    async def _crawl_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Crawl URL через crawl4ai."""
        start_time = time.time()
        
        try:
            # Проверка ограничений домена
            if not await self._is_url_allowed(url):
                logger.info(f"URL {url} is not allowed by domain restrictions")
                return None
            
            # Настройки crawl
            timeout = self.config.get('crawl4ai', {}).get('timeout_seconds', 30)
            
            # Crawl с извлечением текста
            result = await self.crawler.arun(
                url=url,
                extraction_strategy=LLMExtractionStrategy(
                    provider="openai",
                    api_token="dummy",  # Не используется для простого извлечения
                    instruction="Extract the main content and return as markdown"
                ),
                timeout=timeout,
                wait_for="networkidle"
            )
            
            if result.success and result.extracted_content:
                # Подсчёт слов
                word_count = len(result.extracted_content.split())
                
                crawl_result = {
                    'url': url,
                    'title': result.metadata.get('title', ''),
                    'content': result.extracted_content,
                    'markdown': result.markdown,
                    'word_count': word_count,
                    'crawled_at': datetime.now(timezone.utc).isoformat(),
                    'latency_ms': int((time.time() - start_time) * 1000)
                }
                
                # Метрики
                enrichment_latency_seconds.labels(
                    provider='crawl4ai',
                    operation='crawl'
                ).observe(time.time() - start_time)
                
                enrichment_requests_total.labels(
                    provider='crawl4ai',
                    operation='crawl',
                    success=True
                ).inc()
                
                return crawl_result
            else:
                logger.warning(f"Crawl failed for {url}: {result.error_message}")
                return None
                
        except Exception as e:
            logger.error(f"Crawl error for {url}: {e}")
            
            # Метрики ошибки
            enrichment_requests_total.labels(
                provider='crawl4ai',
                operation='crawl',
                success=False
            ).inc()
            
            return None
    
    async def _is_url_allowed(self, url: str) -> bool:
        """Проверка разрешённости URL."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Проверка заблокированных доменов
            blocked_domains = self.config.get('crawl4ai', {}).get('domain_restrictions', {}).get('blocked_domains', [])
            if any(blocked in domain for blocked in blocked_domains):
                return False
            
            # Проверка разрешённых доменов (если указаны)
            allowed_domains = self.config.get('crawl4ai', {}).get('domain_restrictions', {}).get('allowed_domains', [])
            if allowed_domains and not any(allowed in domain for allowed in allowed_domains):
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"URL validation error: {e}")
            return False
    
    async def _save_enrichment_data(self, post_id: str, enrichment_data: Dict[str, Any]):
        """
        Context7: Сохранение данных enrichment через EnrichmentRepository.
        
        Определяет kind по наличию crawl_results:
        - kind='crawl' если есть crawl_results (результаты crawl4ai)
        - kind='general' если нет crawl_results (простое обогащение через теги/URLs)
        """
        # Context7: Импорт shared репозитория
        import sys
        import os
        shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
        if shared_path not in sys.path:
            sys.path.insert(0, shared_path)
        
        from shared.repositories.enrichment_repository import EnrichmentRepository
        
        try:
            logger.info("enrichment_save_start", extra={"post_id": post_id, "enrichment_keys": list(enrichment_data.keys())})
            
            # Подготовка данных для сохранения
            crawl_results = enrichment_data.get('crawl_results', [])
            
            # Context7: Определяем kind и provider по наличию crawl_results
            if crawl_results:
                # Crawl данные - используем kind='crawl'
                kind = 'crawl'
                provider = 'crawl4ai'
                crawl_md = "\n\n---\n\n".join([
                    result.get('markdown', '') 
                    for result in crawl_results
                ])
            else:
                # Общее обогащение - используем kind='general'
                kind = 'general'
                provider = 'enrichment_task'
                crawl_md = ""
            
            logger.info(f"enrichment_crawl_md: length={len(crawl_md)}")
            
            # Сериализация enrichment_data в JSON (Context7 best practice)
            import json
            
            def clean_none_values(obj):
                """Рекурсивно очищает None значения из словаря."""
                if isinstance(obj, dict):
                    return {k: clean_none_values(v) for k, v in obj.items() if v is not None}
                elif isinstance(obj, list):
                    return [clean_none_values(item) for item in obj if item is not None]
                else:
                    return obj
            
            # Очищаем None значения из enrichment_data
            cleaned_data = clean_none_values(enrichment_data)
            logger.info(f"enrichment_data_cleaned: original_keys={list(enrichment_data.keys())}, cleaned_keys={list(cleaned_data.keys())}")
            
            # Структурируем данные для JSONB поля data
            enrichment_payload = {
                'enrichment_data': cleaned_data,
                'urls': [r.get('url') for r in crawl_results if r.get('url')] if crawl_results else enrichment_data.get('urls', []),
                'word_count': sum(r.get('word_count', 0) or 0 for r in crawl_results) if crawl_results else enrichment_data.get('word_count', 0),
                'latency_ms': enrichment_data.get('latency_ms', 0),
                'created_at': datetime.now(timezone.utc).isoformat(),
                'source': provider,
                'metadata': {k: v for k, v in enrichment_data.items() if k not in ['crawl_results', 'latency_ms']}
            }
            
            # Добавляем crawl_md только для crawl kind
            if kind == 'crawl':
                enrichment_payload['crawl_md'] = crawl_md
            
            # Используем EnrichmentRepository (принимает SQLAlchemy AsyncSession)
            repo = EnrichmentRepository(self.db_session)
            await repo.upsert_enrichment(
                post_id=post_id,
                kind=kind,
                provider=provider,
                data=enrichment_payload,
                params_hash=None,
                status='ok',
                error=None,
                trace_id=enrichment_data.get('trace_id')
            )
            
            logger.info("Enrichment data saved via EnrichmentRepository", extra={"post_id": post_id, "kind": kind, "provider": provider})
            
        except Exception as e:
            logger.error(f"Failed to save enrichment data: {e}")
            raise
    
    async def _publish_enriched_event(self, original_event: Dict[str, Any], enrichment_data: Dict[str, Any]):
        """Публикация события posts.enriched."""
        try:
            logger.info("enrichment_publish_start", extra={"post_id": original_event.get('post_id'), "enrichment_keys": list(enrichment_data.keys())})
            
            # Context7 best practice: очищаем None значения перед сериализацией
            def clean_none_values(obj):
                """Рекурсивно очищает None значения из словаря."""
                if isinstance(obj, dict):
                    return {k: clean_none_values(v) for k, v in obj.items() if v is not None}
                elif isinstance(obj, list):
                    return [clean_none_values(item) for item in obj if item is not None]
                else:
                    return obj
            
            cleaned_enrichment_data = clean_none_values(enrichment_data)
            logger.info(f"enrichment_data_cleaned: original_keys={list(enrichment_data.keys())}, cleaned_keys={list(cleaned_enrichment_data.keys())}")
            
            # Создание события (используем правильную схему)
            from events.schemas.posts_enriched_v1 import PostEnrichedEventV1
            
            logger.info("enrichment_event_creation", extra={"post_id": original_event.get('post_id'), "idempotency_key": original_event.get('idempotency_key')})
            
            enriched_event = PostEnrichedEventV1(
                idempotency_key=original_event['idempotency_key'],
                post_id=original_event['post_id'],
                enrichment_data=cleaned_enrichment_data,
                source_urls=cleaned_enrichment_data.get('source_urls', []),
                word_count=cleaned_enrichment_data.get('total_word_count', 0),
                skipped=False
            )
            
            logger.info("enrichment_event_created", extra={"post_id": enriched_event.post_id})
            
            # Публикация через event publisher
            from event_bus import EventPublisher
            publisher = EventPublisher(self.consumer.client)
            await publisher.publish_event('posts.enriched', enriched_event)
            
            logger.debug(f"Published post.enriched event for {original_event['post_id']}")
            
        except Exception as e:
            logger.error(f"Failed to publish enriched event: {e}")
            raise
    
    async def _publish_skipped_enrichment(self, original_event: Dict[str, Any], skip_reason: str):
        """Публикация события posts.enriched для пропущенного поста."""
        try:
            # Создание события
            enriched_event = PostEnrichedEvent(
                idempotency_key=original_event['idempotency_key'],
                post_id=original_event['post_id'],
                enrichment_data={},
                source_urls=[],
                word_count=0,
                skipped=True,
                skip_reason=skip_reason
            )
            
            # Публикация через event publisher
            from event_bus import EventPublisher
            publisher = EventPublisher(self.consumer.client)
            await publisher.publish_event('posts.enriched', enriched_event)
            
        except Exception as e:
            logger.error(f"Failed to publish skipped enrichment event: {e}")
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики worker."""
        return {
            **self.stats,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    async def stop(self):
        """Остановка worker."""
        await self.consumer.stop()
        await self.crawler.close()
        logger.info("Enrichment worker stopped")

# ============================================================================
# FACTORY FUNCTION
# ============================================================================

async def create_enrichment_worker(
    db_session,
    redis_client,
    enrichment_config: Dict[str, Any],
    consumer_group: str = "enrich_workers",
    consumer_name: str = "worker-1"
) -> EnrichmentWorker:
    """Создание enrichment worker."""
    from event_bus import create_consumer
    
    # Создание consumer
    consumer = await create_consumer(
        stream_name='posts.tagged',
        group_name=consumer_group,
        consumer_name=consumer_name
    )
    
    return EnrichmentWorker(consumer, db_session, redis_client, enrichment_config)

# ============================================================================
# CONFIG LOADER
# ============================================================================

def load_enrichment_config(config_path: str = "worker/config/enrichment_policy.yml") -> Dict[str, Any]:
    """Загрузка конфигурации enrichment."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        logger.error(f"Failed to load enrichment config: {e}")
        return {}

# ============================================================================
# MAIN LOOP
# ============================================================================

async def main():
    """Основной цикл enrichment worker."""
    import os
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    import redis.asyncio as redis
    from event_bus import init_event_bus
    
    # Конфигурация
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    # Заменяем postgresql:// на postgresql+asyncpg:// для async драйвера
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    config_path = os.getenv("ENRICHMENT_CONFIG_PATH", "worker/config/enrichment_policy.yml")
    
    # Инициализация event bus
    await init_event_bus(redis_url)
    
    # Загрузка конфигурации
    enrichment_config = load_enrichment_config(config_path)
    
    # Создание соединений
    engine = create_async_engine(db_url)
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    async with AsyncSession(engine) as db_session:
        # Создание worker
        worker = await create_enrichment_worker(
            db_session=db_session,
            redis_client=redis_client,
            enrichment_config=enrichment_config
        )
        
        try:
            # Запуск обработки
            await worker.start_processing()
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            await worker.stop()
            await redis_client.close()

class EnrichmentTask:
    """Task для обогащения постов."""
    
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.worker = None
    
    async def start(self):
        """Запуск enrichment task."""
        if self.worker is None:
            # Создание worker'а
            from config import settings
            from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
            
            # Context7: Используем SQLAlchemy AsyncSession для совместимости с EnrichmentRepository
            db_url = settings.database_url
            # Заменяем postgresql:// на postgresql+asyncpg:// для async драйвера
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
            engine = create_async_engine(db_url, pool_pre_ping=True)
            db_session = AsyncSession(engine)
            
            # Создание redis клиента
            from event_bus import RedisStreamsClient
            redis_client = RedisStreamsClient(self.redis_url)
            await redis_client.connect()
            
            # Конфигурация
            enrichment_config = {
                'max_urls_per_post': 5,
                'timeout': 30,
                'max_retries': 3
            }
            
            # Создание worker'а
            self.worker = await create_enrichment_worker(
                db_session=db_session,
                redis_client=redis_client,
                enrichment_config=enrichment_config
            )
            
            # Запуск worker'а
            await self.worker.start_processing()
    
    async def stop(self):
        """Остановка enrichment task."""
        if self.worker:
            await self.worker.stop()

if __name__ == "__main__":
    import hashlib
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
