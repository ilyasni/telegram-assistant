"""Задачи планировщика для синхронизации тем и каналов.
Context7: Использует APScheduler для ежемесячного автоматического запуска.
"""

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import List, Dict, Any
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from prometheus_client import Counter, Histogram, Gauge

from parser.tgstat_parser import TgStatParser
from parser.quality_checker import QualityChecker
from database.models import ThemeRepository
from config import settings

logger = structlog.get_logger()

# Context7: Prometheus метрики для мониторинга
themes_processed_total = Counter(
    'tgstat_parser_themes_total',
    'Total number of themes processed',
    ['status']
)

channels_processed_total = Counter(
    'tgstat_parser_channels_total',
    'Total number of channels processed',
    ['status']
)

sync_duration_seconds = Histogram(
    'tgstat_parser_sync_duration_seconds',
    'Duration of sync operation',
    ['operation']
)

sync_in_progress = Gauge(
    'tgstat_parser_sync_in_progress',
    'Whether sync is currently in progress'
)


class SyncService:
    """Сервис синхронизации тем и каналов.
    
    Context7: Реализует синхронизацию с обработкой ошибок и метриками.
    """
    
    def __init__(self):
        """Инициализация сервиса синхронизации."""
        self.parser = TgStatParser()
        logger.info("SyncService initialized")
    
    def sync_all_themes(self) -> Dict[str, Any]:
        """Синхронизация всех тем.
        
        Context7: Парсит список тем и обновляет каналы для каждой темы.
        
        Returns:
            Словарь с результатами синхронизации
        """
        sync_in_progress.set(1)
        start_time = datetime.now(timezone.utc)
        
        logger.info("Starting sync of all themes")
        
        try:
            # Context7: Парсинг списка тем
            with sync_duration_seconds.labels(operation='parse_themes').time():
                themes = self.parser.parse_themes_list()
            
            if not themes:
                logger.warning("No themes found during parsing")
                themes_processed_total.labels(status='empty').inc()
                return {
                    'success': False,
                    'themes_count': 0,
                    'channels_count': 0,
                    'error': 'No themes found'
                }
            
            logger.info("Themes list parsed", themes_count=len(themes))
            
            # Context7: Синхронизация каналов для каждой темы
            total_channels = 0
            successful_themes = 0
            failed_themes = 0
            
            for idx, theme_data in enumerate(themes, 1):
                try:
                    # Context7: Логирование прогресса каждые 10 тем
                    if idx % 10 == 0 or idx == 1:
                        logger.info(
                            "Sync progress",
                            current=idx,
                            total=len(themes),
                            progress_pct=round(idx / len(themes) * 100, 1)
                        )
                    
                    channels_count = self.sync_theme_channels(
                        theme_data['slug'],
                        theme_data['name'],
                        theme_data.get('description')
                    )
                    total_channels += channels_count
                    successful_themes += 1
                    themes_processed_total.labels(status='success').inc()
                    
                    # Context7: Задержка между синхронизацией тем для снижения нагрузки
                    # Context7: Случайная задержка 2-4 секунды для имитации человеческого поведения
                    if idx < len(themes):  # Не задерживаемся после последней темы
                        delay = random.uniform(2, 4)
                        time.sleep(delay)
                    
                except Exception as e:
                    failed_themes += 1
                    themes_processed_total.labels(status='error').inc()
                    logger.error(
                        "Error syncing theme",
                        theme_slug=theme_data.get('slug'),
                        theme_index=idx,
                        total_themes=len(themes),
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True
                    )
                    # Context7: При ошибке тоже делаем задержку, но короче
                    if idx < len(themes):
                        time.sleep(1)
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            result = {
                'success': True,
                'themes_count': len(themes),
                'successful_themes': successful_themes,
                'failed_themes': failed_themes,
                'channels_count': total_channels,
                'duration_seconds': duration
            }
            
            logger.info(
                "Sync completed",
                **result
            )
            
            return result
            
        except Exception as e:
            themes_processed_total.labels(status='error').inc()
            logger.error(
                "Sync failed",
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            return {
                'success': False,
                'error': str(e),
                'error_type': type(e).__name__
            }
        finally:
            sync_in_progress.set(0)
    
    def sync_theme_channels(
        self,
        theme_slug: str,
        theme_name: str = None,
        theme_description: str = None
    ) -> int:
        """Синхронизация каналов для конкретной темы.
        
        Context7: Парсит каналы темы и сохраняет в БД в транзакции.
        
        Args:
            theme_slug: Slug темы
            theme_name: Название темы (опционально)
            theme_description: Описание темы (опционально)
            
        Returns:
            Количество сохранённых каналов
        """
        logger.info("Syncing theme channels", theme_slug=theme_slug)
        
        try:
            # Context7: Парсинг каналов темы
            with sync_duration_seconds.labels(operation='parse_channels').time():
                channels_data = self.parser.parse_theme_channels(theme_slug)
            
            # Context7: Сохранение в БД в транзакции
            # Context7: Сохраняем тему ВСЕГДА, даже если каналов нет или они не изменились
            with ThemeRepository() as repo:
                # Context7: Upsert темы (сохраняем тему всегда)
                theme = repo.upsert_theme(
                    slug=theme_slug,
                    name=theme_name or theme_slug,
                    description=theme_description
                )
                
                # Context7: Убеждаемся, что тема сохранена и имеет ID
                # Context7: Flush уже выполнен в upsert_theme, но проверяем на всякий случай
                if not theme:
                    logger.error("Failed to create or retrieve theme", theme_slug=theme_slug)
                    return 0
                
                # Context7: Если ID ещё не установлен (для новых тем), делаем flush
                if not theme.id:
                    repo.session.flush()
                
                if not theme.id:
                    logger.error("Theme has no ID after flush", theme_slug=theme_slug)
                    return 0
                
                # Context7: Сохранение каналов (если есть)
                channels_count = 0
                if channels_data:
                    # Context7: Проверка качества данных перед сохранением
                    quality_result = QualityChecker.check_quality_constraints(channels_data)
                    if not quality_result['valid']:
                        logger.warning(
                            "Quality check failed, but saving valid channels",
                            theme_slug=theme_slug,
                            errors=quality_result.get('errors', [])[:3]  # Логируем первые 3 ошибки
                        )
                    
                    # Context7: Валидация и сортировка каналов через QualityChecker
                    # Context7: Это гарантирует, что все каналы валидны перед сохранением
                    valid_channels, validation_errors = QualityChecker.validate_and_sort_channels(
                        channels_data,
                        max_channels=30
                    )
                    
                    if validation_errors:
                        logger.warning(
                            "Channel validation errors",
                            theme_slug=theme_slug,
                            errors_count=len(validation_errors),
                            errors=validation_errors[:5]  # Логируем первые 5 ошибок
                        )
                    
                    if valid_channels:
                        channels_count = repo.upsert_theme_channels(
                            theme_id=theme.id,
                            channels_data=valid_channels,
                            max_channels=30
                        )
                    else:
                        logger.warning("No valid channels to save", theme_slug=theme_slug)
                else:
                    # Context7: Если парсер вернул пустой список (из-за кеша или отсутствия каналов),
                    # пересчитываем channels_count из реального количества каналов в БД
                    # Context7: Это предотвращает обнуление channels_count при кешировании
                    from database.models import ThemeChannel
                    actual_count = repo.session.query(ThemeChannel).filter(
                        ThemeChannel.theme_id == theme.id
                    ).count()
                    if actual_count > 0:
                        logger.debug(
                            "Parser returned empty list, using actual count from DB",
                            theme_slug=theme_slug,
                            actual_count=actual_count
                        )
                        channels_count = actual_count
                
                # Context7: Обновляем indexed_at и channels_count всегда
                from datetime import datetime, timezone
                theme.indexed_at = datetime.now(timezone.utc)
                theme.channels_count = channels_count
                
                # Context7: Коммит транзакции
                repo.commit()
                
                logger.info(
                    "Theme saved to database",
                    theme_slug=theme_slug,
                    channels_count=channels_count,
                    theme_id=str(theme.id)
                )
                
                # Context7: Триггер синхронизации подписок пользователей
                # Вызываем HTTP endpoint основного API для синхронизации (неблокирующий)
                try:
                    import requests
                    import os
                    import threading
                    
                    api_base_url = os.getenv("API_BASE_URL", "http://api:8000")
                    sync_url = f"{api_base_url}/api/themes/sync/{theme.id}"
                    
                    def trigger_sync():
                        """Неблокирующий вызов синхронизации в отдельном потоке."""
                        try:
                            requests.post(sync_url, timeout=5)
                            logger.debug(
                                "Theme subscriptions sync triggered",
                                theme_id=str(theme.id),
                                theme_slug=theme_slug
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to trigger theme subscriptions sync",
                                theme_id=str(theme.id),
                                error=str(e)
                            )
                    
                    # Запускаем в отдельном потоке, чтобы не блокировать основной поток
                    sync_thread = threading.Thread(target=trigger_sync, daemon=True)
                    sync_thread.start()
                    
                except Exception as sync_error:
                    # Не критично, если синхронизация не запустилась - можно запустить вручную
                    logger.warning(
                        "Failed to setup theme subscriptions sync trigger",
                        theme_id=str(theme.id),
                        error=str(sync_error)
                    )
            
            channels_processed_total.labels(status='success').inc()
            
            logger.info(
                "Theme channels synced",
                theme_slug=theme_slug,
                channels_count=channels_count
            )
            
            return channels_count
            
        except Exception as e:
            channels_processed_total.labels(status='error').inc()
            logger.error(
                "Error syncing theme channels",
                theme_slug=theme_slug,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            raise


# Context7: Глобальный экземпляр сервиса синхронизации
sync_service = SyncService()


def setup_scheduler() -> AsyncIOScheduler:
    """Настройка планировщика задач.
    
    Context7: Ежемесячный запуск в первый день месяца в 03:00 UTC.
    
    Returns:
        Настроенный AsyncIOScheduler
    """
    scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
    
    # Context7: Ежемесячная задача - первый день месяца в 03:00 UTC
    scheduler.add_job(
        sync_all_themes_task,
        trigger=CronTrigger(
            day=1,  # Первый день месяца
            hour=settings.scheduler_monthly_hour,
            minute=settings.scheduler_monthly_minute
        ),
        id='sync_all_themes_monthly',
        name='Sync all themes from TGStat',
        replace_existing=True
    )
    
    logger.info(
        "Scheduler configured",
        job_id='sync_all_themes_monthly',
        schedule='Monthly on day 1 at 03:00 UTC'
    )
    
    return scheduler


async def sync_all_themes_task():
    """Задача для планировщика - синхронизация всех тем.
    
    Context7: Обёртка для async выполнения синхронизации.
    """
    # Context7: Запуск синхронизации в executor для блокирующих операций
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        sync_service.sync_all_themes
    )
    logger.info("Scheduled sync task completed", result=result)
