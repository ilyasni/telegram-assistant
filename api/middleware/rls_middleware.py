"""
RLS Middleware для установки app.tenant_id в каждом запросе.
Context7: Multi-tenant изоляция через PostgreSQL Row Level Security.
"""

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import structlog
from typing import Optional
from sqlalchemy import text

logger = structlog.get_logger()


class RLSMiddleware(BaseHTTPMiddleware):
    """
    Middleware для установки app.tenant_id в PostgreSQL сессии.
    Используется для RLS (Row Level Security) изоляции данных по tenant_id.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Извлекаем tenant_id из JWT или других источников
        tenant_id = self._extract_tenant_id(request)
        
        # Context7: Сохраняем tenant_id в request.state и ContextVar для использования в get_db()
        if tenant_id:
            request.state.tenant_id = tenant_id
            # Устанавливаем request в ContextVar для доступа в get_db()
            try:
                from api.models.database import _request_context
                _request_context.set(request)
            except Exception:
                pass  # Игнорируем ошибки импорта
        
        response = await call_next(request)
        return response
    
    def _extract_tenant_id(self, request: Request) -> Optional[str]:
        """
        Извлечение tenant_id из запроса (JWT, headers, etc).
        Context7: Используем общую функцию из dependencies.auth для избежания дублирования.
        """
        # Используем общую функцию из dependencies.auth
        try:
            from dependencies.auth import extract_tenant_id_from_jwt
            tenant_id = extract_tenant_id_from_jwt(request)
            if tenant_id:
                return tenant_id
        except Exception:
            pass
        
        # Fallback: Из path параметра (если есть)
        if hasattr(request, 'path_params') and 'tenant_id' in request.path_params:
            return request.path_params['tenant_id']
        
        # Fallback: Из query параметра
        tenant_id = request.query_params.get('tenant_id')
        if tenant_id:
            return tenant_id
        
        return None
    
    async def _set_tenant_id(self, db_session, tenant_id: str):
        """Установка app.tenant_id в PostgreSQL сессии."""
        try:
            # Используем text() для выполнения SQL
            await db_session.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            logger.debug("RLS tenant_id set", tenant_id=tenant_id)
        except Exception as e:
            logger.warning("Failed to set RLS tenant_id", tenant_id=tenant_id, error=str(e))


def set_tenant_id_in_session(db_session, tenant_id: str):
    """
    Context7: Helper функция для установки app.tenant_id в синхронной SQLAlchemy сессии.
    Используется в воркерах и синхронном коде.
    """
    try:
        db_session.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        logger.debug("RLS tenant_id set (sync)", tenant_id=tenant_id)
    except Exception as e:
        logger.warning("Failed to set RLS tenant_id (sync)", tenant_id=tenant_id, error=str(e))


async def set_tenant_id_in_async_session(db_session, tenant_id: str):
    """
    Context7: Helper функция для установки app.tenant_id в async SQLAlchemy сессии.
    Используется в async воркерах и обработчиках.
    """
    try:
        from sqlalchemy import text
        await db_session.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        logger.debug("RLS tenant_id set (async)", tenant_id=tenant_id)
    except Exception as e:
        logger.warning("Failed to set RLS tenant_id (async)", tenant_id=tenant_id, error=str(e))

