"""
Event system для системы управления каналами
[C7-ID: EVENTS-SYSTEM-001]

Поддерживает версионированные схемы событий и Schema Registry
"""

from .schema_registry import SchemaRegistry, get_schema_registry
from .schemas import (
    BaseEvent,
    PostParsedEventV1,
    PostTaggedEventV1,
    PostEnrichedEventV1,
    PostIndexedEventV1,
    PostDeletedEventV1,
    ChannelSubscribedEventV1
)

__all__ = [
    'SchemaRegistry',
    'get_schema_registry',
    'BaseEvent',
    'PostParsedEventV1',
    'PostTaggedEventV1', 
    'PostEnrichedEventV1',
    'PostIndexedEventV1',
    'PostDeletedEventV1',
    'ChannelSubscribedEventV1'
]
