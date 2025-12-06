"""
API Metrics Middleware
======================

Prometheus метрики для API endpoints.
Context7 best practices для observability.
"""

from prometheus_client import Counter, Histogram, REGISTRY
from fastapi import Request
import time
import structlog

logger = structlog.get_logger()

# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

# Context7: Безопасное создание метрик для избежания дублирования
def _safe_create_metric(metric_class, name, *args, **kwargs):
    """Создание метрики с проверкой на дублирование."""
    try:
        return metric_class(name, *args, **kwargs)
    except ValueError as e:
        if 'Duplicated' in str(e) or 'already registered' in str(e).lower():
            try:
                if hasattr(REGISTRY, '_names_to_collectors'):
                    existing = REGISTRY._names_to_collectors.get(name)
                    if existing:
                        logger.debug(f"Found existing metric {name} in REGISTRY, reusing", metric=name)
                        return existing
            except (AttributeError, KeyError, TypeError):
                pass
            logger.warning(f"Metric {name} exists but could not retrieve from REGISTRY, using mock", metric=name)
            class MockMetric:
                def labels(self, **kwargs):
                    return self
                def inc(self, value=1):
                    pass
                def observe(self, value):
                    pass
            return MockMetric()
        raise

# Метрики API endpoints
api_endpoint_requests_total = _safe_create_metric(
    Counter,
    'api_endpoint_requests_total',
    'Total API endpoint requests',
    ['endpoint', 'method', 'status_code']
)

api_endpoint_latency_seconds = _safe_create_metric(
    Histogram,
    'api_endpoint_latency_seconds',
    'API endpoint latency',
    ['endpoint', 'method', 'status_code'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

# ============================================================================
# MIDDLEWARE
# ============================================================================

async def metrics_middleware(request: Request, call_next):
    """
    Middleware для измерения latency и requests всех API endpoints.
    
    Context7: Используется для детального мониторинга API производительности.
    """
    start_time = time.time()
    
    # Пропускаем запросы к метрикам и health checks
    if request.url.path in ['/metrics', '/health', '/health/auth', '/health/bot']:
        return await call_next(request)
    
    response = await call_next(request)
    duration = time.time() - start_time
    
    # Нормализация endpoint (убираем параметры из пути)
    endpoint = request.url.path
    method = request.method
    status_code = response.status_code
    
    # Context7: Обновление метрик
    api_endpoint_requests_total.labels(
        endpoint=endpoint,
        method=method,
        status_code=status_code
    ).inc()
    
    api_endpoint_latency_seconds.labels(
        endpoint=endpoint,
        method=method,
        status_code=status_code
    ).observe(duration)
    
    return response

