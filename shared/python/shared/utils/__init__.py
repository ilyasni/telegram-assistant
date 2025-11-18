"""Utility subpackage exports (Context7)."""

# Context7: Опциональный импорт phash - не блокирует другие утилиты
try:
    from .phash import PhashResult, compute_phash, hamming_distance
    __all__ = [
        "PhashResult",
        "compute_phash",
        "hamming_distance",
    ]
except (RuntimeError, ImportError):
    # phash недоступен (imagehash не установлен) - не критично для других утилит
    __all__ = []


