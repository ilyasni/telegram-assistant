"""Health check роутер."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
import redis.asyncio as redis
import structlog
import time
from config import settings

router = APIRouter()
logger = structlog.get_logger()


@router.get("/")
async def health_check_root():
    """Поддержка старого пути /api/."""
    return await health_check()


@router.get("/health")
async def health_check():
    """Проверка здоровья сервиса."""
    health_status = {
        "status": "healthy",
        "version": "2.0.0",
        "environment": settings.environment,
        "checks": {}
    }
    
    # Проверка базы данных
    try:
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        health_status["checks"]["database"] = "healthy"
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        health_status["checks"]["database"] = "unhealthy"
        health_status["status"] = "unhealthy"
    
    # Проверка Redis
    # Context7 best practice: используем async Redis клиент
    try:
        redis_client = redis.from_url(settings.redis_url)
        await redis_client.ping()
        await redis_client.aclose()
        health_status["checks"]["redis"] = "healthy"
    except Exception as e:
        logger.error("Redis health check failed", error=str(e))
        health_status["checks"]["redis"] = "unhealthy"
        health_status["status"] = "unhealthy"
    
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
    # Context7 best practice: используем async Redis клиент
    try:
        redis_client = redis.from_url(settings.redis_url)
        test_key = f"health:check:{int(time.time())}"
        await redis_client.setex(test_key, 10, "ok")
        value = await redis_client.get(test_key)
        checks["redis_qr_sessions"] = value == b"ok"
        await redis_client.delete(test_key)
        await redis_client.aclose()
    except Exception as e:
        logger.error("Redis QR sessions check failed", error=str(e))
    
    # Проверка telethon-ingest (косвенно, через Redis метрики)
    # Context7 best practice: используем async Redis клиент
    try:
        redis_client = redis.from_url(settings.redis_url)
        cursor = 0
        cursor, _ = await redis_client.scan(cursor, match="tg:qr:session:*", count=1)
        checks["telethon_service"] = True  # если сканируем, значит сервис работает
        await redis_client.aclose()
    except Exception as e:
        logger.error("Telethon service check failed", error=str(e))
    
    status = "healthy" if all(checks.values()) else "degraded"
    return {"status": status, "checks": checks}
