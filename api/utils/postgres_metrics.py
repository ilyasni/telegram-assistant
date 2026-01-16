"""
PostgreSQL Metrics Module
=========================

Prometheus метрики для мониторинга операций PostgreSQL.
Context7 best practices для observability.
"""

from prometheus_client import Counter, Histogram, Gauge, REGISTRY
from functools import wraps
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
                def set(self, value):
                    pass
            return MockMetric()
        raise

# Метрики операций
postgres_operations_total = _safe_create_metric(
    Counter,
    'postgres_operations_total',
    'Total PostgreSQL operations',
    ['operation', 'status']  # select, insert, update, delete, success, error
)

postgres_operation_duration_seconds = _safe_create_metric(
    Histogram,
    'postgres_operation_duration_seconds',
    'PostgreSQL operation duration',
    ['operation'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
)

# Метрики подключений
postgres_connections_active = _safe_create_metric(
    Gauge,
    'postgres_connections_active',
    'Active PostgreSQL connections',
    ['database']
)

postgres_connections_max = _safe_create_metric(
    Gauge,
    'postgres_connections_max',
    'Maximum PostgreSQL connections',
    ['database']
)

# ============================================================================
# DECORATORS
# ============================================================================

def measure_postgres_operation(operation: str):
    """
    Декоратор для измерения операций PostgreSQL.
    
    Args:
        operation: Тип операции (select, insert, update, delete, etc.)
    
    Usage:
        @measure_postgres_operation('upsert_post')
        async def upsert_post(conn, data):
            await conn.execute(...)
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                postgres_operations_total.labels(operation=operation, status='success').inc()
                return result
            except Exception as e:
                postgres_operations_total.labels(operation=operation, status='error').inc()
                raise
            finally:
                duration = time.time() - start_time
                postgres_operation_duration_seconds.labels(operation=operation).observe(duration)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                postgres_operations_total.labels(operation=operation, status='success').inc()
                return result
            except Exception as e:
                postgres_operations_total.labels(operation=operation, status='error').inc()
                raise
            finally:
                duration = time.time() - start_time
                postgres_operation_duration_seconds.labels(operation=operation).observe(duration)
        
        # Определяем, является ли функция async
        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def update_connection_metrics(pool, database_name: str = "default"):
    """
    Обновление метрик подключений для asyncpg pool.
    
    Args:
        pool: asyncpg.Pool instance
        database_name: Имя базы данных для labels
    """
    try:
        if hasattr(pool, '_size') and hasattr(pool, '_maxsize'):
            active = pool._size
            max_conn = pool._maxsize
            postgres_connections_active.labels(database=database_name).set(active)
            postgres_connections_max.labels(database=database_name).set(max_conn)
    except Exception as e:
        logger.debug("Failed to update connection metrics", error=str(e))

def update_connection_metrics_sync(connection, database_name: str = "default"):
    """
    Обновление метрик подключений для sync connection (psycopg2).
    
    Args:
        connection: psycopg2 connection instance
        database_name: Имя базы данных для labels
    """
    try:
        # Для psycopg2 сложнее получить информацию о пуле
        # Используем простую проверку статуса подключения
        if connection and not connection.closed:
            # Устанавливаем метрику как 1, если подключение активно
            # Для более точных метрик нужно использовать connection pool manager
            postgres_connections_active.labels(database=database_name).set(1)
    except Exception as e:
        logger.debug("Failed to update connection metrics", error=str(e))

