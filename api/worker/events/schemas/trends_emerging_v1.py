"""
Trend Emerging Event v1 schema.
Context7: публикуется TrendDetectionWorker при срабатывании порога.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import Field

from .base import BaseEvent


class TrendEmergingEventV1(BaseEvent):
    """Emerging trend notification."""

    cluster_id: str
    cluster_key: str
    post_id: str
    channel_id: str
    channel_title: Optional[str] = None
    primary_topic: str
    keywords: List[str] = Field(default_factory=list)
    freq_short: int
    freq_baseline: int
    source_diversity: int
    burst_score: float
    coherence: float
    detected_at: datetime


