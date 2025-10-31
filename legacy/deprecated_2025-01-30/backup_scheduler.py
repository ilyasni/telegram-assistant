#!/usr/bin/env python3
"""
⚠️ DEPRECATED ⚠️ 
──────────────────────────────────────────────────────────────────────────────
[Context7-DEPRECATED-001] backup_scheduler.py НЕ ДОЛЖЕН ИСПОЛЬЗОВАТЬСЯ!

ПРИЧИНА: Зависит от deprecated UnifiedSessionManager.
ТЕКУЩИЙ ПОДХОД: Используется TelegramClientManager + session_storage.py

ЗАПРЕЩЕНО:
- ❌ Запускать этот скрипт в production
- ❌ Использовать в scheduled tasks/cron

РАЗРЕШЕНО ТОЛЬКО:
- ✅ Чтение кода для миграции
- ✅ Удаление кода (после миграции)

──────────────────────────────────────────────────────────────────────────────
Context7 best practice: Scheduler для автоматических бэкапов сессий.

Планировщик бэкапов с retention политиками и health checks.
"""

import warnings
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Dict, Any
import structlog
import schedule
import time
from datetime import datetime, timedelta

# Добавляем путь к проекту
sys.path.append(str(Path(__file__).parent.parent))

from services.session.session_backup import SessionBackup
# [Context7-DEPRECATED-001] DEPRECATED импорт - НЕ ИСПОЛЬЗОВАТЬ!
from services.session.unified_session_manager import UnifiedSessionManager  # noqa: F401
from services.session.metrics import *

logger = structlog.get_logger()

# [Context7-DEPRECATED-001] Предупреждение при импорте модуля
warnings.warn(
    "scripts.backup_scheduler is DEPRECATED and should not be used! "
    "Depends on deprecated UnifiedSessionManager. "
    "See [Context7-DEPRECATED-001]",
    DeprecationWarning,
    stacklevel=2
)
logger.critical(
    "[Context7-DEPRECATED-001] scripts.backup_scheduler imported! "
    "This script should not be run in production."
)


