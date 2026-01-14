#!/usr/bin/env python3
"""
Скрипт миграции данных для добавления source и theme_id в user_channel.

Context7: Backfill всех существующих записей как source='manual', theme_id=NULL.
"""

import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from models.database import SessionLocal
import structlog

logger = structlog.get_logger()


def migrate_user_channels():
    """Миграция данных в user_channel."""
    db = SessionLocal()
    
    try:
        logger.info("Starting user_channel migration")
        
        # Проверяем, есть ли уже колонка source
        result = db.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'user_channel' AND column_name = 'source'
        """))
        
        if not result.fetchone():
            logger.warning("Column 'source' does not exist. Please run migration first.")
            return
        
        # Backfill всех существующих записей
        update_result = db.execute(text("""
            UPDATE user_channel
            SET source = 'manual', theme_id = NULL, updated_at = NOW()
            WHERE source IS NULL OR theme_id IS NOT NULL
        """))
        
        rows_updated = update_result.rowcount
        db.commit()
        
        logger.info(
            "User channel migration completed",
            rows_updated=rows_updated
        )
        
        # Проверяем результат
        check_result = db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE source = 'manual') as manual_count,
                COUNT(*) FILTER (WHERE source = 'theme') as theme_count,
                COUNT(*) FILTER (WHERE source IS NULL) as null_source_count
            FROM user_channel
        """))
        
        stats = check_result.fetchone()
        logger.info(
            "Migration statistics",
            total=stats.total,
            manual_count=stats.manual_count,
            theme_count=stats.theme_count,
            null_source_count=stats.null_source_count
        )
        
    except Exception as e:
        logger.error(
            "Error during migration",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_user_channels()
