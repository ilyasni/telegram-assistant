"""
Database factories for worker (async operations).

Context7 best practice: фабрика для async-операций с asyncpg и SQLAlchemy async.
Используется в PostPersistenceWorker и других async-воркерах.
"""

import os
import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator, Optional
import structlog

logger = structlog.get_logger()

# ============================================================================
# CONFIGURATION
# ============================================================================

def get_database_urls() -> tuple[str, str]:
    """Context7: Получение URL для sync и async операций с fallback стратегией."""
    use_async = os.getenv("USE_ASYNC_DB", "true").lower() == "true"
    
    if use_async:
        # Context7: Фаза 1 - отдельные URL для sync/async
        sync_url = os.getenv("DB_URL_SYNC")
        async_url = os.getenv("DB_URL_ASYNC")
        
        # Fallback: конвертируем из DATABASE_URL
        if not sync_url or not async_url:
            base_url = os.getenv("DATABASE_URL", "")
            if not base_url:
                raise ValueError("DATABASE_URL must be set when DB_URL_SYNC/ASYNC not provided")
            
            sync_url = sync_url or base_url.replace("+asyncpg", "")
            async_url = async_url or base_url
    else:
        # Context7: Fallback режим - единый URL для всех операций
        base_url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
        if not base_url:
            raise ValueError("DATABASE_URL must be set")
        sync_url = base_url
        async_url = base_url
    
    return sync_url, async_url

# ============================================================================
# ASYNC DATABASE FACTORY
# ============================================================================

def get_async_engine():
    """Создание async SQLAlchemy engine для worker."""
    _, async_url = get_database_urls()
    
    # Убеждаемся, что используется asyncpg драйвер
    if not async_url.startswith("postgresql+asyncpg://"):
        if async_url.startswith("postgresql://"):
            async_url = async_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        else:
            raise ValueError(f"Invalid async database URL: {async_url}")
    
    engine = create_async_engine(
        async_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False
    )
    
    logger.info("Async engine created", url=async_url[:50] + "...")
    return engine

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency для получения async SQLAlchemy session."""
    engine = get_async_engine()
    async_session_factory = async_sessionmaker(
        engine, 
        class_=AsyncSession, 
        expire_on_commit=False
    )
    
    async with async_session_factory() as session:
        yield session

def get_async_session_factory():
    """Фабрика для создания async sessions."""
    engine = get_async_engine()
    return async_sessionmaker(
        engine, 
        class_=AsyncSession, 
        expire_on_commit=False
    )

# ============================================================================
# ASYNCPG POOL FACTORY
# ============================================================================

async def get_asyncpg_pool() -> asyncpg.Pool:
    """Создание asyncpg connection pool для прямых SQL операций."""
    _, async_url = get_database_urls()
    
    # Конвертируем SQLAlchemy URL в asyncpg DSN
    dsn = async_url.replace("postgresql+asyncpg://", "postgresql://")
    
    pool = await asyncpg.create_pool(
        dsn,
        min_size=5,
        max_size=20,
        command_timeout=30,
        server_settings={
            'application_name': 'telegram_worker',
            'timezone': 'UTC'
        }
    )
    
    logger.info("AsyncPG pool created", dsn=dsn[:50] + "...")
    return pool

async def get_asyncpg_connection() -> asyncpg.Connection:
    """Получение одиночного asyncpg соединения."""
    pool = await get_asyncpg_pool()
    return pool.acquire()

# ============================================================================
# HEALTH CHECKS
# ============================================================================

async def check_async_connection() -> bool:
    """Проверка доступности async БД."""
    try:
        pool = await get_asyncpg_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
            if result == 1:
                logger.info("Async DB health check passed")
                return True
        await pool.close()
        return False
    except Exception as e:
        logger.error("Async DB health check failed", error=str(e))
        return False

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_connection_info() -> dict:
    """Информация о подключениях для мониторинга."""
    sync_url, async_url = get_database_urls()
    
    return {
        "use_async_db": os.getenv("USE_ASYNC_DB", "true").lower() == "true",
        "sync_url": sync_url[:50] + "..." if len(sync_url) > 50 else sync_url,
        "async_url": async_url[:50] + "..." if len(async_url) > 50 else async_url,
    }

async def get_health_status() -> dict:
    """Полный статус здоровья БД."""
    return {
        **get_connection_info(),
        "async_healthy": await check_async_connection()
    }
