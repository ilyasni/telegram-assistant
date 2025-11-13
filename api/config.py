"""Конфигурация для API сервиса."""

import json
import os
from pydantic_settings import BaseSettings
from pydantic import field_validator, SecretStr, Field, model_validator
from typing import Optional


class Settings(BaseSettings):
    """Настройки приложения."""
    
    # Database
    database_url: str
    
    # Redis
    redis_url: str
    
    # JWT
    jwt_secret: SecretStr  # Обязательное поле, без дефолта
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
    
    @field_validator("digest_agent_canary_tenants", mode="before")
    @classmethod
    def parse_digest_canary_tenants(cls, v):
        """Context7: поддержка строкового и JSON-формата для списков canary-арендаторов."""
        if isinstance(v, str):
            cleaned = v.strip()
            if not cleaned:
                return []
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
            return [item.strip() for item in cleaned.split(",") if item.strip()]
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
    s3_secret_access_key: SecretStr = SecretStr("")  # SecretStr для безопасности
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
    
    # Context7: Feature flags для multi-tenant rollout
    feature_rls_enabled: bool = False  # Row Level Security (включать поэтапно)
    feature_identity_enabled: bool = True  # Использование identities вместо прямого telegram_id
    feature_rate_limit_per_user: bool = True  # Per-user/membership rate limiting
    digest_agent_enabled: bool = False  # Контроль глобального rollout групповых дайджестов
    digest_agent_canary_tenants: list[str] = []  # Allow-list арендаторов для canary
    digest_agent_version: str = "v1"
    
    # WebApp auth TTL (Context7)
    webapp_auth_ttl_seconds: int = 900  # 15 минут по умолчанию
    
    # SearXNG Configuration - Context7: для внешнего поиска (external search grounding)
    # Основано на best practices из n8n-installer: https://github.com/kossakovsky/n8n-installer
    # Вариант A: Используем внутренний URL для прямого доступа (минуя Caddy)
    # Это позволяет избежать проблем с bot detection при внутреннем использовании
    searxng_url: str = "http://searxng:8080"
    # Вариант B (если нужен HTTPS через Caddy): "https://searxng.produman.studio"
    searxng_enabled: bool = True
    searxng_cache_ttl: int = 3600  # TTL кэша в секундах
    searxng_max_results: int = 5
    searxng_rate_limit_per_user: int = 10  # Запросов в минуту на пользователя
    # Context7: BasicAuth для SearXNG (если требуется)
    searxng_user: str = ""
    searxng_password: SecretStr = SecretStr("")  # SecretStr для безопасности
    
    # Context7: SearXNG Enrichment Configuration - обогащение ответов внешними источниками
    # Используется для улучшения ответов при низкой уверенности или малом количестве результатов
    searxng_enrichment_enabled: bool = True  # Включить/выключить обогащение
    searxng_enrichment_confidence_threshold: float = 0.5  # Порог уверенности для обогащения (0.0-1.0)
    searxng_enrichment_min_results_threshold: int = 3  # Минимальное количество результатов для обогащения
    searxng_enrichment_score_threshold: float = 0.6  # Порог среднего score для обогащения (0.0-1.0)
    searxng_enrichment_max_external_results: int = 2  # Максимум внешних результатов для обогащения
    
    # SaluteSpeech Configuration - Context7: для транскрибации голосовых сообщений
    salutespeech_client_id: str = ""
    salutespeech_client_secret: SecretStr = SecretStr("")  # SecretStr для безопасности
    salutespeech_scope: str = "SALUTE_SPEECH_PERS"
    salutespeech_url: str = "https://smartspeech.sber.ru/rest/v1"
    voice_transcription_enabled: bool = True
    voice_max_duration_sec: int = 60
    voice_cache_ttl: int = 86400
    
    # RAG Conversation Context - Context7: для multi-turn conversations
    rag_conversation_history_enabled: bool = True
    rag_max_conversation_turns: int = 5  # Максимальное количество пар вопрос-ответ для контекста
    rag_conversation_window_hours: int = 24  # Окно времени для истории (часы)
    
    # OpenAI-compatible API (gpt2giga-proxy) - Context7: для работы с GigaChat через LangChain
    # Context7: Используем URL без /v1, так как прокси может перенаправлять на /chat/completions
    # LangChain автоматически добавит /v1 при необходимости
    openai_api_base: str = "http://gpt2giga-proxy:8090"
    openai_api_key: str = "dummy"  # gpt2giga-proxy игнорирует, использует GIGACHAT_CREDENTIALS
    
    # GigaChat Configuration - Context7: для прямого использования GigaChat API
    gigachat_credentials: SecretStr = SecretStr("")  # SecretStr для безопасности
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_proxy_url: str = "http://gpt2giga-proxy:8090"  # URL gpt2giga-proxy для embeddings
    
    # Qdrant Configuration - Context7: для векторного поиска
    qdrant_url: str = "http://qdrant:6333"
    
    # Neo4j Configuration - Context7: для GraphRAG и графа знаний
    neo4j_uri: str = "neo4j://neo4j:7687"
    neo4j_user: str = "neo4j"
    # Context7: Optional для dev окружения, обязательное для production
    neo4j_password: Optional[SecretStr] = Field(
        default=None,
        description="Neo4j password (required in production, optional in dev with default 'changeme')"
    )
    neo4j_vector_index_name: str = "post_embeddings"
    neo4j_fulltext_index_name: str = "post_fulltext"
    neo4j_interest_sync_interval_min: int = 15
    neo4j_max_graph_depth: int = 2  # Максимальная глубина обхода графа для производительности
    
    @model_validator(mode="after")
    def set_neo4j_password_default(self):
        """Context7: Устанавливает дефолтный пароль, если переменная не установлена."""
        if self.neo4j_password is None:
            # Context7: Если переменная NEO4J_PASSWORD не установлена, используем дефолт
            # Это безопасно, так как в docker-compose.yml используется дефолт "changeme" для Neo4j
            # В production рекомендуется установить NEO4J_PASSWORD явно
            self.neo4j_password = SecretStr("changeme")
        return self
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Глобальный экземпляр настроек
settings = Settings()
