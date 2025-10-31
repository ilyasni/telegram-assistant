"""Конфигурация для API сервиса."""

import os
import json
from pydantic_settings import BaseSettings
from typing import Optional
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
    cors_origins: list = ["*"]
    
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
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Глобальный экземпляр настроек
settings = Settings()

# [C7-ID: dev-mode-004] Fail-fast защита CORS в production
try:
    app_env = os.getenv("APP_ENV", "production").lower()
except Exception:
    app_env = "production"

if app_env == "production":
    # Нормализуем значения origins к строкам
    try:
        origins = [str(o).strip() for o in (settings.cors_origins or [])]
    except Exception as e:
        # Явная ошибка конфигурации, чтобы не маскировать проблемy проверкой wildcard
        raise RuntimeError("Invalid CORS_ORIGINS configuration: unable to normalize values") from e
    if any(o == "*" for o in origins):
        raise RuntimeError("CORS '*' запрещён в production")
