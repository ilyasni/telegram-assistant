"""
Context7: Утилиты для работы с tenant_id.
Предоставляет функции для получения системного tenant_id без дублирования кода.
"""

import structlog
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import psycopg2
from psycopg2.extras import RealDictCursor

logger = structlog.get_logger()


async def get_system_tenant_id_async(db_session: AsyncSession) -> Optional[str]:
    """
    Context7: Получение системного tenant_id из БД (async версия).
    
    Используется для совместимости с существующей схемой БД, где groups и group_messages
    требуют tenant_id, хотя данные должны быть глобальными.
    
    Args:
        db_session: AsyncSession для выполнения запроса
        
    Returns:
        tenant_id (str) или None, если tenant не найден
        
    Raises:
        ValueError: если tenant не найден в БД
    """
    try:
        result = await db_session.execute(
            text("SELECT id FROM tenants LIMIT 1")
        )
        system_tenant = result.scalar_one_or_none()
        if not system_tenant:
            logger.error("No tenant found in database")
            raise ValueError("No tenant found in database")
        return str(system_tenant)
    except Exception as e:
        logger.error("Failed to get system tenant_id", error=str(e), exc_info=True)
        raise


def get_system_tenant_id_sync(db_url: str) -> Optional[str]:
    """
    Context7: Получение системного tenant_id из БД (sync версия для psycopg2).
    
    Используется для совместимости с существующей схемой БД, где groups и group_messages
    требуют tenant_id, хотя данные должны быть глобальными.
    
    Args:
        db_url: URL подключения к БД
        
    Returns:
        tenant_id (str) или None, если tenant не найден
        
    Raises:
        ValueError: если tenant не найден в БД
    """
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id FROM tenants LIMIT 1")
        system_tenant_row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not system_tenant_row:
            logger.error("No tenant found in database")
            raise ValueError("No tenant found in database")
        
        return str(system_tenant_row['id'])
    except Exception as e:
        logger.error("Failed to get system tenant_id", error=str(e), exc_info=True)
        raise

