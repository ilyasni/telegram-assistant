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
from typing import Dict, Any

# Runtime guard: предупреждение в production
if os.getenv("ENV") == "production":
    warnings.warn(
        "api.config.feature_flags is deprecated. Use shared.feature_flags instead.",
        DeprecationWarning,
        stacklevel=2
    )

warnings.warn(
    "api.config.feature_flags is DEPRECATED. Use shared.feature_flags instead. "
    "See docs/MIGRATION_FEATURE_FLAGS.md",
    DeprecationWarning,
    stacklevel=2
)

# Re-export из shared для обратной совместимости
try:
    from shared.feature_flags import feature_flags as _shared_flags
    
    class FeatureFlags:
        """Обёртка для обратной совместимости с api.config.feature_flags."""
        
        def __init__(self):
            self._flags: Dict[str, Any] = {}
            self._load_from_shared()
        
        def _load_from_shared(self):
            """Загрузка флагов из shared.feature_flags."""
            # Маппинг старых имён на новые
            self._flags['AUTH_FINALIZE_DB_BYPASS'] = _shared_flags.diagnostics.finalize_db_bypass
            self._flags['AUTH_DETAILED_DIAGNOSTICS'] = _shared_flags.diagnostics.detailed_diagnostics
            self._flags['AUTH_RETRY_OPERATIONAL_ERRORS'] = _shared_flags.diagnostics.retry_operational_errors
            self._flags['AUTH_SOFT_DEGRADATION'] = _shared_flags.diagnostics.soft_degradation
            self._flags['AUTH_LOG_SQL_STATEMENTS'] = _shared_flags.diagnostics.log_sql_statements
        
        def is_enabled(self, flag_name: str) -> bool:
            """Проверка включен ли флаг (backward compatibility)."""
            return self._flags.get(flag_name, False)
        
        def get_all_flags(self) -> Dict[str, Any]:
            """Получение всех флагов."""
            return self._flags.copy()
        
        def set_flag(self, flag_name: str, value: Any) -> None:
            """Установка флага (deprecated, используйте shared.feature_flags)."""
            warnings.warn(
                "set_flag() is deprecated. Feature flags should be set via environment variables.",
                DeprecationWarning,
                stacklevel=2
            )
            self._flags[flag_name] = value
        
        def __str__(self) -> str:
            return f"FeatureFlags({self._flags})"

    feature_flags = FeatureFlags()
    
except ImportError:
    # Fallback на старую реализацию если shared недоступен
    class FeatureFlags:
        """Fallback: старый FeatureFlags если shared недоступен."""
        
        def __init__(self):
            self._flags: Dict[str, Any] = {}
            self._load_from_env()
        
        def _load_from_env(self):
            """Загрузка флагов из переменных окружения."""
            self._flags['AUTH_FINALIZE_DB_BYPASS'] = os.getenv('AUTH_FINALIZE_DB_BYPASS', 'off').lower() == 'on'
            self._flags['AUTH_DETAILED_DIAGNOSTICS'] = os.getenv('AUTH_DETAILED_DIAGNOSTICS', 'on').lower() == 'on'
            self._flags['AUTH_RETRY_OPERATIONAL_ERRORS'] = os.getenv('AUTH_RETRY_OPERATIONAL_ERRORS', 'on').lower() == 'on'
            self._flags['AUTH_SOFT_DEGRADATION'] = os.getenv('AUTH_SOFT_DEGRADATION', 'off').lower() == 'on'
            self._flags['AUTH_LOG_SQL_STATEMENTS'] = os.getenv('AUTH_LOG_SQL_STATEMENTS', 'off').lower() == 'on'
            
            import structlog
            logger = structlog.get_logger()
            logger.warning(
                "Using deprecated api.config.feature_flags (fallback mode). "
                "Install shared package: pip install -e ./shared/python"
            )
        
        def is_enabled(self, flag_name: str) -> bool:
            return self._flags.get(flag_name, False)
        
        def get_all_flags(self) -> Dict[str, Any]:
            return self._flags.copy()
        
        def set_flag(self, flag_name: str, value: Any) -> None:
            self._flags[flag_name] = value
        
        def __str__(self) -> str:
            return f"FeatureFlags({self._flags})"

    feature_flags = FeatureFlags()
