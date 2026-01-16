from fastapi import APIRouter, Request, HTTPException
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
import structlog
import json
from config import settings
import os
import time
import asyncio
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
    namespace="api",
)
TG_UPDATES_DEDUPLICATED = Counter(
    "tg_updates_deduplicated_total",
    "Telegram updates deduplicated (update_id seen)",
    namespace="api",
)
TG_WEBHOOK_LATENCY = Histogram(
    "tg_webhook_latency_seconds",
    "Telegram webhook handling latency (seconds)",
    namespace="api",
)


def init_bot() -> None:
    """
    Инициализация бота.
    
    Context7: Инициализирует бота и dispatcher, но не устанавливает команды здесь.
    Команды устанавливаются асинхронно в ensure_webhook() после настройки webhook.
    """
    global bot, dp
    token = os.getenv("TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set; bot disabled")
        return
    try:
        logger.info("Init bot", token_prefix=token[:6])
        # Context7: Создаем бота с дефолтными настройками
        # Таймауты управляются на уровне сессии aiogram (по умолчанию ~30 секунд)
        _bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        _dp = Dispatcher()
        # Регистрация хендлеров
        # Context7: Регистрируем все routers из handler файлов для поддержки всех команд
        try:
            from bot.handlers.base import router as base_router
            _dp.include_router(base_router)
        except Exception as e:
            logger.error("Failed to register base handlers", error=str(e))
        
        try:
            from bot.handlers.themes_handlers import router as themes_router
            _dp.include_router(themes_router)
        except Exception as e:
            logger.error("Failed to register themes handlers", error=str(e))
        
        try:
            from bot.handlers.channel_handlers import router as channel_router
            _dp.include_router(channel_router)
        except Exception as e:
            logger.error("Failed to register channel handlers", error=str(e))
        
        try:
            from bot.handlers.digest_handlers import router as digest_router
            _dp.include_router(digest_router)
        except Exception as e:
            logger.error("Failed to register digest handlers", error=str(e))
        
        try:
            from bot.handlers.group_handlers import router as group_router
            _dp.include_router(group_router)
        except Exception as e:
            logger.error("Failed to register group handlers", error=str(e))
        
        try:
            from bot.handlers.trends_handlers import router as trends_router
            _dp.include_router(trends_router)
        except Exception as e:
            logger.error("Failed to register trends handlers", error=str(e))
        # Context7: Устанавливаем глобальные переменные напрямую, а не через globals()
        # Это гарантирует, что переменные будут доступны в модуле
        import bot.webhook as webhook_module
        webhook_module.bot = _bot
        webhook_module.dp = _dp
        globals()['bot'], globals()['dp'] = _bot, _dp
        logger.info("Bot initialized")
    except Exception as e:
        logger.error("Failed to init bot", error=str(e))
    


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
        
        # Context7: Детальное логирование для диагностики (используем info для видимости)
        update_type = None
        if update.message:
            update_type = "message"
            # Context7: Логируем все сообщения, не только текстовые
            logger.info(
                "Processing message update",
                update_id=update_id,
                message_text=update.message.text[:100] if update.message.text else None,
                user_id=update.message.from_user.id if update.message.from_user else None,
                has_text=bool(update.message.text)
            )
        elif update.callback_query:
            update_type = "callback_query"
            logger.info(
                "Processing callback query update",
                update_id=update_id,
                callback_data=update.callback_query.data[:100] if update.callback_query.data else None,
                user_id=update.callback_query.from_user.id if update.callback_query.from_user else None
            )
        else:
            update_type = "other"
            logger.info("Processing other update type", update_id=update_id, update_type=type(update).__name__)
        
        # Context7: Обрабатываем update с таймаутом для предотвращения зависаний
        try:
            # Context7: Таймаут обработки update - 30 секунд (разумный лимит для команд бота)
            await asyncio.wait_for(dp.feed_update(bot, update), timeout=30.0)
            TG_WEBHOOK_REQUESTS.labels(outcome="ok").inc()
            logger.info("Update processed successfully", update_id=update_id, update_type=update_type)
            return {"ok": True}
        except asyncio.TimeoutError:
            logger.error(
                "Webhook handler timeout - update processing took too long",
                update_id=update_id,
                update_type=update_type,
                timeout_seconds=30.0
            )
            TG_WEBHOOK_REQUESTS.labels(outcome="error").inc()
            # Always 200 to prevent retries piling up
            return {"ok": True}
        except Exception as feed_error:
            # Context7: Логируем ошибку обработки update отдельно
            logger.error(
                "Error feeding update to dispatcher",
                error=str(feed_error),
                error_type=type(feed_error).__name__,
                update_id=update_id,
                update_type=update_type,
                exc_info=True
            )
            TG_WEBHOOK_REQUESTS.labels(outcome="error").inc()
            # Always 200 to prevent retries piling up
            return {"ok": True}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Webhook handler error",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        TG_WEBHOOK_REQUESTS.labels(outcome="error").inc()
        # Always 200 to prevent retries piling up
        return {"ok": True}
    finally:
        TG_WEBHOOK_LATENCY.observe(time.perf_counter() - start_ts)


async def set_bot_commands() -> None:
    """
    Установить список команд бота для отображения в меню Telegram.
    
    Context7: Следует best practices aiogram для регистрации команд:
    - Использует BotCommand для структурированного описания
    - Команды группируются логически
    - Описания краткие и понятные
    - Обработка ошибок с детальным логированием
    """
    if not bot:
        logger.warning("Bot not configured; skipping commands setup")
        return
    
    try:
        commands = [
            BotCommand(command="start", description="Начать работу с ботом"),
            BotCommand(command="help", description="Показать справку по командам"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="login", description="Войти в систему"),
            BotCommand(command="add_channel", description="Добавить канал"),
            BotCommand(command="my_channels", description="Мои каналы"),
            BotCommand(command="themes", description="Подборки каналов"),
            BotCommand(command="my_themes", description="Мои подборки"),
            BotCommand(command="groups", description="Мои группы"),
            BotCommand(command="add_group", description="Добавить группу"),
            BotCommand(command="group_discovery", description="Поиск групп"),
            BotCommand(command="group_digest", description="Дайджесты по группам"),
            BotCommand(command="ask", description="Задать вопрос"),
            BotCommand(command="search", description="Поиск по каналам"),
            BotCommand(command="recommend", description="Получить рекомендации"),
            BotCommand(command="trends", description="Тренды в каналах"),
            BotCommand(command="subscription", description="Информация о подписке"),
            BotCommand(command="feedback", description="Отправить feedback"),
            BotCommand(command="remove_keyboard", description="Сбросить клавиатуру"),
            BotCommand(command="admin", description="Админ-панель"),
        ]
        
        result = await bot.set_my_commands(commands)
        if result:
            logger.info("Bot commands registered successfully", count=len(commands))
        else:
            logger.warning("Bot commands registration returned False", count=len(commands))
        
        # Context7: Проверяем, что команды действительно установлены
        try:
            registered_commands = await bot.get_my_commands()
            logger.info("Bot commands verified", registered_count=len(registered_commands))
        except Exception as verify_error:
            logger.warning("Failed to verify bot commands", error=str(verify_error))
            
    except Exception as e:
        logger.error(
            "Failed to set bot commands",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )


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
    
    # Context7: Устанавливаем команды бота независимо от результата webhook
    # Это важно, так как команды могут быть установлены даже если webhook уже настроен
    try:
        await set_bot_commands()
    except Exception as e:
        logger.error("Failed to set bot commands in ensure_webhook", error=str(e))


