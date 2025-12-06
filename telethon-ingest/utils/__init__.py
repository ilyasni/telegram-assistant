"""
Context7: Утилиты для telethon-ingest сервиса.
"""

from .tenant_utils import get_system_tenant_id_async, get_system_tenant_id_sync

__all__ = [
    'get_system_tenant_id_async',
    'get_system_tenant_id_sync',
]

