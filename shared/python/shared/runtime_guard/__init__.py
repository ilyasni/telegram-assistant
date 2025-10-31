"""
Runtime Guard для legacy импортов.

[C7-ID: CODE-CLEANUP-026] Context7 best practice: runtime-страж для deprecated кода
"""

import os
import sys
from typing import Optional
from prometheus_client import Counter

# Метрика для отслеживания попыток импорта legacy кода
legacy_import_attempts_total = Counter(
    "legacy_import_attempts_total",
    "Total attempts to import legacy code",
    ["module", "env"],
)


def guard_legacy_import(module_name: str, replacement: str, reason: str = "") -> None:
    """
    Runtime guard для блокировки импорта legacy кода в production.
    
    Args:
        module_name: Имя deprecated модуля
        replacement: Рекомендуемая замена
        reason: Причина deprecation
    """
    env = os.getenv("ENV", "development")
    
    # Увеличиваем метрику
    legacy_import_attempts_total.labels(module=module_name, env=env).inc()
    
    # Блокируем в production
    if env == "production":
        error_msg = (
            f"{module_name} is deprecated and cannot be imported in production. "
            f"Use {replacement} instead."
        )
        if reason:
            error_msg += f" Reason: {reason}"
        raise ImportError(error_msg)

