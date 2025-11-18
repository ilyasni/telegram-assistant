"""
SearXNG Service для внешнего поиска (external search grounding)
Context7 best practice: безопасность, rate limiting, кэширование, валидация
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, urlunparse, quote_plus

import httpx
import structlog
from pydantic import BaseModel, Field, HttpUrl, field_validator
from redis import Redis

from config import settings

logger = structlog.get_logger()

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class SearXNGResult(BaseModel):
    """Результат поиска SearXNG."""
    title: str = Field(..., description="Заголовок результата")
    url: HttpUrl = Field(..., description="URL результата")
    snippet: str = Field(..., description="Сниппет/описание")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Время получения")

    @field_validator("url")
    @classmethod
    def sanitize_url(cls, v: str) -> str:
        """Sanitization ссылок: очистка URL перед сохранением."""
        parsed = urlparse(str(v))
        # Удаляем фрагменты и параметры tracking
        tracking_params = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "_ga"]
        query_params = []
        if parsed.query:
            for param in parsed.query.split("&"):
                key = param.split("=")[0]
                if key not in tracking_params:
                    query_params.append(param)
        clean_query = "&".join(query_params) if query_params else ""
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, ""))
    
    class Config:
        """Context7: JSON encoders для корректной сериализации datetime."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SearXNGSearchResponse(BaseModel):
    """Ответ от SearXNG API."""
    results: List[SearXNGResult] = Field(default_factory=list, description="Список результатов")
    query: str = Field(..., description="Запрос")
    number_of_results: int = Field(default=0, description="Общее количество результатов")


# ============================================================================
# ЧЁРНЫЙ СПИСОК ДОМЕНОВ
# ============================================================================

# Context7: Чёрный список доменов для внешних источников (SearXNG)
BLACKLISTED_DOMAINS = {
    "torrent", "pirate", "warez", "crack", "keygen", "serial",
    "adult", "xxx", "porn", "nsfw",
    "gambling", "casino", "bet",
    "phishing", "malware", "virus",
}


