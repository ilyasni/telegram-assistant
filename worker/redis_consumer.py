"""
⚠️ DEPRECATED ⚠️ 

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Простая реализация для восстановления пайплайна, заменена на tagging_task.py + tag_persistence_task.py
Replacement: worker/tasks/tagging_task.py + worker/tasks/tag_persistence_task.py

Этот файл перемещён в legacy/deprecated_2025-01-30/
[C7-ID: CODE-CLEANUP-025] Context7 best practice: карантин deprecated кода
"""

import warnings
import os
import sys

# Runtime guard: блокируем импорт в production
if os.getenv("ENV") == "production":
    raise ImportError(
        "redis_consumer.py is deprecated and cannot be imported in production. "
        "Use worker/tasks/tagging_task.py + worker/tasks/tag_persistence_task.py instead. "
        "See legacy/deprecated_2025-01-30/README.md"
    )

warnings.warn(
    "worker.redis_consumer is DEPRECATED and has been moved to legacy/. "
    "Use worker/tasks/tagging_task.py + worker/tasks/tag_persistence_task.py instead. "
    "See legacy/deprecated_2025-01-30/README.md",
    DeprecationWarning,
    stacklevel=2
)

# Показать предупреждение при попытке запуска
if __name__ == "__main__":
    print("ERROR: redis_consumer.py is DEPRECATED")
    print("Use: worker/tasks/tagging_task.py + worker/tasks/tag_persistence_task.py")
    print("See: legacy/deprecated_2025-01-30/README.md")
    sys.exit(1)
