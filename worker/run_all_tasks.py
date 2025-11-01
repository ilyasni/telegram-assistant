#!/usr/bin/env python3
"""
Запуск всех worker tasks с supervisor pattern для автоперезапуска.
"""

import asyncio
import sys
import os
import logging

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

from supervisor import TaskSupervisor, TaskConfig
from tasks.tagging_task import TaggingTask
from tasks.enrichment_task import EnrichmentWorker
from tasks.indexing_task import IndexingTask
from tasks.tag_persistence_task import TagPersistenceTask
from tasks.crawl_trigger_task import CrawlTriggerTask
from tasks.post_persistence_task import PostPersistenceWorker
from run_all_tasks_vision_helper import get_s3_config_from_env, get_vision_config_from_env

async def create_tagging_task():
    """Создание и запуск tagging task."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    # По best practices TaggingTask не требует DATABASE_URL и принимает только redis_url
    task = TaggingTask(redis_url)
    await task.start()
    # Context7: Не возвращаемся из start() - задачи работают в бесконечном цикле

async def create_enrichment_task():
    """Создание и запуск enrichment task."""
    # Enrichment task требует специальной инициализации
    from tasks.enrichment_task import main as enrichment_main
    await enrichment_main()

async def create_indexing_task():
    """Создание и запуск indexing task."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    neo4j_url = os.getenv("NEO4J_URL", "bolt://localhost:7687")
    
    task = IndexingTask(redis_url, qdrant_url, neo4j_url)
    await task.start()

async def create_tag_persistence_task():
    """Создание и запуск tag persistence task."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    
    task = TagPersistenceTask(redis_url, database_url)
    await task.start()

async def create_crawl_trigger_task():
    """Создание и запуск crawl trigger task."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    
    # Триггерные теги из конфигурации
    trigger_tags = [
        'longread', 'research', 'paper', 'release', 'law',
        'deepdive', 'analysis', 'report', 'study', 'whitepaper'
    ]
    
    task = CrawlTriggerTask(
        redis_url=redis_url,
        trigger_tags=trigger_tags
    )
    await task.start()

