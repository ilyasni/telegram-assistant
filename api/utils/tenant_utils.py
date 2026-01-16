"""
Context7: Утилиты для работы с tenant_id в API.
Предоставляет функции для получения системного tenant_id без дублирования кода.
"""

import structlog
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = structlog.get_logger()


def get_system_tenant_id(db: Session) -> str:
    """
    Context7: Получение системного tenant_id из БД (sync версия для SQLAlchemy Session).
    
    Используется для совместимости с существующей схемой БД, где groups и group_messages
    требуют tenant_id, хотя данные должны быть глобальными.
    
    Args:
        db: SQLAlchemy Session для выполнения запроса
        
    Returns:
        tenant_id (str)
        
    Raises:
        ValueError: если tenant не найден в БД
    """
    try:
        result = db.execute(text("SELECT id FROM tenants LIMIT 1"))
        system_tenant_row = result.fetchone()
        if not system_tenant_row:
            logger.error("No tenant found in database")
            raise ValueError("No tenant found in database")
        return str(system_tenant_row[0])
    except Exception as e:
        logger.error("Failed to get system tenant_id", error=str(e), exc_info=True)
        raise

