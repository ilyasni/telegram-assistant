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

__all__ = [
    'BaseEvent',
    'PostParsedEventV1',
    'PostTaggedEventV1',
    'PostEnrichedEventV1', 
    'PostIndexedEventV1',
    'PostDeletedEventV1',
    'ChannelSubscribedEventV1'
]
