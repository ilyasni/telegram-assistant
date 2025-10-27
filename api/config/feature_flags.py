"""
Feature Flags Configuration
===========================

Feature flags для тестирования и диагностики.
"""

import os
from typing import Dict, Any

class FeatureFlags:
    """Управление feature flags."""
    
    def __init__(self):
        self._flags: Dict[str, Any] = {}
        self._load_from_env()
    
    def _load_from_env(self):
        """Загрузка флагов из переменных окружения."""
        # Auth finalize bypass для диагностики
        self._flags['AUTH_FINALIZE_DB_BYPASS'] = os.getenv('AUTH_FINALIZE_DB_BYPASS', 'off').lower() == 'on'
        
        # Детальная диагностика
        self._flags['AUTH_DETAILED_DIAGNOSTICS'] = os.getenv('AUTH_DETAILED_DIAGNOSTICS', 'on').lower() == 'on'
        
        # Retry на OperationalError
        self._flags['AUTH_RETRY_OPERATIONAL_ERRORS'] = os.getenv('AUTH_RETRY_OPERATIONAL_ERRORS', 'on').lower() == 'on'
        
        # Мягкая деградация
        self._flags['AUTH_SOFT_DEGRADATION'] = os.getenv('AUTH_SOFT_DEGRADATION', 'off').lower() == 'on'
        
        # Логирование SQL
        self._flags['AUTH_LOG_SQL_STATEMENTS'] = os.getenv('AUTH_LOG_SQL_STATEMENTS', 'off').lower() == 'on'
    
    def is_enabled(self, flag_name: str) -> bool:
        """Проверка включен ли флаг."""
        return self._flags.get(flag_name, False)
    
    def get_all_flags(self) -> Dict[str, Any]:
        """Получение всех флагов."""
        return self._flags.copy()
    
    def set_flag(self, flag_name: str, value: Any) -> None:
        """Установка флага."""
        self._flags[flag_name] = value
    
    def __str__(self) -> str:
        """Строковое представление флагов."""
        return f"FeatureFlags({self._flags})"


# Глобальный экземпляр
feature_flags = FeatureFlags()
