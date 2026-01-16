#!/usr/bin/env python3
"""Скрипт для полной синхронизации всех тем из TGStat.
Context7: Запускает синхронизацию всех 369 тем с логированием прогресса.
"""

import sys
import os
import time
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler.tasks import sync_service
import structlog

logger = structlog.get_logger()

def main():
    """Главная функция синхронизации."""
    logger.info("=" * 60)
    logger.info("Starting full themes synchronization")
    logger.info("=" * 60)
    
    start_time = time.time()
    
    try:
        result = sync_service.sync_all_themes()
        
        duration = time.time() - start_time
        
        logger.info("=" * 60)
        logger.info("SYNCHRONIZATION COMPLETED")
        logger.info("=" * 60)
        logger.info(
            "Sync results",
            success=result.get("success"),
            themes_count=result.get("themes_count"),
            successful_themes=result.get("successful_themes"),
            failed_themes=result.get("failed_themes"),
            channels_count=result.get("channels_count"),
            duration_seconds=round(duration, 1)
        )
        
        print("\n" + "=" * 60)
        print("SYNCHRONIZATION RESULTS")
        print("=" * 60)
        print(f"Success: {result.get('success')}")
        print(f"Total themes found: {result.get('themes_count')}")
        print(f"Successfully synced: {result.get('successful_themes')}")
        print(f"Failed: {result.get('failed_themes')}")
        print(f"Total channels saved: {result.get('channels_count')}")
        print(f"Duration: {round(duration, 1)} seconds ({round(duration/60, 1)} minutes)")
        print("=" * 60)
        
        if result.get("success"):
            sys.exit(0)
        else:
            logger.error("Sync failed", error=result.get("error"))
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.warning("Sync interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error("Sync failed with exception", error=str(e), exc_info=True)
        print(f"\nERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
