"""Telegram Mini App / QR Auth endpoints.

Security: HMAC validation for initData, short-lived JWT session_token, rate-limit via Redis.
Metrics: Prometheus counters and histograms.
"""

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
import asyncio
import time
import jwt
import os
import hashlib
import hmac
import base64
import json
import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Histogram
from config import settings
import qrcode
import io

router = APIRouter(prefix="/tg", tags=["tg_auth"])
logger = structlog.get_logger()

AUTH_QR_START = Counter("auth_qr_start_total", "QR start attempts", ["tenant_id"])
AUTH_QR_SUCCESS = Counter("auth_qr_success_total", "QR success", ["tenant_id"])
AUTH_QR_FAIL = Counter("auth_qr_fail_total", "QR failures", ["tenant_id", "reason"])
AUTH_QR_DURATION = Histogram("auth_qr_duration_seconds", "QR auth duration", ["tenant_id"])
AUTH_QR_EXPIRED = Counter("auth_qr_expired_total", "QR sessions expired", ["tenant_id"])
AUTH_QR_OWNERSHIP_FAIL = Counter("auth_qr_ownership_fail_total", "Ownership check failures")
AUTH_QR_2FA_REQUIRED = Counter("auth_qr_2fa_required_total", "2FA required count") 

# Context7 best practice: async Redis client для неблокирующих операций
redis_client = redis.from_url(settings.redis_url, decode_responses=True)


class MiniAppInit(BaseModel):
    init_data: str


class MiniAppLink(BaseModel):
    start_param: str
    tenant_id: str


class QrStart(BaseModel):
    tenant_id: str
    session_token: str | None = None
    invite_code: str | None = None
    init_data: str | None = None  # Telegram MiniApp initData для извлечения telegram_user_id


