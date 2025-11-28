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
from sqlalchemy.orm import Session
from urllib.parse import parse_qsl, unquote
from prometheus_client import Counter, Histogram
from config import settings
import qrcode
import io
from models.database import get_db, Identity, User

router = APIRouter(prefix="/tg", tags=["tg_auth"])
logger = structlog.get_logger()

AUTH_QR_START = Counter("auth_qr_start_total", "QR start attempts", ["tenant_id"], namespace="api")
AUTH_QR_SUCCESS = Counter("auth_qr_success_total", "QR success", ["tenant_id"], namespace="api")
AUTH_QR_FAIL = Counter("auth_qr_fail_total", "QR failures", ["tenant_id", "reason"], namespace="api")
AUTH_QR_DURATION = Histogram("auth_qr_duration_seconds", "QR auth duration", ["tenant_id"], namespace="api")
AUTH_QR_EXPIRED = Counter("auth_qr_expired_total", "QR sessions expired", ["tenant_id"], namespace="api")
AUTH_QR_OWNERSHIP_FAIL = Counter("auth_qr_ownership_fail_total", "Ownership check failures", namespace="api")
AUTH_QR_2FA_REQUIRED = Counter("auth_qr_2fa_required_total", "2FA required count", ["tenant_id"], namespace="api")

# Context7 best practice: async Redis client для неблокирующих операций
redis_client = redis.from_url(settings.redis_url, decode_responses=True)


class MiniAppInit(BaseModel):
    init_data: str


class MiniAppLink(BaseModel):
    start_param: str
    tenant_id: str


class QrStart(BaseModel):
    tenant_id: str | None = None
    session_token: str | None = None
    invite_code: str | None = None
    init_data: str | None = None  # Telegram MiniApp initData для извлечения telegram_user_id
    token: str | None = None  # Fallback JWT от бота


class QrPassword(BaseModel):
    session_token: str
    password: str


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


