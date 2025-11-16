"""
Telegram WebApp Authentication.
[C7-ID: API-WEBAPP-AUTH-001]

Верификация initData и выдача JWT токенов для Mini App.
"""

import hmac
import hashlib
import json
import time
from typing import Optional
from urllib.parse import parse_qsl
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, model_validator
import structlog
from sqlalchemy.orm import Session
from models.database import get_db, User, Identity
from routers.tg_auth import resolve_tenant_from_token  # type: ignore  # noqa: E402
from config import settings

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])

class WebAppAuthRequest(BaseModel):
    """Запрос на аутентификацию WebApp."""
    init_data: str | None = Field(default=None, description="Telegram WebApp initData")
    token: str | None = Field(default=None, description="Fallback JWT token")

    @model_validator(mode="after")
    def check_payload(cls, data):
        if not data.init_data and not data.token:
            raise ValueError("init_data or token must be provided")
        return data

class WebAppAuthResponse(BaseModel):
    """Ответ с JWT токеном."""
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Тип токена")
    expires_in: int = Field(default=600, description="Время жизни токена в секундах")

def create_jwt(
    user_id: int,
    tenant_id: Optional[str] = None,
    membership_id: Optional[str] = None,
    identity_id: Optional[str] = None,
    tier: Optional[str] = None,
    role: Optional[str] = None,
    audience: str = "webapp",
    exp_minutes: int = 10
) -> str:
    """Создание JWT токена (упрощенная версия)."""
    # TODO: Реализовать полноценную JWT генерацию с подписью
    # Пока возвращаем простой токен
    import base64
    
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user_id),  # telegram_id для обратной совместимости
        "aud": audience,
        "exp": int(time.time()) + (exp_minutes * 60),
        "iat": int(time.time())
    }
    
    # Context7: расширенный payload для multi-tenant
    if tenant_id:
        payload["tenant_id"] = str(tenant_id)
    if membership_id:
        payload["membership_id"] = str(membership_id)
        payload["user_id"] = str(membership_id)  # Для обратной совместимости с get_admin_user
    if identity_id:
        payload["identity_id"] = str(identity_id)
    if tier:
        payload["tier"] = tier
    # [C7-ID: security-admin-003] Добавляем role в JWT payload
    # Context7: Всегда добавляем role, даже если это "user" (для безопасности)
    payload["role"] = role or "user"
    
    # Простая кодировка (в production использовать jwt library)
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip('=')
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    
    # Подпись (упрощенная)
    signature = hmac.new(
        settings.jwt_secret.get_secret_value().encode(),
        f"{header_b64}.{payload_b64}".encode(),
        hashlib.sha256
    ).digest()
    
    signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip('=')
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"

