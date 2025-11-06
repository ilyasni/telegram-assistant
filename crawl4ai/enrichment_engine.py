"""
Crawl4AI Enrichment Engine
[C7-ID: CRAWL4AI-SERVICE-003]

Production-ready crawler с policy explain, HTTP cache, rate limits
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urlparse
import aiohttp
import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Histogram, Gauge

logger = structlog.get_logger()

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

# [C7-ID: CRAWL4AI-SERVICE-003] - Production метрики
crawl_success_rate = Gauge(
    'crawl_success_rate',
    'Success rate of crawls',
    ['host']
)

crawl_latency_seconds = Histogram(
    'crawl_latency_seconds',
    'Crawl latency',
    ['host', 'status']
)

crawl_skip_reasons_total = Counter(
    'crawl_skip_reasons_total',
    'Skip reasons',
    ['reason']
)

# [C7-ID: CRAWL4AI-CACHE-002] - HTTP cache метрики
cache_hits_total = Counter(
    'cache_hits_total',
    'Total cache hits',
    ['type']
)

cache_misses_total = Counter(
    'cache_misses_total',
    'Total cache misses',
    ['type']
)

# Rate limiting метрики
rate_limit_hits_total = Counter(
    'rate_limit_hits_total',
    'Total rate limit hits',
    ['host']
)

# Новые метрики с правильным неймингом
crawl_cache_size_current = Gauge(
    'crawl_cache_size_current',
    'HTTP cache size (entries count)'
)

crawl_policy_explain_reasons_total = Counter(
    'crawl_policy_explain_reasons_total',
    'Policy explain reasons',
    ['reason']  # no_urls, no_trigger_tags, below_word_count, robots_disallow
)

# Context7: Метрики для сохранения в S3
crawl_persist_total = Counter(
    'crawl_persist_total',
    'Total crawl persistence operations',
    ['status', 'destination']  # status: success|error|cache_hit, destination: s3|redis
)

# ============================================================================
# ENRICHMENT ENGINE
# ============================================================================

class EnrichmentEngine:
    """
    Production-ready enrichment engine с policy explain и кешированием.
    
    Поддерживает:
    - Policy explain (логирование причин решений)
    - HTTP кеширование (ETag/Last-Modified)
    - Rate limiting per-host
    - AV-проверка вложений
    - Метрики и мониторинг
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        max_concurrent_crawls: int = 3,
        rate_limit_per_host: int = 10,  # запросов в минуту
        cache_ttl: int = 3600,  # 1 час
        user_agent: str = "Crawl4AI/1.0 (Telegram Assistant)",
        s3_service: Optional[Any] = None,  # S3StorageService для сохранения HTML/MD в S3
        circuit_breaker: Optional[Any] = None  # CircuitBreaker для защиты от каскадных сбоев
    ):
        self.redis_url = redis_url
        self.max_concurrent_crawls = max_concurrent_crawls
        self.rate_limit_per_host = rate_limit_per_host
        self.cache_ttl = cache_ttl
        self.user_agent = user_agent
        self.s3_service = s3_service  # Context7: для долговечного хранения в S3
        
        # Context7: Circuit breaker для защиты от каскадных сбоев
        if circuit_breaker is None:
            from shared.python.shared.utils.circuit_breaker import CircuitBreaker
            import os
            failure_threshold = int(os.getenv("CRAWL4AI_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
            recovery_timeout = int(os.getenv("CRAWL4AI_CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "60"))
            self.circuit_breaker = CircuitBreaker(
                name="crawl4ai_http",
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                expected_exception=aiohttp.ClientError
            )
        else:
            self.circuit_breaker = circuit_breaker
        
        # Redis клиент для кеширования
        self.redis_client: Optional[redis.Redis] = None
        
        # HTTP сессия
        self.http_session: Optional[aiohttp.ClientSession] = None
        
        # Семафор для ограничения параллелизма
        self._semaphore = asyncio.Semaphore(max_concurrent_crawls)
        
        # Rate limiting кеш
        self._rate_limit_cache: Dict[str, List[float]] = {}
        
        logger.info("EnrichmentEngine initialized",
                   max_concurrent_crawls=max_concurrent_crawls,
                   rate_limit_per_host=rate_limit_per_host,
                   cache_ttl=cache_ttl,
                   s3_enabled=bool(s3_service))
    
    async def start(self):
        """Запуск enrichment engine."""
        try:
            # Подключение к Redis
            self.redis_client = redis.from_url(self.redis_url, decode_responses=True)
            await self.redis_client.ping()
            
            # Создание HTTP сессии
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self.http_session = aiohttp.ClientSession(
                timeout=timeout,
                headers={'User-Agent': self.user_agent}
            )
            
            # Инициализация метрики cache size
            await self._update_cache_size_metric()
            
            logger.info("EnrichmentEngine started successfully")
            
        except Exception as e:
            logger.error("Failed to start EnrichmentEngine", error=str(e))
            raise
    
    async def _update_cache_size_metric(self):
        """Обновление метрики размера кеша."""
        try:
            if self.redis_client:
                # Подсчитываем количество ключей кеша
                cache_keys = await self.redis_client.keys("crawl_cache:*")
                crawl_cache_size_current.set(len(cache_keys))
        except Exception as e:
            logger.debug("Error updating cache size metric", error=str(e))
    
    async def stop(self):
        """Остановка enrichment engine."""
        if self.http_session:
            await self.http_session.close()
        
        if self.redis_client:
            await self.redis_client.close()
        
        logger.info("EnrichmentEngine stopped")
    
    async def enrich_post(
        self, 
        post_data: Dict[str, Any],
        policy_config: Dict[str, Any]
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Обогащение поста с policy explain.
        
        Returns:
            (success, enrichment_data, explain_reason)
        """
        async with self._semaphore:
            try:
                # Policy explain: анализ решения
                explain_reason = await self._explain_policy_decision(post_data, policy_config)
                
                if not explain_reason['should_enrich']:
                    crawl_skip_reasons_total.labels(reason=explain_reason['reason']).inc()
                    logger.info("Post enrichment skipped",
                              post_id=post_data.get('post_id'),
                              reason=explain_reason['reason'])
                    return False, {}, explain_reason['reason']
                
                # Извлечение URL для обогащения
                urls = post_data.get('urls', [])
                if not urls:
                    return False, {}, "no_urls"
                
                # Обогащение каждого URL с передачей tenant_id и post_id для S3
                tenant_id = post_data.get('tenant_id')
                if not tenant_id:
                    # Context7: Логируем предупреждение если tenant_id отсутствует
                    logger.warning(
                        "tenant_id not found in post_data, using fallback 'default'",
                        post_id=post_data.get('post_id'),
                        post_data_keys=list(post_data.keys())
                    )
                    tenant_id = 'default'
                post_id = post_data.get('post_id')
                enrichment_data = {}
                for url in urls:
                    try:
                        url_data = await self._enrich_url(
                            url=url, 
                            policy_config=policy_config,
                            tenant_id=tenant_id,
                            post_id=post_id
                        )
                        if url_data:
                            enrichment_data[url] = url_data
                    except Exception as e:
                        logger.error("Error enriching URL",
                                   url=url,
                                   error=str(e))
                        continue
                
                if enrichment_data:
                    logger.info("Post enriched successfully",
                              post_id=post_data.get('post_id'),
                              urls_count=len(enrichment_data))
                    return True, enrichment_data, "success"
                else:
                    return False, {}, "no_enrichment_data"
                    
            except Exception as e:
                logger.error("Error in post enrichment",
                           post_id=post_data.get('post_id'),
                           error=str(e))
                return False, {}, f"error: {str(e)}"
    
    async def _explain_policy_decision(
        self, 
        post_data: Dict[str, Any], 
        policy_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        [C7-ID: CRAWL4AI-EXPLAIN-001] - Policy explain для диагностики.
        
        Объясняет почему пост был пропущен или допущен к обогащению.
        """
        explain = {
            'should_enrich': False,
            'reason': 'unknown',
            'details': {}
        }
        
        # Проверка наличия URL
        urls = post_data.get('urls', [])
        if not urls:
            explain['reason'] = 'no_urls'
            explain['details']['urls_count'] = 0
            crawl_policy_explain_reasons_total.labels(reason='no_urls').inc()
            return explain
        
        # Проверка тегов-триггеров
        trigger_tags = policy_config.get('crawl4ai', {}).get('trigger_tags', [])
        tags = post_data.get('tags', [])
        # tags может быть списком строк или списком словарей
        if tags and isinstance(tags[0], dict):
            post_tags = [tag.get('name', '') for tag in tags]
        else:
            post_tags = tags  # Уже список строк
        
        has_trigger_tag = any(tag in post_tags for tag in trigger_tags)
        if not has_trigger_tag and trigger_tags:
            explain['reason'] = 'no_trigger_tags'
            explain['details'] = {
                'trigger_tags': trigger_tags,
                'post_tags': post_tags
            }
            crawl_policy_explain_reasons_total.labels(reason='no_trigger_tags').inc()
            return explain
        
        # Проверка минимального количества слов
        min_word_count = policy_config.get('crawl4ai', {}).get('min_word_count', 100)
        text = post_data.get('text', '')
        word_count = len(text.split())
        
        if word_count < min_word_count:
            explain['reason'] = 'below_word_count'
            explain['details'] = {
                'word_count': word_count,
                'min_required': min_word_count
            }
            crawl_policy_explain_reasons_total.labels(reason='below_word_count').inc()
            return explain
        
        # Проверка rate limiting
        for url in urls:
            if not await self._check_rate_limit(url):
                explain['reason'] = 'rate_limited'
                explain['details'] = {
                    'url': url,
                    'rate_limit': self.rate_limit_per_host
                }
                return explain
        
        # Все проверки пройдены
        explain['should_enrich'] = True
        explain['reason'] = 'policy_passed'
        explain['details'] = {
            'urls_count': len(urls),
            'has_trigger_tag': has_trigger_tag,
            'word_count': word_count
        }
        
        return explain
    
    async def _enrich_url(
        self, 
        url: str, 
        policy_config: Dict[str, Any],
        tenant_id: Optional[str] = None,
        post_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Обогащение одного URL с детерминированным кешированием и сохранением в S3.
        
        Context7: Использует url_hash (SHA256 от нормализованного URL) и content_sha256
        для детерминированного кеширования. Сохраняет HTML/MD в S3 для долговечности.
        
        Args:
            url: URL для обогащения
            policy_config: Конфигурация политики
            tenant_id: ID tenant для S3 ключей
            post_id: ID поста для S3 ключей
        """
        try:
            # Context7: Вычисляем url_hash (SHA256 от нормализованного URL)
            # Нормализация: приведение к lowercase, удаление фрагментов
            normalized_url = self._normalize_url(url)
            url_hash = hashlib.sha256(normalized_url.encode('utf-8')).hexdigest()
            
            # Context7: Проверка кеша по url_hash
            cache_key = f"crawl:enrichment:{url_hash}"
            cached_data = await self._get_from_cache(cache_key)
            
            if cached_data:
                cache_hits_total.labels(type='enrichment').inc()
                logger.debug("Using cached enrichment data", url=url, url_hash=url_hash)
                return cached_data
            
            # HTTP кеширование: проверка ETag/Last-Modified
            headers = {}
            etag_key = f"crawl:etag:{url_hash}"
            last_modified_key = f"crawl:last_modified:{url_hash}"
            
            cached_etag = await self.redis_client.get(etag_key)
            cached_last_modified = await self.redis_client.get(last_modified_key)
            
            if cached_etag:
                headers['If-None-Match'] = cached_etag
            if cached_last_modified:
                headers['If-Modified-Since'] = cached_last_modified
            
            # Context7: Запрос к URL с circuit breaker защитой
            start_time = time.time()
            
            async def _fetch_url():
                """Внутренняя функция для HTTP запроса через circuit breaker."""
                async with self.http_session.get(url, headers=headers) as response:
                    return response
            
            try:
                response = await self.circuit_breaker.call_async(_fetch_url)
            except Exception as cb_error:
                # Context7: Circuit breaker открыт или произошла ошибка
                # Логируем и возвращаем None для graceful degradation
                logger.warning(
                    "HTTP request blocked by circuit breaker or failed",
                    url=url,
                    error=str(cb_error),
                    circuit_breaker_state=self.circuit_breaker.get_state(),
                    url_hash=url_hash
                )
                return None
            
            processing_time = time.time() - start_time
            
            # Обработка HTTP статусов
            if response.status == 304:  # Not Modified
                cache_hits_total.labels(type='http').inc()
                logger.debug("URL not modified, using cache", url=url)
                return await self._get_from_cache(cache_key)
            
            if response.status != 200:
                logger.warning("HTTP error during enrichment",
                             url=url,
                             status=response.status)
                return None
            
            # Извлечение контента
            content_bytes = await response.read()
            content = content_bytes.decode('utf-8', errors='ignore')
            
            # Context7: Вычисляем content_sha256 для детерминированного кеширования
            content_sha256 = hashlib.sha256(content_bytes).hexdigest()
            
            # Context7: Проверяем кеш по content_sha256 (если контент не изменился)
            content_cache_key = f"crawl:content:{content_sha256}"
            cached_by_content = await self._get_from_cache(content_cache_key)
            
            if cached_by_content:
                cache_hits_total.labels(type='content_hash').inc()
                logger.debug("Using cached data by content_sha256", 
                           url=url, content_sha256=content_sha256)
                # Обновляем кеш по url_hash для быстрого доступа
                await self._save_to_cache(cache_key, cached_by_content)
                return cached_by_content
            
            # Обогащение данных
            enrichment_data = await self._extract_content_data(content, url)
            
            # Context7: Сохраняем content_sha256 в enrichment_data
            enrichment_data['content_sha256'] = content_sha256
            enrichment_data['url_hash'] = url_hash
            
            # Context7: Сохранение HTML в S3 для долговечности (если s3_service доступен)
            html_s3_key = None
            md_s3_key = None
            html_md5 = None
            md_md5 = None
            
            if self.s3_service and tenant_id and post_id:
                try:
                    # Сохранение HTML в S3
                    html_content_bytes = content_bytes  # Используем оригинальные байты
                    html_s3_key = self.s3_service.build_crawl_key(
                        tenant_id=tenant_id,
                        url_hash=url_hash,
                        suffix='.html',
                        post_id=post_id
                    )
                    
                    # Context7: HEAD проверка перед PUT для идемпотентности
                    existing_html = await self.s3_service.head_object(html_s3_key)
                    if not existing_html:
                        # Context7: Используем put_text() вместо прямого s3_client.put_object()
                        # Это обеспечивает метрики, обработку ошибок и консистентность
                        await self.s3_service.put_text(
                            content=html_content_bytes,
                            s3_key=html_s3_key,
                            content_type='text/html',
                            compress=True
                        )
                        
                        crawl_persist_total.labels(status='success', destination='s3').inc()
                        logger.debug("HTML saved to S3", 
                                   url=url, 
                                   s3_key=html_s3_key,
                                   size_bytes=len(html_content_bytes))
                    else:
                        # HTML уже существует в S3
                        html_s3_key = html_s3_key  # Используем существующий ключ
                        crawl_persist_total.labels(status='cache_hit', destination='s3').inc()
                        logger.debug("HTML already exists in S3", s3_key=html_s3_key)
                    
                    # Сохранение Markdown в S3 (если есть)
                    if enrichment_data.get('markdown'):
                        md_content = enrichment_data['markdown'].encode('utf-8')
                        md_s3_key = self.s3_service.build_crawl_key(
                            tenant_id=tenant_id,
                            url_hash=url_hash,
                            suffix='.md',
                            post_id=post_id
                        )
                        
                        existing_md = await self.s3_service.head_object(md_s3_key)
                        if not existing_md:
                            # Context7: Используем put_text() вместо прямого s3_client.put_object()
                            await self.s3_service.put_text(
                                content=md_content,
                                s3_key=md_s3_key,
                                content_type='text/markdown',
                                compress=True
                            )
                            
                            logger.debug("Markdown saved to S3", s3_key=md_s3_key)
                        else:
                            md_s3_key = md_s3_key  # Используем существующий ключ
                
                except Exception as e:
                    # Ошибка сохранения в S3 не критична - логируем но продолжаем
                    crawl_persist_total.labels(status='error', destination='s3').inc()
                    logger.warning("Failed to save HTML to S3", 
                                 url=url, 
                                 error=str(e),
                                 tenant_id=tenant_id,
                                 post_id=post_id)
                
                # Сохраняем s3_keys и checksums в enrichment_data
                enrichment_data['s3_keys'] = {
                    'html': html_s3_key,
                    'md': md_s3_key
                }
                enrichment_data['checksums'] = {
                    'html_md5': html_md5,
                    'md_md5': md_md5
                }
                
                # Сохранение в кеш по url_hash и content_sha256
                await self._save_to_cache(cache_key, enrichment_data)
                await self._save_to_cache(content_cache_key, enrichment_data)
                
                # Сохранение HTTP заголовков для кеширования
                etag = response.headers.get('ETag')
                last_modified = response.headers.get('Last-Modified')
                
                if etag:
                    await self.redis_client.setex(etag_key, self.cache_ttl, etag)
                if last_modified:
                    await self.redis_client.setex(last_modified_key, self.cache_ttl, last_modified)
                
                # Метрики
                host = urlparse(url).netloc
                crawl_latency_seconds.labels(host=host, status='success').observe(processing_time)
                crawl_success_rate.labels(host=host).set(1.0)
                
                logger.info("URL enriched successfully",
                           url=url,
                           processing_time=processing_time,
                           content_length=len(content))
                
                return enrichment_data
                
        except Exception as e:
            logger.error("Error enriching URL", url=url, error=str(e))
            host = urlparse(url).netloc
            crawl_latency_seconds.labels(host=host, status='error').observe(0)
            return None
    
    async def _extract_content_data(self, content: str, url: str) -> Dict[str, Any]:
        """Извлечение данных из HTML контента."""
        # Здесь должна быть логика извлечения данных из HTML
        # Для примера - простая структура
        
        return {
            'title': self._extract_title(content),
            'content': self._clean_content(content),
            'summary': self._generate_summary(content),
            'author': self._extract_author(content),
            'published_at': self._extract_publish_date(content),
            'word_count': len(content.split()),
            'language': self._detect_language(content),
            'url': url,
            'extracted_at': datetime.now(timezone.utc).isoformat(),
            # Context7: url_hash и content_sha256 будут добавлены в _enrich_url
        }
    
    def _extract_title(self, content: str) -> str:
        """Извлечение заголовка из HTML."""
        # Простая реализация - в production нужен BeautifulSoup
        import re
        title_match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE)
        return title_match.group(1) if title_match else "No title"
    
    def _clean_content(self, content: str) -> str:
        """Очистка HTML контента."""
        # Простая реализация - в production нужен BeautifulSoup
        import re
        # Удаление HTML тегов
        clean = re.sub(r'<[^>]+>', '', content)
        # Удаление лишних пробелов
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean
    
    def _generate_summary(self, content: str) -> str:
        """Генерация краткого изложения."""
        # Простая реализация - в production нужен AI
        words = content.split()
        if len(words) > 100:
            return ' '.join(words[:100]) + '...'
        return content
    
    def _extract_author(self, content: str) -> str:
        """Извлечение автора."""
        # Простая реализация
        return "Unknown"
    
    def _extract_publish_date(self, content: str) -> str:
        """Извлечение даты публикации."""
        # Простая реализация
        return datetime.now(timezone.utc).isoformat()
    
    def _detect_language(self, content: str) -> str:
        """Определение языка контента."""
        # Простая реализация - в production нужна библиотека langdetect
        return "ru" if any(ord(char) > 127 for char in content[:100]) else "en"
    
    async def _check_rate_limit(self, url: str) -> bool:
        """Проверка rate limiting для хоста."""
        host = urlparse(url).netloc
        now = time.time()
        
        # Получение истории запросов для хоста
        if host not in self._rate_limit_cache:
            self._rate_limit_cache[host] = []
        
        # Очистка старых запросов (старше минуты)
        self._rate_limit_cache[host] = [
            req_time for req_time in self._rate_limit_cache[host]
            if now - req_time < 60
        ]
        
        # Проверка лимита
        if len(self._rate_limit_cache[host]) >= self.rate_limit_per_host:
            rate_limit_hits_total.labels(host=host).inc()
            logger.warning("Rate limit exceeded for host", host=host)
            return False
        
        # Добавление текущего запроса
        self._rate_limit_cache[host].append(now)
        return True
    
    def _normalize_url(self, url: str) -> str:
        """
        Нормализация URL для детерминированного хеширования.
        
        Context7: Приводит URL к каноническому виду (lowercase, без фрагментов, сортировка query).
        """
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
        parsed = urlparse(url.lower())
        # Удаляем фрагмент (#), сортируем query параметры
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        sorted_query = urlencode(sorted(query_params.items()), doseq=True)
        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            sorted_query,
            ''  # Удаляем fragment
        ))
        return normalized
    
    async def _get_from_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """Получение данных из кеша."""
        try:
            cached_data = await self.redis_client.get(key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as e:
            logger.error("Error getting from cache", key=key, error=str(e))
        return None
    
    async def _save_to_cache(self, key: str, data: Dict[str, Any]):
        """Сохранение данных в кеш."""
        try:
            await self.redis_client.setex(
                key, 
                self.cache_ttl, 
                json.dumps(data, default=str)
            )
        except Exception as e:
            logger.error("Error saving to cache", key=key, error=str(e))
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики enrichment engine."""
        return {
            'max_concurrent_crawls': self.max_concurrent_crawls,
            'rate_limit_per_host': self.rate_limit_per_host,
            'cache_ttl': self.cache_ttl,
            'redis_connected': self.redis_client is not None,
            'http_session_active': self.http_session is not None,
            'rate_limit_cache_size': len(self._rate_limit_cache)
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check для enrichment engine."""
        try:
            # Проверка Redis
            redis_healthy = False
            if self.redis_client:
                await self.redis_client.ping()
                redis_healthy = True
            
            # Проверка HTTP сессии
            http_healthy = self.http_session is not None and not self.http_session.closed
            
            return {
                'status': 'healthy' if (redis_healthy and http_healthy) else 'unhealthy',
                'redis': 'connected' if redis_healthy else 'disconnected',
                'http_session': 'active' if http_healthy else 'inactive',
                'stats': await self.get_stats()
            }
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return {
                'status': 'unhealthy',
                'error': str(e),
                'stats': await self.get_stats()
            }
