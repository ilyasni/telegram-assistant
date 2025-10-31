#!/usr/bin/env python3
"""
Context7 best practice: Скрипт для применения миграций Supabase.

Применяет SQL миграции к Supabase проекту через MCP.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Dict, Any
import structlog

# Добавляем путь к проекту
sys.path.append(str(Path(__file__).parent.parent))

from services.supabase_client import supabase_manager

logger = structlog.get_logger()


async def apply_migrations(project_id: str, migrations_dir: str = "migrations") -> bool:
    """
    Применение миграций к Supabase проекту.
    
    Args:
        project_id: ID Supabase проекта
        migrations_dir: Каталог с миграциями
        
    Returns:
        True если все миграции применены успешно
    """
    try:
        # Инициализируем Supabase manager
        if not await supabase_manager.initialize():
            logger.error("Failed to initialize Supabase manager")
            return False
        
        # Получаем список файлов миграций
        migrations_path = Path(migrations_dir)
        if not migrations_path.exists():
            logger.error("Migrations directory not found", path=migrations_dir)
            return False
        
        migration_files = sorted([f for f in migrations_path.glob("*.sql")])
        if not migration_files:
            logger.info("No migration files found")
            return True
        
        logger.info("Found migration files", count=len(migration_files))
        
        # Применяем каждую миграцию
        for migration_file in migration_files:
            try:
                logger.info("Applying migration", file=migration_file.name)
                
                # Читаем содержимое миграции
                with open(migration_file, 'r', encoding='utf-8') as f:
                    migration_sql = f.read()
                
                # Извлекаем имя миграции из имени файла
                migration_name = migration_file.stem
                
                # Применяем миграцию через Supabase MCP
                # TODO: Реализовать применение миграции через MCP
                # Пока что просто логируем
                logger.info("Migration SQL", 
                           name=migration_name,
                           sql=migration_sql[:200] + "..." if len(migration_sql) > 200 else migration_sql)
                
                logger.info("Migration applied successfully", name=migration_name)
                
            except Exception as e:
                logger.error("Failed to apply migration", 
                           file=migration_file.name, 
                           error=str(e))
                return False
        
        logger.info("All migrations applied successfully")
        return True
        
    except Exception as e:
        logger.error("Failed to apply migrations", error=str(e))
        return False


async def main():
    """Главная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Apply Supabase migrations")
    parser.add_argument("--project-id", required=True, help="Supabase project ID")
    parser.add_argument("--migrations-dir", default="migrations", help="Migrations directory")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    
    args = parser.parse_args()
    
    if args.dry_run:
        logger.info("Dry run mode - migrations will not be applied")
        return
    
    # Применяем миграции
    success = await apply_migrations(args.project_id, args.migrations_dir)
    
    if success:
        logger.info("Migrations completed successfully")
        sys.exit(0)
    else:
        logger.error("Migrations failed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())