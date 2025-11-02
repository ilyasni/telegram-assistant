#!/usr/bin/env python3
"""
Context7 best practice: Скрипт для применения SQL миграций с отслеживанием статуса.
Применяет миграции из каталога migrations/ к PostgreSQL базе данных.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional
import asyncpg
import structlog

logger = structlog.get_logger()


async def ensure_migrations_table(conn: asyncpg.Connection):
    """Создание таблицы для отслеживания примененных миграций."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


async def is_migration_applied(conn: asyncpg.Connection, version: str) -> bool:
    """Проверка, применена ли миграция."""
    result = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = $1)",
        version
    )
    return bool(result)


async def mark_migration_applied(conn: asyncpg.Connection, version: str):
    """Отметка миграции как примененной."""
    await conn.execute(
        "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING",
        version
    )


async def apply_migration(conn: asyncpg.Connection, migration_file: Path) -> bool:
    """Применение одной миграции."""
    version = migration_file.stem
    
    # Проверяем, применена ли уже миграция
    if await is_migration_applied(conn, version):
        logger.info(f"Migration {version} already applied, skipping")
        return True
    
    logger.info(f"Applying migration: {version}")
    
    try:
        # Читаем SQL из файла
        with open(migration_file, 'r', encoding='utf-8') as f:
            sql = f.read()
        
        # Применяем в транзакции
        async with conn.transaction():
            await conn.execute(sql)
            await mark_migration_applied(conn, version)
        
        logger.info(f"Migration {version} applied successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to apply migration {version}: {e}")
        return False


async def apply_migrations(db_url: str, migrations_dir: str = "migrations") -> bool:
    """Применение всех миграций из каталога."""
    migrations_path = Path(migrations_dir)
    if not migrations_path.exists():
        logger.error(f"Migrations directory not found: {migrations_dir}")
        return False
    
    # Получаем список SQL файлов миграций
    migration_files = sorted(migrations_path.glob("*.sql"))
    if not migration_files:
        logger.info("No migration files found")
        return True
    
    logger.info(f"Found {len(migration_files)} migration files")
    
    # Подключаемся к БД
    conn = await asyncpg.connect(db_url)
    try:
        # Создаем таблицу отслеживания миграций
        await ensure_migrations_table(conn)
        
        # Применяем каждую миграцию
        for migration_file in migration_files:
            success = await apply_migration(conn, migration_file)
            if not success:
                return False
        
        logger.info("All migrations applied successfully")
        return True
        
    finally:
        await conn.close()


async def main():
    """Главная функция."""
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    migrations_dir = os.getenv("MIGRATIONS_DIR", "telethon-ingest/migrations")
    
    logger.info(f"Applying migrations from {migrations_dir} to {db_url}")
    
    success = await apply_migrations(db_url, migrations_dir)
    
    if success:
        logger.info("Migrations completed successfully")
        sys.exit(0)
    else:
        logger.error("Migrations failed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

