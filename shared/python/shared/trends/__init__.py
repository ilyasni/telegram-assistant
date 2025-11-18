"""
Shared helpers for trend detection modules.

Context7: пакет объявлен для переиспользования схем ключей Redis
между API, worker и ботом.
"""

from .redis_schema import (  # noqa: F401
    TrendRedisSchema,
    TrendWindow,
    TRENDS_EMERGING_STREAM,
    FREQUENCY_WINDOWS_MINUTES,
)

__all__ = [
    "TrendRedisSchema",
    "TrendWindow",
    "TRENDS_EMERGING_STREAM",
    "FREQUENCY_WINDOWS_MINUTES",
]


