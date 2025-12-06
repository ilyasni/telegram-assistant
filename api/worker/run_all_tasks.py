#!/usr/bin/env python3
"""
Запуск всех worker tasks с supervisor pattern для автоперезапуска.
"""

import asyncio
import sys
import os
import logging
from pathlib import Path

import importlib
import yaml

# Добавляем текущую директорию и project root в PYTHONPATH
CURRENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_DIR))

PROJECT_ROOT = Path("/opt/telegram-assistant")
PROJECT_API = PROJECT_ROOT / "api"
PROJECT_WORKER = PROJECT_ROOT / "worker"
TASKS_DIR = CURRENT_DIR / "tasks"

for candidate in (
    PROJECT_ROOT,
    PROJECT_API,
    PROJECT_WORKER,
    TASKS_DIR,
    PROJECT_API / "worker",
):
    try:
        if candidate.exists():
            sys.path.insert(0, str(candidate))
    except Exception:
        continue


def _import_task(module: str, attr: str | None = None):
    """
    Универсальный импорт с fallback:
    1. tasks.<module>
    2. worker.tasks.<module>
    """
    for prefix in ("tasks", "worker.tasks"):
        try:
            loaded = importlib.import_module(f"{prefix}.{module}")
            return getattr(loaded, attr) if attr else loaded
        except ModuleNotFoundError:
            continue
    loaded = importlib.import_module(module)  # последняя попытка (если путь абсолютный)
    return getattr(loaded, attr) if attr else loaded

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

from supervisor import TaskSupervisor, TaskConfig

TaggingTask = _import_task("tagging_task", "TaggingTask")
EnrichmentWorker = _import_task("enrichment_task", "EnrichmentWorker")
IndexingTask = _import_task("indexing_task", "IndexingTask")
TagPersistenceTask = _import_task("tag_persistence_task", "TagPersistenceTask")
CrawlTriggerTask = _import_task("crawl_trigger_task", "CrawlTriggerTask")
PostPersistenceWorker = _import_task("post_persistence_task", "PostPersistenceWorker")
RetaggingTask = _import_task("retagging_task", "RetaggingTask")
AlbumAssemblerTask = _import_task("album_assembler_task", "AlbumAssemblerTask")
TrendDetectionWorker = _import_task("trends_worker", "TrendDetectionWorker")
TrendEditorAgent = _import_task("trends_editor_agent", "TrendEditorAgent")
create_trend_editor_agent = _import_task("trends_editor_agent", "create_trend_editor_agent")
TrendRefinementTask = _import_task("trends_refinement_task", "TrendRefinementTask")
create_refinement_task = _import_task("trends_refinement_task", "create_refinement_task")

digest_worker = _import_task("digest_worker")
create_digest_worker_task = getattr(digest_worker, "create_digest_worker_task")
digest_jobs_processed_total = getattr(digest_worker, "digest_jobs_processed_total")
digest_worker_generation_seconds = getattr(digest_worker, "digest_worker_generation_seconds")
digest_worker_send_seconds = getattr(digest_worker, "digest_worker_send_seconds")

create_digest_context_task = _import_task("context_events_task", "create_digest_context_task")

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
    try:
        logger.info("create_crawl_trigger_task: Starting initialization")
    except Exception as e:
        logger.error(f"create_crawl_trigger_task: Error in initial logging: {e}")
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")

    default_trigger_tags = [
        'longread', 'research', 'paper', 'release', 'law',
        'deepdive', 'analysis', 'report', 'study', 'whitepaper'
    ]
    trigger_tags = default_trigger_tags

    try:
        logger.info("create_crawl_trigger_task: Loading config")
    except Exception as e:
        logger.error(f"create_crawl_trigger_task: Error in config loading logging: {e}")

    config_env_path = os.getenv("ENRICHMENT_CONFIG_PATH", "/app/config/enrichment_policy.yml")
    candidate_paths = [
        Path(config_env_path),
        Path(__file__).resolve().parent / "config" / "enrichment_policy.yml",
        Path(__file__).resolve().parent.parent / "config" / "enrichment_policy.yml",
    ]

    for path in candidate_paths:
        if not path:
            continue
        if not path.is_absolute():
            path = (Path(__file__).resolve().parent / path).resolve()
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as cfg:
                config = yaml.safe_load(cfg) or {}
                loaded_tags = config.get("crawl4ai", {}).get("trigger_tags")
                if isinstance(loaded_tags, list) and loaded_tags:
                    trigger_tags = [str(tag).strip() for tag in loaded_tags if str(tag).strip()]
                    trigger_tags = list(dict.fromkeys(trigger_tags))
                    try:
                        logger.info(
                            f"Crawl trigger tags loaded from config: {str(path)}, tags_count={len(trigger_tags)}"
                        )
                    except Exception as log_err:
                        logger.error(f"Error in logger.info: {log_err}")
                else:
                    try:
                        logger.debug(
                            f"Crawl trigger tags list empty in config, using defaults: {str(path)}"
                        )
                    except Exception as log_err:
                        logger.error(f"Error in logger.debug: {log_err}")
            break
        except Exception as config_error:
            try:
                logger.warning(
                    f"Failed to load crawl trigger tags: {str(path)}, error={str(config_error)}"
                )
            except Exception as log_err:
                logger.error(f"Error in logger.warning: {log_err}, original_error={str(config_error)}")
    else:
        try:
            logger.debug(
                f"Using default crawl trigger tags: tags_count={len(trigger_tags)}"
            )
        except Exception as log_err:
            logger.error(f"Error in logger.debug (default tags): {log_err}")
    
    try:
        logger.info("create_crawl_trigger_task: Creating CrawlTriggerTask instance")
    except Exception as e:
        logger.error(f"create_crawl_trigger_task: Error before creating task: {e}")
    
    try:
        task = CrawlTriggerTask(
            redis_url=redis_url,
            trigger_tags=trigger_tags,
            db_dsn=database_url
        )
        logger.info("create_crawl_trigger_task: CrawlTriggerTask created, calling start()")
    except Exception as e:
        logger.error(f"create_crawl_trigger_task: Error creating CrawlTriggerTask: {e}", exc_info=True)
        raise
    
    try:
        await task.start()
    except Exception as e:
        logger.error(f"create_crawl_trigger_task: Error in task.start(): {e}", exc_info=True)
        raise

