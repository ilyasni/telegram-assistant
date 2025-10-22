"""Health check роутер."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
import redis
import structlog
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
    try:
        redis_client = redis.from_url(settings.redis_url)
        redis_client.ping()
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
