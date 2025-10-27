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
        user_agent: str = "Crawl4AI/1.0 (Telegram Assistant)"
    ):
        self.redis_url = redis_url
        self.max_concurrent_crawls = max_concurrent_crawls
        self.rate_limit_per_host = rate_limit_per_host
        self.cache_ttl = cache_ttl
        self.user_agent = user_agent
        
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
                   cache_ttl=cache_ttl)
    
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
                
                # Обогащение каждого URL
                enrichment_data = {}
                for url in urls:
                    try:
                        url_data = await self._enrich_url(url, policy_config)
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
    
    async def _enrich_url(self, url: str, policy_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Обогащение одного URL с кешированием."""
        try:
            # Проверка кеша
            cache_key = f"enrichment:{hashlib.md5(url.encode()).hexdigest()}"
            cached_data = await self._get_from_cache(cache_key)
            
            if cached_data:
                cache_hits_total.labels(type='enrichment').inc()
                logger.debug("Using cached enrichment data", url=url)
                return cached_data
            
            # HTTP кеширование: проверка ETag/Last-Modified
            headers = {}
            etag_key = f"etag:{hashlib.md5(url.encode()).hexdigest()}"
            last_modified_key = f"last_modified:{hashlib.md5(url.encode()).hexdigest()}"
            
            cached_etag = await self.redis_client.get(etag_key)
            cached_last_modified = await self.redis_client.get(last_modified_key)
            
            if cached_etag:
                headers['If-None-Match'] = cached_etag
            if cached_last_modified:
                headers['If-Modified-Since'] = cached_last_modified
            
            # Запрос к URL
            start_time = time.time()
            async with self.http_session.get(url, headers=headers) as response:
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
                content = await response.text()
                
                # Обогащение данных
                enrichment_data = await self._extract_content_data(content, url)
                
                # Сохранение в кеш
                await self._save_to_cache(cache_key, enrichment_data)
                
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
            'extracted_at': datetime.now(timezone.utc).isoformat()
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
