from typing import Optional

"""
Версионированные схемы событий
[C7-ID: EVENTS-SCHEMA-001]

Поддерживает v1 и v2 схемы для эволюции событий без breaking changes
"""

from .base import BaseEvent
from .posts_parsed_v1 import PostParsedEventV1
from .posts_tagged_v1 import PostTaggedEventV1
from .posts_enriched_v1 import PostEnrichedEventV1
from .posts_indexed_v1 import PostIndexedEventV1
from .posts_deleted_v1 import PostDeletedEventV1
from .channels_v1 import ChannelSubscribedEventV1
from .albums_parsed_v1 import AlbumParsedEventV1
from .album_assembled_v1 import AlbumAssembledEventV1
from .trends_emerging_v1 import TrendEmergingEventV1

# Context7: Vision events для S3 + Vision integration
try:
    from .posts_vision_v1 import VisionUploadedEventV1, VisionAnalyzedEventV1, VisionSkippedEventV1, MediaFile
except ImportError:
    # Fallback если модуль не существует
    VisionUploadedEventV1 = None
    VisionAnalyzedEventV1 = None
    VisionSkippedEventV1 = None
    MediaFile = None

__all__ = [
    'BaseEvent',
    'PostParsedEventV1',
    'PostTaggedEventV1',
    'PostEnrichedEventV1', 
    'PostIndexedEventV1',
    'PostDeletedEventV1',
    'ChannelSubscribedEventV1',
    'AlbumParsedEventV1',
    'AlbumAssembledEventV1',
    'TrendEmergingEventV1',
]

# Context7: Добавляем Vision events в __all__ если они доступны
if VisionUploadedEventV1 is not None:
    __all__.extend(['VisionUploadedEventV1', 'VisionAnalyzedEventV1', 'VisionSkippedEventV1', 'MediaFile'])