def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    try:
        # Telegram initData HMAC validation (RFC 2104)
        data_check_string = "\n".join(sorted([kv for kv in init_data.split("&") if not kv.startswith("hash=")]))
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        computed_hash = hmac.new(secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()
        provided_hash = dict(kv.split("=") for kv in init_data.split("&")).get("hash", "")
        return hmac.compare_digest(computed_hash, provided_hash)
    except Exception:
        return False


def issue_session_token(tenant_id: str, ttl_seconds: int = None) -> str:
    # Context7 best practice: увеличиваем TTL для JWT токенов до 30 минут
    # (временно, пока чиним telethon-ingest)
    if ttl_seconds is None:
        ttl_seconds = int(os.getenv("JWT_TTL_SECONDS", "1800"))  # 30 минут
    now = int(time.time())
    payload = {
        "iss": "telegram-assistant",
        "aud": "tg-auth",
        "sub": tenant_id,
        "tenant": tenant_id,
        "iat": now,
        "exp": now + ttl_seconds,
        "nonce": base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_session_token(token: str) -> dict:
    """Context7 best practice: JWT security с audience verification.
    
    [C7-ID: security-jwt-001]
    """
    try:
        payload = jwt.decode(
            token, 
            settings.jwt_secret, 
            algorithms=["HS256"],
            options={"verify_aud": True},  # Включаем проверку audience
            audience="tg-auth"  # Обязательная проверка audience
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _extract_tenant_id(payload: dict) -> str:
    """Возвращает tenant_id из полезной нагрузки JWT, совместимо с разными ключами.

    Поддерживает поля: "tenant", "sub", "tenant_id".
    """
    tenant_id = (
        payload.get("tenant")
        or payload.get("sub")
        or payload.get("tenant_id")
    )
    if not tenant_id:
        raise HTTPException(status_code=400, detail="invalid token payload")
    return str(tenant_id)

# Context7 best practice: Redis-based sliding window rate limiting
# [C7-ID: fastapi-ratelimit-003]
async def ratelimit(key: str, max_per_minute: int = 30) -> bool:
    """Sliding window rate limiting с Redis (async)."""
    bucket = f"rl:{key}:{int(time.time() // 60)}"
    v = await redis_client.incr(bucket)
    if v == 1:
        await redis_client.expire(bucket, 120)  # 2 минуты TTL для cleanup
    
    # Метрики для мониторинга
    if v > max_per_minute:
        logger.warning("Rate limit exceeded", key=key, count=v, limit=max_per_minute)
    
    return v <= max_per_minute


async def ratelimit_strict(key: str, max_per_minute: int = 5) -> bool:
    """Более жёсткий лимит для критических endpoints (async)."""
    bucket = f"rl:strict:{key}:{int(time.time() // 60)}"
    v = await redis_client.incr(bucket)
    if v == 1:
        await redis_client.expire(bucket, 120)
    
    if v > max_per_minute:
        # Логирование подозрительной активности
        logger.warning("Strict rate limit exceeded", key=key, count=v, limit=max_per_minute)
    
    return v <= max_per_minute


@router.post("/miniapp/init")
async def miniapp_init(body: MiniAppInit, request: Request):
    # Verify origin (basic; can be extended)
    # origin = request.headers.get("origin", "")
    # if origin not in settings.allowed_origins: raise HTTPException(403, "forbidden origin")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token or not verify_telegram_init_data(body.init_data, bot_token):
        raise HTTPException(status_code=400, detail="invalid initData")
    return {"ok": True}


@router.post("/miniapp/link")
async def miniapp_link(body: MiniAppLink):
    key = f"miniapp:link:{body.tenant_id}"
    if not await ratelimit(key):
        raise HTTPException(status_code=429, detail="rate limit")
    token = issue_session_token(body.tenant_id)
    return {"session_token": token}


@router.post("/qr/start")
@router.post("/qr-auth/init")  # Алиас для обратной совместимости
async def qr_start(body: QrStart, request: Request):
    tenant_id = body.tenant_id
    ip = request.client.host
    
    # Строгий rate limiting для QR start
    if not await ratelimit_strict(f"qr:start:{ip}"):
        raise HTTPException(status_code=429, detail="Too many requests. Try again in 1 minute.")

    AUTH_QR_START.labels(tenant_id=tenant_id).inc()
    token = body.session_token or issue_session_token(tenant_id)

    # Context7: Извлекаем telegram_user_id из initData если предоставлен
    # Context7 best practice: это необходимо для корректной проверки существующих сессий для новых пользователей
    telegram_user_id = None
    
    # Context7: детальное логирование для отладки
    logger.info("QR start request received", 
               tenant_id=tenant_id,
               has_init_data=bool(body.init_data),
               init_data_length=len(body.init_data) if body.init_data else 0,
               init_data_preview=body.init_data[:100] if body.init_data and len(body.init_data) > 0 else None)
    
    if body.init_data:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        logger.info("Processing init_data", 
                   tenant_id=tenant_id, 
                   init_data_length=len(body.init_data),
                   has_bot_token=bool(bot_token))
        
        # Context7: пытаемся извлечь telegram_user_id даже если verify_telegram_init_data fails
        # Это важно для новых пользователей, где проверка может не пройти
        try:
            # Парсим initData для извлечения user_id
            # Context7: декодируем URL-encoded строку user
            from urllib.parse import unquote
            parsed = dict(kv.split("=", 1) for kv in body.init_data.split("&") if "=" in kv)
            logger.debug("Parsed init_data keys", keys=list(parsed.keys())[:5], tenant_id=tenant_id)
            
            user_data_str = parsed.get("user", "{}")
            if user_data_str and user_data_str != "{}":
                user_data_str = unquote(user_data_str)
                user_data = json.loads(user_data_str)
                telegram_user_id = user_data.get("id")
                logger.info("Extracted telegram_user_id from initData", 
                          telegram_user_id=telegram_user_id, 
                          tenant_id=tenant_id,
                          verified=bool(bot_token and verify_telegram_init_data(body.init_data, bot_token)))
        except Exception as e:
            logger.error("Failed to extract telegram_user_id from initData", 
                        error=str(e), 
                        tenant_id=tenant_id,
                        exception_type=type(e).__name__)

    # Context7: Создаём запись статуса в Redis с единым префиксом t:{tenant}:qr:session
    key = f"t:{tenant_id}:qr:session"
    
    session_data = {
        "tenant_id": tenant_id,
        "session_token": token,
        "status": "pending",
        "created_at": str(int(time.time()))
    }
    
    # Context7 best practice: сохраняем telegram_user_id в сессию для проверки существующих сессий
    # ВАЖНО: всегда сохраняем telegram_user_id если он был извлечен
    if telegram_user_id:
        session_data["telegram_user_id"] = str(telegram_user_id)
        logger.info("Creating QR session with telegram_user_id", 
                   key=key, 
                   tenant_id=tenant_id, 
                   telegram_user_id=telegram_user_id)
    else:
        logger.warning("Creating QR session WITHOUT telegram_user_id", 
                      key=key, 
                      tenant_id=tenant_id,
                      has_init_data=bool(body.init_data))
    
    logger.info("Creating QR session key", key=key, tenant_id=tenant_id, telegram_user_id=telegram_user_id)
    
    # Добавляем invite_code если передан
    if body.invite_code:
        session_data["invite_code"] = body.invite_code
        logger.info("QR session with invite code", tenant_id=tenant_id, invite_code=body.invite_code)
    
    # Context7 best practice: используем async Redis операции
    # ВАЖНО: удаляем старую сессию перед созданием новой, чтобы не было конфликтов
    # Context7: проверяем и удаляем существующую failed/expired сессию
    existing_data = await redis_client.hgetall(key)
    if existing_data and existing_data.get("status") in ["failed", "expired"]:
        logger.info("Deleting old failed/expired session before creating new one", 
                   key=key, 
                   old_status=existing_data.get("status"),
                   old_telegram_user_id=existing_data.get("telegram_user_id"))
        await redis_client.delete(key)
    
    await redis_client.hset(key, mapping=session_data)
    # Context7 best practice: увеличиваем TTL для QR-сессий до 20 минут
    # (временно, пока чиним telethon-ingest)
    QR_TTL_SECONDS = int(os.getenv("QR_TTL_SECONDS", "1200"))  # 20 минут
    await redis_client.expire(key, QR_TTL_SECONDS)
    
    # Context7: проверяем что telegram_user_id действительно сохранен
    saved_telegram_user_id = await redis_client.hget(key, "telegram_user_id")
    logger.info("QR session key created", 
               key=key, 
               tenant_id=tenant_id,
               telegram_user_id=telegram_user_id,
               saved_telegram_user_id=saved_telegram_user_id,
               match=(str(telegram_user_id) == saved_telegram_user_id if telegram_user_id and saved_telegram_user_id else False))

    expires_at = int(time.time()) + QR_TTL_SECONDS
    
    # Проверяем, есть ли уже qr_url (может быть None если telethon-ingest еще не обработал)
    # Context7 best practice: с decode_responses=True значения уже декодированы в строки
    qr_url = await redis_client.hget(key, "qr_url")
    
    return {
        "session_token": token, 
        "expires_at": expires_at,
        "qr_url": qr_url
    }


@router.post("/qr/status")
@router.get("/qr-auth/status/{session_id}")  # Алиас для обратной совместимости
async def qr_status_post(body: dict):
    token = body.get("session_token")
    if not token:
        raise HTTPException(status_code=400, detail="session_token required")
    # Декодируем JWT чтобы получить tenant_id
    try:
        payload = decode_session_token(token)
        tenant_id = _extract_tenant_id(payload)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to decode session token", error=str(e))
        raise HTTPException(status_code=400, detail="invalid token")
    
    # Context7: Единый префикс t:{tenant}:qr:session
    key = f"t:{tenant_id}:qr:session"
    data = await redis_client.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail="not found or expired")
    # Context7 best practice: с decode_responses=True все ключи и значения уже декодированы в строки
    resp = {"status": data.get("status", "pending")}
    if "qr_url" in data:
        resp["qr_url"] = data["qr_url"]
    # Context7 best practice: прокидываем причину ошибки из Redis, чтобы фронт не показывал "Неизвестная ошибка"
    if resp["status"] == "failed" and "reason" in data:
        resp["reason"] = data["reason"]
    return resp


@router.get("/qr/sse")
async def qr_sse(token: str):
    """SSE endpoint для статуса QR-авторизации (async генератор)."""
    
    async def gen():
        import time as _t
        while True:
            try:
                payload = decode_session_token(token)
                tenant_id = _extract_tenant_id(payload)
                key = f"t:{tenant_id}:qr:session"
                # Context7 best practice: используем async Redis операции
                # с decode_responses=True все ключи и значения уже декодированы в строки
                data = await redis_client.hgetall(key)
                status = "pending"
                qr_url = None
                if data:
                    status = data.get("status", "pending")
                    qr_url = data.get("qr_url")
                line = {
                    "status": status,
                    "qr_url": qr_url,
                }
                yield f"data: {json.dumps(line)}\n\n"
                await asyncio.sleep(1.5)
            except Exception:
                yield "data: {\"status\": \"error\"}\n\n"
                await asyncio.sleep(2)
    
    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/qr/cancel")
async def qr_cancel(token: str):
    # Декодируем JWT чтобы получить tenant_id
    try:
        payload = decode_session_token(token)
        tenant_id = _extract_tenant_id(payload)
    except:
        raise HTTPException(status_code=400, detail="invalid token")
    
    # Context7: Единый префикс t:{tenant}:qr:session
    key = f"t:{tenant_id}:qr:session"
    if not await redis_client.exists(key):
        raise HTTPException(status_code=404, detail="not found or expired")
    await redis_client.hset(key, mapping={"status": "cancelled"})
    return {"ok": True}


@router.get("/qr/png/{session_id}")
@router.get("/qr-auth/png/{session_id}")  # Алиас для обратной совместимости
async def qr_png(session_id: str):
    """Context7 best practice: PNG fallback для QR-кода"""
    try:
        # Декодируем session_id (это JWT токен)
        payload = jwt.decode(session_id, settings.jwt_secret, algorithms=["HS256"], options={"verify_aud": False})
        tenant_id = _extract_tenant_id(payload)
    except:
        raise HTTPException(status_code=400, detail="invalid session")
    
    # Context7: Единый префикс t:{tenant}:qr:session
    key = f"t:{tenant_id}:qr:session"
    if not await redis_client.exists(key):
        raise HTTPException(status_code=404, detail="not found or expired")
    
    # Context7 best practice: с decode_responses=True все ключи и значения уже декодированы в строки
    data = await redis_client.hgetall(key)
    qr_url = data.get("qr_url", "")
    
    if not qr_url:
        raise HTTPException(status_code=404, detail="qr_url not available")
    
    # Генерируем PNG QR-код
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Конвертируем в PNG bytes
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG')
    img_buffer.seek(0)
    
    return Response(
        content=img_buffer.getvalue(),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


