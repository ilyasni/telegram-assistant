"""
⚠️ DEPRECATED ⚠️ 
──────────────────────────────────────────────────────────────────────────────
[Context7-DEPRECATED-001] miniapp_auth.py НЕ ДОЛЖЕН ИСПОЛЬЗОВАТЬСЯ!

ПРИЧИНА: Роутер не подключен к main.py, зависит от deprecated UnifiedSessionManager.
ТЕКУЩИЙ ПОДХОД: Используется api/routers/tg_auth.py для авторизации через miniapp.

ЗАПРЕЩЕНО:
- ❌ Подключать этот роутер к FastAPI app
- ❌ Использовать endpoints из этого модуля
- ❌ Импортировать UnifiedSessionManager/QRAuthManager отсюда

РАЗРЕШЕНО ТОЛЬКО:
- ✅ Чтение кода для миграции
- ✅ Удаление кода (после миграции)

──────────────────────────────────────────────────────────────────────────────
Context7: MiniApp API (Telegram WebApp initData) → QR session (Telethon) flow.
Strict initData verify, replay protection, opaque tokens, tenant isolation.
"""

import warnings
import os
import hmac
import hashlib
import time
import secrets
import json
import uuid
import qrcode
import io
import base64
import asyncio
from urllib.parse import parse_qsl
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request, Header, Body
from pydantic import BaseModel
import structlog
import redis.asyncio as redis
from prometheus_client import Counter, Histogram, Gauge

# [Context7-DEPRECATED-001] DEPRECATED импорты - НЕ ИСПОЛЬЗОВАТЬ!
# TODO: Удалить после завершения миграции
from services.session.unified_session_manager import UnifiedSessionManager, SessionState  # noqa: F401
from services.session.qr_auth_manager import QRAuthManager  # noqa: F401
from services.session.metrics import *

logger = structlog.get_logger()

# [Context7-DEPRECATED-001] Предупреждение при импорте модуля
warnings.warn(
    "api.miniapp_auth is DEPRECATED and should not be used! "
    "Router is not connected to main.py. Use api/routers/tg_auth.py instead. "
    "See [Context7-DEPRECATED-001]",
    DeprecationWarning,
    stacklevel=2
)
logger.critical(
    "[Context7-DEPRECATED-001] api.miniapp_auth module imported! "
    "This module should not be used in production."
)

# Router подключается в main с prefix="/miniapp"
router = APIRouter()

# Prometheus Metrics
MINIAPP_VERIFY_TOTAL = Counter("miniapp_verify_total", "Total MiniApp verify attempts", ["status"])
QR_SESSION_CREATED_TOTAL = Counter("qr_session_created_total", "Total QR sessions created", ["tenant"])
QR_STATUS_POLLED_TOTAL = Counter("qr_status_polled_total", "Total QR status polls", ["status"])
QR_FINALIZE_TOTAL = Counter("qr_finalize_total", "Total QR finalizations", ["status"])
QR_FAILED_TOTAL = Counter("qr_failed_total", "Total failed QR operations", ["reason"])
MINIAPP_VERIFY_LATENCY_SECONDS = Histogram("miniapp_verify_latency_seconds", "Latency of MiniApp verify endpoint")
QR_FINALIZE_LATENCY_SECONDS = Histogram("qr_finalize_latency_seconds", "Latency of QR finalize operation")
QR_SESSIONS_ACTIVE = Gauge("qr_sessions_active", "Number of active QR sessions", ["tenant"])


# --- Models ---
class MiniappAuthVerifyRequest(BaseModel):
    init_data: str

class MiniappAuthVerifyResponse(BaseModel):
    ok: bool
    token: str
    user: Dict[str, Any]

class MiniappQrSessionRequest(BaseModel):
    app_id: str

class MiniappQrSessionResponse(BaseModel):
    session_id: str
    qr_png_base64: str
    expires_in: int

class MiniappQrStatusResponse(BaseModel):
    status: str
    user_id: Optional[int] = None
    error: Optional[str] = None
    requires_password: Optional[bool] = False

class MiniappQrPasswordRequest(BaseModel):
    session_id: str
    password: str

class MiniappQrPasswordResponse(BaseModel):
    status: str
    message: str

class MiniappLogoutResponse(BaseModel):
    ok: bool

INIT_DATA_WHITELIST = {
    "auth_date", "query_id", "user", "receiver", "chat", "chat_type", "chat_instance", "start_param"
}


def _compute_telegram_secret(bot_token: str) -> bytes:
    """Вычисляет секретный ключ для верификации initData."""
    return hashlib.sha256(bot_token.encode()).digest()

def constant_time_compare(val1: str, val2: str) -> bool:
    """Constant-time сравнение строк для предотвращения timing attacks."""
    return hmac.compare_digest(val1.encode(), val2.encode())

