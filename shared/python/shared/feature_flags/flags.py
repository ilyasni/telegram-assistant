"""
Unified Feature Flags System with Pydantic Settings and OpenFeature semantics.

[C7-ID: FEATURE-FLAGS-002] Context7 best practice: типобезопасные feature flags
See: https://github.com/pydantic/pydantic-settings
"""

import os
from typing import List, Optional
from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    import structlog
    logger = structlog.get_logger()
except ImportError:
    # Fallback если structlog недоступен
    import logging
    logger = logging.getLogger(__name__)


class FlagVariant(str, Enum):
    """OpenFeature-compatible flag variants."""
    ON = "on"
    OFF = "off"


class FlagReason(str, Enum):
    """OpenFeature-compatible evaluation reasons."""
    DEFAULT = "DEFAULT"
    TARGETING_MATCH = "TARGETING_MATCH"
    SPLIT = "SPLIT"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class IntegrationFlags(BaseSettings):
    """Integration feature flags (Neo4j, GigaChat, OpenRouter, etc.)."""
    
    model_config = SettingsConfigDict(
        env_prefix="FEATURE_",
        case_sensitive=False,
    )
    
    neo4j_enabled: bool = Field(default=True, description="Enable Neo4j integration")
    gigachat_enabled: bool = Field(default=True, description="Enable GigaChat AI provider")
    openrouter_enabled: bool = Field(default=True, description="Enable OpenRouter fallback")
    crawl4ai_enabled: bool = Field(default=True, description="Enable Crawl4AI integration")
    legacy_redis_consumer_enabled: bool = Field(
        default=False,
        description="Enable legacy Redis consumer (deprecated)"
    )


class DiagnosticFlags(BaseSettings):
    """Diagnostic feature flags for debugging and testing."""
    
    model_config = SettingsConfigDict(
        env_prefix="AUTH_",
        case_sensitive=False,
    )
    
    finalize_db_bypass: bool = Field(
        default=False,
        alias="AUTH_FINALIZE_DB_BYPASS",
        description="Bypass DB operations in auth finalize (diagnostics)"
    )
    detailed_diagnostics: bool = Field(
        default=True,
        alias="AUTH_DETAILED_DIAGNOSTICS",
        description="Enable detailed diagnostic logging"
    )
    retry_operational_errors: bool = Field(
        default=True,
        alias="AUTH_RETRY_OPERATIONAL_ERRORS",
        description="Retry on OperationalError"
    )
    soft_degradation: bool = Field(
        default=False,
        alias="AUTH_SOFT_DEGRADATION",
        description="Enable soft degradation mode"
    )
    log_sql_statements: bool = Field(
        default=False,
        alias="AUTH_LOG_SQL_STATEMENTS",
        description="Log SQL statements (debugging)"
    )


class ExperimentFlags(BaseSettings):
    """Experiment flags for A/B testing (future use)."""
    
    model_config = SettingsConfigDict(
        env_prefix="EXPERIMENT_",
        case_sensitive=False,
    )
    
    # Placeholder for future experiments
    pass


class FeatureFlags:
    """
    Unified Feature Flags System.
    
    Combines integration, diagnostic, and experiment flags with:
    - Type safety via Pydantic
    - OpenFeature-compatible semantics
    - Runtime cache with TTL support (future)
    """
    
    def __init__(self):
        self.integrations = IntegrationFlags()
        self.diagnostics = DiagnosticFlags()
        self.experiments = ExperimentFlags()
        
        # Runtime cache for AI providers
        self._providers_cache: Optional[List[str]] = None
        self._last_refresh_ok: bool = True
        
        self._log_status()
    
    def _log_status(self) -> None:
        """Логировать статус всех feature flags."""
        logger.info(
            "Feature flags initialized",
            integrations={
                "neo4j": self.integrations.neo4j_enabled,
                "gigachat": self.integrations.gigachat_enabled,
                "openrouter": self.integrations.openrouter_enabled,
                "crawl4ai": self.integrations.crawl4ai_enabled,
            },
            diagnostics={
                "detailed": self.diagnostics.detailed_diagnostics,
                "retry_errors": self.diagnostics.retry_operational_errors,
            },
            last_refresh_ok=self._last_refresh_ok,
        )
    
    def get_flag(
        self,
        flag_name: str,
        default: bool = False,
        variant: Optional[FlagVariant] = None,
    ) -> tuple[bool, FlagReason]:
        """
        Get flag value with OpenFeature semantics.
        
        Returns:
            (value, reason) tuple
        """
        # Check integrations
        if hasattr(self.integrations, flag_name):
            value = getattr(self.integrations, flag_name, default)
            return value, FlagReason.DEFAULT
        
        # Check diagnostics (with AUTH_ prefix handling)
        if flag_name.startswith("AUTH_"):
            clean_name = flag_name.replace("AUTH_", "").lower()
            if hasattr(self.diagnostics, clean_name):
                value = getattr(self.diagnostics, clean_name, default)
                return value, FlagReason.DEFAULT
        
        # Check experiments
        if hasattr(self.experiments, flag_name):
            value = getattr(self.experiments, flag_name, default)
            return value, FlagReason.DEFAULT
        
        logger.warning(f"Unknown flag: {flag_name}, returning default", default=default)
        return default, FlagReason.ERROR
    
    def is_enabled(self, flag_name: str, default: bool = False) -> bool:
        """Check if flag is enabled (backward compatibility)."""
        value, _ = self.get_flag(flag_name, default)
        return value
    
    def get_available_ai_providers(self) -> List[str]:
        """
        Returns list of available AI providers.
        GigaChat PRIMARY, OpenRouter FALLBACK.
        Cached for performance.
        """
        if self._providers_cache is not None:
            return self._providers_cache
        
        providers = []
        
        # GigaChat PRIMARY (including embeddings)
        if self.integrations.gigachat_enabled:
            if os.getenv("GIGACHAT_API_KEY") or os.getenv("GIGACHAT_CREDENTIALS"):
                providers.append("gigachat")
                logger.info("GigaChat enabled as PRIMARY provider")
        
        # OpenRouter FALLBACK (free models only)
        if self.integrations.openrouter_enabled:
            if os.getenv("OPENROUTER_API_KEY"):
                providers.append("openrouter")
                logger.info(
                    "OpenRouter enabled as FALLBACK provider",
                    model=os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct:free"),
                )
        
        self._providers_cache = providers
        return providers
    
    def refresh(self) -> bool:
        """
        Hot reload flags from environment (future: Redis/DB provider).
        Returns True if refresh succeeded.
        """
        try:
            self.integrations = IntegrationFlags()
            self.diagnostics = DiagnosticFlags()
            self.experiments = ExperimentFlags()
            self._providers_cache = None  # Invalidate cache
            self._last_refresh_ok = True
            logger.info("Feature flags refreshed successfully")
            return True
        except Exception as e:
            logger.error("Feature flags refresh failed", error=str(e))
            self._last_refresh_ok = False
            return False


# Global singleton instance
feature_flags = FeatureFlags()

