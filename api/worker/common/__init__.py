# Package marker for worker.common utilities.
from __future__ import annotations

"""
Общие утилиты для worker.

Context7: пакет для переиспользуемых компонентов (state store, retry helpers,
baseline-tools и др.), которые не тянут тяжёлых зависимостей.
"""

__all__ = ["digest_state_store"]