def verify_init_data(init_data: str, bot_token: str, redis_client: redis.Redis) -> Dict[str, Any]:
    """Строгая верификация Telegram WebApp initData с replay protection."""
    with MINIAPP_VERIFY_LATENCY_SECONDS.time():
        if not bot_token:
            MINIAPP_VERIFY_TOTAL.labels(status="fail_config").inc()
            raise HTTPException(status_code=503, detail="Bot token not configured")
        
        # 1) parse querystring -> dict
        parsed_data = parse_qsl(init_data, keep_blank_values=True)
        data = {k: v for k, v in parsed_data}
        
        # 2) extract 'hash' and compute data_check_string
        provided_hash = data.pop("hash", None)
        if not provided_hash:
            MINIAPP_VERIFY_TOTAL.labels(status="fail_hash_missing").inc()
            raise HTTPException(status_code=400, detail="Missing hash in init_data")
        
        # Whitelist keys and sort by key
        whitelisted_keys = ["auth_date", "query_id", "user", "chat_instance", "chat_type", "chat", "receiver", "start_param", "can_send_messages"]
        
        # Filter and sort
        pairs = []
        for k in sorted(data.keys()):
            if k in whitelisted_keys:
                pairs.append(f"{k}={data[k]}")
        
        data_check_string = "\n".join(pairs)
        
        # 3) secret_key = HMAC_SHA256(key="WebAppData", message=bot_token)
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        
        # 4) calc_hash = HMAC_SHA256(key=secret_key, message=data_check_string)
        calc_hash = hmac.new(secret_key, data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()
        
        # 5) constant_time_compare(calc_hash, provided_hash)
        if not constant_time_compare(calc_hash, provided_hash):
            MINIAPP_VERIFY_TOTAL.labels(status="fail_hash_mismatch").inc()
            raise HTTPException(status_code=401, detail="Invalid init_data hash")
        
        # 6) check auth_date freshness (<= 15 минут) and reject future timestamps
        auth_date = int(data.get("auth_date", "0") or "0")
        current_time = int(time.time())
        
        if not (current_time - 900 <= auth_date <= current_time):  # 15 min tolerance
            MINIAPP_VERIFY_TOTAL.labels(status="fail_ttl").inc()
            raise HTTPException(status_code=400, detail="init_data expired or from future")
        
        # 7) Replay protection: store initData.hash in Redis
        # Use SETNX with TTL to prevent replay attacks
        replay_key = f"webapp_replay:{provided_hash}"
        if not asyncio.run(redis_client.setnx(replay_key, "1")):  # Use asyncio.run for sync context
            MINIAPP_VERIFY_TOTAL.labels(status="fail_replay").inc()
            raise HTTPException(status_code=409, detail="init_data already used (replay attack)")
        asyncio.run(redis_client.expire(replay_key, 900))
        
        MINIAPP_VERIFY_TOTAL.labels(status="ok").inc()
        return data


# --- Dependencies ---
async def get_redis_client(request: Request) -> redis.Redis:
    """Получение Redis клиента из app.state."""
    return request.app.state.redis_client

async def get_qr_auth_manager(request: Request) -> QRAuthManager:
    """Получение QRAuthManager из app.state."""
    manager = request.app.state.qr_auth_manager
    if not manager:
        logger.error("QRAuthManager not initialized in app_state")
        raise HTTPException(status_code=503, detail="QRAuthManager not available")
    return manager

async def require_miniapp_auth(request: Request) -> Dict[str, Any]:
    """Зависимость для проверки MiniApp токена."""
    user_id = request.state.user_id
    tenant_id = request.state.tenant_id
    miniapp_token = request.state.miniapp_token
    if not user_id or not tenant_id or not miniapp_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"user_id": user_id, "tenant_id": tenant_id, "miniapp_token": miniapp_token}


