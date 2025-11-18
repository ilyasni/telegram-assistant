"""
Context7 P3: Схемы событий для persona messages (sideloading).

События:
- persona_message_ingested: Сообщение из личного диалога/группы импортировано
- persona_graph_updated: Граф persona обновлён в Neo4j
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID


class PersonaMessageIngestedEventV1(BaseModel):
    """
    Событие: Сообщение из личного диалога/группы импортировано.
    
    Context7 P3: Публикуется при импорте сообщений через SideloadService.
    """
    
    # Идентификаторы
    user_id: str = Field(..., description="Telegram ID пользователя")
    tenant_id: str = Field(..., description="Tenant ID")
    message_id: Optional[str] = Field(None, description="UUID сообщения в БД")
    telegram_message_id: int = Field(..., description="Telegram Message ID")
    
    # Контент
    dialog_type: str = Field(..., description="Тип диалога: 'dm' или 'group'")
    content: Optional[str] = Field(None, description="Текст сообщения (ограничен до 500 символов)")
    posted_at: str = Field(..., description="ISO формат даты сообщения")
    
    # Метаданные
    source: str = Field(..., description="Источник: 'dm', 'group', 'persona'")
    created_at: str = Field(..., description="ISO формат времени создания события")
    
    # Опциональные поля
    sender_tg_id: Optional[int] = Field(None, description="Telegram ID отправителя")
    sender_username: Optional[str] = Field(None, description="Username отправителя")
    has_media: bool = Field(False, description="Наличие медиа вложений")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "123456789",
                "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
                "message_id": "660e8400-e29b-41d4-a716-446655440000",
                "telegram_message_id": 12345,
                "dialog_type": "dm",
                "content": "Hello!",
                "posted_at": "2025-01-21T12:00:00Z",
                "source": "dm",
                "created_at": "2025-01-21T12:00:01Z",
                "sender_tg_id": 987654321,
                "has_media": False
            }
        }


class PersonaGraphUpdatedEventV1(BaseModel):
    """
    Событие: Граф persona обновлён в Neo4j.
    
    Context7 P3: Публикуется после создания/обновления узлов и связей в Neo4j
    для persona-based Graph-RAG.
    """
    
    # Идентификаторы
    user_id: str = Field(..., description="Telegram ID пользователя")
    tenant_id: str = Field(..., description="Tenant ID")
    
    # Типы операций
    operation: str = Field(..., description="Тип операции: 'persona_created', 'dialogue_created', 'message_linked'")
    
    # Данные графа
    persona_node_id: Optional[str] = Field(None, description="ID узла Persona в Neo4j")
    dialogue_node_id: Optional[str] = Field(None, description="ID узла Dialogue в Neo4j")
    message_node_id: Optional[str] = Field(None, description="ID узла Message в Neo4j")
    
    # Метаданные
    created_at: str = Field(..., description="ISO формат времени создания события")
    relationships_created: int = Field(0, description="Количество созданных связей")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "123456789",
                "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
                "operation": "persona_created",
                "persona_node_id": "persona:123456789",
                "dialogue_node_id": "dialogue:987654321",
                "created_at": "2025-01-21T12:00:01Z",
                "relationships_created": 3
            }
        }

