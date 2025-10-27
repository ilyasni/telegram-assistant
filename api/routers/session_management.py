"""
Session Management API
=====================

API endpoints для управления Telegram сессиями с атомарными операциями,
идемпотентностью, наблюдаемостью и понятным rollback.
"""

from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
import structlog

from services.session_manager import session_manager
from config import settings

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/sessions", tags=["session-management"])


class SessionSaveRequest(BaseModel):
    """Запрос на сохранение сессии."""
    tenant_id: str
    user_id: str
    session_string: str
    telegram_user_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    invite_code: Optional[str] = None
    force_update: bool = False


class SessionSaveResponse(BaseModel):
    """Ответ на сохранение сессии."""
    success: bool
    session_id: Optional[str] = None
    error_code: Optional[str] = None
    error_details: Optional[str] = None


class SessionStatusResponse(BaseModel):
    """Ответ со статусом сессии."""
    session_id: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    auth_error: Optional[str] = None
    error_details: Optional[str] = None


class SessionRevokeRequest(BaseModel):
    """Запрос на отзыв сессии."""
    reason: str = "manual_revoke"


@router.post("/save", response_model=SessionSaveResponse)
async def save_session(
    request: SessionSaveRequest,
    http_request: Request
):
    """
    Атомарное сохранение Telegram сессии.
    
    Принципы:
    - Атомарность: все операции в транзакциях
    - Идемпотентность: повторные вызовы безопасны
    - Наблюдаемость: детальные метрики и логи
    - Rollback: четкие стратегии отката
    """
    try:
        logger.info(
            "Session save request",
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            telegram_user_id=request.telegram_user_id,
            session_length=len(request.session_string),
            force_update=request.force_update,
            client_ip=http_request.client.host
        )
        
        success, session_id, error_code, error_details = await session_manager.save_telegram_session(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            session_string=request.session_string,
            telegram_user_id=request.telegram_user_id,
            first_name=request.first_name,
            last_name=request.last_name,
            username=request.username,
            invite_code=request.invite_code,
            force_update=request.force_update
        )
        
        if success:
            logger.info(
                "Session saved successfully",
                session_id=session_id,
                tenant_id=request.tenant_id,
                user_id=request.user_id
            )
            
            return SessionSaveResponse(
                success=True,
                session_id=session_id
            )
        else:
            logger.error(
                "Session save failed",
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                error_code=error_code,
                error_details=error_details
            )
            
            return SessionSaveResponse(
                success=False,
                error_code=error_code,
                error_details=error_details
            )
            
    except Exception as e:
        logger.error(
            "Unexpected error in session save",
            error=str(e),
            tenant_id=request.tenant_id,
            user_id=request.user_id
        )
        
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/status/{tenant_id}/{user_id}", response_model=SessionStatusResponse)
async def get_session_status(
    tenant_id: str,
    user_id: str,
    http_request: Request
):
    """Получение статуса сессии."""
    try:
        logger.info(
            "Session status request",
            tenant_id=tenant_id,
            user_id=user_id,
            client_ip=http_request.client.host
        )
        
        session_data = await session_manager.get_session_status(tenant_id, user_id)
        
        if not session_data:
            return SessionStatusResponse()
        
        return SessionStatusResponse(**session_data)
        
    except Exception as e:
        logger.error(
            "Error getting session status",
            error=str(e),
            tenant_id=tenant_id,
            user_id=user_id
        )
        
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.post("/revoke/{tenant_id}/{user_id}")
async def revoke_session(
    tenant_id: str,
    user_id: str,
    request: SessionRevokeRequest,
    http_request: Request
):
    """Отзыв сессии с логированием."""
    try:
        logger.info(
            "Session revoke request",
            tenant_id=tenant_id,
            user_id=user_id,
            reason=request.reason,
            client_ip=http_request.client.host
        )
        
        success = await session_manager.revoke_session(
            tenant_id=tenant_id,
            user_id=user_id,
            reason=request.reason
        )
        
        if success:
            logger.info(
                "Session revoked successfully",
                tenant_id=tenant_id,
                user_id=user_id,
                reason=request.reason
            )
            
            return {"success": True, "message": "Session revoked successfully"}
        else:
            logger.error(
                "Failed to revoke session",
                tenant_id=tenant_id,
                user_id=user_id,
                reason=request.reason
            )
            
            raise HTTPException(
                status_code=500,
                detail="Failed to revoke session"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Unexpected error in session revoke",
            error=str(e),
            tenant_id=tenant_id,
            user_id=user_id
        )
        
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.post("/cleanup")
async def cleanup_expired_sessions(
    http_request: Request,
    hours: int = 24
):
    """Очистка просроченных сессий."""
    try:
        logger.info(
            "Session cleanup request",
            hours=hours,
            client_ip=http_request.client.host
        )
        
        cleaned_count = await session_manager.cleanup_expired_sessions(hours)
        
        logger.info(
            "Session cleanup completed",
            cleaned_count=cleaned_count,
            hours=hours
        )
        
        return {
            "success": True,
            "cleaned_count": cleaned_count,
            "hours": hours
        }
        
    except Exception as e:
        logger.error(
            "Error in session cleanup",
            error=str(e),
            hours=hours
        )
        
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/health")
async def session_health():
    """Health check для сервиса управления сессиями."""
    try:
        # Простая проверка подключения к БД
        # В реальном приложении можно добавить более детальные проверки
        
        return {
            "status": "healthy",
            "service": "session-manager",
            "version": "1.0.0"
        }
        
    except Exception as e:
        logger.error("Session health check failed", error=str(e))
        
        raise HTTPException(
            status_code=503,
            detail="Service unhealthy"
        )