# --- Endpoints ---
@router.post("/auth/verify", response_model=MiniappAuthVerifyResponse)
async def miniapp_verify(
    req: MiniappAuthVerifyRequest, 
    request: Request,
    redis_client: redis.Redis = Depends(get_redis_client)
):
    """Верификация initData и выдача opaque токена."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not set")
    
    payload = verify_init_data(req.init_data, bot_token, redis_client)
    
    user_data_str = payload.get("user")
    if not user_data_str:
        MINIAPP_VERIFY_TOTAL.labels(status="fail_no_user").inc()
        raise HTTPException(status_code=400, detail="User data not found in init_data")
    
    try:
        user = json.loads(user_data_str)
        telegram_user_id = user.get("id")
        if not telegram_user_id:
            raise ValueError("User ID not found in user data")
    except (json.JSONDecodeError, ValueError) as e:
        MINIAPP_VERIFY_TOTAL.labels(status="fail_user_parse").inc()
        logger.error("Failed to parse user data from init_data", error=str(e), user_data_str=user_data_str)
        raise HTTPException(status_code=400, detail="Invalid user data in init_data")
    
    # TODO: Upsert user by telegram_id in DB
    # For now, we just use telegram_user_id as our internal user_id and tenant_id
    internal_user_id = str(telegram_user_id)
    tenant_id = str(telegram_user_id) # Using user_id as tenant_id for simplicity
    
    # Issue opaque token
    miniapp_token = str(uuid.uuid4())
    token_data = {
        "user_id": internal_user_id,
        "tenant_id": tenant_id,
        "iat": int(time.time())
    }
    token_key = f"tenant:{tenant_id}:miniapp_token:{miniapp_token}"
    await redis_client.setex(token_key, 86400, json.dumps(token_data))  # 24h TTL
    
    logger.info("MiniApp auth verified", 
                telegram_user_id=telegram_user_id, 
                tenant_id=tenant_id, 
                miniapp_token_prefix=miniapp_token[:8])
    
    return MiniappAuthVerifyResponse(
        ok=True, 
        token=miniapp_token, 
        user=user
    )


@router.post("/qr/session", response_model=MiniappQrSessionResponse)
async def create_qr_session(
    req: MiniappQrSessionRequest,
    auth: Dict[str, Any] = Depends(require_miniapp_auth),
    qr_auth_manager: QRAuthManager = Depends(get_qr_auth_manager),
    redis_client: redis.Redis = Depends(get_redis_client)
):
    """Создание QR сессии для авторизации."""
    tenant_id = auth["tenant_id"]
    user_id = auth["user_id"]
    miniapp_token = auth["miniapp_token"]
    
    # Rate limiting
    rate_limit_key = f"tenant:{tenant_id}:rl:qr_session:{miniapp_token}"
    current_requests = await redis_client.incr(rate_limit_key)
    if current_requests == 1:
        await redis_client.expire(rate_limit_key, 60) # 1 minute window
    
    if current_requests > 10: # 10 requests per minute per token
        raise HTTPException(status_code=429, detail="rate_limited")
    
    try:
        qr_session_data = await qr_auth_manager.create_qr_session(
            user_id=user_id, 
            tenant_id=tenant_id, 
            app_id=req.app_id
        )
        
        QR_SESSION_CREATED_TOTAL.labels(tenant=tenant_id).inc()
        QR_SESSIONS_ACTIVE.labels(tenant=tenant_id).inc()
        
        logger.info("QR session created", 
                    tenant_id=tenant_id, 
                    user_id=user_id, 
                    session_id=qr_session_data.session_id)
        
        return MiniappQrSessionResponse(
            session_id=qr_session_data.session_id,
            qr_png_base64=qr_session_data.qr_png_base64,
            expires_in=qr_session_data.expires_in
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create QR session", 
                    tenant_id=tenant_id, 
                    user_id=user_id, 
                    error=str(e))
        QR_FAILED_TOTAL.labels(reason="internal_qr_create").inc()
        raise HTTPException(status_code=500, detail="internal_error")

@router.get("/qr/status", response_model=MiniappQrStatusResponse)
async def get_qr_status(
    session_id: str,
    auth: Dict[str, Any] = Depends(require_miniapp_auth),
    qr_auth_manager: QRAuthManager = Depends(get_qr_auth_manager)
):
    """Получение статуса QR сессии."""
    tenant_id = auth["tenant_id"]
    user_id = auth["user_id"]
    
    try:
        auth_state = await qr_auth_manager.get_qr_status(session_id, tenant_id)
        
        status_str = auth_state.state.value.lower()
        
        QR_STATUS_POLLED_TOTAL.labels(status=status_str).inc()
        
        if auth_state.state == SessionState.AUTHORIZED:
            QR_FINALIZE_TOTAL.labels(status="authorized").inc()
            QR_SESSIONS_ACTIVE.labels(tenant=tenant_id).dec()
        elif auth_state.state == SessionState.EXPIRED:
            QR_FINALIZE_TOTAL.labels(status="expired").inc()
            QR_SESSIONS_ACTIVE.labels(tenant=tenant_id).dec()
        elif auth_state.state == SessionState.FAILED:
            QR_FINALIZE_TOTAL.labels(status="failed").inc()
            QR_FAILED_TOTAL.labels(reason=auth_state.error_message or "unknown").inc()
            QR_SESSIONS_ACTIVE.labels(tenant=tenant_id).dec()
        
        logger.info("QR status polled", 
                    tenant_id=tenant_id, 
                    user_id=user_id, 
                    session_id=session_id, 
                    status=status_str,
                    telegram_user_id=auth_state.telegram_user_id)
        
        return MiniappQrStatusResponse(
            status=status_str,
            user_id=auth_state.telegram_user_id,
            error=auth_state.error_message,
            requires_password=(auth_state.state == SessionState.PENDING_PASSWORD)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get QR status", 
                    tenant_id=tenant_id, 
                    user_id=user_id, 
                    session_id=session_id, 
                    error=str(e))
        QR_FAILED_TOTAL.labels(reason="internal_qr_status").inc()
        raise HTTPException(status_code=500, detail="internal_error")

@router.post("/qr/password", response_model=MiniappQrPasswordResponse)
async def submit_password(
    req: MiniappQrPasswordRequest,
    auth: Dict[str, Any] = Depends(require_miniapp_auth),
    qr_auth_manager: QRAuthManager = Depends(get_qr_auth_manager),
    redis_client: redis.Redis = Depends(get_redis_client)
):
    """Отправка 2FA пароля для финализации QR авторизации."""
    tenant_id = auth["tenant_id"]
    user_id = auth["user_id"]
    miniapp_token = auth["miniapp_token"]
    
    # Rate limiting
    rate_limit_key = f"tenant:{tenant_id}:rl:qr_password:{miniapp_token}"
    current_requests = await redis_client.incr(rate_limit_key)
    if current_requests == 1:
        await redis_client.expire(rate_limit_key, 60) # 1 minute window
    
    if current_requests > 5: # 5 requests per minute per token
        raise HTTPException(status_code=429, detail="rate_limited")
    
    try:
        auth_state = await qr_auth_manager.finalize_qr(
            session_id=req.session_id, 
            tenant_id=tenant_id, 
            password=req.password,
            expected_user_id=int(user_id) # Ownership check
        )
        
        if auth_state.state == SessionState.AUTHORIZED:
            QR_FINALIZE_TOTAL.labels(status="authorized").inc()
            QR_SESSIONS_ACTIVE.labels(tenant=tenant_id).dec()
            logger.info("2FA password verified, QR authorized", 
                        tenant_id=tenant_id, 
                        user_id=user_id, 
                        session_id=req.session_id)
            return MiniappQrPasswordResponse(status="authorized", message="Password verified successfully")
        elif auth_state.state == SessionState.PENDING_PASSWORD:
            QR_FAILED_TOTAL.labels(reason="invalid_password").inc()
            logger.warning("2FA password invalid", 
                           tenant_id=tenant_id, 
                           user_id=user_id, 
                           session_id=req.session_id)
            raise HTTPException(status_code=400, detail="invalid_password")
        elif auth_state.state == SessionState.FAILED:
            QR_FINALIZE_TOTAL.labels(status="failed").inc()
            QR_FAILED_TOTAL.labels(reason=auth_state.error_message or "unknown").inc()
            QR_SESSIONS_ACTIVE.labels(tenant=tenant_id).dec()
            if auth_state.error_message == "ownership_mismatch":
                raise HTTPException(status_code=409, detail="ownership_mismatch")
            raise HTTPException(status_code=500, detail=auth_state.error_message or "internal_error")
        else:
            QR_FAILED_TOTAL.labels(reason="unexpected_state").inc()
            raise HTTPException(status_code=500, detail="internal_error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to submit 2FA password", 
                    tenant_id=tenant_id, 
                    user_id=user_id, 
                    session_id=req.session_id, 
                    error=str(e))
        QR_FAILED_TOTAL.labels(reason="internal_password_submit").inc()
        raise HTTPException(status_code=500, detail="internal_error")

@router.post("/logout", response_model=MiniappLogoutResponse)
async def miniapp_logout(
    auth: Dict[str, Any] = Depends(require_miniapp_auth),
    qr_auth_manager: QRAuthManager = Depends(get_qr_auth_manager),
    redis_client: redis.Redis = Depends(get_redis_client)
):
    """Выход из MiniApp и инвалидация токена."""
    tenant_id = auth["tenant_id"]
    user_id = auth["user_id"]
    miniapp_token = auth["miniapp_token"]
    
    # Invalidate miniapp_token
    token_key = f"tenant:{tenant_id}:miniapp_token:{miniapp_token}"
    await redis_client.delete(token_key)
    
    # Invalidate active QR sessions for this user/tenant
    # This would require iterating through active sessions, which is complex.
    # For now, rely on TTL for QR sessions.
    
    logger.info("MiniApp logged out", 
                tenant_id=tenant_id, 
                user_id=user_id, 
                miniapp_token_prefix=miniapp_token[:8])
    
    return MiniappLogoutResponse(ok=True)


@router.get("/health")
async def health_check():
    """Health check endpoint для MiniApp API."""
    try:
        # Проверяем состояние session manager и Redis
        return {
            "status": "healthy",
            "timestamp": int(time.time()),
            "service": "miniapp_auth",
            "version": "1.0.0"
        }
        
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(status_code=500, detail="Health check failed")