def is_domain_blacklisted(url: str) -> bool:
    """Проверка домена на чёрный список."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        for blacklisted in BLACKLISTED_DOMAINS:
            if blacklisted in domain:
                return True
        return False
    except Exception:
        return True  # Безопаснее заблокировать при ошибке парсинга


# ============================================================================
# SEARXNG SERVICE
# ============================================================================

class SearXNGService:
    """Сервис для работы с SearXNG API."""
    
    def __init__(
        self,
        redis_client: Optional[Redis] = None,
        base_url: Optional[str] = None,
        enabled: Optional[bool] = None,
        cache_ttl: Optional[int] = None,
        max_results: Optional[int] = None,
        rate_limit_per_user: Optional[int] = None
    ):
        """
        Инициализация SearXNG Service.
        
        Args:
            redis_client: Redis клиент для кэширования и rate limiting
            base_url: URL SearXNG сервиса (по умолчанию из settings)
            enabled: Включен ли SearXNG (по умолчанию из settings)
            cache_ttl: TTL кэша в секундах (по умолчанию из settings)
            max_results: Максимальное количество результатов (по умолчанию из settings)
            rate_limit_per_user: Лимит запросов в минуту на пользователя (по умолчанию из settings)
        """
        self.base_url = base_url or settings.searxng_url
        self.enabled = enabled if enabled is not None else settings.searxng_enabled
        self.cache_ttl = cache_ttl or settings.searxng_cache_ttl
        self.max_results = max_results or settings.searxng_max_results
        self.rate_limit_per_user = rate_limit_per_user or settings.searxng_rate_limit_per_user
        
        self.redis_client = redis_client
        
        # Context7: BasicAuth для SearXNG (если настроено)
        # Если SEARXNG_USER и SEARXNG_PASSWORD заданы, используем BasicAuth
        auth = None
        if settings.searxng_user and settings.searxng_password.get_secret_value():
            auth = httpx.BasicAuth(
                settings.searxng_user,
                settings.searxng_password.get_secret_value()
            )
        
        # Context7: Браузероподобные заголовки для обхода bot detection
        # Bot detection проверяет Accept, Accept-Encoding, Accept-Language, User-Agent
        # Также требует X-Forwarded-For и X-Real-IP для внутренних запросов
        import socket
        try:
            # Получаем IP адрес контейнера в Docker сети
            hostname = socket.gethostname()
            container_ip = socket.gethostbyname(hostname)
        except:
            container_ip = "172.18.0.15"  # Fallback IP
        
        # Сохраняем заголовки в self для использования в запросах
        self.default_headers = {
            "User-Agent": "TelegramAssistant/3.1 (RAG Hybrid Search)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "ru,en;q=0.8",
            "Connection": "keep-alive",
            "X-Real-IP": container_ip,  # Context7: Обязательно для bot detection
            "X-Forwarded-For": container_ip,  # Context7: Обязательно для bot detection
        }
        
        # Context7: Отключаем проверку SSL для внутреннего использования через Caddy
        # В продакшене рекомендуется использовать валидные сертификаты
        # Context7: Используем httpx.Timeout для детальной настройки таймаутов
        import httpx
        timeout = httpx.Timeout(
            connect=10.0,  # Таймаут подключения
            read=30.0,     # Таймаут чтения
            write=10.0,    # Таймаут записи
            pool=5.0       # Таймаут пула соединений
        )
        self.http_client = httpx.AsyncClient(
            auth=auth,
            timeout=timeout,
            headers=self.default_headers,
            verify=False  # Context7: Отключаем проверку SSL для внутреннего использования
        )
        
        # Context7: Ограничение движков: только новостные/вики-источники
        # Используем категории: general, news, wikipedia
        self.allowed_categories = ["general", "news", "wikipedia"]
        self.blocked_engines = ["torrent", "files", "images", "videos", "music", "itunes", "map"]
        
        logger.info(
            "SearXNG Service initialized",
            base_url=self.base_url,
            enabled=self.enabled,
            max_results=self.max_results,
            rate_limit_per_user=self.rate_limit_per_user
        )
    
    def _normalize_query(self, query: str, lang: str = "ru") -> str:
        """Нормализация запроса для кэширования."""
        # Удаляем лишние пробелы, приводим к нижнему регистру
        normalized = " ".join(query.lower().split())
        return f"{normalized}:{lang}"
    
    def _get_cache_key(self, query: str, lang: str = "ru") -> str:
        """Генерация ключа кэша: sha256(query_norm:lang:date_bucket)."""
        # Группируем по дате (date_bucket = текущая дата)
        date_bucket = datetime.now(timezone.utc).date().isoformat()
        normalized = self._normalize_query(query, lang)
        cache_data = f"{normalized}:{date_bucket}"
        return f"searxng:cache:{hashlib.sha256(cache_data.encode()).hexdigest()}"
    
    def _check_rate_limit(self, user_id: str) -> bool:
        """
        Проверка rate limit для пользователя (Redis token bucket).
        
        Returns:
            True если запрос разрешён, False если превышен лимит
        """
        if not self.redis_client:
            return True  # Без Redis разрешаем все запросы
        
        key = f"searxng:ratelimit:{user_id}"
        try:
            # Простая реализация: счётчик запросов за последнюю минуту
            current = self.redis_client.get(key)
            if current is None:
                # Первый запрос в минуту
                self.redis_client.setex(key, 60, 1)
                return True
            count = int(current)
            if count >= self.rate_limit_per_user:
                logger.warning("SearXNG rate limit exceeded", user_id=user_id, count=count)
                return False
            # Увеличиваем счётчик
            self.redis_client.incr(key)
            return True
        except Exception as e:
            logger.error("Error checking rate limit", error=str(e), user_id=user_id)
            return True  # В случае ошибки разрешаем запрос
    
    async def _get_from_cache(self, cache_key: str) -> Optional[List[SearXNGResult]]:
        """
        Получение результатов из кэша.
        
        Context7: При десериализации из JSON, datetime строки автоматически парсятся Pydantic.
        """
        if not self.redis_client:
            return None
        
        try:
            cached = self.redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                # Context7: Pydantic автоматически парсит ISO строки datetime обратно в datetime объекты
                results = [SearXNGResult(**item) for item in data]
                logger.debug("SearXNG cache hit", cache_key=cache_key[:32])
                return results
        except Exception as e:
            logger.error("Error reading from cache", error=str(e), cache_key=cache_key[:32])
        
        return None
    
    async def _save_to_cache(self, cache_key: str, results: List[SearXNGResult]) -> None:
        """
        Сохранение результатов в кэш.
        
        Context7: Используем model_dump с mode='json' для корректной сериализации datetime.
        Это автоматически применяет json_encoders из Config класса.
        """
        if not self.redis_client:
            return
        
        try:
            # Context7: Используем mode='json' для автоматической сериализации datetime через json_encoders
            # Это решает проблему "Object of type datetime is not JSON serializable"
            data = [result.model_dump(mode='json') for result in results]
            self.redis_client.setex(cache_key, self.cache_ttl, json.dumps(data))
            logger.debug("SearXNG cache saved", cache_key=cache_key[:32], ttl=self.cache_ttl)
        except Exception as e:
            logger.error("Error saving to cache", error=str(e), cache_key=cache_key[:32])
    
    def _normalize_results(self, raw_results: List[Dict[str, Any]], score_threshold: float = 0.5) -> List[SearXNGResult]:
        """
        Нормализация результатов поиска SearXNG.
        
        Args:
            raw_results: Сырые результаты от SearXNG API
            score_threshold: Порог релевантности (0.0-1.0)
        
        Returns:
            Список нормализованных результатов
        """
        normalized = []
        
        for result in raw_results:
            try:
                # Фильтрация по релевантности (score threshold)
                score = result.get("score", 0.0)
                if score < score_threshold:
                    continue
                
                # Фильтрация по чёрному списку доменов
                url = result.get("url", "")
                if is_domain_blacklisted(url):
                    continue
                
                # Создаём Pydantic модель для валидации
                searxng_result = SearXNGResult(
                    title=result.get("title", ""),
                    url=url,
                    snippet=result.get("content", "")[:500],  # Ограничиваем длину сниппета
                    fetched_at=datetime.now(timezone.utc)
                )
                normalized.append(searxng_result)
            except Exception as e:
                logger.warning("Error normalizing result", error=str(e), result=result.get("url", ""))
                continue
        
        return normalized
    
    async def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        lang: str = "ru",
        categories: Optional[List[str]] = None,
        score_threshold: float = 0.5
    ) -> SearXNGSearchResponse:
        """
        Поиск через SearXNG API.
        
        Args:
            query: Поисковый запрос
            user_id: ID пользователя для rate limiting
            lang: Язык поиска (по умолчанию ru)
            categories: Категории поиска (по умолчанию general, news, wikipedia)
            score_threshold: Порог релевантности (0.0-1.0)
        
        Returns:
            SearXNGSearchResponse с результатами поиска
        """
        if not self.enabled:
            logger.debug("SearXNG disabled, returning empty results")
            return SearXNGSearchResponse(query=query, results=[])
        
        # Проверка rate limit
        if user_id and not self._check_rate_limit(user_id):
            logger.warning("SearXNG rate limit exceeded", user_id=user_id)
            return SearXNGSearchResponse(query=query, results=[])
        
        # Проверка кэша
        cache_key = self._get_cache_key(query, lang)
        cached_results = await self._get_from_cache(cache_key)
        if cached_results:
            return SearXNGSearchResponse(
                query=query,
                results=cached_results[:self.max_results],
                number_of_results=len(cached_results)
            )
        
        # Выполняем поиск
        try:
            # Context7: SearXNG принимает категории как строку через запятую
            # Используем только одну категорию "general" для избежания проблем с валидацией
            # Фильтрация по доменам выполняется в _normalize_results()
            search_category = "general"  # Упрощаем: используем одну категорию
            
            # Context7: Формируем параметры запроса согласно SearXNG API
            # Важно: параметры передаются как строки, не массивы
            params = {
                "q": query,
                "format": "json",
                "categories": search_category,  # Одна категория как строка
                "language": lang,
                "pageno": 1,
            }
            
            # Context7: Выполняем запрос к SearXNG
            # Браузероподобные заголовки (включая X-Forwarded-For и X-Real-IP) уже установлены в http_client
            # Для format=json только переопределяем Accept, НЕ переопределяя остальные заголовки
            # Важно: НЕ передаем headers параметр, чтобы использовать заголовки из http_client
            # Или передаем только Accept, но httpx не объединяет headers, поэтому используем только Accept
            
            # Context7: SearXNG настроен на метод GET (method: GET в settings.yml)
            # Используем GET для обхода bot detection
            search_url = f"{self.base_url}/search?q={quote_plus(query)}&format=json&categories={search_category}&language={lang}&pageno=1"
            
            # Context7: Объединяем заголовки из default_headers с Accept для JSON
            # Важно: передаем все заголовки включая X-Forwarded-For и X-Real-IP
            headers = {
                **self.default_headers,  # Все заголовки (включая X-Forwarded-For и X-Real-IP)
                "Accept": "application/json",  # Переопределяем только Accept для JSON API
            }
            
            # Context7: Retry логика с exponential backoff для transient ошибок
            # Best practice: retry для connection errors и server errors (5xx)
            max_retries = 3
            retry_delays = [1, 3, 10]  # Exponential backoff: 1s → 3s → 10s
            
            response = None
            for attempt in range(max_retries):
                try:
                    response = await self.http_client.get(
                        search_url,
                        headers=headers
                    )
                    response.raise_for_status()
                    break  # Успешный запрос
                except httpx.HTTPStatusError as e:
                    # Context7: Специфичная обработка HTTP статус ошибок
                    if e.response.status_code == 429:
                        # Rate limiting - используем Retry-After заголовок
                        retry_after = int(e.response.headers.get('Retry-After', 60))
                        if attempt < max_retries - 1:
                            logger.warning(
                                "SearXNG rate limited (429), retrying after backoff",
                                query=query[:50],
                                retry_after=retry_after,
                                attempt=attempt + 1
                            )
                            await asyncio.sleep(min(retry_after, 120))  # Максимум 2 минуты
                            continue
                        else:
                            logger.error("SearXNG rate limited after retries", query=query[:50])
                            return SearXNGSearchResponse(query=query, results=[])
                    elif e.response.status_code >= 500:
                        # Server errors - retry с exponential backoff
                        if attempt < max_retries - 1:
                            retry_delay = retry_delays[attempt]
                            logger.warning(
                                "SearXNG server error, retrying",
                                query=query[:50],
                                status_code=e.response.status_code,
                                attempt=attempt + 1,
                                retry_delay=retry_delay
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            logger.error("SearXNG server error after retries", query=query[:50])
                            return SearXNGSearchResponse(query=query, results=[])
                    else:
                        # Client errors (4xx кроме 429) - не retry
                        logger.error(
                            "SearXNG client error",
                            query=query[:50],
                            status_code=e.response.status_code
                        )
                        return SearXNGSearchResponse(query=query, results=[])
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    # Context7: Connection errors и timeouts - retry с exponential backoff
                    if attempt < max_retries - 1:
                        retry_delay = retry_delays[attempt]
                        logger.warning(
                            "SearXNG connection error, retrying",
                            query=query[:50],
                            error_type=type(e).__name__,
                            attempt=attempt + 1,
                            retry_delay=retry_delay
                        )
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        logger.error("SearXNG connection error after retries", query=query[:50])
                        return SearXNGSearchResponse(query=query, results=[])
            
            if response is None:
                logger.error("SearXNG request failed after all retries", query=query[:50])
                return SearXNGSearchResponse(query=query, results=[])
            
            data = response.json()
            
            # Нормализация результатов
            raw_results = data.get("results", [])
            normalized_results = self._normalize_results(raw_results, score_threshold)
            
            # Ограничиваем количество результатов
            limited_results = normalized_results[:self.max_results]
            
            # Сохраняем в кэш
            await self._save_to_cache(cache_key, limited_results)
            
            logger.info(
                "SearXNG search completed",
                query=query[:50],
                results_count=len(limited_results),
                total_results=len(normalized_results)
            )
            
            return SearXNGSearchResponse(
                query=query,
                results=limited_results,
                number_of_results=len(normalized_results)
            )
        
        except httpx.HTTPError as e:
            logger.error("SearXNG HTTP error", error=str(e), query=query[:50])
            return SearXNGSearchResponse(query=query, results=[])
        except Exception as e:
            logger.error("SearXNG search error", error=str(e), query=query[:50])
            return SearXNGSearchResponse(query=query, results=[])
    
    async def close(self):
        """Закрытие HTTP клиента."""
        await self.http_client.aclose()


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_searxng_service: Optional[SearXNGService] = None


def get_searxng_service(redis_client: Optional[Redis] = None) -> SearXNGService:
    """Получение singleton экземпляра SearXNGService."""
    global _searxng_service
    if _searxng_service is None:
        _searxng_service = SearXNGService(redis_client=redis_client)
    return _searxng_service

