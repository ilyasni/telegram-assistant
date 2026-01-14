"""Основной модуль TGStat Parser Service.
Context7: Точка входа с инициализацией всех компонентов, health check и планировщика.
"""

import asyncio
import os
import signal
import sys
import time
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from scheduler.tasks import setup_scheduler
from config import settings
import uvicorn

# Context7: Установка DISPLAY для Selenium (Xvfb запускается через xvfb-run)
# Context7: xvfb-run автоматически устанавливает DISPLAY, но проверяем на всякий случай
if not os.environ.get('DISPLAY'):
    # Context7: Пытаемся найти активный Xvfb display
    import subprocess
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if 'Xvfb' in line and ':' in line:
                # Context7: Извлекаем номер display из процесса Xvfb
                parts = line.split()
                for part in parts:
                    if part.startswith(':') and part[1:].isdigit():
                        os.environ['DISPLAY'] = part
                        break
                if os.environ.get('DISPLAY'):
                    break
    except Exception:
        pass  # Context7: Если не удалось, xvfb-run установит DISPLAY сам

# Context7: Настройка структурированного логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.dict_tracebacks,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Context7: Глобальный планировщик
scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events для FastAPI.
    
    Context7: Управление жизненным циклом приложения (старт/остановка планировщика).
    Context7: Используем async context manager для правильной работы с FastAPI 0.104+
    """
    global scheduler
    
    # Startup
    # Context7: Явное логирование для диагностики
    logger.info("Application startup - lifespan started")
    
    # Context7: Инициализация scheduler для периодических задач
    # Context7: Подробное логирование всех этапов запуска scheduler
    scheduler_start_time = time.time()
    try:
        logger.info(
            "Scheduler initialization started",
            timestamp=time.time(),
            step="import"
        )
        
        logger.info(
            "Calling setup_scheduler()",
            timestamp=time.time(),
            step="start_call"
        )
        scheduler = setup_scheduler()
        
        logger.info(
            "Scheduler instance created",
            timestamp=time.time(),
            step="instance_created",
            scheduler_id=id(scheduler) if scheduler else None
        )
        
        if scheduler and not scheduler.running:
            logger.info("Starting scheduler...")
            scheduler.start()
            
            jobs = scheduler.get_jobs() if scheduler else []
            scheduler_duration = time.time() - scheduler_start_time
            
            logger.info(
                "Scheduler started successfully",
                timestamp=time.time(),
                step="start_complete",
                duration_seconds=round(scheduler_duration, 3),
                job_count=len(jobs),
                job_ids=[j.id for j in jobs] if jobs else []
            )
        else:
            logger.warning("Scheduler already running or not initialized", running=scheduler.running if scheduler else False)
            
    except Exception as e:
        scheduler_duration = time.time() - scheduler_start_time
        logger.error(
            "Error starting scheduler",
            timestamp=time.time(),
            step="start_error",
            error=str(e),
            error_type=type(e).__name__,
            duration_seconds=round(scheduler_duration, 3),
            exc_info=True
        )
        # Context7: Продолжаем без scheduler, чтобы приложение могло запуститься
    
    logger.info("Lifespan startup complete, yielding control")
    yield
    
    # Shutdown
    logger.info("Lifespan shutdown started")
    
    # Context7: Остановка scheduler
    # Context7: Graceful shutdown с таймаутом и подробным логированием
    shutdown_start_time = time.time()
    try:
        logger.info(
            "Scheduler shutdown started",
            timestamp=time.time(),
            step="shutdown_start"
        )
        
        if scheduler:
            jobs_before = scheduler.get_jobs() if scheduler.running else []
            logger.info(
                "Scheduler state before shutdown",
                timestamp=time.time(),
                step="shutdown_precheck",
                running=scheduler.running,
                jobs_count=len(jobs_before),
                job_ids=[job.id for job in jobs_before] if jobs_before else []
            )
            
            scheduler.shutdown()
            
            shutdown_duration = time.time() - shutdown_start_time
            logger.info(
                "Scheduler stopped successfully",
                timestamp=time.time(),
                step="shutdown_complete",
                duration_seconds=round(shutdown_duration, 3)
            )
        else:
            logger.warning("Scheduler was not initialized, skipping shutdown")
            
    except Exception as e:
        shutdown_duration = time.time() - shutdown_start_time
        logger.error(
            "Error stopping scheduler",
            timestamp=time.time(),
            step="shutdown_error",
            error=str(e),
            error_type=type(e).__name__,
            duration_seconds=round(shutdown_duration, 3),
            exc_info=True
        )
    
    logger.info("Application shutdown")


# Context7: Импорт endpoints для получения routes
from api import endpoints

# Context7: Пересоздаём app с lifespan для FastAPI 0.104+
# Context7: Копируем все routes из endpoints
app = FastAPI(
    title="TGStat Parser API",
    description="API для парсинга и синхронизации тем и каналов из TGStat",
    version="1.0.0",
    lifespan=lifespan
)

# Context7: Копируем все routes из endpoints
# Context7: Важно копировать routes до запуска сервера
for route in endpoints.app.routes:
    app.router.routes.append(route)

logger.info("FastAPI app created with lifespan", routes_count=len(app.routes))


def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown.
    
    Context7: Корректное завершение работы при получении сигналов.
    """
    signal_name = signal.Signals(signum).name
    logger.info(
        "Received signal",
        signal=signal_name,
        signum=signum
    )
    
    if scheduler:
        scheduler.shutdown()
    
    sys.exit(0)


def main():
    """Главная функция запуска сервиса.
    
    Context7: Инициализация и запуск HTTP сервера с планировщиком.
    """
    global scheduler
    
    # Context7: Настройка обработчиков сигналов
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(
        "Starting TGStat Parser Service",
        api_host=settings.api_host,
        api_port=settings.api_port,
        log_level=settings.log_level
    )
    
    # Context7: Запуск FastAPI сервера
    # Context7: Планировщик запускается автоматически через lifespan context manager
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=True
    )


if __name__ == "__main__":
    main()
