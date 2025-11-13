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
    
    # Embeddings Configuration
    # Context7: Размерность зависит от модели:
    # - EmbeddingsGigaR: 2560 измерений (используется по умолчанию)
    # - Embeddings (Giga-Embeddings-instruct): 2048 измерений
    # Источник: https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gigachat-embeddings")
    EMBED_DIM: int = int(os.getenv("EMBED_DIM", os.getenv("EMBEDDING_DIMENSION", "2560")))
    INDEXER_EMBED_IF_MISSING: bool = os.getenv("INDEXER_EMBED_IF_MISSING", "true").lower() == "true"
    
    # GigaChat
    GIGACHAT_BASE_URL: str = os.getenv("GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1")
    GIGACHAT_EMBEDDINGS_MODEL: str = os.getenv("GIGACHAT_EMBEDDINGS_MODEL", "Embeddings")
    
    # RAG settings
    qdrant_url: str = os.getenv("QDRANT_URL", "http://qdrant:6333")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "telegram_posts")
    
    # Neo4j settings
    # Context7: Исправлен дефолтный пароль (должен совпадать с docker-compose.yml)
    neo4j_url: str = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
    neo4j_username: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "neo4j123")
    
    # Analytics settings
    analytics_enabled: bool = os.getenv("ANALYTICS_ENABLED", "true").lower() == "true"
    analytics_batch_size: int = int(os.getenv("ANALYTICS_BATCH_SIZE", "100"))
    
    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")


settings = Settings()