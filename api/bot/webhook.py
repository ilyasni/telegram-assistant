from fastapi import APIRouter, Request, HTTPException
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import structlog
import json
from config import settings
import os
import time
import redis.asyncio as redis
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()
router = APIRouter()

bot: Bot | None = None
dp: Dispatcher | None = None

# One source of truth for webhook path
WEBHOOK_PATH = "/tg/bot/webhook"

# Context7 best practice: async Redis client for deduplication gate
redis_client = redis.from_url(settings.redis_url, decode_responses=True)

# Prometheus metrics
TG_WEBHOOK_REQUESTS = Counter(
    "tg_webhook_requests_total",
    "Telegram webhook requests processed",
    ["outcome"],  # ok | dedup | unauthorized | error
)
TG_UPDATES_DEDUPLICATED = Counter(
    "tg_updates_deduplicated_total",
    "Telegram updates deduplicated (update_id seen)",
)
TG_WEBHOOK_LATENCY = Histogram(
    "tg_webhook_latency_seconds",
    "Telegram webhook handling latency (seconds)",
)


def init_bot() -> None:
    global bot, dp
    token = os.getenv("TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set; bot disabled")
        return
    try:
        logger.info("Init bot", token_prefix=token[:6])
        _bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        _dp = Dispatcher()
        # Регистрация хендлеров
        try:
            from bot.handlers import router as handlers_router  # local import to avoid cycles
            _dp.include_router(handlers_router)
        except Exception as e:
            logger.error("Failed to register bot handlers", error=str(e))
        globals()['bot'], globals()['dp'] = _bot, _dp
        logger.info("Bot initialized")
    except Exception as e:
        logger.error("Failed to init bot", error=str(e))
    # Регистрация хендлеров
    


@router.post("/bot/webhook")
async def telegram_webhook(request: Request):
    start_ts = time.perf_counter()
    try:
        # Secret token validation — on mismatch return 403 and do not log body
        if settings.bot_webhook_secret:
            hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if hdr != settings.bot_webhook_secret:
                logger.warning("Webhook unauthorized", has_header=bool(hdr))
                TG_WEBHOOK_REQUESTS.labels(outcome="unauthorized").inc()
                raise HTTPException(status_code=403, detail="forbidden")

        data = await request.json()

        # Deduplication by update_id (idempotency)
        # Context7 best practice: используем async Redis операции
        update_id = data.get("update_id")
        if update_id is not None:
            dedup_key = f"tg:update:{update_id}"
            is_new = await redis_client.set(dedup_key, "1", nx=True, ex=86400)
            if not is_new:
                TG_UPDATES_DEDUPLICATED.inc()
                TG_WEBHOOK_REQUESTS.labels(outcome="dedup").inc()
                return {"ok": True}

        # Проверяем готовность бота через app.state
        app_state = getattr(request.app, "state", None)
        if not app_state or not getattr(app_state, "bot_ready", False):
            logger.error("Bot not ready")
            TG_WEBHOOK_REQUESTS.labels(outcome="error").inc()
            return JSONResponse({"ok": False, "error": "bot_not_ready"}, status_code=503)
        
        if not bot or not dp:
            init_bot()
            if not bot or not dp:
                logger.error("Bot not configured")
                TG_WEBHOOK_REQUESTS.labels(outcome="error").inc()
                return JSONResponse({"ok": False, "error": "bot_not_configured"}, status_code=503)

        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
        TG_WEBHOOK_REQUESTS.labels(outcome="ok").inc()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Webhook handler error", error=str(e))
        TG_WEBHOOK_REQUESTS.labels(outcome="error").inc()
        # Always 200 to prevent retries piling up
        return {"ok": True}
    finally:
        TG_WEBHOOK_LATENCY.observe(time.perf_counter() - start_ts)


async def ensure_webhook() -> None:
    """Установить вебхук в Telegram с секретом, если задано.

    BOT_PUBLIC_URL не должен содержать путь — путь добавляется здесь единообразно.
    """
    if not bot:
        init_bot()
    if not bot:
        logger.warning("Bot not configured; skipping webhook setup")
        return
    if not settings.bot_public_url:
        logger.warning("BOT_PUBLIC_URL not set; skipping webhook setup")
        return
    try:
        target_url = settings.bot_public_url.rstrip("/") + WEBHOOK_PATH
        info = await bot.get_webhook_info()
        if info.url != target_url:
            await bot.set_webhook(
                url=target_url,
                secret_token=settings.bot_webhook_secret,
                allowed_updates=["message", "edited_message", "callback_query", "web_app_data"],
                drop_pending_updates=False,
                max_connections=40,
            )
        logger.info("Webhook set", url=target_url)
    except Exception as e:
        logger.error("Failed to set webhook", error=str(e))


