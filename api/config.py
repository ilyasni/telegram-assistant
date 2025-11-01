"""Конфигурация для API сервиса."""

import json
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    """Настройки приложения."""
    
    # Database
    database_url: str
    
    # Redis
    redis_url: str
    
    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    
    # Application
    environment: str = "development"
    log_level: str = "INFO"
    api_title: str = "Telegram Assistant API"
    api_version: str = "2.0.0"
    
    # CORS
    # Context7 best practice: безопасные дефолты (не используем wildcard с credentials)
    cors_origins: list = []
    
    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Парсинг CORS_ORIGINS: поддерживает JSON массив и строку через запятую."""
        if isinstance(v, str):
            # Попытка распарсить как JSON массив
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
            # Если не JSON, парсим как строку через запятую
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # Bot/Webhook
    telegram_bot_token: str | None = None
    bot_webhook_secret: str | None = None
    bot_public_url: str | None = None
    default_tenant_id: str | None = None
    
    # S3 Storage (Cloud.ru) - Context7: добавлены согласно плану Vision + S3 Integration
    # Cloud.ru Object Storage Service (OBS) - S3-compatible API
    s3_endpoint_url: str = "https://s3.cloud.ru"
    s3_bucket_name: str = "bucket-467940"  # Локальное имя для SDK операций
    s3_region: str = "ru-central-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_default_tenant_id: str = ""
    s3_use_compression: bool = True
    s3_compression_level: int = 6
    s3_multipart_threshold_mb: int = 5
    s3_presigned_ttl_seconds: int = 3600
    
    # Storage Limits (15 GB) - Context7: критическое ограничение
    s3_total_limit_gb: float = 15.0
    s3_emergency_threshold_gb: float = 14.0
    s3_per_tenant_limit_gb: float = 2.0
    s3_media_max_gb: float = 10.0
    s3_vision_max_gb: float = 2.0
    s3_crawl_max_gb: float = 2.0
    
    # Lifecycle TTL
    s3_media_ttl_days: int = 30
    s3_vision_ttl_days: int = 14
    s3_crawl_ttl_days: int = 7
    
    # Emergency cleanup
    s3_enable_emergency_cleanup: bool = True
    s3_cleanup_check_interval_hours: int = 6
    s3_target_after_cleanup_gb: float = 12.0
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Глобальный экземпляр настроек
settings = Settings()