async def create_post_persistence_task():
    """Создание и запуск PostPersistenceWorker."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    worker = PostPersistenceWorker(redis_url=redis_url, database_url=database_url)
    await worker.initialize()
    await worker.start()

async def create_retagging_task():
    """Context7: Создание и запуск Retagging Task."""
    try:
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        logger.info("Creating RetaggingTask instance")
        task = RetaggingTask(redis_url)
        logger.info("RetaggingTask instance created, calling start()")
        await task.start()
        # Context7: Не возвращаемся из start() - задачи работают в бесконечном цикле
    except Exception as e:
        logger.warning(
            "RetaggingTask skipped",
            error=str(e),
            error_type=type(e).__name__,
            error_repr=repr(e),
            exc_info=True
        )
        raise

async def create_album_assembler_task():
    """Context7: Создание и запуск Album Assembler Task (Phase 2-4)."""
    try:
        import redis.asyncio as redis
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from event_bus import EventPublisher, RedisStreamsClient
        from api.services.s3_storage import S3StorageService
        from run_all_tasks_vision_helper import get_s3_config_from_env
        
        # Инициализация Redis
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        # Context7: AlbumAssemblerTask использует redis.asyncio.Redis напрямую
        redis_client = redis.Redis.from_url(redis_url, decode_responses=False)
        
        # Инициализация БД
        db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
        if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
        
        engine = create_async_engine(db_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        db_session = async_session()
        
        # Инициализация EventPublisher
        # Context7: EventPublisher требует RedisStreamsClient (обёртку над redis.Redis)
        redis_streams_client = RedisStreamsClient(redis_url)
        await redis_streams_client.connect()
        event_publisher = EventPublisher(redis_streams_client)
        
        # Инициализация S3 (опционально, для сохранения vision summary)
        s3_service = None
        try:
            s3_config = get_s3_config_from_env()
            if s3_config and s3_config.get('access_key_id') and s3_config.get('secret_access_key'):
                s3_service = S3StorageService(
                    endpoint_url=s3_config['endpoint_url'],
                    access_key_id=s3_config['access_key_id'],
                    secret_access_key=s3_config['secret_access_key'],
                    bucket_name=s3_config['bucket_name'],
                    region=s3_config.get('region', 'ru-central-1')
                )
                logger.info("S3 service initialized for album assembler")
        except Exception as e:
            logger.warning(f"S3 service not available for album assembler: {e}")
        
        # Создание задачи
        task = AlbumAssemblerTask(
            redis_client=redis_client,
            db_session=db_session,
            event_publisher=event_publisher,
            s3_service=s3_service
        )
        
        logger.info("AlbumAssemblerTask created and starting...")
        await task.start()
        # Context7: Не возвращаемся из start() - задача работает в бесконечном цикле
        
    except Exception as e:
        logger.error(f"Failed to create AlbumAssemblerTask: {e}", exc_info=True)
        raise


async def create_trend_worker_task():
    """Создание и запуск TrendDetectionWorker (reactive тренды)."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres",
    )
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    worker = TrendDetectionWorker(
        redis_url=redis_url,
        database_url=database_url,
        qdrant_url=qdrant_url,
    )
    await worker.start()


async def create_trend_editor_agent_task():
    """Context7: Создание и запуск TrendEditorAgent (редактор карточек трендов)."""
    agent = await create_trend_editor_agent()
    await agent.start()

