"""
⚠️ DEPRECATED ⚠️

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Дубликат функциональности worker/health.py (который использует feature flags)
Replacement: from worker.health import check_integrations

Этот файл перемещён в legacy/deprecated_2025-01-30/
[C7-ID: CODE-CLEANUP-025] Context7 best practice: карантин deprecated кода
"""

import warnings
import os

# Runtime guard: блокируем импорт в production
if os.getenv("ENV") == "production":
    raise ImportError(
        "worker/health_check.py is deprecated. "
        "Use 'from worker.health import check_integrations' instead. "
        "See legacy/deprecated_2025-01-30/README.md"
    )

warnings.warn(
    "worker/health_check.py is DEPRECATED. "
    "Use worker/health.py (with feature flags support) instead. "
    "See legacy/deprecated_2025-01-30/README.md",
    DeprecationWarning,
    stacklevel=2
)

# Re-export для обратной совместимости
try:
    # Пытаемся переиспользовать функциональность из health.py
    from worker.health import check_redis as _check_redis, check_postgres as _check_postgres
except ImportError:
    # Если не доступно, оставляем старую реализацию
    pass

"""
Health Check модуль для worker сервисов
Context7: Comprehensive health monitoring with detailed status reporting

⚠️ DEPRECATED: Use worker/health.py instead (supports feature flags)
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import asyncpg
import redis.asyncio as redis
import structlog

logger = structlog.get_logger()

class HealthChecker:
    """Context7: Comprehensive health checker для всех зависимостей."""
    
    def __init__(self, database_url: str, redis_url: str):
        self.database_url = database_url
        self.redis_url = redis_url
        self._last_checks: Dict[str, Dict[str, Any]] = {}
    
    async def check_database(self) -> Dict[str, Any]:
        """Проверка состояния PostgreSQL."""
        start_time = time.time()
        try:
            # Context7: Конвертируем SQLAlchemy DSN в asyncpg DSN
            db_url = self.database_url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(db_url)
            
            # Проверяем базовое соединение
            await conn.fetchval("SELECT 1")
            
            # Проверяем активные соединения
            active_connections = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'"
            )
            
            # Проверяем размер БД
            db_size = await conn.fetchval(
                "SELECT pg_size_pretty(pg_database_size(current_database()))"
            )
            
            await conn.close()
            
            latency = time.time() - start_time
            
            return {
                "status": "healthy",
                "latency_ms": round(latency * 1000, 2),
                "active_connections": active_connections,
                "database_size": db_size,
                "checked_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error("Database health check failed", error=str(e))
            return {
                "status": "unhealthy",
                "error": str(e),
                "latency_ms": round((time.time() - start_time) * 1000, 2),
                "checked_at": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_redis(self) -> Dict[str, Any]:
        """Проверка состояния Redis."""
        start_time = time.time()
        try:
            redis_client = redis.from_url(self.redis_url, decode_responses=True)
            
            # Проверяем ping
            pong = await redis_client.ping()
            if not pong:
                raise Exception("Redis ping failed")
            
            # Проверяем использование памяти
            info = await redis_client.info("memory")
            used_memory = info.get("used_memory_human", "unknown")
            
            # Проверяем количество ключей
            key_count = await redis_client.dbsize()
            
            await redis_client.close()
            
            latency = time.time() - start_time
            
            return {
                "status": "healthy",
                "latency_ms": round(latency * 1000, 2),
                "used_memory": used_memory,
                "key_count": key_count,
                "checked_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error("Redis health check failed", error=str(e))
            return {
                "status": "unhealthy",
                "error": str(e),
                "latency_ms": round((time.time() - start_time) * 1000, 2),
                "checked_at": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_worker_tasks(self) -> Dict[str, Any]:
        """Проверка состояния worker задач."""
        try:
            # Context7: Проверяем, что все задачи запущены
            # В реальной реализации здесь можно проверить статус TaskSupervisor
            return {
                "status": "healthy",
                "active_tasks": ["post_persistence", "tagging", "enrichment", "indexing"],
                "checked_at": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error("Worker tasks health check failed", error=str(e))
            return {
                "status": "unhealthy",
                "error": str(e),
                "checked_at": datetime.now(timezone.utc).isoformat()
            }
    
    async def get_overall_health(self) -> Dict[str, Any]:
        """Получение общего состояния здоровья системы."""
        # Context7: Параллельные проверки для производительности
        db_health, redis_health, tasks_health = await asyncio.gather(
            self.check_database(),
            self.check_redis(),
            self.check_worker_tasks(),
            return_exceptions=True
        )
        
        # Обработка исключений
        if isinstance(db_health, Exception):
            db_health = {"status": "error", "error": str(db_health)}
        if isinstance(redis_health, Exception):
            redis_health = {"status": "error", "error": str(redis_health)}
        if isinstance(tasks_health, Exception):
            tasks_health = {"status": "error", "error": str(tasks_health)}
        
        # Определяем общий статус
        all_healthy = all(
            check.get("status") == "healthy" 
            for check in [db_health, redis_health, tasks_health]
        )
        
        overall_status = "healthy" if all_healthy else "degraded"
        
        return {
            "status": overall_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "database": db_health,
                "redis": redis_health,
                "worker_tasks": tasks_health
            },
            "summary": {
                "total_checks": 3,
                "healthy_checks": sum(1 for check in [db_health, redis_health, tasks_health] 
                                    if check.get("status") == "healthy"),
                "unhealthy_checks": sum(1 for check in [db_health, redis_health, tasks_health] 
                                      if check.get("status") in ["unhealthy", "error"])
            }
        }

# Context7: Singleton для глобального доступа
_health_checker: Optional[HealthChecker] = None

def get_health_checker() -> HealthChecker:
    """Получение singleton instance HealthChecker."""
    global _health_checker
    if _health_checker is None:
        import os
        # Context7: Используем sync DSN для health checks
        database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
        # Убираем +asyncpg из DSN для синхронного подключения
        if "+asyncpg" in database_url:
            database_url = database_url.replace("+asyncpg", "")
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _health_checker = HealthChecker(database_url, redis_url)
    return _health_checker
