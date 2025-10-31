"""
⚠️ DEPRECATED ⚠️ 

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Роутер не подключен к main.py, зависит от deprecated UnifiedSessionManager
Replacement: api/routers/tg_auth.py для авторизации через miniapp

Этот файл перемещён в legacy/deprecated_2025-01-30/
[C7-ID: CODE-CLEANUP-025] Context7 best practice: карантин deprecated кода
"""

import warnings
import os

# Runtime guard: блокируем импорт в production
if os.getenv("ENV") == "production":
    raise ImportError(
        "miniapp_auth.py is deprecated and cannot be imported in production. "
        "Use api/routers/tg_auth.py instead. "
        "See legacy/deprecated_2025-01-30/README.md"
    )

warnings.warn(
    "api.miniapp_auth is DEPRECATED and has been moved to legacy/. "
    "Use api/routers/tg_auth.py instead. "
    "See legacy/deprecated_2025-01-30/README.md",
    DeprecationWarning,
    stacklevel=2
)

# Заглушка для обратной совместимости
router = None