async def create_trend_refinement_task():
    """Context7: Создание и запуск TrendRefinementTask (периодический рефайнмент кластеров)."""
    # Импортируем функцию напрямую, так как глобальный импорт может не работать
    _create_refinement_task = _import_task("trends_refinement_task", "create_refinement_task")
    task = _create_refinement_task()
    await task.start()

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
    # Context7: Импорт метрик S3 для автоматической регистрации
    try:
        from shared.s3_storage.service import (
            s3_operations_total,
            s3_upload_duration_seconds,
            s3_file_size_bytes,
            s3_compression_ratio
        )
        logger.debug("S3 metrics imported successfully")
    except ImportError:
        logger.warning("S3 metrics not available (shared.s3_storage may not be loaded)")
    # Context7: Импорт метрик ретеггинга для регистрации
    try:
        from tasks.retagging_task import (
            retagging_processed_total,
            retagging_duration_seconds,
            retagging_dlq_total,
            retagging_skipped_total
        )
    except ImportError:
        logger.debug("RetaggingTask metrics not available (module may not be loaded)")
    
    # Context7: Импорт метрик album assembler для регистрации (Phase 4)
    try:
        from tasks.album_assembler_task import (
            albums_parsed_total,
            albums_assembled_total,
            album_assembly_lag_seconds,
            album_items_count_gauge,
            album_vision_summary_size_bytes,
            album_aggregation_duration_ms
        )
    except ImportError:
        logger.debug("AlbumAssemblerTask metrics not available (module may not be loaded)")
    
    # Context7: Импорт метрик post persistence для регистрации
    try:
        from tasks.post_persistence_task import (
            post_persistence_processed_total,
            post_persistence_latency_seconds,
            post_persistence_db_operations_total,
            post_persistence_pel_size
        )
    except ImportError:
        logger.debug("PostPersistenceTask metrics not available (module may not be loaded)")
    
    # Context7: Импорт метрик PostgreSQL для регистрации
    try:
        from api.utils.postgres_metrics import (
            postgres_operations_total,
            postgres_operation_duration_seconds,
            postgres_connections_active,
            postgres_connections_max
        )
    except ImportError:
        logger.debug("PostgreSQL metrics not available (module may not be loaded)")
    
    # Context7: Импорт метрик Qdrant для регистрации
    try:
        from worker.integrations.qdrant_client import (
            qdrant_operations_total,
            qdrant_operation_duration_seconds,
            qdrant_collection_size,
            qdrant_collection_indexed
        )
    except ImportError:
        logger.debug("Qdrant metrics not available (module may not be loaded)")
    
    # Context7: Метрики refinement автоматически регистрируются при импорте модуля
    # Импорт здесь не нужен, чтобы избежать дублирования регистрации
    try:
        # Только проверяем, что модуль доступен для импорта
        import api.worker.trends_refinement_service
        logger.debug("Trend refinement service module available")
    except ImportError:
        logger.debug("Trend refinement service not available (module may not be loaded)")
    
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
        
        # Инициализация метрик digest worker
        for stage in ["generate", "send"]:
            for status in ["success", "failed"]:
                digest_jobs_processed_total.labels(stage=stage, status=status).inc(0)
        for status in ["success", "failed"]:
            digest_worker_generation_seconds.labels(status=status).observe(0)
            digest_worker_send_seconds.labels(status=status).observe(0)

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
    
    # Context7: Retagging Task (подписан на posts.vision.analyzed)
    supervisor.register_task(TaskConfig(
        name="retagging",
        task_func=create_retagging_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    # Context7: Album Assembler Task (Phase 2-4)
    supervisor.register_task(TaskConfig(
        name="album_assembler",
        task_func=create_album_assembler_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))

    supervisor.register_task(TaskConfig(
        name="trend_detection",
        task_func=create_trend_worker_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    # Context7: Trend Editor Agent для улучшения качества карточек
    supervisor.register_task(TaskConfig(
        name="trend_editor",
        task_func=create_trend_editor_agent_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    supervisor.register_task(TaskConfig(
        name="digest_worker",
        task_func=create_digest_worker_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    supervisor.register_task(TaskConfig(
        name="digest_context_observer",
        task_func=create_digest_context_task,
        max_retries=5,
        initial_backoff=1.0,
        max_backoff=60.0,
        backoff_multiplier=2.0
    ))
    
    # Context7: Trend Refinement Task для периодического улучшения качества кластеров
    refinement_enabled = os.getenv("TREND_REFINEMENT_ENABLED", "true").lower() == "true"
    if refinement_enabled:
        try:
            # Пробуем импортировать функцию напрямую
            _create_refinement_task_func = _import_task("trends_refinement_task", "create_refinement_task")
            
            async def _create_trend_refinement_task():
                """Обёртка для создания TrendRefinementTask."""
                task = _create_refinement_task_func()
                await task.start()
            
            supervisor.register_task(TaskConfig(
                name="trend_refinement",
                task_func=_create_trend_refinement_task,
                max_retries=5,
                initial_backoff=1.0,
                max_backoff=60.0,
                backoff_multiplier=2.0
            ))
            logger.info("Trend refinement task registered successfully")
        except Exception as exc:
            logger.error("Failed to register trend refinement task", error=str(exc), exc_info=True)
    
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
