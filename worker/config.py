"""Конфигурация для Event Worker."""

import os
from typing import List


class Settings:
    """Настройки приложения."""
    
    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    
    # Database
    database_url: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:54322/postgres")
    
    # Worker settings
    worker_name: str = os.getenv("WORKER_NAME", "event-worker")
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    retry_delay: int = int(os.getenv("RETRY_DELAY", "5"))
    
    # Consumer groups
    consumer_groups: List[str] = [
        "rag-indexer",
        "webhook-notifier", 
        "tagging",
        "analytics"
    ]
    
    # Webhook settings
    webhook_timeout: int = int(os.getenv("WEBHOOK_TIMEOUT", "30"))
    webhook_retry_count: int = int(os.getenv("WEBHOOK_RETRY_COUNT", "3"))
    
    # RAG settings
    qdrant_url: str = os.getenv("QDRANT_URL", "http://qdrant:6333")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "telegram_posts")
    
    # Analytics settings
    analytics_enabled: bool = os.getenv("ANALYTICS_ENABLED", "true").lower() == "true"
    analytics_batch_size: int = int(os.getenv("ANALYTICS_BATCH_SIZE", "100"))
    
    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")


settings = Settings()