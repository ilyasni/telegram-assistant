"""
Dependencies для аутентификации и извлечения tenant_id из JWT.
Context7: Multi-tenant изоляция через JWT payload.
"""

from fastapi import Request, HTTPException, Depends
from typing import Optional
import structlog
import jwt
import base64
import json
from config import settings
from sqlalchemy.orm import Session
from models.database import get_db, User, Identity
import uuid

logger = structlog.get_logger()


def extract_tenant_id_from_jwt(request: Request) -> Optional[str]:
    """
    Context7: Извлечение tenant_id из JWT токена в Authorization header.
    
    Returns:
        tenant_id из JWT payload или None, если токен отсутствует/невалиден
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.split(' ', 1)[1]
    try:
        # Декодируем без верификации (для извлечения данных)
        # В production нужно использовать полную верификацию
        parts = token.split('.')
        if len(parts) != 3:
            return None
        
        payload_b64 = parts[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        
        # Проверка времени истечения
        import time
        if payload.get('exp', 0) < int(time.time()):
            return None
        
        return payload.get('tenant_id')
    except Exception as e:
        logger.debug("Failed to extract tenant_id from JWT", error=str(e))
        return None


def get_current_tenant_id(request: Request) -> str:
    """
    Context7: Dependency для получения текущего tenant_id из JWT.
    
    Raises:
        HTTPException 401: если токен отсутствует или невалиден
    """
    tenant_id = extract_tenant_id_from_jwt(request)
    
    # Также проверяем request.state (устанавливается RLSMiddleware)
    if not tenant_id and hasattr(request, 'state'):
        tenant_id = getattr(request.state, 'tenant_id', None)
    
    if not tenant_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: tenant_id not found in JWT token"
        )
    
    return tenant_id


def get_current_tenant_id_optional(request: Request) -> Optional[str]:
    """
    Context7: Dependency для получения tenant_id из JWT (опционально).
    
    Returns:
        tenant_id или None, если токен отсутствует
    """
    tenant_id = extract_tenant_id_from_jwt(request)
    
    # Также проверяем request.state (устанавливается RLSMiddleware)
    if not tenant_id and hasattr(request, 'state'):
        tenant_id = getattr(request.state, 'tenant_id', None)
    
    return tenant_id


def extract_user_id_from_jwt(request: Request) -> Optional[str]:
    """
    [C7-ID: security-admin-002] Извлечение user_id из JWT токена.
    
    Returns:
        user_id из JWT payload или None, если токен отсутствует/невалиден
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.split(' ', 1)[1]
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        
        payload_b64 = parts[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        
        # Проверка времени истечения
        import time
        if payload.get('exp', 0) < int(time.time()):
            return None
        
        return payload.get('user_id') or payload.get('membership_id')
    except Exception as e:
        logger.debug("Failed to extract user_id from JWT", error=str(e))
        return None


def get_admin_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    [C7-ID: security-admin-002] Dependency для получения текущего админа из JWT.
    
    Raises:
        HTTPException 401: если токен отсутствует или невалиден
        HTTPException 403: если пользователь не является админом
    """
    user_id = extract_user_id_from_jwt(request)
    
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: user_id not found in JWT token"
        )
    
    try:
        user = db.query(User).filter(User.id == uuid.UUID(user_id)).first()
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid user_id format")
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # [C7-ID: security-admin-002] Проверка роли админа
    if user.role != 'admin':
        logger.warning(
            "Non-admin user attempted admin access",
            user_id=str(user.id),
            role=user.role
        )
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )
    
    return user