class BackupScheduler:
    """
    Context7: Планировщик автоматических бэкапов сессий.
    
    Features:
    - Планирование бэкапов по расписанию
    - Retention политики
    - Health checks
    - Метрики и логирование
    """
    
    def __init__(self, session_manager: UnifiedSessionManager, 
                 supabase_client=None):
        self.session_manager = session_manager
        self.backup_service = SessionBackup(supabase_client)
        self.running = False
        self.backup_interval = 24  # hours
        self.cleanup_interval = 7  # days
        
    async def start(self):
        """Запуск планировщика."""
        try:
            logger.info("Starting backup scheduler")
            
            # Проверяем health
            health = await self.backup_service.health_check()
            if health["status"] != "healthy":
                logger.error("Backup service unhealthy", health=health)
                return False
            
            # Настраиваем расписание
            self._setup_schedule()
            
            self.running = True
            
            # Запускаем планировщик
            while self.running:
                try:
                    schedule.run_pending()
                    await asyncio.sleep(60)  # Проверяем каждую минуту
                except Exception as e:
                    logger.error("Error in backup scheduler", error=str(e))
                    await asyncio.sleep(300)  # Ждем 5 минут при ошибке
            
            logger.info("Backup scheduler stopped")
            
        except Exception as e:
            logger.error("Failed to start backup scheduler", error=str(e))
            return False
    
    def _setup_schedule(self):
        """Настройка расписания бэкапов."""
        try:
            # Ежедневные бэкапы в 2:00
            schedule.every().day.at("02:00").do(
                asyncio.create_task, self._run_daily_backups()
            )
            
            # Еженедельная очистка в воскресенье в 3:00
            schedule.every().sunday.at("03:00").do(
                asyncio.create_task, self._run_cleanup()
            )
            
            # Ежедневная проверка здоровья в 1:00
            schedule.every().day.at("01:00").do(
                asyncio.create_task, self._run_health_check()
            )
            
            logger.info("Backup schedule configured", 
                       backup_interval=f"{self.backup_interval}h",
                       cleanup_interval=f"{self.cleanup_interval}d")
            
        except Exception as e:
            logger.error("Failed to setup schedule", error=str(e))
            raise
    
    async def _run_daily_backups(self):
        """Выполнение ежедневных бэкапов."""
        try:
            logger.info("Starting daily backups")
            
            # Получаем список всех сессий
            sessions = await self._get_all_sessions()
            
            backup_count = 0
            success_count = 0
            
            for session in sessions:
                try:
                    tenant_id = session["tenant_id"]
                    app_id = session["app_id"]
                    session_path = session["session_path"]
                    
                    # Проверяем, нужно ли делать бэкап
                    if await self._should_backup(tenant_id, app_id, session_path):
                        backup_url = await self.backup_service.backup_session(
                            tenant_id, app_id, session_path
                        )
                        
                        if backup_url:
                            success_count += 1
                            logger.info("Session backed up", 
                                       tenant_id=tenant_id, 
                                       app_id=app_id,
                                       backup_url=backup_url)
                        else:
                            logger.warning("Session backup failed", 
                                         tenant_id=tenant_id, 
                                         app_id=app_id)
                    
                    backup_count += 1
                    
                except Exception as e:
                    logger.error("Error backing up session", 
                               tenant_id=session.get("tenant_id"),
                               app_id=session.get("app_id"),
                               error=str(e))
                    continue
            
            logger.info("Daily backups completed", 
                       total=backup_count, 
                       successful=success_count)
            
        except Exception as e:
            logger.error("Daily backups failed", error=str(e))
    
    async def _run_cleanup(self):
        """Выполнение очистки старых бэкапов."""
        try:
            logger.info("Starting backup cleanup")
            
            # Получаем список всех сессий
            sessions = await self._get_all_sessions()
            
            cleanup_count = 0
            
            for session in sessions:
                try:
                    tenant_id = session["tenant_id"]
                    app_id = session["app_id"]
                    
                    # Очищаем старые бэкапы
                    await self.backup_service.cleanup_old_backups(tenant_id, app_id)
                    cleanup_count += 1
                    
                except Exception as e:
                    logger.error("Error cleaning up backups", 
                               tenant_id=session.get("tenant_id"),
                               app_id=session.get("app_id"),
                               error=str(e))
                    continue
            
            logger.info("Backup cleanup completed", 
                       sessions_processed=cleanup_count)
            
        except Exception as e:
            logger.error("Backup cleanup failed", error=str(e))
    
    async def _run_health_check(self):
        """Выполнение проверки здоровья."""
        try:
            logger.info("Running backup health check")
            
            health = await self.backup_service.health_check()
            
            if health["status"] == "healthy":
                logger.info("Backup service healthy", health=health)
            else:
                logger.warning("Backup service unhealthy", health=health)
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
    
    async def _get_all_sessions(self) -> List[Dict[str, Any]]:
        """
        Получение списка всех сессий.
        
        Returns:
            Список сессий
        """
        try:
            # TODO: Реализовать получение сессий из БД
            # Пока что возвращаем заглушку
            return []
            
        except Exception as e:
            logger.error("Failed to get sessions", error=str(e))
            return []
    
    async def _should_backup(self, tenant_id: str, app_id: str, 
                           session_path: str) -> bool:
        """
        Проверка, нужно ли делать бэкап сессии.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            session_path: Путь к сессии
            
        Returns:
            True если нужен бэкап
        """
        try:
            # Проверяем существование файла
            if not os.path.exists(session_path):
                return False
            
            # Проверяем, есть ли уже бэкап за последние 24 часа
            backups = await self.backup_service.list_backups(tenant_id, app_id)
            
            if not backups:
                return True
            
            # Проверяем последний бэкап
            last_backup = backups[0]
            last_backup_time = datetime.fromisoformat(
                last_backup["created_at"].replace("Z", "+00:00")
            )
            
            # Если последний бэкап старше 24 часов, делаем новый
            if datetime.now() - last_backup_time > timedelta(hours=24):
                return True
            
            return False
            
        except Exception as e:
            logger.error("Failed to check backup need", 
                        tenant_id=tenant_id, 
                        app_id=app_id, 
                        error=str(e))
            return False
    
    async def stop(self):
        """Остановка планировщика."""
        self.running = False
        logger.info("Backup scheduler stop requested")
    
    async def run_manual_backup(self, tenant_id: str, app_id: str) -> bool:
        """
        Ручной бэкап сессии.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            
        Returns:
            True если бэкап успешен
        """
        try:
            # Получаем путь к сессии
            session_path = self.session_manager._get_session_path(tenant_id, app_id)
            
            if not os.path.exists(session_path):
                logger.warning("Session file not found", 
                             tenant_id=tenant_id, 
                             app_id=app_id,
                             path=session_path)
                return False
            
            # Выполняем бэкап
            backup_url = await self.backup_service.backup_session(
                tenant_id, app_id, session_path
            )
            
            if backup_url:
                logger.info("Manual backup completed", 
                           tenant_id=tenant_id, 
                           app_id=app_id,
                           backup_url=backup_url)
                return True
            else:
                logger.error("Manual backup failed", 
                           tenant_id=tenant_id, 
                           app_id=app_id)
                return False
                
        except Exception as e:
            logger.error("Manual backup failed", 
                        tenant_id=tenant_id, 
                        app_id=app_id, 
                        error=str(e))
            return False
    
    async def list_backups(self, tenant_id: str, app_id: str) -> List[Dict[str, Any]]:
        """
        Получение списка бэкапов для сессии.
        
        Args:
            tenant_id: ID арендатора
            app_id: ID приложения
            
        Returns:
            Список бэкапов
        """
        try:
            return await self.backup_service.list_backups(tenant_id, app_id)
            
        except Exception as e:
            logger.error("Failed to list backups", 
                        tenant_id=tenant_id, 
                        app_id=app_id, 
                        error=str(e))
            return []


