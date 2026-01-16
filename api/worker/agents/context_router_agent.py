"""
Context Router Agent - универсальный router для определения типа запроса и выбора pipeline.

Performance guardrails:
- Сверхлёгкий router: сначала дешёвая эвристика, LLM только для неочевидных кейсов
- Жёсткий timeout (500-800ms), fallback на старый IntentRouter
- Кэш маршрутов: (tenant_id, normalized_query_signature) → route_type
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID

import structlog
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from prometheus_client import Counter, Histogram, Gauge

from api.worker.tasks.group_digest_agent import load_gigachat_credentials

logger = structlog.get_logger(__name__)

# Метрики (условная регистрация для избежания дублирования)
# Используем try-except при регистрации для обработки дублирования
try:
    router_requests_total = Counter(
        'context_router_requests_total',
        'Total context router requests',
        ['route_type', 'method']  # method: 'heuristic' или 'llm'
    )
except ValueError:
    from prometheus_client import REGISTRY
    router_requests_total = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'context_router_requests_total':
                router_requests_total = collector
                break
    except Exception:
        pass

try:
    router_latency_seconds = Histogram(
        'context_router_latency_seconds',
        'Context router latency',
        ['method']
    )
except ValueError:
    from prometheus_client import REGISTRY
    router_latency_seconds = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'context_router_latency_seconds':
                router_latency_seconds = collector
                break
    except Exception:
        pass

try:
    router_cache_hits_total = Counter(
        'context_router_cache_hits_total',
        'Context router cache hits'
    )
except ValueError:
    from prometheus_client import REGISTRY
    router_cache_hits_total = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'context_router_cache_hits_total':
                router_cache_hits_total = collector
                break
    except Exception:
        pass

try:
    router_cache_size = Gauge(
        'context_router_cache_size',
        'Context router cache size'
    )
except ValueError:
    from prometheus_client import REGISTRY
    router_cache_size = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'context_router_cache_size':
                router_cache_size = collector
                break
    except Exception:
        pass

# Конфигурация
ROUTER_TIMEOUT_MS = int(os.getenv("CONTEXT_ROUTER_TIMEOUT_MS", "800"))
ROUTER_CACHE_TTL_SECONDS = int(os.getenv("CONTEXT_ROUTER_CACHE_TTL_SECONDS", "3600"))  # 1 час
ROUTER_CACHE_MAX_SIZE = int(os.getenv("CONTEXT_ROUTER_CACHE_MAX_SIZE", "10000"))


class RouteResult:
    """Результат маршрутизации."""
    
    def __init__(
        self,
        route_type: str,
        confidence: float,
        reasoning: str,
        method: str = "heuristic",  # 'heuristic' или 'llm'
    ):
        self.route_type = route_type
        self.confidence = confidence
        self.reasoning = reasoning
        self.method = method


class ContextRouterAgent:
    """Универсальный router agent для определения типа запроса."""
    
    # Эвристические паттерны для быстрой маршрутизации (без LLM)
    HEURISTIC_PATTERNS = {
        "digest": [
            r"\b(дайджест|digest|итоги|сводка|резюме|summary)\b",
            r"\b(что\s+было|что\s+произошло|новости\s+за)\b",
        ],
        "trend": [
            r"\b(тренд|trend|тренды|популярн|горяч|viral)\b",
            r"\b(что\s+в\s+тренде|что\s+набирает)\b",
        ],
        "admin": [
            r"^/(admin|settings|config|настройки|админ)",
            r"\b(управление|настройка|конфигурация)\b",
        ],
        "enrichment": [
            r"\b(обогати|enrich|дополни|подробнее\s+о)\b",
        ],
    }
    
    def __init__(self):
        self._cache: Dict[str, Tuple[RouteResult, float]] = {}  # key -> (result, timestamp)
        self._llm_chain = None
        self._gigachat_credentials = None
        self._init_llm()
    
    def _init_llm(self):
        """Инициализация LLM для сложных случаев."""
        try:
            self._gigachat_credentials = load_gigachat_credentials()
            self._llm_chain = self._create_router_chain()
        except Exception as exc:
            logger.warning("context_router.llm_init_failed", error=str(exc))
            self._gigachat_credentials = None
            self._llm_chain = None
    
    def _create_router_chain(self) -> Optional[Any]:
        """Создать LLM chain для маршрутизации."""
        if not self._gigachat_credentials:
            return None
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты - маршрутизатор запросов для Telegram Assistant.

Определи тип запроса пользователя и верни один из вариантов:
- "digest" - запрос на дайджест/сводку
- "qna" - вопрос-ответ через RAG
- "trend" - запрос на тренды
- "enrichment" - обогащение контента
- "search" - поиск по контенту
- "admin" - административные команды

Верни только одно слово - тип запроса."""),
            ("human", "Запрос: {query}"),
        ])
        
        try:
            from langchain_gigachat import GigaChat
            llm = GigaChat(
                credentials=self._gigachat_credentials.get("credentials"),
                scope=self._gigachat_credentials.get("scope", "GIGACHAT_API_PERS"),
                base_url=self._gigachat_credentials.get("base_url"),
                verify_ssl_certs=self._gigachat_credentials.get("verify_ssl_certs", False),
                model="GigaChat-Pro",
                temperature=0.1,
                timeout=ROUTER_TIMEOUT_MS / 1000.0,  # timeout в секундах
            )
            return prompt | llm | StrOutputParser()
        except Exception as exc:
            logger.warning("context_router.chain_creation_failed", error=str(exc))
            return None
    
    def _normalize_query(self, query: str) -> str:
        """Нормализация запроса для кэширования."""
        # Удаляем персонализированные части, приводим к нижнему регистру
        normalized = query.lower().strip()
        # Удаляем множественные пробелы
        normalized = re.sub(r'\s+', ' ', normalized)
        # Обрезаем до разумной длины
        normalized = normalized[:200]
        return normalized
    
    def _get_cache_key(self, tenant_id: str, query: str) -> str:
        """Получить ключ кэша."""
        normalized = self._normalize_query(query)
        signature = hashlib.sha256(f"{tenant_id}:{normalized}".encode()).hexdigest()[:16]
        return f"router:{tenant_id}:{signature}"
    
    def _get_from_cache(self, key: str) -> Optional[RouteResult]:
        """Получить результат из кэша."""
        if key not in self._cache:
            return None
        
        result, timestamp = self._cache[key]
        age = time.time() - timestamp
        
        if age > ROUTER_CACHE_TTL_SECONDS:
            # Истёк TTL, удаляем из кэша
            del self._cache[key]
            return None
        
        if router_cache_hits_total:
            router_cache_hits_total.inc()
        return result
    
    def _set_cache(self, key: str, result: RouteResult):
        """Сохранить результат в кэш."""
        # Очистка кэша при превышении размера (LRU)
        if len(self._cache) >= ROUTER_CACHE_MAX_SIZE:
            # Удаляем самые старые записи
            sorted_items = sorted(self._cache.items(), key=lambda x: x[1][1])
            items_to_remove = len(sorted_items) - ROUTER_CACHE_MAX_SIZE + 1
            for i in range(items_to_remove):
                del self._cache[sorted_items[i][0]]
        
        self._cache[key] = (result, time.time())
        if router_cache_size:
            router_cache_size.set(len(self._cache))
    
    def _heuristic_route(self, query: str) -> Optional[RouteResult]:
        """
        Быстрая эвристическая маршрутизация без LLM.
        
        Performance: Используется для очевидных случаев, избегая LLM-вызовов.
        """
        query_lower = query.lower()
        
        # Проверка паттернов по приоритету
        for route_type, patterns in self.HEURISTIC_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, query_lower, re.IGNORECASE):
                    return RouteResult(
                        route_type=route_type,
                        confidence=0.9,  # Высокая уверенность для эвристики
                        reasoning=f"Matched pattern: {pattern}",
                        method="heuristic"
                    )
        
        # Проверка команд (начинаются с /)
        if query.strip().startswith("/"):
            command = query.strip().split()[0].lower()
            if command in ["/digest", "/дайджест", "/summary"]:
                return RouteResult("digest", 0.95, "Command match", "heuristic")
            if command in ["/trend", "/тренд", "/trends"]:
                return RouteResult("trend", 0.95, "Command match", "heuristic")
            if command.startswith("/admin") or command.startswith("/settings"):
                return RouteResult("admin", 0.95, "Command match", "heuristic")
        
        return None
    
    def _llm_route(self, query: str, tenant_id: str) -> RouteResult:
        """
        Маршрутизация через LLM для неочевидных случаев.
        
        Performance: Используется только если эвристика не сработала.
        Timeout: 500-800ms, fallback на 'qna' при ошибке.
        """
        if not self._llm_chain:
            # Fallback на qna, если LLM недоступен
            return RouteResult("qna", 0.5, "LLM unavailable, defaulting to qna", "llm")
        
        start_time = time.time()
        try:
            if router_latency_seconds:
                with router_latency_seconds.labels(method="llm").time():
                    result_text = self._llm_chain.invoke({"query": query})
                    latency = time.time() - start_time
            else:
                result_text = self._llm_chain.invoke({"query": query})
                latency = time.time() - start_time
                
                # Парсинг результата
                route_type = result_text.strip().lower()
                valid_routes = {"digest", "qna", "trend", "enrichment", "search", "admin"}
                
                if route_type not in valid_routes:
                    logger.warning(
                        "context_router.invalid_route",
                        route_type=route_type,
                        query=query[:100]
                    )
                    route_type = "qna"  # Fallback
                
                return RouteResult(
                    route_type=route_type,
                    confidence=0.7,  # Средняя уверенность для LLM
                    reasoning=f"LLM classification (latency: {latency:.3f}s)",
                    method="llm"
                )
        except Exception as exc:
            latency = time.time() - start_time
            logger.warning(
                "context_router.llm_failed",
                error=str(exc),
                latency=latency,
                query=query[:100]
            )
            # Fallback на qna при ошибке
            return RouteResult("qna", 0.3, f"LLM error: {str(exc)[:100]}", "llm")
    
    def route(
        self,
        query: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        recent_events: Optional[List[Dict[str, Any]]] = None,
    ) -> RouteResult:
        """
        Определить тип запроса и выбрать pipeline.
        
        Performance guardrails:
        1. Проверка кэша
        2. Эвристическая маршрутизация (быстро, без LLM)
        3. LLM маршрутизация (только для неочевидных случаев, с timeout)
        
        Args:
            query: Запрос пользователя
            tenant_id: ID тенанта
            user_id: ID пользователя (опционально)
            recent_events: Последние события из episodic memory (опционально)
        
        Returns:
            RouteResult с типом маршрута, уверенностью и методом
        """
        start_time = time.time()
        
        # 1. Проверка кэша
        cache_key = self._get_cache_key(tenant_id, query)
        cached_result = self._get_from_cache(cache_key)
        if cached_result:
            if router_requests_total:
                router_requests_total.labels(route_type=cached_result.route_type, method="cache").inc()
            return cached_result
        
        # 2. Эвристическая маршрутизация (быстро, без LLM)
        heuristic_result = self._heuristic_route(query)
        if heuristic_result:
            self._set_cache(cache_key, heuristic_result)
            if router_requests_total:
                router_requests_total.labels(route_type=heuristic_result.route_type, method="heuristic").inc()
            if router_latency_seconds:
                router_latency_seconds.labels(method="heuristic").observe(time.time() - start_time)
            return heuristic_result
        
        # 3. LLM маршрутизация (только для неочевидных случаев)
        llm_result = self._llm_route(query, tenant_id)
        self._set_cache(cache_key, llm_result)
        if router_requests_total:
            router_requests_total.labels(route_type=llm_result.route_type, method="llm").inc()
        if router_latency_seconds:
            router_latency_seconds.labels(method="llm").observe(time.time() - start_time)
        
        return llm_result


# Singleton instance
_context_router: Optional[ContextRouterAgent] = None


def get_context_router() -> ContextRouterAgent:
    """Получить экземпляр ContextRouterAgent."""
    global _context_router
    if _context_router is None:
        _context_router = ContextRouterAgent()
    return _context_router

