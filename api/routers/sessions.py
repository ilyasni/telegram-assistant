"""Context7 best practice: API endpoints для управления Telegram сессиями."""

from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from models.database import get_db
from models.database import TelegramSession, TelegramAuthLog
# from services.session_storage import session_storage

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionResponse(BaseModel):
    """Response модель для Telegram сессии."""
    id: str
    tenant_id: str
    user_id: Optional[str]
    status: str
    created_at: str
    updated_at: str
    telegram_user_id: Optional[int] = None


class SessionListResponse(BaseModel):
    """Response для списка сессий."""
    sessions: List[SessionResponse]
    total: int
    page: int
    per_page: int


class SessionRevokeRequest(BaseModel):
    """Request для отзыва сессии."""
    reason: str = "manual"


@router.get("/", response_model=SessionListResponse)
def list_sessions(
    tenant_id: Optional[str] = Query(None, description="Фильтр по tenant_id"),
    status: Optional[str] = Query(None, description="Фильтр по статусу"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    per_page: int = Query(20, ge=1, le=100, description="Количество на странице"),
    db: Session = Depends(get_db)
):
    """Context7 best practice: получение списка Telegram сессий."""
    try:
        # Context7 best practice: построение запроса с фильтрами
        query = db.query(TelegramSession)
        
        if tenant_id:
            query = query.filter(TelegramSession.tenant_id == tenant_id)
        
        if status:
            query = query.filter(TelegramSession.status == status)
        
        # Context7 best practice: пагинация
        total = query.count()
        offset = (page - 1) * per_page
        
        sessions = query.order_by(TelegramSession.created_at.desc()).offset(offset).limit(per_page).all()
        
        return SessionListResponse(
            sessions=[
                SessionResponse(
                    id=str(session.id),
                    tenant_id=str(session.tenant_id),
                    user_id=str(session.user_id) if session.user_id else None,
                    status=session.status,
                    created_at=session.created_at.isoformat(),
                    updated_at=session.updated_at.isoformat()
                )
                for session in sessions
            ],
            total=total,
            page=page,
            per_page=per_page
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list sessions: {str(e)}")


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, db: Session = Depends(get_db)):
    """Context7 best practice: получение конкретной сессии."""
    try:
        session = db.query(TelegramSession).filter(TelegramSession.id == session_id).first()
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return SessionResponse(
            id=str(session.id),
            tenant_id=str(session.tenant_id),
            user_id=str(session.user_id) if session.user_id else None,
            status=session.status,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get session: {str(e)}")


@router.post("/{session_id}/revoke")
def revoke_session(
    session_id: str,
    request: SessionRevokeRequest,
    db: Session = Depends(get_db)
):
    """Context7 best practice: отзыв Telegram сессии."""
    try:
        # Context7 best practice: проверка существования сессии
        session = db.query(TelegramSession).filter(TelegramSession.id == session_id).first()
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if session.status != "authorized":
            raise HTTPException(status_code=400, detail="Session is not active")
        
        # Context7 best practice: отзыв сессии в БД
        session.status = "revoked"
        session.updated_at = datetime.utcnow()
        db.commit()
        
        # Логирование отзыва
        auth_log = TelegramAuthLog(
            session_id=session.id,
            event="session_revoked",
            reason=request.reason
        )
        db.add(auth_log)
        db.commit()
        
        return {"message": "Session revoked successfully", "session_id": session_id}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to revoke session: {str(e)}")


@router.get("/{session_id}/logs")
def get_session_logs(
    session_id: str,
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    db: Session = Depends(get_db)
):
    """Context7 best practice: получение логов сессии."""
    try:
        # Context7 best practice: проверка существования сессии
        session = db.query(TelegramSession).filter(TelegramSession.id == session_id).first()
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Context7 best practice: получение логов
        logs = db.query(TelegramAuthLog).filter(
            TelegramAuthLog.session_id == session_id
        ).order_by(TelegramAuthLog.at.desc()).limit(limit).all()
        
        return {
            "session_id": session_id,
            "logs": [
                {
                    "id": str(log.id),
                    "event": log.event,
                    "reason": log.reason,
                    "error_code": log.error_code,
                    "ip": log.ip,
                    "user_agent": log.user_agent,
                    "latency_ms": log.latency_ms,
                    "at": log.at.isoformat(),
                    "meta": log.meta
                }
                for log in logs
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get session logs: {str(e)}")


@router.post("/cleanup")
def cleanup_expired_sessions(
    days: int = Query(30, ge=1, le=365, description="Количество дней для хранения"),
    db: Session = Depends(get_db)
):
    """Context7 best practice: очистка истекших сессий."""
    try:
        # Context7 best practice: очистка истекших сессий
        expired_sessions = db.query(TelegramSession).filter(
            TelegramSession.status == "authorized",
            TelegramSession.created_at < datetime.utcnow() - timedelta(days=days)
        ).all()
        
        for session in expired_sessions:
            session.status = "expired"
            session.updated_at = datetime.utcnow()
            
            # Логирование очистки
            auth_log = TelegramAuthLog(
                session_id=session.id,
                event="cleanup_expired",
                reason=f"automated_cleanup_{days}_days"
            )
            db.add(auth_log)
        
        db.commit()
        cleaned_count = len(expired_sessions)
        
        return {"message": f"Expired sessions cleaned up (older than {days} days)"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cleanup sessions: {str(e)}")


@router.get("/stats/summary")
def get_session_stats(db: Session = Depends(get_db)):
    """Context7 best practice: статистика по сессиям."""
    try:
        # Context7 best practice: агрегированная статистика
        from sqlalchemy import func
        stats = db.query(
            TelegramSession.status,
            func.count(TelegramSession.id).label('count')
        ).group_by(TelegramSession.status).all()
        
        total_sessions = db.query(TelegramSession).count()
        
        return {
            "total_sessions": total_sessions,
            "by_status": {stat.status: stat.count for stat in stats},
            "active_sessions": next(
                (stat.count for stat in stats if stat.status == "authorized"), 0
            )
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get session stats: {str(e)}")
