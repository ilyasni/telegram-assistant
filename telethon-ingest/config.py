"""Конфигурация для telethon-ingest сервиса."""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Настройки приложения."""
    
    # Telegram API
    master_api_id: int
    master_api_hash: str
    
    # Database
    database_url: str
    
    # Redis
    redis_url: str
    
    # Application
    environment: str = "development"
    log_level: str = "INFO"
    
    # Telethon settings
    session_name: str = "telegram_assistant"
    flood_sleep_threshold: int = 60
    retry_delay: int = 1
    max_retries: int = 3
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Глобальный экземпляр настроек
settings = Settings()
