"""
Единая утилита для получения строк подключения к БД.

[C7-ID: CODE-CLEANUP-031] Context7 best practice: единая логика подключения к БД
с обратной совместимостью и маппингом старых ENV переменных.
"""

import os
import warnings
from typing import Literal, Optional
import structlog

logger = structlog.get_logger()

# Маппинг старых переменных на новые (для обратной совместимости)
_OLD_ENV_MAPPING = {
    "POSTGRES_HOST": "DB_HOST",
    "POSTGRES_PORT": "DB_PORT",
    "POSTGRES_USER": "DB_USER",
    "POSTGRES_PASSWORD": "DB_PASSWORD",
    "POSTGRES_DB": "DB_NAME",
}


def _warn_old_env(old_var: str, new_var: str) -> None:
    """Предупреждение об использовании устаревшей переменной окружения."""
    if os.getenv(old_var):
        warnings.warn(
            f"Environment variable {old_var} is deprecated. Use {new_var} instead. "
            "See docs/CONFIG_COMPAT_MATRIX.md",
            DeprecationWarning,
            stacklevel=3
        )
        logger.warning(
            "deprecated_env_var",
            old_var=old_var,
            new_var=new_var
        )


def get_database_url(
    kind: Literal["rw", "ro"] = "rw",
    async_: bool = False
) -> str:
    """
    Получение строки подключения к БД с приоритетом: ENV > .env > docker secrets.
    
    Args:
        kind: Тип подключения - "rw" (read-write) или "ro" (read-only)
        async_: Если True, возвращает async URL (postgresql+asyncpg://), иначе sync (postgresql://)
        
    Returns:
        Строка подключения к БД
        
    Raises:
        ValueError: Если не удалось получить строку подключения
    """
    # Приоритет 1: Специфичные переменные для sync/async
    if async_:
        async_url = os.getenv("DB_URL_ASYNC") or os.getenv("DATABASE_URL")
        if async_url:
            # Убеждаемся что используется asyncpg драйвер
            if async_url.startswith("postgresql://"):
                async_url = async_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif not async_url.startswith("postgresql+asyncpg://"):
                raise ValueError(f"Invalid async database URL format: {async_url}")
            return async_url
    else:
        sync_url = os.getenv("DB_URL_SYNC")
        if sync_url:
            # Убираем asyncpg драйвер если есть
            sync_url = sync_url.replace("postgresql+asyncpg://", "postgresql://")
            return sync_url
    
    # Приоритет 2: DATABASE_URL (конвертируем при необходимости)
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if async_:
            # Убеждаемся что используется asyncpg
            if database_url.startswith("postgresql://"):
                database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif not database_url.startswith("postgresql+asyncpg://"):
                # Добавляем asyncpg если нет драйвера
                database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        else:
            # Убираем asyncpg для sync
            database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        return database_url
    
    # Приоритет 3: Собираем из отдельных переменных (с обратной совместимостью)
    # Проверяем старые переменные и предупреждаем
    for old_var, new_var in _OLD_ENV_MAPPING.items():
        _warn_old_env(old_var, new_var)
    
    # Используем новые переменные с fallback на старые
    db_host = os.getenv("DB_HOST") or os.getenv("POSTGRES_HOST") or os.getenv("SUPABASE_DB_HOST", "supabase-db")
    db_port = os.getenv("DB_PORT") or os.getenv("POSTGRES_PORT") or os.getenv("SUPABASE_DB_PORT", "5432")
    db_user = os.getenv("DB_USER") or os.getenv("POSTGRES_USER") or os.getenv("SUPABASE_DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or os.getenv("SUPABASE_DB_PASSWORD", "")
    db_name = os.getenv("DB_NAME") or os.getenv("POSTGRES_DB") or os.getenv("SUPABASE_DB_NAME", "postgres")
    
    if not db_password:
        raise ValueError(
            "Database password not found. Set DB_PASSWORD, POSTGRES_PASSWORD, or SUPABASE_DB_PASSWORD. "
            "Alternatively, set DATABASE_URL or DB_URL_SYNC/DB_URL_ASYNC."
        )
    
    # Формируем URL
    scheme = "postgresql+asyncpg" if async_ else "postgresql"
    url = f"{scheme}://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    
    return url


def get_redis_url() -> str:
    """
    Получение URL Redis с приоритетом: REDIS_URL > defaults.
    
    Returns:
        Строка подключения к Redis
    """
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis_url
    
    # Fallback: собираем из отдельных переменных
    redis_host = os.getenv("REDIS_HOST", "redis")
    redis_port = os.getenv("REDIS_PORT", "6379")
    redis_db = os.getenv("REDIS_DB", "0")
    
    return f"redis://{redis_host}:{redis_port}/{redis_db}"

