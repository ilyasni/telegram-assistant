"""Health check роутер."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
import redis.asyncio as redis
import structlog
import time
import asyncio
from typing import Dict, Any
from config import settings
from prometheus_client import Counter, Histogram, Gauge

# Context7: Импорт кэша и circuit breaker
from utils.health_cache import get_health_cache
from shared.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

router = APIRouter()
logger = structlog.get_logger()

# Context7: Метрики Prometheus для health checks
health_check_duration_seconds = Histogram(
    'health_check_duration_seconds',
    'Duration of health check execution in seconds',
    ['check_type'],  # check_type: database, redis, scheduler
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0)
)

health_check_failures_total = Counter(
    'health_check_failures_total',
    'Total health check failures',
    ['check_type', 'error_type']  # error_type: timeout, exception, circuit_open
)

health_check_cache_hits_total = Counter(
    'health_check_cache_hits_total',
    'Total health check cache hits',
    ['check_type']
)

# Context7: Circuit breakers для зависимостей
_db_circuit_breaker: CircuitBreaker = None
_redis_circuit_breaker: CircuitBreaker = None


def get_db_circuit_breaker() -> CircuitBreaker:
    """Получить circuit breaker для БД."""
    global _db_circuit_breaker
    if _db_circuit_breaker is None:
        _db_circuit_breaker = CircuitBreaker(
            name="health_check_database",
            failure_threshold=5,
            recovery_timeout=60,
            expected_exception=Exception
        )
    return _db_circuit_breaker


def get_redis_circuit_breaker() -> CircuitBreaker:
    """Получить circuit breaker для Redis."""
    global _redis_circuit_breaker
    if _redis_circuit_breaker is None:
        _redis_circuit_breaker = CircuitBreaker(
            name="health_check_redis",
            failure_threshold=5,
            recovery_timeout=60,
            expected_exception=Exception
        )
    return _redis_circuit_breaker


@router.get("/")
async def health_check_root():
    """Поддержка старого пути /api/."""
    return await health_check()


async def _check_database() -> Dict[str, Any]:
    """
    Проверка базы данных с таймаутом и circuit breaker.
    
    Context7: Использует circuit breaker и таймауты для защиты от каскадных сбоев.
    """
    start_time = time.time()
    check_type = "database"
    circuit_breaker = get_db_circuit_breaker()
    
    def _db_check_sync():
        """Синхронная проверка БД."""
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        return {"status": "healthy"}
    
    try:
        # Context7: Таймаут 10 секунд для БД проверки
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: circuit_breaker.call_sync(_db_check_sync)
            ),
            timeout=10.0
        )
        duration = time.time() - start_time
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        return result
        
    except CircuitBreakerOpenError:
        duration = time.time() - start_time
        health_check_failures_total.labels(check_type=check_type, error_type="circuit_open").inc()
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        logger.warning("Database circuit breaker is OPEN")
        return {"status": "unhealthy", "reason": "circuit_breaker_open"}
    
    except asyncio.TimeoutError:
        duration = time.time() - start_time
        health_check_failures_total.labels(check_type=check_type, error_type="timeout").inc()
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        logger.error("Database health check timeout", duration_seconds=round(duration, 3))
        return {"status": "unhealthy", "reason": "timeout"}
    except Exception as e:
        duration = time.time() - start_time
        health_check_failures_total.labels(check_type=check_type, error_type="exception").inc()
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        logger.error("Database health check failed", error=str(e), error_type=type(e).__name__)
        return {"status": "unhealthy", "reason": str(e)}


async def _check_redis() -> Dict[str, Any]:
    """
    Проверка Redis с таймаутом и circuit breaker.
    
    Context7: Использует circuit breaker и таймауты для защиты от каскадных сбоев.
    """
    start_time = time.time()
    check_type = "redis"
    
    circuit_breaker = get_redis_circuit_breaker()
    
    async def _redis_check():
        """Асинхронная проверка Redis."""
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
        await redis_client.aclose()
        return {"status": "healthy"}
    
    try:
        # Context7: Таймаут 5 секунд для Redis проверки
        result = await asyncio.wait_for(
            circuit_breaker.call_async(_redis_check),
            timeout=5.0
        )
        duration = time.time() - start_time
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        return result
        
    except CircuitBreakerOpenError:
        duration = time.time() - start_time
        health_check_failures_total.labels(check_type=check_type, error_type="circuit_open").inc()
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        logger.warning("Redis circuit breaker is OPEN")
        return {"status": "unhealthy", "reason": "circuit_breaker_open"}
    
    except asyncio.TimeoutError:
        duration = time.time() - start_time
        health_check_failures_total.labels(check_type=check_type, error_type="timeout").inc()
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        logger.error("Redis health check timeout", duration_seconds=round(duration, 3))
        return {"status": "unhealthy", "reason": "timeout"}
    except Exception as e:
        duration = time.time() - start_time
        health_check_failures_total.labels(check_type=check_type, error_type="exception").inc()
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        logger.error("Redis health check failed", error=str(e), error_type=type(e).__name__)
        return {"status": "unhealthy", "reason": str(e)}


async def _check_scheduler() -> Dict[str, Any]:
    """
    Проверка статуса scheduler.
    
    Context7: Проверка статуса scheduler без таймаута (быстрая локальная проверка).
    """
    check_type = "scheduler"
    start_time = time.time()
    
    try:
        from tasks.scheduler_tasks import scheduler
        if scheduler:
            jobs = scheduler.get_jobs() if scheduler.running else []
            result = {
                "running": scheduler.running,
                "jobs_count": len(jobs),
                "job_ids": [job.id for job in jobs] if jobs else []
            }
        else:
            result = {
                "running": False,
                "jobs_count": 0,
                "error": "Scheduler instance is None"
            }
        
        duration = time.time() - start_time
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        return result
        
    except Exception as e:
        duration = time.time() - start_time
        health_check_failures_total.labels(check_type=check_type, error_type="exception").inc()
        health_check_duration_seconds.labels(check_type=check_type).observe(duration)
        logger.error("Failed to check scheduler status", error=str(e), error_type=type(e).__name__)
        return {
            "running": False,
            "jobs_count": 0,
            "error": str(e)
        }


@router.get("/health")
async def health_check():
    """
    Проверка здоровья сервиса.
    
    Context7: Использует кэширование, circuit breaker и таймауты для повышения стабильности.
    """
    health_status = {
        "status": "healthy",
        "version": "2.0.0",
        "environment": settings.environment,
        "checks": {}
    }
    
    # Context7: Используем кэш для health checks
    cache = get_health_cache()
    
    # Проверка базы данных с кэшированием
    try:
        db_result = await cache.get("database", _check_database, invalidate_on_error=True)
        health_status["checks"]["database"] = db_result.get("status", "unknown")
        if db_result.get("status") == "unhealthy":
            health_status["status"] = "unhealthy"
    except Exception as e:
        logger.error("Failed to check database via cache", error=str(e))
        health_status["checks"]["database"] = "unhealthy"
        health_status["status"] = "unhealthy"
    
    # Проверка Redis с кэшированием
    try:
        redis_result = await cache.get("redis", _check_redis, invalidate_on_error=True)
        health_status["checks"]["redis"] = redis_result.get("status", "unknown")
        if redis_result.get("status") == "unhealthy":
            health_status["status"] = "unhealthy"
    except Exception as e:
        logger.error("Failed to check redis via cache", error=str(e))
        health_status["checks"]["redis"] = "unhealthy"
        health_status["status"] = "unhealthy"
    
    # Проверка scheduler (без кэширования, быстрая проверка)
    try:
        scheduler_result = await _check_scheduler()
        health_status["checks"]["scheduler"] = scheduler_result
        if not scheduler_result.get("running", False):
            if health_status["status"] == "healthy":
                health_status["status"] = "degraded"
    except Exception as e:
        logger.error("Failed to check scheduler", error=str(e))
        health_status["checks"]["scheduler"] = {
            "running": False,
            "jobs_count": 0,
            "error": str(e)
        }
        if health_status["status"] == "healthy":
            health_status["status"] = "degraded"
    
    # Возврат статуса
    if health_status["status"] == "unhealthy":
        raise HTTPException(status_code=503, detail=health_status)
    
    return health_status


@router.get("/ready")
async def readiness_check():
    """Проверка готовности сервиса."""
    # Простая проверка готовности
    return {"status": "ready", "message": "Service is ready to accept requests"}


@router.get("/health/auth")
async def health_auth():
    """Проверка здоровья QR-авторизации."""
    checks = {
        "redis_qr_sessions": False,
        "telethon_service": False
    }
    
    # Проверка Redis
    # Context7 best practice: используем async Redis клиент с decode_responses=True для консистентности
    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        test_key = f"health:check:{int(time.time())}"
        await redis_client.setex(test_key, 10, "ok")
        value = await redis_client.get(test_key)
        # Context7 best practice: с decode_responses=True значения возвращаются как строки
        checks["redis_qr_sessions"] = value == "ok"
        await redis_client.delete(test_key)
        await redis_client.aclose()
    except Exception as e:
        logger.error("Redis QR sessions check failed", error=str(e))
    
    # Проверка telethon-ingest (косвенно, через Redis метрики)
    # Context7 best practice: используем async Redis клиент с decode_responses=True для консистентности
    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        cursor = 0
        cursor, _ = await redis_client.scan(cursor, match="tg:qr:session:*", count=1)
        checks["telethon_service"] = True  # если сканируем, значит сервис работает
        await redis_client.aclose()
    except Exception as e:
        logger.error("Telethon service check failed", error=str(e))
    
    status = "healthy" if all(checks.values()) else "degraded"
    return {"status": status, "checks": checks}