async def create_post_persistence_task():
    """Создание и запуск PostPersistenceWorker."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    worker = PostPersistenceWorker(redis_url=redis_url, database_url=database_url)
    await worker.initialize()
    await worker.start()

async def create_vision_analysis_task():
    """Context7: Создание и запуск Vision Analysis Task."""
    try:
        # Context7: Настройка sys.path для доступа к api модулю (cross-service import)
        # В production worker контейнере api должен быть доступен через volume mount или в образе
        import sys
        import os
        
        # Вариант 1: /opt/telegram-assistant/api (volume mount)
        api_mount = '/opt/telegram-assistant/api'
        if os.path.exists(api_mount) and api_mount not in sys.path:
            sys.path.insert(0, api_mount)
            logger.debug(f"Added {api_mount} to sys.path for api imports")
        
        # Вариант 2: project_root (dev)
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        api_dev = os.path.join(project_root, 'api')
        if os.path.exists(api_dev) and api_dev not in sys.path:
            sys.path.insert(0, api_dev)
            logger.debug(f"Added {api_dev} to sys.path for api imports")
        
        from tasks.vision_analysis_task import create_vision_analysis_task as create_task
        
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        # Context7: VisionAnalysisTask требует asyncpg драйвер для SQLAlchemy async
        database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
        # Убеждаемся, что используется asyncpg, а не psycopg2
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            logger.debug(f"Converted database_url to use asyncpg: {database_url.split('@')[0]}@...")
        
        # Context7: Получение конфигурации из env
        s3_config = get_s3_config_from_env()
        vision_config = get_vision_config_from_env()
        
        # Создание задачи
        task = await create_task(
            redis_url=redis_url,
            database_url=database_url,
            s3_config=s3_config,
            vision_config=vision_config
        )
        
        logger.info("VisionAnalysisTask created successfully")
        await task.start()
        # Context7: Не возвращаемся из start() - задачи работают в бесконечном цикле
        
    except ValueError as e:
        # Context7: Если не хватает credentials - пропускаем задачу с warning
        logger.warning(f"VisionAnalysisTask skipped: {e}")
        logger.warning("Для включения Vision Analysis установите GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET")
    except Exception as e:
        logger.error(f"Failed to create VisionAnalysisTask: {e}", exc_info=True)
        raise

async def main():
    """Запуск всех tasks с supervisor."""
    print("🚀 Starting worker with supervisor...")
    
    # [C7-ID: METRICS-REGISTRATION] Импорт метрик для автоматической регистрации
    # Эти импорты нужны для того, чтобы метрики были доступны через /metrics endpoint
    import metrics
    from metrics import posts_processed_total  # Для Grafana dashboard
    from event_bus import posts_in_queue_total, stream_pending_size  # Метрики очередей
    from ai_providers.gigachain_adapter import tagging_requests_total, tagging_latency_seconds
    from ai_providers.embedding_service import embedding_requests_total, embedding_latency_seconds
    from tasks.tagging_task import tagging_processed_total
    from tasks.indexing_task import indexing_processed_total
    
    # Context7: Инициализация метрик нулевыми значениями для экспорта в Prometheus
    # Метрики должны быть установлены хотя бы раз, чтобы Prometheus их увидел
    try:
        # Инициализация posts_processed_total для всех возможных комбинаций stage/success
        for stage in ['parsing', 'tagging', 'enrichment', 'indexing']:
            for success in ['true', 'false', 'error', 'skip', 'attempt']:
                posts_processed_total.labels(stage=stage, success=success).inc(0)
        
        # Инициализация posts_in_queue_total для основных стримов
        # Используем логические имена стримов (ключи словаря STREAMS)
        from event_bus import STREAMS
        if STREAMS:
            for stream_name in STREAMS.keys():
                posts_in_queue_total.labels(queue=stream_name, status='total').set(0)
                posts_in_queue_total.labels(queue=stream_name, status='pending').set(0)
                posts_in_queue_total.labels(queue=stream_name, status='new').set(0)
                stream_pending_size.labels(stream=stream_name).set(0)
        else:
            # Fallback: используем известные имена стримов
            for stream_name in ['posts.parsed', 'posts.tagged', 'posts.enriched', 'posts.indexed']:
                posts_in_queue_total.labels(queue=stream_name, status='total').set(0)
                posts_in_queue_total.labels(queue=stream_name, status='pending').set(0)
                posts_in_queue_total.labels(queue=stream_name, status='new').set(0)
                stream_pending_size.labels(stream=stream_name).set(0)
        
        logger.info("Metrics initialized with zero values")
    except Exception as e:
        logger.warning(f"Failed to initialize metrics: {e}", error=str(e))
    
    # Запуск HTTP сервера для метрик
    from prometheus_client import start_http_server
    metrics_port = int(os.getenv("METRICS_PORT", "8001"))
    print(f"Starting metrics server on port {metrics_port}", flush=True)
    try:
        start_http_server(metrics_port)
        print(f"Metrics server started on port {metrics_port}", flush=True)
        logger.info(f"Metrics server started on port {metrics_port}")
    except OSError as e:
        if e.errno == 98:  # Address already in use
            print(f"Metrics server already running on port {metrics_port}", flush=True)
            logger.warning(f"Metrics server already running on port {metrics_port}")
        else:
            print(f"Error starting metrics server: {e}", flush=True)
            raise
    
    supervisor = TaskSupervisor()
    
    # Регистрация tasks
    supervisor.register_task(TaskConfig(
        name="tagging",
        task_func=create_tagging_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    supervisor.register_task(TaskConfig(
        name="enrichment",
        task_func=create_enrichment_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    supervisor.register_task(TaskConfig(
        name="indexing",
        task_func=create_indexing_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    supervisor.register_task(TaskConfig(
        name="tag_persistence",
        task_func=create_tag_persistence_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    supervisor.register_task(TaskConfig(
        name="crawl_trigger",
        task_func=create_crawl_trigger_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))

    # Post persistence должен идти первым этапом после parsed
    supervisor.register_task(TaskConfig(
        name="post_persistence",
        task_func=create_post_persistence_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    # Context7: Vision Analysis Task (опционально, требуется GigaChat credentials)
    supervisor.register_task(TaskConfig(
        name="vision_analysis",
        task_func=create_vision_analysis_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    try:
        await supervisor.start_all()
    except KeyboardInterrupt:
        print("🛑 Stopping supervisor...")
        await supervisor.stop_all()
    except Exception as e:
        print(f"❌ Supervisor error: {e}")
        await supervisor.stop_all()
        raise

if __name__ == "__main__":
    asyncio.run(main())