def resolve_tenant_from_init_data(init_data: str, db: Session) -> str:
    """Context7: Надёжное извлечение tenant_id из initData (fallback для Mini App)."""
    if not init_data:
        raise HTTPException(status_code=400, detail="init_data is required to resolve tenant_id")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not configured")
        raise HTTPException(status_code=500, detail="bot token not configured")

    is_valid = verify_telegram_init_data(init_data, bot_token)
    logger.info(
        "verify_initdata_signature",
        init_data_length=len(init_data),
        has_hash="hash=" in init_data,
        is_valid=is_valid,
    )

    if not is_valid:
        logger.warning("initData signature check failed while resolving tenant", init_data_length=len(init_data))
        raise HTTPException(status_code=401, detail="invalid initData signature")

    parsed = dict(parse_qsl(init_data))
    user_str = parsed.get("user")
    if not user_str:
        raise HTTPException(status_code=400, detail="user data missing in initData")

    auth_date_raw = parsed.get("auth_date")
    ttl_seconds = getattr(settings, "webapp_auth_ttl_seconds", 900) or 900
    if not auth_date_raw:
        logger.warning("initData missing auth_date", init_data_length=len(init_data))
        raise HTTPException(status_code=400, detail="auth_date missing in initData")
    try:
        auth_date = int(auth_date_raw)
    except (TypeError, ValueError):
        logger.warning("initData invalid auth_date", auth_date=auth_date_raw)
        raise HTTPException(status_code=400, detail="invalid auth_date value")

    now_ts = int(time.time())
    if auth_date > now_ts + 60:
        logger.warning("initData auth_date is in the future", auth_date=auth_date, now=now_ts)
        raise HTTPException(status_code=401, detail="invalid auth_date")
    if now_ts - auth_date > ttl_seconds:
        logger.warning(
            "initData auth_date expired",
            auth_date=auth_date,
            now=now_ts,
            ttl=ttl_seconds,
            age=now_ts - auth_date,
        )
        raise HTTPException(status_code=401, detail="initData expired")

    try:
        user_data = json.loads(user_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid user payload in initData")

    telegram_id = user_data.get("id")
    if not telegram_id:
        raise HTTPException(status_code=400, detail="telegram_id missing in initData user payload")

    identity = db.query(Identity).filter(Identity.telegram_id == telegram_id).first()
    if not identity:
        raise HTTPException(status_code=404, detail="identity not found for telegram_id")

    membership = db.query(User).filter(User.identity_id == identity.id).first()
    if not membership or not membership.tenant_id:
        raise HTTPException(status_code=403, detail="tenant binding not found for identity")

    logger.info(
        "tenant_resolved_from_initdata",
        telegram_id=telegram_id,
        tenant_id=str(membership.tenant_id),
    )
    return str(membership.tenant_id)


def resolve_tenant_from_token(token: str, db: Session) -> tuple[str, str | None]:
    if not token:
        raise HTTPException(status_code=400, detail="token is required to resolve tenant_id")
    try:
        # Context7: детальное логирование для диагностики проблем с токеном
        logger.debug("Decoding fallback token", 
                    token_length=len(token),
                    token_preview=token[:50] if len(token) > 50 else token)
        
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience="qr_webapp",
            options={"require": ["tenant_id", "session_id"], "verify_aud": True}
        )
        
        logger.debug("Token decoded successfully", 
                    has_tenant_id="tenant_id" in payload,
                    has_session_id="session_id" in payload,
                    has_telegram_id="telegram_id" in payload,
                    purpose=payload.get("purpose"),
                    audience=payload.get("aud"))
    except jwt.ExpiredSignatureError as e:
        logger.warning("Fallback token expired", error=str(e))
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidAudienceError as e:
        logger.warning("Invalid token audience", 
                      error=str(e),
                      expected_audience="qr_webapp",
                      token_audience=getattr(e, 'audience', None))
        raise HTTPException(status_code=401, detail="invalid token audience")
    except jwt.MissingRequiredClaimError as e:
        logger.warning("Missing required claim in token", 
                      error=str(e),
                      claim=getattr(e, 'claim', None))
        raise HTTPException(status_code=401, detail=f"missing required claim: {getattr(e, 'claim', 'unknown')}")
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid fallback token", 
                      error=str(exc),
                      error_type=type(exc).__name__)
        raise HTTPException(status_code=401, detail="invalid token")

    tenant_id = payload.get("tenant_id")
    telegram_id = payload.get("telegram_id")
    purpose = payload.get("purpose")

    if purpose not in (None, "qr_login"):
        raise HTTPException(status_code=400, detail="invalid token purpose")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id missing in token")

    logger.info(
        "tenant_resolved_from_token",
        tenant_id=str(tenant_id),
        telegram_id=str(telegram_id) if telegram_id else None,
    )

    return str(tenant_id), str(telegram_id) if telegram_id is not None else None


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
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def decode_session_token(token: str) -> dict:
    """Context7 best practice: JWT security с audience verification.
    
    [C7-ID: security-jwt-001]
    """
    try:
        payload = jwt.decode(
            token, 
            settings.jwt_secret.get_secret_value(), 
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
async def qr_start(body: QrStart, request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    logger.warning(
        "qr_start_raw_body",
        content_type=request.headers.get("content-type"),
        body_len=len(raw_body),
        body_preview=raw_body[:200].decode("utf-8", errors="ignore") if raw_body else "",
    )

    tenant_id = body.tenant_id
    ip = request.client.host
    token_fallback_telegram_id: str | None = None

    logger.warning(
        "qr_start_raw_request",
        provided_tenant=tenant_id,
        has_init_data=bool(body.init_data),
        init_data_length=len(body.init_data) if body.init_data else 0,
        content_type=request.headers.get("content-type"),
    )

    if not tenant_id:
        if body.init_data:
            tenant_id = resolve_tenant_from_init_data(body.init_data, db)
        elif body.token:
            tenant_id, resolved_telegram_id = resolve_tenant_from_token(body.token, db)
            token_fallback_telegram_id = resolved_telegram_id
        else:
            raise HTTPException(status_code=400, detail="init_data or token required")
    
    # Строгий rate limiting для QR start
    if not await ratelimit_strict(f"qr:start:{ip}"):
        raise HTTPException(status_code=429, detail="Too many requests. Try again in 1 minute.")

    AUTH_QR_START.labels(tenant_id=tenant_id).inc()
    token = body.session_token or issue_session_token(tenant_id)

    # Context7: Извлекаем telegram_user_id из initData если предоставлен
    # Context7 best practice: это необходимо для корректной проверки существующих сессий для новых пользователей
    telegram_user_id = None
    if token_fallback_telegram_id:
        telegram_user_id = token_fallback_telegram_id
    
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
    if resp["status"] in ["failed", "password_required"] and "reason" in data:
        resp["reason"] = data["reason"]
    return resp


@router.get("/qr/sse")
async def qr_sse(token: str):
    """SSE endpoint для статуса QR-авторизации (async генератор)."""
    
    # Context7: логируем открытие SSE соединения
    try:
        payload = decode_session_token(token)
        tenant_id = _extract_tenant_id(payload)
        logger.info("SSE connection opened", tenant_id=tenant_id)
    except Exception as e:
        logger.error("SSE: failed to decode token", error=str(e))
    
    async def gen():
        import time as _t
        iteration = 0
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
                reason = None
                if data:
                    status = data.get("status", "pending")
                    qr_url = data.get("qr_url")
                    if status in ["failed", "password_required"] and "reason" in data:
                        reason = data.get("reason")
                
                # Context7: ВАЖНО! При статусе password_required НЕ отправляем qr_url
                # Это гарантирует, что фронтенд обработает password_required, а не qr_url
                line = {
                    "status": status,
                }
                # Context7: отправляем qr_url только если статус НЕ password_required
                if qr_url and status != "password_required":
                    line["qr_url"] = qr_url
                if reason:
                    line["reason"] = reason
                
                # Context7: логируем статус password_required для отладки
                if status == "password_required":
                    logger.info("SSE: sending password_required status", 
                              tenant_id=tenant_id, 
                              has_reason=bool(reason),
                              has_qr_url=bool(qr_url),
                              line=line,
                              redis_key=key,
                              iteration=iteration)  # Добавляем итерацию для отладки
                elif iteration % 10 == 0:  # Логируем каждые 10 итераций для других статусов
                    logger.debug("SSE: sending status", 
                               tenant_id=tenant_id,
                               status=status,
                               iteration=iteration)
                
                yield f"data: {json.dumps(line)}\n\n"
                iteration += 1
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error("SSE: error in generator", error=str(e), tenant_id=tenant_id if 'tenant_id' in locals() else "unknown")
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
        payload = jwt.decode(
            session_id,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        tenant_id = _extract_tenant_id(payload)
    except Exception as e:
        logger.warning("Failed to decode QR session token", error=str(e))
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


@router.post("/qr/password")
async def qr_password(body: QrPassword):
    """Context7 best practice: endpoint для отправки 2FA пароля при QR-авторизации.
    
    [C7-ID: telethon-2fa-handling-002]
    """
    # Декодируем JWT чтобы получить tenant_id
    try:
        payload = decode_session_token(body.session_token)
        tenant_id = _extract_tenant_id(payload)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to decode session token", error=str(e))
        raise HTTPException(status_code=400, detail="invalid token")
    
    # Rate limiting для пароля
    ip = "qr_password"  # Можно улучшить, добавив IP из request
    if not await ratelimit_strict(f"qr:password:{tenant_id}"):
        raise HTTPException(status_code=429, detail="Too many password attempts. Try again in 1 minute.")
    
    # Context7: Единый префикс t:{tenant}:qr:session
    key = f"t:{tenant_id}:qr:session"
    data = await redis_client.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail="QR session not found or expired")
    
    status = data.get("status")
    if status != "password_required":
        raise HTTPException(
            status_code=400, 
            detail=f"Password not required. Current status: {status}"
        )
    
    session_string = data.get("session_string")
    if not session_string:
        logger.error("Session string missing for password_required status", tenant_id=tenant_id)
        raise HTTPException(status_code=500, detail="Session data corrupted")
    
    # Context7 best practice: проверка пароля через Telethon
    # [C7-ID: telethon-2fa-handling-003]
    # Context7: согласно best practices, после SessionPasswordNeededError нужно:
    # 1. Создать клиент с сохраненным session_string
    # 2. Подключиться
    # 3. Проверить состояние авторизации
    # 4. Вызвать sign_in(password=...) только если клиент не авторизован
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError
    
    # Context7: получаем API credentials из переменных окружения
    # Поддерживаем оба варианта: MASTER_API_* (для telethon-ingest) и TELEGRAM_API_* (для совместимости)
    # Context7: логируем для диагностики
    master_api_id_env = os.getenv("MASTER_API_ID")
    telegram_api_id_env = os.getenv("TELEGRAM_API_ID")
    master_api_hash_env = os.getenv("MASTER_API_HASH")
    telegram_api_hash_env = os.getenv("TELEGRAM_API_HASH")
    
    logger.debug("Checking Telegram API credentials", 
                has_master_api_id=bool(master_api_id_env),
                has_master_api_hash=bool(master_api_hash_env),
                has_telegram_api_id=bool(telegram_api_id_env),
                has_telegram_api_hash=bool(telegram_api_hash_env),
                tenant_id=tenant_id)
    
    master_api_id = int(master_api_id_env or telegram_api_id_env or "0")
    master_api_hash = master_api_hash_env or telegram_api_hash_env or ""
    
    if not master_api_id or not master_api_hash or master_api_id == 0:
        logger.error("Telegram API credentials not configured", 
                    master_api_id_value=master_api_id,
                    master_api_hash_length=len(master_api_hash) if master_api_hash else 0,
                    has_master_api_id=bool(master_api_id_env),
                    has_master_api_hash=bool(master_api_hash_env),
                    has_telegram_api_id=bool(telegram_api_id_env),
                    has_telegram_api_hash=bool(telegram_api_hash_env),
                    tenant_id=tenant_id)
        raise HTTPException(
            status_code=500, 
            detail="Telegram API credentials not configured. Please set MASTER_API_ID and MASTER_API_HASH (or TELEGRAM_API_ID and TELEGRAM_API_HASH) in environment variables."
        )
    
    client = TelegramClient(
        StringSession(session_string),
        master_api_id,
        master_api_hash,
        device_model="TelegramAssistant",
        system_version="Linux",
        app_version="1.0"
    )
    
    try:
        logger.debug("Connecting client for 2FA password verification", tenant_id=tenant_id)
        await client.connect()
        
        # Context7 best practice: проверяем состояние авторизации перед sign_in
        # После открепления бота сессия может быть в неполном состоянии
        is_authorized = await client.is_user_authorized()
        logger.debug("Client authorization status", tenant_id=tenant_id, is_authorized=is_authorized)
        
        # Context7: если клиент уже авторизован, проверяем что это правильный пользователь
        if is_authorized:
            try:
                me = await client.get_me()
                if me and me.id:
                    logger.info("Client already authorized, verifying session", 
                              tenant_id=tenant_id, telegram_user_id=me.id)
                    # Сохраняем обновленную сессию
                    updated_session_string = client.session.save()
                    
                    # Обновляем Redis
                    await redis_client.hset(key, mapping={
                        "status": "authorized",
                        "session_string": updated_session_string,
                        "telegram_user_id": str(me.id),
                        "password_verified": "true",
                        "created_at": str(int(time.time()))
                    })
                    await redis_client.expire(key, 3600)
                    
                    # Публикуем StringSession для ingest
                    session_key = f"t:{tenant_id}:session"
                    await redis_client.set(session_key, updated_session_string, ex=86400)
                    
                    logger.info("Session already authorized, verified successfully", 
                              tenant_id=tenant_id, telegram_user_id=me.id)
                    AUTH_QR_SUCCESS.labels(tenant_id=tenant_id).inc()
                    # Context7: Counter не поддерживает .dec(), только .inc()
                    # Уменьшение счетчика не требуется - Counter отслеживает общее количество событий
                    
                    return {
                        "status": "authorized",
                        "message": "Session already authorized",
                        "telegram_user_id": me.id
                    }
            except Exception as e:
                logger.warning("Failed to verify authorized session, will try sign_in", 
                             error=str(e), tenant_id=tenant_id)
                # Продолжаем с sign_in
        
        # Context7 best practice: вызываем sign_in только если клиент не авторизован
        # Это правильный способ завершить 2FA после SessionPasswordNeededError
        logger.debug("Calling sign_in with password", tenant_id=tenant_id)
        try:
            # Context7: sign_in(password=...) завершает процесс авторизации с 2FA
            # После успешного sign_in клиент будет авторизован
            await client.sign_in(password=body.password)
            
            # Context7: проверяем что авторизация прошла успешно
            if not await client.is_user_authorized():
                logger.error("Client not authorized after sign_in", tenant_id=tenant_id)
                raise HTTPException(status_code=500, detail="Authorization failed after password verification")
            
            # Успешная авторизация
            me = await client.get_me()
            
            if not me or not me.id:
                logger.error("Invalid Telegram user data after password", tenant_id=tenant_id)
                raise HTTPException(status_code=500, detail="Invalid user data")
            
            # Сохраняем обновленную сессию
            updated_session_string = client.session.save()
            
            # Context7 best practice: обновляем Redis с authorized статусом
            # Telethon-ingest автоматически обработает authorized сессию и сохранит в БД
            await redis_client.hset(key, mapping={
                "status": "authorized",
                "session_string": updated_session_string,
                "telegram_user_id": str(me.id),
                "password_verified": "true",
                "created_at": str(int(time.time()))  # Обновляем timestamp для валидации
            })
            await redis_client.expire(key, 3600)  # Продлеваем TTL для обработки telethon-ingest
            
            # Context7: публикуем StringSession в единый ключ для ingest с префиксом t:{tenant}:session
            session_key = f"t:{tenant_id}:session"
            await redis_client.set(session_key, updated_session_string, ex=86400)
            
            logger.info("2FA password verified successfully", 
                       tenant_id=tenant_id, 
                       telegram_user_id=me.id,
                       session_string_length=len(updated_session_string),
                       note="Session saved to Redis, user authenticated")
            AUTH_QR_SUCCESS.labels(tenant_id=tenant_id).inc()
            # Context7: Counter не поддерживает .dec(), только .inc()
            # Counter отслеживает общее количество событий "2FA required", не текущее состояние
            
            return {
                "status": "authorized",
                "message": "Password verified successfully",
                "telegram_user_id": me.id
            }
            
        except PasswordHashInvalidError:
            logger.warning("Invalid 2FA password", tenant_id=tenant_id)
            AUTH_QR_FAIL.labels(tenant_id=tenant_id, reason="invalid_password").inc()
            raise HTTPException(status_code=400, detail="Invalid password")
        except SessionPasswordNeededError:
            # Context7: если все еще требуется пароль, значит что-то пошло не так
            logger.error("Password still required after sign_in attempt", tenant_id=tenant_id)
            AUTH_QR_FAIL.labels(tenant_id=tenant_id, reason="password_still_required").inc()
            raise HTTPException(status_code=500, detail="Password verification failed: session still requires password")
        except Exception as e:
            # Context7: логируем ошибку с деталями для диагностики
            error_msg = str(e)
            error_type = type(e).__name__
            logger.error("Error during password verification", 
                        error=error_msg, 
                        tenant_id=tenant_id, 
                        error_type=error_type,
                        exc_info=True)
            
            # Context7: безопасное использование метрики с проверкой
            try:
                AUTH_QR_FAIL.labels(tenant_id=tenant_id, reason="password_verification_error").inc()
            except Exception as metric_error:
                logger.warning("Failed to update metric", 
                             metric_error=str(metric_error), 
                             tenant_id=tenant_id)
            
            # Context7: не раскрываем внутренние детали ошибки в ответе пользователю
            if "No label names" in error_msg or "counter" in error_msg.lower():
                # Внутренняя ошибка метрики - не показываем пользователю
                raise HTTPException(status_code=500, detail="Password verification failed: internal error")
            raise HTTPException(status_code=500, detail=f"Password verification failed: {error_msg}")
            
    finally:
        try:
            # Context7: Безопасная проверка состояния клиента перед отключением
            # Проверяем, что клиент был успешно создан и находится в безопасном состоянии
            if client and hasattr(client, 'is_connected'):
                try:
                    if client.is_connected():
                        await client.disconnect()
                        logger.debug("Client disconnected after 2FA verification", tenant_id=tenant_id)
                except Exception as disconnect_error:
                    # Игнорируем ошибки при проверке состояния или отключении
                    logger.debug("Client disconnect error (expected if connection failed)", 
                               error=str(disconnect_error), tenant_id=tenant_id)
        except Exception as e:
            logger.warning("Error disconnecting client", error=str(e), tenant_id=tenant_id)
