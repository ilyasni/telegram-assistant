"""
⚠️ DEPRECATED ⚠️ 

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Зависит от deprecated UnifiedSessionManager
Replacement: TelegramClientManager + session_storage.py

Этот файл перемещён в legacy/deprecated_2025-01-30/
[C7-ID: CODE-CLEANUP-025] Context7 best practice: карантин deprecated кода
"""

import warnings
import os
import sys
from pathlib import Path

# Runtime guard: блокируем импорт в production
if os.getenv("ENV") == "production":
    raise ImportError(
        "backup_scheduler.py is deprecated and cannot be imported in production. "
        "See legacy/deprecated_2025-01-30/README.md"
    )

warnings.warn(
    "backup_scheduler.py is DEPRECATED and has been moved to legacy/. "
    "Use TelegramClientManager + session_storage.py instead. "
    "See legacy/deprecated_2025-01-30/README.md",
    DeprecationWarning,
    stacklevel=2
)

# Показать предупреждение при попытке запуска
if __name__ == "__main__":
    print("ERROR: backup_scheduler.py is DEPRECATED")
    print("See: legacy/deprecated_2025-01-30/README.md")
    sys.exit(1)
