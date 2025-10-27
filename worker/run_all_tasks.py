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

async def main():
    """Запуск всех tasks с supervisor."""
    print("🚀 Starting worker with supervisor...")
    
    # [C7-ID: METRICS-REGISTRATION] Импорт метрик для автоматической регистрации
    # Эти импорты нужны для того, чтобы метрики были доступны через /metrics endpoint
    import metrics
    from ai_providers.gigachain_adapter import tagging_requests_total, tagging_latency_seconds
    from ai_providers.embedding_service import embedding_requests_total, embedding_latency_seconds
    from tasks.tagging_task import tagging_processed_total
    from tasks.indexing_task import indexing_processed_total
    
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
