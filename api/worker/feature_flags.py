"""
⚠️ DEPRECATED ⚠️

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Дубликат функциональности, миграция на shared.feature_flags
Replacement: from shared.feature_flags import feature_flags

Этот файл будет перемещён в legacy/ после полной миграции.
[C7-ID: CODE-CLEANUP-029] Context7 best practice: миграция на shared.feature_flags
"""

import warnings
import os

# Централизованные депрекейты
try:
    from shared.deprecations import warn_deprecated
    warn_deprecated(
        module="worker.feature_flags",
        replacement="shared.feature_flags",
        remove_by="2025-02-13"
    )
except ImportError:
    # Fallback если shared недоступен
    warnings.warn(
        "worker.feature_flags is DEPRECATED. Use shared.feature_flags instead. "
        "See docs/MIGRATION_FEATURE_FLAGS.md",
        DeprecationWarning,
        stacklevel=2
    )

# Re-export из shared для обратной совместимости
try:
    from shared.feature_flags import feature_flags
    from shared.feature_flags import FeatureFlags, IntegrationFlags, DiagnosticFlags
except ImportError:
    # Fallback на старую реализацию если shared недоступен
    import os
    from typing import Optional, List
    import structlog

    logger = structlog.get_logger()

    class FeatureFlags:
        """Fallback: старый FeatureFlags если shared недоступен."""
        
        def __init__(self):
            self.neo4j_enabled = self._get_flag("FEATURE_NEO4J_ENABLED", True)
            self.gigachat_enabled = self._get_flag("FEATURE_GIGACHAT_ENABLED", True)
            self.openrouter_enabled = self._get_flag("FEATURE_OPENROUTER_ENABLED", True)
            self.crawl4ai_enabled = self._get_flag("FEATURE_CRAWL4AI_ENABLED", True)
            self.legacy_redis_consumer_enabled = self._get_flag("LEGACY_REDIS_CONSUMER_ENABLED", False)
            self._providers_cache: Optional[List[str]] = None
            self._log_status()
        
        def _get_flag(self, name: str, default: bool) -> bool:
            value = os.getenv(name, str(default)).lower()
            return value in ("true", "1", "yes", "on")
        
        def _log_status(self):
            logger.warning(
                "Using deprecated worker.feature_flags (fallback mode). "
                "Install shared package: pip install -e ./shared/python"
            )
            logger.info("Feature flags initialized", 
                        neo4j=self.neo4j_enabled,
                        gigachat=self.gigachat_enabled,
                        openrouter=self.openrouter_enabled,
                        crawl4ai=self.crawl4ai_enabled,
                        legacy_redis_consumer=self.legacy_redis_consumer_enabled)
        
        def get_available_ai_providers(self) -> List[str]:
            """Возвращает список доступных AI провайдеров."""
            if self._providers_cache is not None:
                return self._providers_cache
            
            providers = []
            
            if self.gigachat_enabled and (os.getenv("GIGACHAT_API_KEY") or os.getenv("GIGACHAT_CREDENTIALS")):
                providers.append("gigachat")
                logger.info("GigaChat enabled as PRIMARY provider")
            
            if self.openrouter_enabled and os.getenv("OPENROUTER_API_KEY"):
                providers.append("openrouter")
                logger.info("OpenRouter enabled as FALLBACK provider", 
                           model=os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct:free"))
            
            self._providers_cache = providers
            return providers

    feature_flags = FeatureFlags()
