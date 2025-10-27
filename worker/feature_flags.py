"""
Feature flags для graceful degradation интеграций.
[C7-ID: FEATURE-FLAGS-001]
"""
import os
from typing import Optional, List
import structlog

logger = structlog.get_logger()

class FeatureFlags:
    """Feature flags для graceful degradation интеграций."""
    
    def __init__(self):
        self.neo4j_enabled = self._get_flag("FEATURE_NEO4J_ENABLED", True)
        self.gigachat_enabled = self._get_flag("FEATURE_GIGACHAT_ENABLED", True)
        self.openrouter_enabled = self._get_flag("FEATURE_OPENROUTER_ENABLED", True)
        self.crawl4ai_enabled = self._get_flag("FEATURE_CRAWL4AI_ENABLED", True)
        
        # [DEPRECATED] Legacy Redis Consumer
        self.legacy_redis_consumer_enabled = self._get_flag("LEGACY_REDIS_CONSUMER_ENABLED", False)
        
        self._providers_cache: Optional[List[str]] = None
        self._log_status()
    
    def _get_flag(self, name: str, default: bool) -> bool:
        """Получить значение feature flag из env."""
        value = os.getenv(name, str(default)).lower()
        return value in ("true", "1", "yes", "on")
    
    def _log_status(self):
        """Логировать статус всех feature flags."""
        logger.info("Feature flags initialized", 
                    neo4j=self.neo4j_enabled,
                    gigachat=self.gigachat_enabled,
                    openrouter=self.openrouter_enabled,
                    crawl4ai=self.crawl4ai_enabled,
                    legacy_redis_consumer=self.legacy_redis_consumer_enabled)
    
    def get_available_ai_providers(self) -> List[str]:
        """
        Возвращает список доступных AI провайдеров.
        GigaChat PRIMARY, OpenRouter FALLBACK.
        С кешированием для производительности.
        """
        if self._providers_cache is not None:
            return self._providers_cache
        
        providers = []
        
        # GigaChat PRIMARY (включая embeddings)
        if self.gigachat_enabled and (os.getenv("GIGACHAT_API_KEY") or os.getenv("GIGACHAT_CREDENTIALS")):
            providers.append("gigachat")
            logger.info("GigaChat enabled as PRIMARY provider")
        
        # OpenRouter FALLBACK (только бесплатные модели)
        if self.openrouter_enabled and os.getenv("OPENROUTER_API_KEY"):
            providers.append("openrouter")
            logger.info("OpenRouter enabled as FALLBACK provider", 
                       model=os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct:free"))
        
        self._providers_cache = providers
        return providers

# Singleton
feature_flags = FeatureFlags()
