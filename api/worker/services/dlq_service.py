"""
DLQ Service - сервис для работы с Dead Letter Queue.

Performance guardrails:
- max_attempts per event (например 3)
- Поле next_retry_at и exponential backoff
- Если превышено - помечаем event как permanent_failure, только ручной разбор
- Мониторинг: метрики dlq_events_total, dlq_reprocessed_total, auto_heal_success_rate
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

import structlog
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from api.models.database import DLQEvent, SessionLocal
from prometheus_client import Counter, Histogram, Gauge

logger = structlog.get_logger(__name__)

# Метрики (префикс dlq_db_ для отличия от Redis Streams DLQ)
# Используем try-except при регистрации для обработки дублирования
try:
    dlq_db_events_total = Counter(
        'dlq_db_events_total',
        'Total DLQ events in database',
        ['entity_type', 'status']
    )
except ValueError:
    from prometheus_client import REGISTRY
    dlq_db_events_total = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'dlq_db_events_total':
                dlq_db_events_total = collector
                break
    except Exception:
        pass

try:
    dlq_db_reprocessed_total = Counter(
        'dlq_db_reprocessed_total',
        'Total DLQ events reprocessed from database',
        ['entity_type', 'result']
    )
except ValueError:
    from prometheus_client import REGISTRY
    dlq_db_reprocessed_total = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'dlq_db_reprocessed_total':
                dlq_db_reprocessed_total = collector
                break
    except Exception:
        pass

try:
    dlq_db_auto_heal_success_rate = Gauge(
        'dlq_db_auto_heal_success_rate',
        'Auto-heal success rate from database DLQ',
        ['entity_type']
    )
except ValueError:
    from prometheus_client import REGISTRY
    dlq_db_auto_heal_success_rate = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'dlq_db_auto_heal_success_rate':
                dlq_db_auto_heal_success_rate = collector
                break
    except Exception:
        pass

# Конфигурация
DLQ_MAX_ATTEMPTS = int(os.getenv("DLQ_MAX_ATTEMPTS", "3"))
DLQ_BACKOFF_BASE_SECONDS = int(os.getenv("DLQ_BACKOFF_BASE_SECONDS", "60"))  # 1 минута


class DLQService:
    """Сервис для работы с Dead Letter Queue."""
    
    def __init__(self, db: Optional[Session] = None):
        self._db = db
        self._max_attempts = DLQ_MAX_ATTEMPTS
        self._backoff_base = DLQ_BACKOFF_BASE_SECONDS
    
    def _get_db(self) -> Session:
        """Получить сессию БД."""
        if self._db:
            return self._db
        return SessionLocal()
    
    def _close_db(self, db: Session) -> None:
        """Закрыть сессию БД, если она была создана локально."""
        if not self._db:
            db.close()
    
    def add_event(
        self,
        tenant_id: UUID | str,
        entity_type: str,
        event_type: str,
        payload: Dict[str, Any],
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        stack_trace: Optional[str] = None,
        entity_id: Optional[UUID | str] = None,
        max_attempts: Optional[int] = None,
    ) -> DLQEvent:
        """
        Добавить событие в DLQ.
        
        Args:
            tenant_id: ID тенанта
            entity_type: Тип сущности
            event_type: Тип события
            payload: Полезная нагрузка события
            error_code: Код ошибки
            error_message: Сообщение об ошибке
            stack_trace: Трассировка стека
            entity_id: ID сущности
            max_attempts: Максимальное количество попыток (по умолчанию из конфигурации)
        
        Returns:
            Созданная запись DLQEvent
        """
        db = self._get_db()
        try:
            # Нормализация UUID
            if isinstance(tenant_id, str):
                tenant_id = UUID(tenant_id)
            if entity_id and isinstance(entity_id, str):
                entity_id = UUID(entity_id)
            
            max_attempts = max_attempts or self._max_attempts
            
            # Вычисляем next_retry_at с exponential backoff
            next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=self._backoff_base)
            
            event = DLQEvent(
                tenant_id=tenant_id,
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                payload=payload,
                error_code=error_code,
                error_message=error_message[:1000] if error_message else None,  # Ограничение длины
                stack_trace=stack_trace[:5000] if stack_trace else None,  # Ограничение длины
                retry_count=0,
                max_attempts=max_attempts,
                next_retry_at=next_retry_at,
                first_seen_at=datetime.now(timezone.utc),
                status="pending",
            )
            
            db.add(event)
            db.commit()
            db.refresh(event)
            
            # Метрики
            if dlq_db_events_total:
                dlq_db_events_total.labels(entity_type=entity_type, status="pending").inc()
            
            logger.info(
                "dlq.event_added",
                tenant_id=str(tenant_id),
                entity_type=entity_type,
                event_type=event_type,
                event_id=str(event.id),
                next_retry_at=next_retry_at.isoformat()
            )
            
            return event
        except Exception as exc:
            db.rollback()
            logger.error(
                "dlq.add_failed",
                tenant_id=str(tenant_id),
                entity_type=entity_type,
                error=str(exc)
            )
            raise
        finally:
            self._close_db(db)
    
    def get_ready_for_retry(self, limit: int = 100) -> List[DLQEvent]:
        """
        Получить события, готовые для повторной обработки.
        
        Args:
            limit: Максимальное количество событий
        
        Returns:
            Список событий, готовых для retry
        """
        db = self._get_db()
        try:
            now = datetime.now(timezone.utc)
            
            events = db.query(DLQEvent).filter(
                and_(
                    DLQEvent.status == "pending",
                    DLQEvent.next_retry_at <= now,
                    DLQEvent.retry_count < DLQEvent.max_attempts
                )
            ).order_by(DLQEvent.next_retry_at).limit(limit).all()
            
            return events
        except Exception as exc:
            logger.error("dlq.get_ready_failed", error=str(exc))
            raise
        finally:
            self._close_db(db)
    
    def mark_reprocessed(
        self,
        event_id: UUID | str,
        success: bool,
    ) -> None:
        """
        Пометить событие как обработанное.
        
        Args:
            event_id: ID события
            success: Успешно ли обработано
        """
        db = self._get_db()
        try:
            if isinstance(event_id, str):
                event_id = UUID(event_id)
            
            event = db.query(DLQEvent).filter(DLQEvent.id == event_id).first()
            if not event:
                logger.warning("dlq.event_not_found", event_id=str(event_id))
                return
            
            if success:
                event.status = "reprocessed"
                event.last_attempt_at = datetime.now(timezone.utc)
                if dlq_db_reprocessed_total:
                    dlq_db_reprocessed_total.labels(entity_type=event.entity_type, result="success").inc()
            else:
                # Увеличиваем retry_count и вычисляем next_retry_at
                event.retry_count += 1
                event.last_attempt_at = datetime.now(timezone.utc)
                
                if event.retry_count >= event.max_attempts:
                    # Превышено max_attempts - помечаем как permanent_failure
                    event.status = "permanent_failure"
                    if dlq_db_events_total:
                        dlq_db_events_total.labels(entity_type=event.entity_type, status="permanent_failure").inc()
                    logger.warning(
                        "dlq.permanent_failure",
                        event_id=str(event_id),
                        retry_count=event.retry_count,
                        max_attempts=event.max_attempts
                    )
                else:
                    # Exponential backoff
                    backoff_seconds = self._backoff_base * (2 ** event.retry_count)
                    event.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
                    if dlq_db_reprocessed_total:
                        dlq_db_reprocessed_total.labels(entity_type=event.entity_type, result="retry").inc()
            
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("dlq.mark_reprocessed_failed", event_id=str(event_id), error=str(exc))
            raise
        finally:
            self._close_db(db)


# Singleton instance
_dlq_service: Optional[DLQService] = None


def get_dlq_service(db: Optional[Session] = None) -> DLQService:
    """Получить экземпляр DLQService."""
    global _dlq_service
    if db:
        return DLQService(db=db)
    if _dlq_service is None:
        _dlq_service = DLQService()
    return _dlq_service

