"""
Episodic Memory Service - сервис для записи и чтения истории действий, ошибок и попыток.

Performance guardrails:
- Логируем только высокоуровневые события (run_started/run_completed/error/retry)
- Retention: 30-90 дней (настраивается через TTL)
- Индексы оптимизированы для чтения по tenant_id, entity_type, created_at
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

import structlog
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from api.models.database import EpisodicMemory, SessionLocal
from prometheus_client import Counter, Histogram

logger = structlog.get_logger(__name__)

# Метрики (условная регистрация для избежания дублирования)
# Используем try-except при регистрации для обработки дублирования
try:
    episodic_memory_events_total = Counter(
        'episodic_memory_events_total',
        'Total episodic memory events recorded',
        ['entity_type', 'event_type']
    )
except ValueError:
    # Метрика уже зарегистрирована, получаем из registry
    from prometheus_client import REGISTRY
    episodic_memory_events_total = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'episodic_memory_events_total':
                episodic_memory_events_total = collector
                break
    except Exception:
        pass

try:
    episodic_memory_reads_total = Counter(
        'episodic_memory_reads_total',
        'Total episodic memory reads',
        ['operation']
    )
except ValueError:
    from prometheus_client import REGISTRY
    episodic_memory_reads_total = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'episodic_memory_reads_total':
                episodic_memory_reads_total = collector
                break
    except Exception:
        pass

try:
    episodic_memory_read_duration_seconds = Histogram(
        'episodic_memory_read_duration_seconds',
        'Duration of episodic memory read operations',
        ['operation']
    )
except ValueError:
    from prometheus_client import REGISTRY
    episodic_memory_read_duration_seconds = None
    try:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_name') and collector._name == 'episodic_memory_read_duration_seconds':
                episodic_memory_read_duration_seconds = collector
                break
    except Exception:
        pass

# Конфигурация
DEFAULT_RETENTION_DAYS = int(os.getenv("EPISODIC_MEMORY_RETENTION_DAYS", "90"))
MAX_EVENTS_PER_QUERY = int(os.getenv("EPISODIC_MEMORY_MAX_EVENTS_PER_QUERY", "1000"))


class EpisodicMemoryService:
    """Сервис для работы с episodic memory."""
    
    def __init__(self, db: Optional[Session] = None):
        self._db = db
        self._retention_days = DEFAULT_RETENTION_DAYS
    
    def _get_db(self) -> Session:
        """Получить сессию БД."""
        if self._db:
            return self._db
        return SessionLocal()
    
    def _close_db(self, db: Session) -> None:
        """Закрыть сессию БД, если она была создана локально."""
        if not self._db:
            db.close()
    
    def record_event(
        self,
        tenant_id: UUID | str,
        entity_type: str,
        event_type: str,
        metadata: Optional[Dict[str, Any]] = None,
        entity_id: Optional[UUID | str] = None,
    ) -> EpisodicMemory:
        """
        Записать событие в episodic memory.
        
        Performance: Логируем только высокоуровневые события.
        Низкоуровневые шаги не записываются для экономии места.
        
        Args:
            tenant_id: ID тенанта
            entity_type: Тип сущности ('digest', 'trend', 'enrichment', 'indexing', 'rag')
            event_type: Тип события ('run_started', 'run_completed', 'error', 'retry', 'quality_low')
            metadata: Дополнительные метаданные события
            entity_id: ID сущности (опционально)
        
        Returns:
            Созданная запись EpisodicMemory
        """
        db = self._get_db()
        try:
            # Валидация типов событий (performance: только высокоуровневые)
            valid_event_types = {'run_started', 'run_completed', 'error', 'retry', 'quality_low', 'fallback_used'}
            if event_type not in valid_event_types:
                logger.warning(
                    "episodic_memory.invalid_event_type",
                    event_type=event_type,
                    valid_types=list(valid_event_types)
                )
                # Не записываем невалидные события для экономии места
                raise ValueError(f"Invalid event_type: {event_type}. Valid types: {valid_event_types}")
            
            # Нормализация UUID
            if isinstance(tenant_id, str):
                tenant_id = UUID(tenant_id)
            if entity_id and isinstance(entity_id, str):
                entity_id = UUID(entity_id)
            
            event = EpisodicMemory(
                tenant_id=tenant_id,
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                event_metadata=metadata or {},
                created_at=datetime.now(timezone.utc)
            )
            
            db.add(event)
            db.commit()
            db.refresh(event)
            
            # Метрики
            if episodic_memory_events_total:
                episodic_memory_events_total.labels(
                    entity_type=entity_type,
                    event_type=event_type
                ).inc()
            
            logger.debug(
                "episodic_memory.event_recorded",
                tenant_id=str(tenant_id),
                entity_type=entity_type,
                event_type=event_type,
                entity_id=str(entity_id) if entity_id else None
            )
            
            return event
        except Exception as exc:
            db.rollback()
            logger.error(
                "episodic_memory.record_failed",
                tenant_id=str(tenant_id),
                entity_type=entity_type,
                event_type=event_type,
                error=str(exc)
            )
            raise
        finally:
            self._close_db(db)
    
    def get_recent_events(
        self,
        tenant_id: UUID | str,
        entity_type: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
        hours: int = 24,
    ) -> List[EpisodicMemory]:
        """
        Получить последние события для self-tuning.
        
        Performance: Использует индексы по tenant_id, entity_type, created_at.
        Ограничение по количеству записей для предотвращения больших выборок.
        
        Args:
            tenant_id: ID тенанта
            entity_type: Фильтр по типу сущности (опционально)
            event_type: Фильтр по типу события (опционально)
            limit: Максимальное количество записей (по умолчанию 100)
            hours: Количество часов назад для выборки (по умолчанию 24)
        
        Returns:
            Список событий, отсортированных по времени создания (новые первыми)
        """
        if episodic_memory_read_duration_seconds:
            with episodic_memory_read_duration_seconds.labels(operation="get_recent_events").time():
                return self._get_recent_events_impl(tenant_id, entity_type, event_type, limit, hours)
        else:
            return self._get_recent_events_impl(tenant_id, entity_type, event_type, limit, hours)
    
    def _get_recent_events_impl(
        self,
        tenant_id: UUID | str,
        entity_type: Optional[str],
        event_type: Optional[str],
        limit: int,
        hours: int,
    ) -> List[EpisodicMemory]:
        """Внутренняя реализация get_recent_events."""
        db = self._get_db()
        try:
            # Нормализация UUID
            if isinstance(tenant_id, str):
                tenant_id = UUID(tenant_id)
                
                # Ограничение лимита для производительности
                limit = min(limit, MAX_EVENTS_PER_QUERY)
                
                # Время начала выборки
                since = datetime.now(timezone.utc) - timedelta(hours=hours)
                
                # Построение запроса с использованием индексов
                query = db.query(EpisodicMemory).filter(
                    and_(
                        EpisodicMemory.tenant_id == tenant_id,
                        EpisodicMemory.created_at >= since
                    )
                )
                
                if entity_type:
                    query = query.filter(EpisodicMemory.entity_type == entity_type)
                
                if event_type:
                    query = query.filter(EpisodicMemory.event_type == event_type)
                
                # Сортировка по created_at DESC (использует индекс)
                events = query.order_by(desc(EpisodicMemory.created_at)).limit(limit).all()
                
                if episodic_memory_reads_total:
                    episodic_memory_reads_total.labels(operation="get_recent_events").inc()
                
                logger.debug(
                    "episodic_memory.recent_events_fetched",
                    tenant_id=str(tenant_id),
                    entity_type=entity_type,
                    event_type=event_type,
                    count=len(events),
                    hours=hours
                )
                
            return events
        except Exception as exc:
            logger.error(
                "episodic_memory.read_failed",
                tenant_id=str(tenant_id),
                entity_type=entity_type,
                error=str(exc)
            )
            raise
        finally:
            self._close_db(db)
    
    def get_error_history(
        self,
        tenant_id: UUID | str,
        entity_type: Optional[str] = None,
        days: int = 7,
        limit: int = 50,
    ) -> List[EpisodicMemory]:
        """
        Получить историю ошибок для анализа.
        
        Performance: Использует индекс по event_type='error' и created_at.
        
        Args:
            tenant_id: ID тенанта
            entity_type: Фильтр по типу сущности (опционально)
            days: Количество дней назад для выборки (по умолчанию 7)
            limit: Максимальное количество записей (по умолчанию 50)
        
        Returns:
            Список ошибок, отсортированных по времени создания (новые первыми)
        """
        with episodic_memory_read_duration_seconds.labels(operation="get_error_history").time():
            return self.get_recent_events(
                tenant_id=tenant_id,
                entity_type=entity_type,
                event_type='error',
                limit=limit,
                hours=days * 24
            )
    
    def cleanup_old_events(self, retention_days: Optional[int] = None) -> int:
        """
        Очистить старые события (retention policy).
        
        Performance: Выполняется периодически (например, раз в день) для освобождения места.
        
        Args:
            retention_days: Количество дней для хранения (по умолчанию из конфигурации)
        
        Returns:
            Количество удаленных записей
        """
        db = self._get_db()
        try:
            retention = retention_days or self._retention_days
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention)
            
            deleted = db.query(EpisodicMemory).filter(
                EpisodicMemory.created_at < cutoff_date
            ).delete(synchronize_session=False)
            
            db.commit()
            
            logger.info(
                "episodic_memory.cleanup_completed",
                retention_days=retention,
                deleted_count=deleted,
                cutoff_date=cutoff_date.isoformat()
            )
            
            return deleted
        except Exception as exc:
            db.rollback()
            logger.error(
                "episodic_memory.cleanup_failed",
                retention_days=retention,
                error=str(exc)
            )
            raise
        finally:
            self._close_db(db)


# Singleton instance
_episodic_memory_service: Optional[EpisodicMemoryService] = None


def get_episodic_memory_service(db: Optional[Session] = None) -> EpisodicMemoryService:
    """Получить экземпляр EpisodicMemoryService."""
    global _episodic_memory_service
    if db:
        return EpisodicMemoryService(db=db)
    if _episodic_memory_service is None:
        _episodic_memory_service = EpisodicMemoryService()
    return _episodic_memory_service

