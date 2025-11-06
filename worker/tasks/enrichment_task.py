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
from sqlalchemy import text

from event_bus import EventConsumer, ConsumerConfig, PostEnrichedEvent, get_event_publisher
from metrics import (
    enrichment_requests_total,
    enrichment_latency_seconds,
    enrichment_skipped_total,
    posts_processed_total,
    enrichment_triggers_total,
    enrichment_crawl_requests_total,
    enrichment_crawl_duration_seconds,
    enrichment_budget_checks_total
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
        
        # Context7: Убрали локальный Playwright - используем внешний crawl4ai сервис через Redis Streams
        # Crawl4AI работает через stream:posts:crawl (обрабатывается crawl_trigger_task и crawl4ai сервисом)
        
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
            
            # [C7-ID: ENRICH-URLS-001] Источники URLs: event.urls → regex из text → []
            urls = list(event_data.get('urls', []) or [])
            if not urls:
                # Парсинг URLs из текста
                text = post_context.get('content', '') or event_data.get('text', '') or ''
                urls = self._extract_urls_from_text(text)
            
            logger.info(f"Post {post_id} URLs: {len(urls)} from event/parsing")
            
            # [C7-ID: ENRICH-DBG-011] Построение enriched_event с логами «между шагами»
            logger.info("enrich_build_start", extra={"post_id": post_id})
            
            # Построение enriched_event
            # Context7: Добавляем tenant_id из post_context для multi-tenant изоляции
            # Context7: Если tenant_id отсутствует в post_context, пытаемся получить из БД
            tenant_id = None
            if post_context:
                tenant_id = post_context.get("tenant_id")
            
            # Context7: Если tenant_id отсутствует или равен 'default', пытаемся получить из БД
            if not tenant_id or tenant_id == "default":
                try:
                    tenant_id_result = await self.db_session.execute(
                        text("""
                            SELECT COALESCE(
                                (SELECT u.tenant_id::text FROM users u 
                                 JOIN user_channel uc ON uc.user_id = u.id 
                                 WHERE uc.channel_id = c.id 
                                 LIMIT 1),
                                CAST(pe_tags.data->>'tenant_id' AS text),
                                CAST(c.settings->>'tenant_id' AS text),
                                'default'
                            ) as tenant_id
                            FROM posts p
                            JOIN channels c ON c.id = p.channel_id
                            LEFT JOIN post_enrichment pe_tags 
                                ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
                            WHERE p.id = :post_id
                            LIMIT 1
                        """),
                        {"post_id": post_id}
                    )
                    row = tenant_id_result.fetchone()
                    if row:
                        tenant_id_db = str(row[0]) if row[0] else None
                        if tenant_id_db and tenant_id_db != "default":
                            tenant_id = tenant_id_db
                            logger.debug(
                                "Using tenant_id from DB in enrichment_task",
                                post_id=post_id,
                                tenant_id=tenant_id
                            )
                except Exception as e:
                    logger.debug(
                        "Failed to get tenant_id from DB in enrichment_task",
                        post_id=post_id,
                        error=str(e)
                    )
            
            # Context7: Fallback на 'default' если все еще не найден
            if not tenant_id or tenant_id == "default":
                tenant_id = "default"
                logger.warning(
                    "tenant_id not found or is 'default' in enrichment_task, using 'default'",
                    post_id=post_id,
                    tenant_id_from_post_context=post_context.get("tenant_id") if post_context else None
                )
            
            # Context7: Проверка триггеров enrichment с post_content из БД
            post_content = post_context.get('content', '') if post_context else ''
            logger.debug("enrich_trigger_check_start", extra={
                "post_id": post_id,
                "has_post_content": bool(post_content),
                "content_length": len(post_content) if post_content else 0,
                "tags_count": len(tags),
                "urls_count": len(urls) if urls else 0
            })
            should_enrich, skip_reason, trigger_reason, triggers = await self._should_enrich_post(
                post_id=post_id,
                tags=tags,
                post_content=post_content
            )
            logger.debug("enrich_trigger_check_done", extra={
                "post_id": post_id,
                "should_enrich": should_enrich,
                "skip_reason": skip_reason,
                "trigger_reason": trigger_reason,
                "triggers": triggers
            })
            
            if not should_enrich:
                logger.info(f"Post {post_id} skipped from enrichment: {skip_reason}")
                await self._publish_skipped_enrichment(event_data, skip_reason)
                self.stats['posts_skipped'] += 1
                enrichment_skipped_total.labels(reason=skip_reason).inc()
                # МЕТРИКА: пропуск обогащения
                posts_processed_total.labels(stage='enrichment', success='skip').inc()
                return
            
            # Context7: Обогащение через crawl4ai (если есть URLs)
            logger.debug("enrich_crawl_check", extra={
                "post_id": post_id,
                "has_urls": bool(urls),
                "urls_count": len(urls) if urls else 0,
                "urls_type": type(urls).__name__
            })
            crawl_enrichment = None
            if urls:
                # Создаём post dict для _enrich_post_urls
                post_for_enrichment = {
                    'urls': urls,
                    'tenant_id': tenant_id,
                    'post_id': post_id  # Context7: Добавляем post_id для логирования
                }
                try:
                    logger.info("enrich_crawl_start", extra={"post_id": post_id, "urls_count": len(urls), "urls": urls[:3]})
                    crawl_enrichment = await self._enrich_post_urls(post_for_enrichment, tenant_id=tenant_id)
                    logger.info("enrich_crawl_done", extra={
                        "post_id": post_id,
                        "has_results": crawl_enrichment is not None,
                        "results_count": len(crawl_enrichment.get('crawl_results', [])) if (crawl_enrichment and isinstance(crawl_enrichment, dict)) else 0,
                        "crawl_enrichment_type": type(crawl_enrichment).__name__ if crawl_enrichment else None
                    })
                except Exception as e:
                    logger.exception("enrich_crawl_error", extra={
                        "post_id": post_id,
                        "error": str(e),
                        "urls_count": len(urls)
                    })
                    # Продолжаем обработку даже при ошибке crawl
                    crawl_enrichment = None
            
            # Формирование enrichment_data с metadata
            enrichment_data = {
                "kind": "enrichment",
                "source": "enrichment_task", 
                "version": "v1",
                "tags": tags,          # используем теги из входящего события
                "entities": [],
                "embedding": None,     # индексатор дожмет (см. флаг INDEXER_EMBED_IF_MISSING)
                "urls": urls,
                "reason": trigger_reason,  # Context7: Самый высокий приоритет триггера
                "metadata": {
                    "triggers": triggers,  # Context7: Список всех сработавших триггеров
                    "crawl_priority": "high" if trigger_reason == "trigger:url" and crawl_enrichment else "normal"
                }
            }
            
            # Context7: Добавляем crawl данные если есть
            # _enrich_post_urls возвращает enrichment_data с crawl_results, если есть результаты
            if crawl_enrichment:
                # crawl_enrichment может быть словарем с crawl_results или None
                if isinstance(crawl_enrichment, dict) and 'crawl_results' in crawl_enrichment:
                    # Переносим crawl_results в enrichment_data
                    enrichment_data['crawl_results'] = crawl_enrichment.get('crawl_results', [])
                    enrichment_data['total_word_count'] = crawl_enrichment.get('total_word_count', 0)
                    # Также сохраняем полный объект для совместимости
                    enrichment_data['crawl_data'] = crawl_enrichment
                else:
                    # Legacy формат (если crawl_enrichment - это один результат)
                    enrichment_data['crawl_data'] = crawl_enrichment
            
            enriched_event = {
                "idempotency_key": event_data.get('idempotency_key', f"{post_id}:enriched:v1"),
                "post_id": post_id,               # UUID-строка!
                "tenant_id": tenant_id,  # Context7: tenant_id для multi-tenant (из post_context или БД)
                "channel_id": post_context.get("channel_id", "") if post_context else "",
                "text": post_context.get("content", "") if post_context else "",
                "telegram_post_url": post_context.get("telegram_post_url", "") if post_context else "",
                "posted_at": post_context.get("posted_at", datetime.now(timezone.utc)) if post_context else datetime.now(timezone.utc),
                "enrichment_data": enrichment_data,
            }
            
            logger.info("enrich_build_done", extra={
                "out_keys": list(enriched_event.keys()),
                "enrich_keys": list(enrichment_data.keys()),
                "has_crawl": "crawl_data" in enrichment_data
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
            try:
                logger.info("enrich_save_start", extra={"post_id": sanitized_post_id_for_save, "enrichment_kind": enrichment_data.get("kind", "unknown")})
                await self._save_enrichment_data(sanitized_post_id_for_save, enrichment_data)
                logger.info("enrich_save_ok", extra={"post_id": sanitized_post_id_for_save})
            except Exception as e:
                logger.exception("enrich_save_fail", extra={
                    "error": str(e),
                    "post_id": sanitized_post_id_for_save,
                    "enrichment_kind": enrichment_data.get("kind", "unknown")
                })
                # Продолжаем обработку даже при ошибке сохранения - публикуем событие
                # Это позволит индексатору работать, даже если БД недоступна
            
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
    
    async def _check_enrichment_triggers(self, post_id: str, tags: List[Dict[str, Any]], post_content: Optional[str] = None) -> tuple[bool, str, List[str]]:
        """
        Context7: Проверка триггеров обогащения с OR логикой и приоритетами.
        
        Приоритеты:
        1. URL (trigger:url) - если есть URL в посте
        2. trigger_tags (trigger:tag) - если есть совпадение тегов
        3. word_count (trigger:wordcount) - если текст >= min_word_count
        
        Returns:
            (should_enrich: bool, reason: str, triggers: List[str])
            - reason: самый высокий приоритет из сработавших триггеров
            - triggers: список всех сработавших триггеров
        """
        triggers = []
        
        # Приоритет 1: URL триггер
        if post_content:
            urls = self._extract_urls_from_text(post_content)
            if urls:
                triggers.append("trigger:url")
                enrichment_triggers_total.labels(type="url", decision="hit").inc()
            else:
                enrichment_triggers_total.labels(type="url", decision="miss").inc()
        
        # Приоритет 2: trigger_tags
        trigger_tags_config = self.config.get('crawl4ai', {}).get('trigger_tags', [])
        if trigger_tags_config:
            post_tags = [tag.get('name', '').lower() for tag in tags]
            if any(tag.lower() in post_tags for tag in trigger_tags_config):
                triggers.append("trigger:tag")
                enrichment_triggers_total.labels(type="tag", decision="hit").inc()
            else:
                enrichment_triggers_total.labels(type="tag", decision="miss").inc()
        
        # Приоритет 3: word_count
        if post_content:
            min_word_count = self.config.get('crawl4ai', {}).get('min_word_count', 100)
            word_count = len(post_content.split())
            if word_count >= min_word_count:
                triggers.append("trigger:wordcount")
                enrichment_triggers_total.labels(type="wordcount", decision="hit").inc()
            else:
                enrichment_triggers_total.labels(type="wordcount", decision="miss").inc()
        
        if not triggers:
            return False, 'no_triggers', []
        
        # Определяем reason как самый высокий приоритет
        if "trigger:url" in triggers:
            reason = "trigger:url"
        elif "trigger:tag" in triggers:
            reason = "trigger:tag"
        else:
            reason = "trigger:wordcount"
        
        return True, reason, triggers
    
    async def _should_enrich_post(self, post_id: str, tags: List[Dict[str, Any]], post_content: Optional[str] = None) -> tuple[bool, str, Optional[str], Optional[List[str]]]:
        """
        Проверка, нужно ли обогащать пост.
        Context7: Использует OR логику с приоритетами через _check_enrichment_triggers.
        
        Returns:
            (should_enrich: bool, reason: str, trigger_reason: Optional[str], triggers: Optional[List[str]])
            - reason: причина skip (если should_enrich=False) или None
            - trigger_reason: самый высокий приоритет триггера (если should_enrich=True)
            - triggers: список всех сработавших триггеров (если should_enrich=True)
        """
        # Проверка включения enrichment
        if not self.config.get('enrichment', {}).get('enabled', True):
            return False, 'enrichment_disabled', None, None
        
        # [C7-ID: ENRICH-LIMITS-002] Feature flags для обхода лимитов
        skip_limits_env = os.getenv("ENRICHMENT_SKIP_LIMITS", "false")
        skip_limits = skip_limits_env.lower() == "true"
        logger.info("enrich_limits_check", extra={"env": skip_limits_env, "skip": skip_limits, "post_id": post_id})
        
        if skip_limits:
            logger.warning("enrich_limits_skipped: feature_flag enabled", extra={"post_id": post_id})
            # Пропускаем проверку лимитов, возвращаем True для продолжения обогащения
            return True, 'skip_limits_feature_flag', 'skip_limits_feature_flag', []
        else:
            # Проверка лимитов пользователя
            if not await self._check_user_limits(post_id):
                logger.warning("enrich_limits_exceeded", extra={"post_id": post_id})
                return False, 'user_limits_exceeded', None, None
        
        # Получаем контент поста для проверки триггеров (если не передан)
        if post_content is None:
            post = await self._get_post_content(post_id)
            post_content = post.get('content', '') if post else ''
        
        # Context7: Проверка триггеров с OR логикой и приоритетами
        should_enrich, trigger_reason, triggers = await self._check_enrichment_triggers(
            post_id=post_id,
            tags=tags,
            post_content=post_content
        )
        
        if not should_enrich:
            return False, trigger_reason, None, None
        
        # Логируем сработавшие триггеры
        logger.info(
            "enrichment_triggers_checked",
            extra={
                "post_id": post_id,
                "reason": trigger_reason,
                "triggers": triggers,
                "trigger_count": len(triggers)
            }
        )
        
        return True, None, trigger_reason, triggers
    
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
        """
        Извлечение URLs из текста через URLNormalizer.
        Context7: Использует улучшенную экстракцию с поддержкой Markdown/Telegram форматов.
        """
        # Импортируем URLNormalizer
        from services.url_normalizer import URLNormalizer
        
        # Создаём нормализатор (если ещё не создан)
        if not hasattr(self, '_url_normalizer'):
            strip_params = self.config.get('crawl4ai', {}).get('strip_query_params', [])
            self._url_normalizer = URLNormalizer(strip_params=strip_params)
        
        # Извлекаем и нормализуем URL
        urls = self._url_normalizer.extract_urls_from_text(text)
        return urls

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

    async def _check_budget(self, tenant_id: str, domain: str) -> tuple[bool, Optional[str]]:
        """
        Context7: Проверка бюджетов для CRAWL4AI (per-tenant daily, per-domain hourly).
        Использует Redis для on-line счетчиков.
        
        Args:
            tenant_id: ID tenant
            domain: Домен URL для проверки hourly лимита
            
        Returns:
            (is_allowed: bool, reason_if_denied: Optional[str])
        """
        if not self.redis_client:
            # Если Redis недоступен, разрешаем (graceful degradation)
            logger.warning("Budget check Redis unavailable, allowing request")
            return True, None
        
        crawl_config = self.config.get('crawl4ai', {})
        budgets = crawl_config.get('budgets', {})
        
        per_tenant_daily = budgets.get('per_tenant_daily', 300)
        per_domain_hourly = budgets.get('per_domain_hourly', 60)
        
        try:
            # Проверка per-tenant daily budget
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            tenant_key = f"enrich:budget:tenant:{tenant_id}:{today}"
            
            # Получаем текущее значение
            tenant_count = await self.redis_client.get(tenant_key)
            tenant_count = int(tenant_count) if tenant_count else 0
            
            if tenant_count >= per_tenant_daily:
                logger.warning(
                    "Enrichment budget exceeded (tenant daily)",
                    tenant_id=tenant_id,
                    count=tenant_count,
                    limit=per_tenant_daily
                )
                enrichment_budget_checks_total.labels(type="tenant", result="denied").inc()
                return False, "budget_exceeded:tenant_daily"
            else:
                enrichment_budget_checks_total.labels(type="tenant", result="allowed").inc()
            
            # Проверка per-domain hourly budget
            if domain:
                current_hour = datetime.now(timezone.utc).strftime("%Y-%m-%d:%H")
                domain_key = f"enrich:budget:domain:{domain}:{current_hour}"
                
                domain_count = await self.redis_client.get(domain_key)
                domain_count = int(domain_count) if domain_count else 0
                
                if domain_count >= per_domain_hourly:
                    logger.warning(
                        "Enrichment budget exceeded (domain hourly)",
                        domain=domain,
                        count=domain_count,
                        limit=per_domain_hourly
                    )
                    enrichment_budget_checks_total.labels(type="domain", result="denied").inc()
                    return False, "budget_exceeded:domain_hourly"
                else:
                    enrichment_budget_checks_total.labels(type="domain", result="allowed").inc()
            
            return True, None
            
        except Exception as e:
            logger.error("Budget check error", tenant_id=tenant_id, domain=domain, error=str(e))
            # При ошибке разрешаем (graceful degradation)
            return True, None
    
    async def _increment_budget(self, tenant_id: str, domain: str):
        """
        Context7: Инкремент счетчиков бюджетов после успешного crawl.
        Использует Redis для on-line счетчиков с TTL.
        
        Args:
            tenant_id: ID tenant
            domain: Домен URL
        """
        if not self.redis_client:
            return
        
        try:
            # Инкремент per-tenant daily
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            tenant_key = f"enrich:budget:tenant:{tenant_id}:{today}"
            await self.redis_client.incr(tenant_key)
            # TTL: 25 часов для daily
            await self.redis_client.expire(tenant_key, 25 * 3600)
            
            # Инкремент per-domain hourly
            if domain:
                current_hour = datetime.now(timezone.utc).strftime("%Y-%m-%d:%H")
                domain_key = f"enrich:budget:domain:{domain}:{current_hour}"
                await self.redis_client.incr(domain_key)
                # TTL: 2 часа для hourly
                await self.redis_client.expire(domain_key, 2 * 3600)
            
        except Exception as e:
            logger.error("Budget increment error", tenant_id=tenant_id, domain=domain, error=str(e))
    
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
            SELECT 
                p.id, 
                p.content, 
                p.channel_id, 
                p.telegram_post_url, 
                p.posted_at, 
                p.telegram_message_id,
                COALESCE(
                    (SELECT u.tenant_id::text FROM users u 
                     JOIN user_channel uc ON uc.user_id = u.id 
                     WHERE uc.channel_id = c.id 
                     LIMIT 1),
                    CAST(pe_tags.data->>'tenant_id' AS text),
                    CAST(c.settings->>'tenant_id' AS text),
                    'default'
                ) as tenant_id
            FROM posts p
            JOIN channels c ON p.channel_id = c.id
            LEFT JOIN post_enrichment pe_tags 
                ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
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
    
    async def _enrich_post_urls(self, post: Dict[str, Any], tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Context7: Получение crawl результатов из post_enrichment таблицы.
        Crawl4AI обрабатывается внешним сервисом через stream:posts:crawl.
        Этот метод проверяет, есть ли уже результаты в БД.
        """
        urls = post.get('urls', [])
        if not urls:
            return None
        
        post_id = post.get('id') or post.get('post_id')
        if not post_id:
            logger.warning("No post_id in post data for crawl enrichment check")
            return None
        
        # Получаем tenant_id если не передан
        if not tenant_id:
            tenant_id = post.get('tenant_id')
            
            # Context7: Если tenant_id отсутствует или равен 'default', пытаемся получить из БД
            if not tenant_id or tenant_id == 'default':
                try:
                    tenant_id_result = await self.db_session.execute(
                        text("""
                            SELECT COALESCE(
                                (SELECT u.tenant_id::text FROM users u 
                                 JOIN user_channel uc ON uc.user_id = u.id 
                                 WHERE uc.channel_id = c.id 
                                 LIMIT 1),
                                CAST(pe_tags.data->>'tenant_id' AS text),
                                CAST(c.settings->>'tenant_id' AS text),
                                'default'
                            ) as tenant_id
                            FROM posts p
                            JOIN channels c ON c.id = p.channel_id
                            LEFT JOIN post_enrichment pe_tags 
                                ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
                            WHERE p.id = :post_id
                            LIMIT 1
                        """),
                        {"post_id": post_id}
                    )
                    row = tenant_id_result.fetchone()
                    if row and row[0]:
                        tenant_id_db = str(row[0]) if row[0] else None
                        if tenant_id_db and tenant_id_db != "default":
                            tenant_id = tenant_id_db
                except Exception as e:
                    logger.debug(
                        "Failed to get tenant_id from DB in _enrich_post_urls",
                        post_id=post_id,
                        error=str(e)
                    )
            
            if not tenant_id or tenant_id == 'default':
                tenant_id = 'default'
        
        # Context7: Проверяем наличие crawl результатов в post_enrichment
        try:
            crawl_result = await self.db_session.execute(
                text("""
                    SELECT data, crawl_md, updated_at
                    FROM post_enrichment
                    WHERE post_id = :post_id AND kind = 'crawl'
                    ORDER BY updated_at DESC
                    LIMIT 1
                """),
                {"post_id": post_id}
            )
            row = crawl_result.fetchone()
            
            if row and row[0]:  # data JSONB не пустой
                crawl_data = row[0] if isinstance(row[0], dict) else json.loads(row[0]) if isinstance(row[0], str) else {}
                crawl_md = row[1] if row[1] else crawl_data.get('crawl_md') or crawl_data.get('markdown')
                
                if crawl_md or crawl_data.get('urls'):
                    # Формируем результат в формате, ожидаемом enrichment_data
                    enrichment_data = {
                        'source_urls': urls,
                        'crawl_results': [],
                        'total_word_count': crawl_data.get('word_count', 0) or 0,
                        'crawled_at': crawl_data.get('crawled_at') or (row[2].isoformat() if row[2] else datetime.now(timezone.utc).isoformat())
                    }
                    
                    # Извлекаем crawl_results из data
                    urls_data = crawl_data.get('urls', [])
                    if urls_data:
                        for url_data in urls_data:
                            enrichment_data['crawl_results'].append({
                                'url': url_data.get('url'),
                                'markdown': crawl_md,  # Используем общий crawl_md или из url_data
                                'word_count': url_data.get('word_count', 0),
                                'status': url_data.get('status', 'ok')
                            })
                    elif crawl_md:
                        # Если нет urls_data, но есть crawl_md, создаем один результат
                        enrichment_data['crawl_results'].append({
                            'url': urls[0] if urls else None,
                            'markdown': crawl_md,
                            'word_count': len(crawl_md.split()) if crawl_md else 0,
                            'status': 'ok'
                        })
                    
                    logger.info(
                        "Found crawl results in post_enrichment",
                        post_id=post_id,
                        results_count=len(enrichment_data['crawl_results'])
                    )
                    return enrichment_data
                else:
                    logger.debug(
                        "Crawl enrichment exists but no content",
                        post_id=post_id
                    )
            else:
                logger.debug(
                    "No crawl enrichment found in post_enrichment",
                    post_id=post_id
                )
        except Exception as e:
            logger.error(
                f"Failed to check crawl enrichment in DB: post_id={post_id}, error={str(e)}",
                exc_info=True
            )
        
        # Context7: Результатов нет - crawl4ai сервис обработает позже через stream:posts:crawl
        # Возвращаем None, чтобы enrichment продолжился без crawl данных
        return None
    
    def _get_enrichment_key(self, url: str) -> str:
        """
        Context7: Генерация enrichment ключа для глобальной дедупликации.
        Использует нормализованный URL + policy_version (без post_id для глобальной дедупликации).
        
        Args:
            url: URL для нормализации
            
        Returns:
            SHA256 хеш нормализованного URL + policy_version
        """
        import hashlib
        
        # Получаем нормализатор
        if not hasattr(self, '_url_normalizer'):
            from services.url_normalizer import URLNormalizer
            strip_params = self.config.get('crawl4ai', {}).get('strip_query_params', [])
            self._url_normalizer = URLNormalizer(strip_params=strip_params)
        
        # Нормализуем URL
        normalized_url = self._url_normalizer.normalize_url(url)
        
        # Получаем policy_version (версия политики для инвалидации кеша)
        policy_version = self.config.get('crawl4ai', {}).get('policy_version', 'v1')
        
        # Генерируем ключ
        key_data = f"{normalized_url}:{policy_version}"
        enrichment_key = hashlib.sha256(key_data.encode('utf-8')).hexdigest()
        
        return enrichment_key
    
    async def _check_url_crawled(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Context7: Проверка, был ли URL уже обработан (глобальная дедупликация).
        Возвращает кешированный результат если он есть.
        
        Args:
            url: URL для проверки
            
        Returns:
            Кешированный результат crawl или None
        """
        if not self.config.get('crawl4ai', {}).get('caching', {}).get('enabled', True):
            return None
        
        enrichment_key = self._get_enrichment_key(url)
        cache_key = f"enrich:crawl:{enrichment_key}"
        
        try:
            cached_data = await self.redis_client.get(cache_key)
            if cached_data:
                try:
                    result = json.loads(cached_data)
                    logger.debug("Cached crawl result found", url=url, enrichment_key=enrichment_key[:16])
                    return result
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse cached crawl result: url={url}")
                    return None
        except Exception as e:
            logger.error(f"Error checking cached crawl: url={url}, error={str(e)}", exc_info=True)
        
        return None
    
    async def _mark_url_crawled(self, url: str, result: Dict[str, Any]):
        """
        Context7: Сохранение результата crawl в Redis с TTL (глобальная дедупликация).
        
        Args:
            url: URL (используется для генерации ключа)
            result: Результат crawl для сохранения
        """
        if not self.config.get('crawl4ai', {}).get('caching', {}).get('enabled', True):
            return
        
        enrichment_key = self._get_enrichment_key(url)
        cache_key = f"enrich:crawl:{enrichment_key}"
        
        ttl_days = self.config.get('crawl4ai', {}).get('caching', {}).get('ttl_days', 7)
        ttl_seconds = int(ttl_days * 24 * 3600)
        
        try:
            await self.redis_client.setex(
                cache_key,
                ttl_seconds,
                json.dumps(result)
            )
            logger.debug("Crawl result cached", url=url, enrichment_key=enrichment_key[:16], ttl_days=ttl_days)
        except Exception as e:
            logger.error(f"Error caching crawl result: url={url}, error={str(e)}", exc_info=True)
    
    async def _get_cached_crawl(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получение кешированного результата crawl (legacy метод).
        Context7: Использует _check_url_crawled для глобальной дедупликации.
        """
        return await self._check_url_crawled(url)
    
    async def _cache_crawl_result(self, url: str, result: Dict[str, Any]):
        """
        Кеширование результата crawl (legacy метод).
        Context7: Использует _mark_url_crawled для глобальной дедупликации.
        """
        await self._mark_url_crawled(url, result)
    
    # Context7: Убрали _crawl_url - crawling теперь обрабатывается внешним crawl4ai сервисом
    # через stream:posts:crawl (публикуется crawl_trigger_task, обрабатывается crawl4ai сервисом)
    
    def _get_domain_from_url(self, url: str) -> str:
        """Извлечение домена из URL для метрик."""
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower() or "unknown"
        except Exception:
            return "unknown"
    
    def _validate_url_security(self, url: str) -> tuple[bool, Optional[str]]:
        """
        Context7: SSRF защита - валидация URL безопасности.
        
        Проверки:
        - Разрешить только http:// и https://
        - Запретить: file:, data:, ftp:, gopher:, localhost, 127.0.0.1, ::1
        - Запретить RFC1918: 10.x.x.x, 172.16-31.x.x, 192.168.x.x
        - Запретить link-local: 169.254.x.x
        
        Returns:
            (is_allowed: bool, reason_if_denied: Optional[str])
        """
        try:
            parsed = urlparse(url)
            scheme = parsed.scheme.lower()
            
            # 1. Разрешаем только http:// и https://
            if scheme not in ['http', 'https']:
                return False, f"ssrf_denied: scheme_not_allowed ({scheme})"
            
            # 2. Проверка hostname на локальные адреса
            hostname = parsed.hostname
            if not hostname:
                return False, "ssrf_denied: no_hostname"
            
            hostname_lower = hostname.lower()
            
            # Запрещаем localhost и его варианты
            if hostname_lower in ['localhost', '127.0.0.1', '::1', '0.0.0.0']:
                return False, "ssrf_denied: localhost"
            
            # Проверка на IP адреса (RFC1918 и link-local)
            import ipaddress
            try:
                ip = ipaddress.ip_address(hostname)
                
                # Запрещаем private IP (RFC1918)
                if ip.is_private:
                    return False, "ssrf_denied: private_ip"
                
                # Запрещаем link-local
                if ip.is_link_local:
                    return False, "ssrf_denied: link_local"
                
                # Запрещаем loopback
                if ip.is_loopback:
                    return False, "ssrf_denied: loopback"
                
            except ValueError:
                # Не IP адрес, проверяем домен
                pass
            
            # 3. Проверка заблокированных доменов
            denylist_domains = self.config.get('crawl4ai', {}).get('denylist_domains', [])
            if denylist_domains:
                for blocked in denylist_domains:
                    if blocked.lower() in hostname_lower:
                        return False, f"ssrf_denied: denylist_domain ({blocked})"
            
            # 4. Проверка разрешённых доменов (whitelist)
            allowlist_domains = self.config.get('crawl4ai', {}).get('allowlist_domains', [])
            if allowlist_domains:
                # Если whitelist не пустой, разрешаем только домены из whitelist
                if not any(allowed.lower() in hostname_lower for allowed in allowlist_domains):
                    return False, "ssrf_denied: not_in_allowlist"
            
            return True, None
            
        except Exception as e:
            logger.error(f"URL security validation error: url={url}, error={str(e)}", exc_info=True)
            return False, f"ssrf_denied: validation_error ({str(e)})"
    
    async def _is_url_allowed(self, url: str) -> bool:
        """
        Проверка разрешённости URL (legacy метод).
        Context7: Использует _validate_url_security для SSRF защиты.
        """
        is_allowed, reason = self._validate_url_security(url)
        if not is_allowed:
            logger.warning(f"URL blocked by security check: url={url}, reason={reason}")
        return is_allowed
    
    async def _save_enrichment_data(self, post_id: str, enrichment_data: Dict[str, Any]):
        """
        Context7: Сохранение данных enrichment через EnrichmentRepository.
        
        Определяет kind по наличию crawl_results:
        - kind='crawl' если есть crawl_results (результаты crawl4ai)
        - kind='general' если нет crawl_results (простое обогащение через теги/URLs)
        """
        # Context7: Импорт shared репозитория с правильной обработкой путей
        # Best practice: сначала пробуем импорт напрямую (если пакет установлен через pip install -e)
        # Затем добавляем пути для dev окружения (volume mounts)
        import sys
        import os
        
        try:
            # Попытка 1: Прямой импорт (если пакет установлен через pip install -e)
            from shared.repositories.enrichment_repository import EnrichmentRepository
        except ImportError:
            # Попытка 2: Добавление пути из worker контейнера
            # Context7: Dockerfile устанавливает через `pip install -e /app/shared/python`
            # Поэтому пакет должен быть доступен напрямую, но если нет - добавляем пути
            shared_paths = [
                '/app/shared/python',  # Production путь в контейнере (из Dockerfile)
                os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared', 'python')),  # Dev путь от worker/tasks
                os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python')),  # Альтернативный путь
            ]
            
            for shared_path in shared_paths:
                if os.path.exists(shared_path) and shared_path not in sys.path:
                    sys.path.insert(0, shared_path)
                    logger.debug(f"Added shared path to sys.path: {shared_path}")
            
            try:
                from shared.repositories.enrichment_repository import EnrichmentRepository
            except ImportError as e:
                logger.error(f"Failed to import EnrichmentRepository: {e}", extra={
                    "sys_path": sys.path[:5],  # Первые 5 путей для отладки
                    "shared_paths_tried": shared_paths,
                    "cwd": os.getcwd()
                })
                # Context7: Graceful degradation - логируем ошибку, но не падаем
                # Продолжаем обработку без сохранения в БД (данные уже в событии posts.enriched)
                logger.warning("DB save failed, but continuing with event emission", extra={
                    "post_id": post_id,
                    "error": str(e)
                })
                return
        
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
            
            # Context7: Добавляем crawl_md в data JSONB для синхронизации с legacy полем
            # Это позволяет EnrichmentRepository синхронизировать legacy поле crawl_md
            if kind == 'crawl' and crawl_md:
                enrichment_payload['crawl_md'] = crawl_md
                # Также добавляем в enrichment_data для полноты
                enrichment_payload['enrichment_data']['crawl_md'] = crawl_md
            
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
        # Context7: Убрали await self.crawler.close() - используем внешний crawl4ai сервис
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