async def main():
    """Главная функция."""
    import argparse
    import redis.asyncio as redis
    import psycopg2
    
    parser = argparse.ArgumentParser(description="Session Backup Scheduler")
    parser.add_argument("--database-url", required=True, help="Database URL")
    parser.add_argument("--redis-url", required=True, help="Redis URL")
    parser.add_argument("--supabase-url", help="Supabase URL")
    parser.add_argument("--supabase-key", help="Supabase service key")
    parser.add_argument("--manual-backup", help="Manual backup for tenant:app")
    parser.add_argument("--list-backups", help="List backups for tenant:app")
    
    args = parser.parse_args()
    
    try:
        # Подключаемся к Redis
        redis_client = redis.from_url(args.redis_url)
        
        # Подключаемся к БД
        db_connection = psycopg2.connect(args.database_url)
        
        # Создаем UnifiedSessionManager
        session_manager = UnifiedSessionManager(redis_client, db_connection)
        if not await session_manager.initialize():
            logger.error("Failed to initialize UnifiedSessionManager")
            sys.exit(1)
        
        # Создаем Supabase клиент если указан
        supabase_client = None
        if args.supabase_url and args.supabase_key:
            from supabase import create_client
            supabase_client = create_client(args.supabase_url, args.supabase_key)
        
        # Создаем планировщик
        scheduler = BackupScheduler(session_manager, supabase_client)
        
        # Выполняем команду
        if args.manual_backup:
            tenant_id, app_id = args.manual_backup.split(":")
            success = await scheduler.run_manual_backup(tenant_id, app_id)
            sys.exit(0 if success else 1)
        
        elif args.list_backups:
            tenant_id, app_id = args.list_backups.split(":")
            backups = await scheduler.list_backups(tenant_id, app_id)
            print(f"Found {len(backups)} backups for {tenant_id}/{app_id}")
            for backup in backups:
                print(f"  {backup['name']} - {backup['created_at']} ({backup['size']} bytes)")
        
        else:
            # Запускаем планировщик
            await scheduler.start()
        
    except Exception as e:
        logger.error("Backup scheduler failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())