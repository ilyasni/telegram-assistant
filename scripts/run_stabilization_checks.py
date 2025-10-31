#!/usr/bin/env python3
"""
Запуск всех проверок стабилизации пайплайна.

Context7 best practice: комплексная проверка event-driven архитектуры,
идемпотентности, и сквозного потока данных.
"""

import asyncio
import os
import sys
import subprocess
import structlog

logger = structlog.get_logger()

async def run_command(cmd: list, description: str) -> bool:
    """Запуск команды и проверка результата."""
    logger.info(f"Running: {description}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"✅ {description} - SUCCESS")
        if result.stdout:
            logger.debug("STDOUT", output=result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ {description} - FAILED", 
                    returncode=e.returncode,
                    stderr=e.stderr)
        return False
    except Exception as e:
        logger.error(f"❌ {description} - ERROR", error=str(e))
        return False

async def check_redis_connection():
    """Проверка подключения к Redis."""
    logger.info("Checking Redis connection...")
    
    try:
        import redis.asyncio as redis
        redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
        await redis_client.ping()
        await redis_client.close()
        logger.info("✅ Redis connection - SUCCESS")
        return True
    except Exception as e:
        logger.error("❌ Redis connection - FAILED", error=str(e))
        return False

async def check_database_connection():
    """Проверка подключения к БД."""
    logger.info("Checking database connection...")
    
    try:
        import asyncpg
        database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
        # Конвертируем SQLAlchemy URL в asyncpg DSN
        dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        
        conn = await asyncpg.connect(dsn)
        await conn.execute("SELECT 1")
        await conn.close()
        logger.info("✅ Database connection - SUCCESS")
        return True
    except Exception as e:
        logger.error("❌ Database connection - FAILED", error=str(e))
        return False

async def create_consumer_groups():
    """Создание consumer groups."""
    return await run_command(
        ["python", "scripts/create_consumer_groups.py", "--create"],
        "Create consumer groups"
    )

async def run_smoke_test():
    """Запуск смоук-теста."""
    return await run_command(
        ["python", "scripts/smoke_test_pipeline.py"],
        "Smoke test pipeline"
    )

async def check_telethon_ingest():
    """Проверка telethon-ingest сервиса."""
    logger.info("Checking telethon-ingest service...")
    
    # Проверка импортов
    try:
        sys.path.insert(0, "telethon-ingest")
        from database import check_sync_connection
        from event_bus_sync import EventBusSync
        
        # Проверка БД
        db_healthy = check_sync_connection()
        
        # Проверка Redis
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        event_bus = EventBusSync(redis_url)
        redis_healthy = event_bus.health_check()
        
        if db_healthy and redis_healthy:
            logger.info("✅ telethon-ingest service - SUCCESS")
            return True
        else:
            logger.error("❌ telethon-ingest service - FAILED", 
                        db_healthy=db_healthy, 
                        redis_healthy=redis_healthy)
            return False
            
    except Exception as e:
        logger.error("❌ telethon-ingest service - ERROR", error=str(e))
        return False

async def check_worker_tasks():
    """Проверка worker tasks."""
    logger.info("Checking worker tasks...")
    
    try:
        sys.path.insert(0, "worker")
        from database import check_async_connection
        from tasks.post_persistence_task import PostPersistenceTask
        
        # Проверка БД
        db_healthy = await check_async_connection()
        
        if db_healthy:
            logger.info("✅ worker tasks - SUCCESS")
            return True
        else:
            logger.error("❌ worker tasks - FAILED", db_healthy=db_healthy)
            return False
            
    except Exception as e:
        logger.error("❌ worker tasks - ERROR", error=str(e))
        return False

async def main():
    """Главная функция проверок."""
    logger.info("Starting stabilization checks")
    
    checks = [
        ("Redis Connection", check_redis_connection()),
        ("Database Connection", check_database_connection()),
        ("Telethon Ingest Service", check_telethon_ingest()),
        ("Worker Tasks", check_worker_tasks()),
        ("Create Consumer Groups", create_consumer_groups()),
        ("Smoke Test Pipeline", run_smoke_test()),
    ]
    
    results = []
    
    for name, check_coro in checks:
        logger.info(f"Running check: {name}")
        result = await check_coro
        results.append((name, result))
        
        if not result:
            logger.error(f"Check failed: {name}")
            # Продолжаем выполнение остальных проверок
    
    # Итоговый отчёт
    logger.info("=== STABILIZATION CHECKS SUMMARY ===")
    
    passed = 0
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status} - {name}")
        if result:
            passed += 1
    
    logger.info(f"Results: {passed}/{total} checks passed")
    
    if passed == total:
        logger.info("🎉 All checks passed! Pipeline is stabilized.")
        return True
    else:
        logger.error(f"⚠️ {total - passed} checks failed. Pipeline needs attention.")
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
