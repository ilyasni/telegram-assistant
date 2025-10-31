"""
⚠️ DEPRECATED ⚠️

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Дубликат функциональности worker/health.py (который использует feature flags)
Replacement: from worker.health import check_integrations

Этот файл перемещён из worker/health_check.py в legacy/
[C7-ID: CODE-CLEANUP-025] Context7 best practice: карантин deprecated кода
"""

import warnings
import os

# Runtime guard: блокируем импорт в production
if os.getenv("ENV") == "production":
    raise ImportError(
        "worker/health_check.py is deprecated. "
        "Use 'from worker.health import check_integrations' instead. "
        "See legacy/deprecated_2025-01-30/README.md"
    )

warnings.warn(
    "worker/health_check.py is DEPRECATED. "
    "Use worker/health.py (with feature flags support) instead. "
    "See legacy/deprecated_2025-01-30/README.md",
    DeprecationWarning,
    stacklevel=2
)

# Re-export для обратной совместимости (redirect to worker.health)
try:
    from worker.health import check_integrations
    from worker.health import check_redis, check_postgres, check_qdrant
    
    # Re-export HealthChecker если используется
    try:
        from worker.health_check import HealthChecker
    except ImportError:
        # Если HealthChecker не нужен, просто re-export функции
        pass
except ImportError:
    raise ImportError(
        "Cannot import from worker.health. "
        "Please ensure worker.health is available."
    )
