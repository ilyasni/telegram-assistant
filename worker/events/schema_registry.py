"""
Schema Registry для версионированных событий
[C7-ID: EVENTS-REGISTRY-001]

Поддерживает runtime валидацию и миграцию между версиями событий
"""

import logging
from typing import Dict, Type, Any, Optional
from datetime import datetime, timezone

from .schemas import (
    BaseEvent,
    PostParsedEventV1,
    PostTaggedEventV1,
    PostEnrichedEventV1,
    PostIndexedEventV1,
    PostDeletedEventV1,
    ChannelSubscribedEventV1
)

logger = logging.getLogger(__name__)


class SchemaRegistry:
    """
    Registry для управления версионированными схемами событий.
    
    Поддерживает:
    - Runtime валидацию событий
    - Миграцию между версиями
    - Кеширование схем
    """
    
    def __init__(self):
        """Инициализация registry с v1 схемами."""
        self._schemas: Dict[str, Dict[str, Type[BaseEvent]]] = {}
        self._migrations: Dict[str, Dict[str, callable]] = {}
        
        # Регистрация v1 схем
        self._register_v1_schemas()
        
        logger.info("Schema Registry initialized with v1 schemas")
    
    def _register_v1_schemas(self):
        """Регистрация v1 схем событий."""
        v1_schemas = {
            "posts.parsed": {"v1": PostParsedEventV1},
            "posts.tagged": {"v1": PostTaggedEventV1},
            "posts.enriched": {"v1": PostEnrichedEventV1},
            "posts.indexed": {"v1": PostIndexedEventV1},
            "posts.deleted": {"v1": PostDeletedEventV1},
            "channels.subscribed": {"v1": ChannelSubscribedEventV1}
        }
        
        for event_type, versions in v1_schemas.items():
            self._schemas[event_type] = versions
            logger.debug(f"Registered {event_type} schemas: {list(versions.keys())}")
    
    def get_schema(self, event_type: str, version: str = "v1") -> Optional[Type[BaseEvent]]:
        """
        Получить схему события по типу и версии.
        
        Args:
            event_type: Тип события (например, "posts.parsed")
            version: Версия схемы (по умолчанию "v1")
            
        Returns:
            Класс схемы события или None
        """
        if event_type not in self._schemas:
            logger.warning(f"Unknown event type: {event_type}")
            return None
        
        if version not in self._schemas[event_type]:
            logger.warning(f"Unknown version {version} for event type {event_type}")
            return None
        
        return self._schemas[event_type][version]
    
    def validate_event(self, event_data: Dict[str, Any], event_type: str, version: str = "v1") -> Optional[BaseEvent]:
        """
        Валидировать данные события по схеме.
        
        Args:
            event_data: Данные события
            event_type: Тип события
            version: Версия схемы
            
        Returns:
            Валидированный объект события или None
        """
        schema_class = self.get_schema(event_type, version)
        if not schema_class:
            return None
        
        try:
            # Валидация через Pydantic
            event = schema_class(**event_data)
            logger.debug(f"Event validated: {event_type}:{version}")
            return event
        except Exception as e:
            logger.error(f"Event validation failed: {event_type}:{version}, error: {e}")
            return None
    
    def migrate_event(self, event_data: Dict[str, Any], event_type: str, from_version: str, to_version: str) -> Optional[Dict[str, Any]]:
        """
        Мигрировать событие между версиями.
        
        Args:
            event_data: Данные события
            event_type: Тип события
            from_version: Исходная версия
            to_version: Целевая версия
            
        Returns:
            Мигрированные данные события или None
        """
        if from_version == to_version:
            return event_data
        
        migration_key = f"{event_type}:{from_version}->{to_version}"
        
        if migration_key not in self._migrations:
            logger.warning(f"No migration found for {migration_key}")
            return None
        
        try:
            migration_func = self._migrations[migration_key]
            migrated_data = migration_func(event_data)
            logger.debug(f"Event migrated: {migration_key}")
            return migrated_data
        except Exception as e:
            logger.error(f"Event migration failed: {migration_key}, error: {e}")
            return None
    
    def register_migration(self, event_type: str, from_version: str, to_version: str, migration_func: callable):
        """
        Зарегистрировать функцию миграции между версиями.
        
        Args:
            event_type: Тип события
            from_version: Исходная версия
            to_version: Целевая версия
            migration_func: Функция миграции
        """
        migration_key = f"{event_type}:{from_version}->{to_version}"
        self._migrations[migration_key] = migration_func
        logger.info(f"Migration registered: {migration_key}")
    
    def get_available_versions(self, event_type: str) -> list[str]:
        """
        Получить доступные версии для типа события.
        
        Args:
            event_type: Тип события
            
        Returns:
            Список доступных версий
        """
        if event_type not in self._schemas:
            return []
        
        return list(self._schemas[event_type].keys())
    
    def get_available_event_types(self) -> list[str]:
        """
        Получить доступные типы событий.
        
        Returns:
            Список доступных типов событий
        """
        return list(self._schemas.keys())
    
    def get_schema_info(self, event_type: str, version: str = "v1") -> Optional[Dict[str, Any]]:
        """
        Получить информацию о схеме события.
        
        Args:
            event_type: Тип события
            version: Версия схемы
            
        Returns:
            Информация о схеме или None
        """
        schema_class = self.get_schema(event_type, version)
        if not schema_class:
            return None
        
        return {
            "event_type": event_type,
            "version": version,
            "schema_class": schema_class.__name__,
            "fields": list(schema_class.__fields__.keys()),
            "required_fields": [
                field for field, info in schema_class.__fields__.items()
                if info.is_required()
            ]
        }


# Глобальный экземпляр registry
_schema_registry: Optional[SchemaRegistry] = None


def get_schema_registry() -> SchemaRegistry:
    """
    Получить глобальный экземпляр Schema Registry.
    
    Returns:
        Экземпляр SchemaRegistry
    """
    global _schema_registry
    
    if _schema_registry is None:
        _schema_registry = SchemaRegistry()
        logger.info("Global Schema Registry initialized")
    
    return _schema_registry


def validate_event_data(event_data: Dict[str, Any], event_type: str, version: str = "v1") -> Optional[BaseEvent]:
    """
    Удобная функция для валидации данных события.
    
    Args:
        event_data: Данные события
        event_type: Тип события
        version: Версия схемы
        
    Returns:
        Валидированный объект события или None
    """
    registry = get_schema_registry()
    return registry.validate_event(event_data, event_type, version)
