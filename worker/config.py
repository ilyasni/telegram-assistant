"""Конфигурация для Worker сервиса."""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Настройки приложения."""
    
    # Database
    database_url: str
    
    # Redis
    redis_url: str
    
    # Qdrant
    qdrant_url: str
    
    # Application
    environment: str = "development"
    log_level: str = "INFO"
    
    # Worker settings
    batch_size: int = 10
    processing_interval: int = 5  # секунды
    max_retries: int = 3
    retry_delay: int = 1
    
    # Embedding settings
    embedding_model: str = "hash-based"  # Используем хеш-эмбеддинги для демонстрации
    embedding_dimension: int = 128  # Уменьшаем размерность для хеш-эмбеддингов
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Глобальный экземпляр настроек
settings = Settings()
