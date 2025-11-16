"""
Redis key schema for trend detection pipeline.

Context7: определяет единообразные имена ключей/окон для reactive и stable режимов.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class TrendWindow(str, Enum):
    """Поддерживаемые окна агрегации."""

    SHORT_5M = "5m"
    MID_1H = "1h"
    LONG_24H = "24h"

    @property
    def seconds(self) -> int:
        return {
            TrendWindow.SHORT_5M: 5 * 60,
            TrendWindow.MID_1H: 60 * 60,
            TrendWindow.LONG_24H: 24 * 60 * 60,
        }[self]


FREQUENCY_WINDOWS_MINUTES: Dict[TrendWindow, int] = {
    TrendWindow.SHORT_5M: 5,
    TrendWindow.MID_1H: 60,
    TrendWindow.LONG_24H: 24 * 60,
}

TRENDS_EMERGING_STREAM = "stream:trends.emerging"


@dataclass(frozen=True)
class TrendRedisSchema:
    """Хелперы для генерации ключей Redis."""

    namespace: str = "trend"

    def freq_key(self, cluster_id: str, window: TrendWindow) -> str:
        """Ключ для частоты упоминаний (time-series bucket)."""
        return f"{self.namespace}:{cluster_id}:freq:{window.value}"

    def roc_key(self, cluster_id: str) -> str:
        """Ключ для rate-of-change."""
        return f"{self.namespace}:{cluster_id}:roc"

    def burst_key(self, cluster_id: str) -> str:
        """Ключ для burst score (например, CUSUM)."""
        return f"{self.namespace}:{cluster_id}:burst"

    def source_set_key(self, cluster_id: str) -> str:
        """Множество источников (для source diversity)."""
        return f"{self.namespace}:{cluster_id}:sources"

    def coherence_key(self, cluster_id: str) -> str:
        """Последнее значение coherence score."""
        return f"{self.namespace}:{cluster_id}:coherence"

    def emerging_stream(self) -> str:
        """Имя Stream для событий emerging трендов."""
        return TRENDS_EMERGING_STREAM