@router.post("/telegram-webapp", response_model=WebAppAuthResponse)
async def verify_webapp_init_data(body: WebAppAuthRequest, db: Session = Depends(get_db)):
    """
    Верификация Telegram WebApp initData.
    Возвращает короткоживущий JWT (5-10 мин) с tenant_id, membership_id, identity_id, tier.
    """
    try:
        if body.init_data:
            init_data = body.init_data
            bot_token = settings.telegram_bot_token
            
            if not bot_token:
                raise HTTPException(status_code=500, detail="Bot token not configured")
            
            parsed = dict(parse_qsl(init_data))
            hash_received = parsed.pop('hash', None)
            
            if not hash_received:
                raise HTTPException(status_code=401, detail="Missing hash in initData")
            
            data_check_arr = [f"{k}={v}" for k, v in sorted(parsed.items())]
            data_check_string = "\n".join(data_check_arr)
            
            secret_key = hmac.new(
                b"WebAppData",
                bot_token.encode(),
                hashlib.sha256
            ).digest()
            
            hash_calculated = hmac.new(
                secret_key,
                data_check_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            if hash_calculated != hash_received:
                logger.warning(
                    "Invalid WebApp initData signature",
                    received_hash=hash_received[:8] + "...",
                    calculated_hash=hash_calculated[:8] + "...",
                )
                raise HTTPException(status_code=401, detail="Invalid signature")
            
            auth_date = int(parsed.get('auth_date', 0))
            current_time = int(time.time())

            ttl_seconds = int(getattr(settings, 'webapp_auth_ttl_seconds', 900))
            if current_time - auth_date > ttl_seconds:
                logger.warning(
                    "WebApp initData expired",
                    auth_date=auth_date,
                    current_time=current_time,
                    age_seconds=current_time - auth_date,
                    ttl_seconds=ttl_seconds,
                )
                raise HTTPException(status_code=401, detail="Init data expired")
            
            user_data_str = parsed.get('user', '{}')
            try:
                user_data = json.loads(user_data_str)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid user data")
            
            user_id = user_data.get('id')
            if not user_id:
                raise HTTPException(status_code=400, detail="Missing user ID")
            
            identity = db.query(Identity).filter(Identity.telegram_id == user_id).first()
            membership = None
            tenant_id = None
            membership_id = None
            tier = None
            role = None
            if identity:
                membership = db.query(User).filter(User.identity_id == identity.id).first()
                if membership:
                    tenant_id = str(membership.tenant_id)
                    membership_id = str(membership.id)
                    tier = membership.tier or "free"
                    role = membership.role or "user"
            
            jwt_token = create_jwt(
                user_id=user_id,
                tenant_id=tenant_id,
                membership_id=membership_id,
                identity_id=str(identity.id) if identity else None,
                tier=tier,
                role=role,
                audience="webapp",
                exp_minutes=10
            )
            
            logger.info(
                "WebApp authentication successful",
                user_id=user_id,
                tenant_id=tenant_id,
                membership_id=membership_id,
                identity_id=str(identity.id) if identity else None,
                tier=tier,
                role=role,
                username=user_data.get('username'),
                role_in_jwt=role,
            )
            
            return WebAppAuthResponse(
                access_token=jwt_token,
                token_type="bearer",
                expires_in=600
            )

        # Fallback: одноразовый JWT из URL
        tenant_id_token, telegram_id = resolve_tenant_from_token(body.token, db)
        if not telegram_id:
            raise HTTPException(status_code=400, detail="telegram_id missing in token")

        identity = db.query(Identity).filter(Identity.telegram_id == int(telegram_id)).first()
        if not identity:
            raise HTTPException(status_code=404, detail="identity not found for telegram_id")

        membership_query = db.query(User).filter(User.identity_id == identity.id)
        if tenant_id_token:
            membership_query = membership_query.filter(User.tenant_id == tenant_id_token)
        membership = membership_query.first()
        if not membership:
            raise HTTPException(status_code=403, detail="membership not found for identity")

        tenant_id = str(membership.tenant_id)
        membership_id = str(membership.id)
        tier = membership.tier or "free"
        role = membership.role or "user"

        if role != "admin":
            logger.warning(
                "WebApp fallback token used by non-admin user",
                telegram_id=telegram_id,
                tenant_id=tenant_id,
                role=role,
            )
            raise HTTPException(status_code=403, detail="Admin privileges required")

        jwt_token = create_jwt(
            user_id=int(telegram_id),
            tenant_id=tenant_id,
            membership_id=membership_id,
            identity_id=str(identity.id),
            tier=tier,
            role=role,
            audience="webapp",
            exp_minutes=10
        )

        logger.info(
            "WebApp authentication via fallback token successful",
            telegram_id=telegram_id,
            tenant_id=tenant_id,
            membership_id=membership_id,
            identity_id=str(identity.id),
            tier=tier,
            role=role,
        )

        return WebAppAuthResponse(
            access_token=jwt_token,
            token_type="bearer",
            expires_in=600
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("WebApp authentication failed", error=str(e))
        raise HTTPException(status_code=500, detail="Authentication failed")

def verify_jwt_token(token: str) -> Optional[dict]:
    """Верификация JWT токена (упрощенная версия)."""
    try:
        # TODO: Реализовать полноценную JWT верификацию
        # Пока возвращаем mock данные
        parts = token.split('.')
        if len(parts) != 3:
            return None
        
        # Декодирование payload
        import base64
        payload_b64 = parts[1]
        # Добавление padding если нужно
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        
        # Проверка времени истечения
        if payload.get('exp', 0) < int(time.time()):
            return None
        
        return payload
        
    except Exception as e:
        logger.error("JWT verification failed", error=str(e))
        return None
