"""
Outbox Repository для идемпотентной публикации событий.
[C7-ID: API-OUTBOX-001]

Обеспечивает надежную доставку событий через outbox pattern.
"""

import json
import hashlib
from typing import Dict, Any, Optional
from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import Depends
from models.database import get_db
from sqlalchemy import text
import structlog

logger = structlog.get_logger()

class OutboxRepository:
    """Repository для работы с outbox events."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def enqueue(
        self,
        event_type: str,
        payload: Dict[str, Any],
        aggregate_id: str,
        idempotency_key: str
    ) -> UUID:
        """
        Добавление события в outbox с идемпотентностью.
        
        Args:
            event_type: Тип события (например, "channels.subscribed.v1")
            payload: Данные события
            aggregate_id: ID агрегата (channel_id)
            idempotency_key: Ключ идемпотентности
            
        Returns:
            UUID события
        """
        try:
            event_id = uuid4()
            content_hash = self._calculate_content_hash(payload)
            
            # Вставка в outbox_events с проверкой идемпотентности
            query = text("""
                INSERT INTO outbox_events (
                    id, event_type, payload, aggregate_id, content_hash,
                    idempotency_key, created_at, schema_version, trace_id
                ) VALUES (
                    :id, :event_type, :payload, :aggregate_id, :content_hash,
                    :idempotency_key, :created_at, :schema_version, :trace_id
                )
                ON CONFLICT (aggregate_id, event_type, content_hash) DO NOTHING
                RETURNING id
            """)
            
            result = self.db.execute(query, {
                "id": event_id,
                "event_type": event_type,
                "payload": json.dumps(payload),
                "aggregate_id": aggregate_id,
                "content_hash": content_hash,
                "idempotency_key": idempotency_key,
                "created_at": datetime.now(timezone.utc),
                "schema_version": "v1",
                "trace_id": payload.get("trace_id", "unknown")
            })
            
            # Проверка, была ли вставлена новая запись
            row = result.fetchone()
            if row:
                logger.info("Event enqueued to outbox",
                           event_id=event_id,
                           event_type=event_type,
                           aggregate_id=aggregate_id)
                return event_id
            else:
                # Событие уже существует (идемпотентность)
                logger.debug("Event already exists in outbox",
                            event_type=event_type,
                            aggregate_id=aggregate_id,
                            idempotency_key=idempotency_key)
                
                # Получение существующего event_id
                existing_query = text("""
                    SELECT id FROM outbox_events
                    WHERE aggregate_id = :aggregate_id
                    AND event_type = :event_type
                    AND content_hash = :content_hash
                """)
                
                existing_result = self.db.execute(existing_query, {
                    "aggregate_id": aggregate_id,
                    "event_type": event_type,
                    "content_hash": content_hash
                })
                
                existing_row = existing_result.fetchone()
                return existing_row.id if existing_row else event_id
                
        except Exception as e:
            logger.error("Failed to enqueue event",
                        event_type=event_type,
                        aggregate_id=aggregate_id,
                        error=str(e))
            raise
    
    def _calculate_content_hash(self, payload: Dict[str, Any]) -> str:
        """Вычисление хеша содержимого для дедупликации."""
        # Сортировка ключей для консистентного хеша
        sorted_payload = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(sorted_payload.encode()).hexdigest()
    
    def get_pending_events(self, limit: int = 100) -> list:
        """Получение pending событий для обработки."""
        try:
            query = text("""
                SELECT id, event_type, payload, aggregate_id, created_at, trace_id
                FROM outbox_events
                WHERE processed_at IS NULL
                ORDER BY created_at ASC
                LIMIT :limit
            """)
            
            result = self.db.execute(query, {"limit": limit})
            return [dict(row._mapping) for row in result.fetchall()]
            
        except Exception as e:
            logger.error("Failed to get pending events", error=str(e))
            return []
    
    async def mark_processed(self, event_id: UUID, success: bool = True, error: Optional[str] = None):
        """Отметка события как обработанного."""
        try:
            query = text("""
                UPDATE outbox_events
                SET processed_at = :processed_at,
                    retry_count = retry_count + 1,
                    last_error = :error
                WHERE id = :event_id
            """)
            
            self.db.execute(query, {
                "event_id": event_id,
                "processed_at": datetime.now(timezone.utc) if success else None,
                "error": error
            })
            
            logger.debug("Event marked as processed",
                        event_id=event_id,
                        success=success)
            
        except Exception as e:
            logger.error("Failed to mark event as processed",
                        event_id=event_id,
                        error=str(e))
            raise

def get_outbox_repository(db: Session = Depends(get_db)) -> OutboxRepository:
    """Dependency для получения OutboxRepository."""
    return OutboxRepository(db)
