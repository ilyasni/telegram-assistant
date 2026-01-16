"""Telegram bot handlers with full functionality."""

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Voice
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from bot.states import DigestStates, AddChannelStates, ChannelManagementStates, SearchStates, FeedbackStates
import html
import httpx
import structlog
import re
import io
import uuid
import jwt
import time
from typing import Optional
from datetime import datetime
from urllib.parse import urljoin, urlencode, urlparse, parse_qsl, urlunparse
from config import settings
from utils.telegram_formatter import markdown_to_telegram_chunks
from bot.utils import extract_username_from_telegram_url

logger = structlog.get_logger()
router = Router()

# API base URL
API_BASE = "http://api:8000"

# Mini App configuration
DEFAULT_MINIAPP_HOST = settings.bot_public_url.rstrip("/") if settings.bot_public_url else "https://produman.studio"
MINIAPP_ROOT_URL = urljoin(DEFAULT_MINIAPP_HOST + "/", "tg/app/")
MINIAPP_ADMIN_START_PARAM = "admin"
QR_TOKEN_AUDIENCE = "qr_webapp"


def _append_query_params(url: str, params: dict[str, str | int | None]) -> str:
    """Добавляет query-параметры к URL, сохраняя существующие значения."""
    if not params:
        return url

    filtered = {k: v for k, v in params.items() if v not in (None, "", [])}
    if not filtered:
        return url

    parsed = urlparse(url)
    existing = dict(parse_qsl(parsed.query))
    existing.update({k: str(v) for k, v in filtered.items()})
    new_query = urlencode(existing)
    return urlunparse(parsed._replace(query=new_query))


def _miniapp_entry_url(entry: str = "") -> str:
    """Возвращает URL для конкретного entry-поинта Mini App."""
    if not entry:
        return MINIAPP_ROOT_URL
    return urljoin(MINIAPP_ROOT_URL, entry)


def _generate_qr_fallback_token(tenant_id: str, telegram_id: int | None) -> str:
    """Генерирует короткоживущий JWT для fallback-аутентификации в Mini App."""
    secret = settings.jwt_secret.get_secret_value()
    session_id = str(uuid.uuid4())
    ttl_seconds = int(getattr(settings, "webapp_auth_ttl_seconds", 900) or 900)
    now_ts = int(time.time())

    payload = {
        "tenant_id": str(tenant_id),
        "session_id": session_id,
        "purpose": "qr_login",
        "aud": QR_TOKEN_AUDIENCE,
        "iat": now_ts,
        "exp": now_ts + ttl_seconds,
    }
    if telegram_id:
        payload["telegram_id"] = int(telegram_id)

    token = jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)
    return token


def _build_miniapp_url(entry: str, tenant_id: str | None, telegram_id: int | None, extra_params: Optional[dict[str, str]] = None) -> str:
    """Формирует URL Mini App с безопасным fallback-токеном."""
    base_url = _miniapp_entry_url(entry)
    params: dict[str, str | int | None] = dict(extra_params or {})

    if tenant_id:
        try:
            params["token"] = _generate_qr_fallback_token(str(tenant_id), telegram_id)
        except Exception as exc:
            logger.warning(
                "Failed to generate MiniApp fallback token",
                error=str(exc),
                tenant_id=str(tenant_id),
                telegram_id=str(telegram_id) if telegram_id is not None else None,
            )

    return _append_query_params(base_url, params)


def _resolve_qr_webapp_url(tenant_id: str | None, telegram_id: int | None, invite_code: str | None = None) -> str:
    """Возвращает URL для входа в QR Mini App с учётом инвайта и токена."""
    params: dict[str, str] = {}
    if invite_code:
        params["invite"] = invite_code
    return _build_miniapp_url("", tenant_id, telegram_id, params)


def _resolve_admin_webapp_url(tenant_id: str | None, telegram_id: int | None) -> str:
    """Возвращает URL для админского entry-поинта Mini App."""
    params = {"tgWebAppStartParam": MINIAPP_ADMIN_START_PARAM}
    return _build_miniapp_url("admin.html", tenant_id, telegram_id, params)

# Context7: Роутеры из подмодулей подключаются в webhook.py, не здесь
# Это предотвращает дублирование подключения роутеров и конфликты при инициализации
# Роутеры подключаются один раз в init_bot() в webhook.py


def _kb_login(url: Optional[str] = None):
    """Клавиатура для авторизации: открывает Mini App."""
    target_url = url or MINIAPP_ROOT_URL
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть QR аутентификацию", web_app={"url": target_url})]
    ])


def _kb_login_with_invite(invite_code: str, url: Optional[str] = None):
    """Клавиатура для авторизации с инвайт-кодом."""
    target_url = url or _resolve_qr_webapp_url(None, None, invite_code)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть QR аутентификацию", web_app={"url": target_url})]
    ])


def _kb_main_menu():
    """Главное меню бота."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Мои каналы", callback_data="menu:channels")],
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data="menu:add_channel")],
        [InlineKeyboardButton(text="📚 Подборки", callback_data="themes:list")],
        [InlineKeyboardButton(text="👥 Мои группы", callback_data="menu:groups")],
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="menu:search")],
        [InlineKeyboardButton(text="📰 Дайджесты", callback_data="digest:menu")],
        [InlineKeyboardButton(text="📈 Тренды", callback_data="trends:menu")],
        [InlineKeyboardButton(text="💎 Подписка", callback_data="menu:subscription")],
    ])


def _kb_channels_list(channels: list):
    """
    Клавиатура со списком каналов.
    
    Context7: Telegram ограничивает размер reply markup (максимум ~4096 байт).
    Ограничиваем количество кнопок до 50 для предотвращения ошибки "reply markup is too long".
    """
    builder = InlineKeyboardBuilder()
    
    # Context7: Ограничиваем количество кнопок до 50 для предотвращения ошибки "reply markup is too long"
    # Telegram имеет ограничение на размер reply markup (~4096 байт)
    MAX_BUTTONS = 50
    channels_to_show = channels[:MAX_BUTTONS]
    
    for channel in channels_to_show:
        # Context7: Обрезаем длину текста кнопки до 64 символов (лимит Telegram)
        title = channel.get('title', 'Без названия')
        if len(title) > 60:
            title = title[:57] + "..."
        builder.button(
            text=f"📺 {title}",
            callback_data=f"channel:view:{channel['id']}"
        )
    
    # Context7: Если каналов больше MAX_BUTTONS, показываем информацию об этом
    if len(channels) > MAX_BUTTONS:
        builder.button(
            text=f"📄 Показано {MAX_BUTTONS} из {len(channels)}",
            callback_data="menu:channels_info"
        )
    
    builder.button(text="➕ Добавить канал", callback_data="menu:add_channel")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def _kb_channel_actions(channel_id: str):
    """Клавиатура действий с каналом."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📰 Дайджест", callback_data=f"channel:digest:{channel_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"channel:delete:{channel_id}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"channel:refresh:{channel_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu:channels")],
    ])

def _kb_channel_digest_period(channel_id: str):
    """Клавиатура выбора периода для дайджеста."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 День", callback_data=f"channel:digest:{channel_id}:1")],
        [InlineKeyboardButton(text="📅 Неделя", callback_data=f"channel:digest:{channel_id}:7")],
        [InlineKeyboardButton(text="📅 Месяц", callback_data=f"channel:digest:{channel_id}:30")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"channel:view:{channel_id}")],
    ])


def _kb_confirm_delete(channel_id: str):
    """Клавиатура подтверждения удаления."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"channel:delete_confirm:{channel_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"channel:view:{channel_id}")],
    ])


# Команды пользователя

@router.message(Command("start"))
async def cmd_start(msg: Message):
    """Обработчик команды /start."""
    try:
        # 1) Попытка проверить/создать/обновить пользователя — но UX не блокируем
        user_payload: Optional[dict] = None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
                user_data = {
                    "telegram_id": msg.from_user.id,
                    "username": msg.from_user.username,
                    "first_name": msg.from_user.first_name,
                    "last_name": msg.from_user.last_name
                }
                if r.status_code == 404:
                    # Пользователь не найден - создаем
                    created = await client.post(f"{API_BASE}/api/users/", json=user_data)
                    if created.status_code in (200, 201):
                        user_payload = created.json()
                    else:
                        logger.warning(
                            "Failed to bootstrap user on /start",
                            user_id=msg.from_user.id,
                            status_code=created.status_code,
                            response=created.text[:200] if hasattr(created, "text") else None,
                        )
                elif r.status_code == 200:
                    # Пользователь существует - обновляем данные
                    user_payload = r.json()
                    try:
                        updated = await client.put(f"{API_BASE}/api/users/{msg.from_user.id}", json=user_data)
                        if updated.status_code == 200:
                            user_payload = updated.json()
                    except Exception as update_error:
                        logger.warning(
                            "Failed to update user on /start",
                            user_id=msg.from_user.id,
                            error=str(update_error),
                        )
                else:
                    logger.warning(
                        "Unexpected status while fetching user on /start",
                        user_id=msg.from_user.id,
                        status_code=r.status_code,
                    )
        except Exception as e:
            logger.warning("User bootstrap failed (non-blocking)", error=str(e))

        # 2) Всегда показываем приветствие и Mini App кнопку (baseline-first UX)
        tenant_id = str(user_payload.get("tenant_id")) if user_payload and user_payload.get("tenant_id") else None
        webapp_url = _resolve_qr_webapp_url(tenant_id, msg.from_user.id)
        await msg.answer(
            "Откройте Telegram в браузере и нажмите кнопку ниже для отображения QR-кода и подключения к боту.",
            reply_markup=_kb_login(webapp_url)
        )
        
    except Exception as e:
        logger.error("Error in cmd_start (fallback path)", error=str(e))
        # Даже при ошибке показываем Mini App, чтобы не блокировать вход
        fallback_url = _resolve_qr_webapp_url(None, msg.from_user.id)
        await msg.answer(
            "Откройте Telegram в браузере и нажмите кнопку ниже для отображения QR-кода и подключения к боту.",
            reply_markup=_kb_login(fallback_url)
        )


@router.message(Command("help"))
async def cmd_help(msg: Message):
    """
    Обработчик команды /help с описанием всех доступных функций.
    
    Context7: Следует best practices aiogram для команды help:
    - Структурированное форматирование с эмодзи
    - Группировка команд по категориям
    - Примеры использования
    - Информация о дополнительных возможностях
    """
    try:
        # Context7: Детальное логирование для диагностики
        logger.info(
            "Help command received",
            user_id=msg.from_user.id,
            username=msg.from_user.username
        )
        
        help_text = """🤖 <b>Помощь по командам бота</b>

<b>🚀 Основные команды</b>
/start — Начать работу с ботом
/help — Показать эту справку

<b>📺 Управление каналами</b>
/add_channel @channel_name — Добавить канал для отслеживания
Пример: <code>/add_channel @durov</code> или <code>/add_channel https://t.me/durov</code>

/my_channels — Показать список ваших подписанных каналов

<b>📚 Подборки каналов</b>
/themes — Показать доступные подборки каналов
/my_themes — Показать подключенные подборки

<b>🔍 Поиск и вопросы</b>
/ask <i>ваш вопрос</i> — Задать вопрос ассистенту
Пример: <code>/ask Что нового в AI?</code>

<b>📈 Тренды</b>
/trends — Показать тренды в каналах

<b>👥 Группы</b>
/groups — Показать подключённые группы
/add_group @group_name — Добавить группу по username или ссылке
Пример: <code>/add_group @SergeXXI</code> или <code>/add_group https://t.me/SergeXXI</code>
/group_discovery — Найти доступные чаты и подключить новые

<b>💬 Текстовые и голосовые сообщения</b>
Вы можете просто написать вопрос текстом — бот автоматически обработает запрос через RAG.

Также поддерживаются голосовые сообщения — бот распознает речь и ответит на ваш вопрос.

<b>💎 Подписка</b>
/subscription — Информация о вашей подписке и лимитах

<b>💬 Feedback</b>
/feedback — Отправить комментарий, предложение или пожелание
Пример: <code>/feedback</code> — затем введите ваш текст

<b>💡 Советы</b>
• Задавайте вопросы естественным языком
• Используйте голосовые сообщения для быстрого ввода
• Команды работают без аргументов — просто отправьте текст
• Результаты поиска включают ссылки на источники

<b>📝 Примечание</b>
Для входа в систему используйте Mini App через кнопку внизу (команда /login временно отключена)."""
        
        await msg.answer(
            help_text,
            parse_mode="HTML",
            reply_markup=_kb_login(_resolve_qr_webapp_url(None, msg.from_user.id))
        )
        logger.info("Help sent successfully", user_id=msg.from_user.id)
        
    except Exception as e:
        logger.error(
            "Error in /help command",
            error=str(e),
            error_type=type(e).__name__,
            user_id=msg.from_user.id,
            exc_info=True
        )
        try:
            await msg.answer("❌ Произошла ошибка при отображении справки. Попробуйте позже.")
        except Exception as e2:
            logger.error(
                "Failed to send error message to user",
                error=str(e2),
                user_id=msg.from_user.id
            )


@router.message(Command("menu"))
async def cmd_menu(msg: Message):
    """Обработчик команды /menu — показывает главное меню."""
    try:
        # Context7: Детальное логирование для диагностики
        logger.info(
            "Menu command received",
            user_id=msg.from_user.id,
            username=msg.from_user.username,
            chat_id=msg.chat.id
        )
        
        # Создаем клавиатуру
        keyboard = _kb_main_menu()
        logger.debug("Main menu keyboard created", user_id=msg.from_user.id)
        
        # Context7: Отправляем сообщение с обработкой ошибок
        try:
            # Отправляем сообщение
            await msg.answer(
                "🤖 <b>Главное меню</b>\n\n"
                "Выберите действие:",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            logger.info("Menu sent successfully", user_id=msg.from_user.id)
            return
        except Exception as send_error:
            # Context7: Если не удалось отправить, пробуем еще раз без клавиатуры
            logger.warning(
                "Failed to send menu with keyboard, retrying without keyboard",
                error=str(send_error),
                error_type=type(send_error).__name__,
                user_id=msg.from_user.id
            )
            try:
                # Отправляем основное сообщение
                await msg.answer(
                    "🤖 <b>Главное меню</b>\n\n"
                    "Выберите действие:",
                    parse_mode="HTML"
                )
                # Context7: Пытаемся отправить клавиатуру отдельно, но не критично если не получится
                try:
                    await msg.answer(
                        "Выберите действие:",
                        reply_markup=keyboard
                    )
                except Exception as keyboard_error:
                    # Context7: Если не удалось отправить клавиатуру - не критично, основное сообщение уже отправлено
                    logger.warning(
                        "Failed to send keyboard separately, but main message sent",
                        error=str(keyboard_error),
                        user_id=msg.from_user.id
                    )
                logger.info("Menu sent in parts successfully", user_id=msg.from_user.id)
                return
            except Exception as retry_error:
                logger.error(
                    "Failed to send menu even without keyboard",
                    error=str(retry_error),
                    user_id=msg.from_user.id
                )
                # Context7: Пробрасываем исключение только если все попытки не удались
                raise
        
    except Exception as e:
        # Context7: Детальное логирование ошибки
        logger.error(
            "Error in /menu command",
            error=str(e),
            error_type=type(e).__name__,
            user_id=msg.from_user.id,
            username=msg.from_user.username,
            exc_info=True
        )
        try:
            await msg.answer("❌ Произошла ошибка при открытии меню. Попробуйте позже.")
        except Exception as e2:
            logger.error(
                "Failed to send error message to user",
                error=str(e2),
                user_id=msg.from_user.id
            )


@router.message(Command("remove_keyboard"))
async def cmd_remove_keyboard(msg: Message):
    """
    Сброс клавиатуры ответа (Reply Keyboard) у пользователя.
    
    Удаляет кастомную клавиатуру и возвращает стандартную клавиатуру Telegram.
    """
    from aiogram.types import ReplyKeyboardRemove
    
    await msg.answer(
        "⌨️ <b>Клавиатура сброшена</b>\n\n"
        "Стандартная клавиатура восстановлена.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(remove_keyboard=True)
    )


@router.message(Command("login"))
async def cmd_login_disabled(msg: Message):
    """Временная заглушка для /login."""
    await msg.answer(
        "🔧 <b>Команда /login временно недоступна</b>\n\n"
        "Используй /start — там есть актуальная кнопка Mini App.",
        reply_markup=_kb_login(_resolve_qr_webapp_url(None, msg.from_user.id))
    )


@router.message(Command("my_channels"))
async def cmd_my_channels(msg: Message):
    """Обработчик команды /my_channels."""
    await _show_channels(msg)


@router.message(Command("ask"))
async def cmd_ask(msg: Message):
    """Обработчик команды /ask для RAG поиска."""
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Использование: <code>/ask ваш вопрос</code>\n"
            "Пример: <code>/ask Что нового в AI?</code>"
        )
        return
    
    question = args[1]
    await _rag_query(msg, question)


@router.message(Command("search"))
async def cmd_search(msg: Message):
    """Обработчик команды /search."""
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Использование: <code>/search запрос</code>\n"
            "Пример: <code>/search машинное обучение</code>"
        )
        return
    
    query = args[1]
    await _rag_query(msg, query, intent_override="search")


@router.message(Command("recommend"))
async def cmd_recommend(msg: Message):
    """Обработчик команды /recommend."""
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Использование: <code>/recommend запрос</code>\n"
            "Пример: <code>/recommend интересные новости про AI</code>"
        )
        return
    
    query = args[1]
    await _rag_query(msg, query, intent_override="recommend")




@router.message(Command("subscription"))
async def cmd_subscription(msg: Message):
    """Обработчик команды /subscription."""
    await _show_subscription(msg)


@router.message(Command("feedback"))
async def cmd_feedback(msg: Message, state: FSMContext):
    """Обработчик команды /feedback для отправки комментариев и пожеланий."""
    await state.set_state(FeedbackStates.waiting_message)
    await msg.answer(
        "💬 <b>Отправка feedback</b>\n\n"
        "Пожалуйста, опишите ваше предложение, комментарий или пожелание.\n\n"
        "Мы ценим ваше мнение и обязательно рассмотрим ваше сообщение.\n\n"
        "Используйте /cancel для отмены.",
        parse_mode="HTML"
    )


@router.message(FeedbackStates.waiting_message)
async def process_feedback_message(msg: Message, state: FSMContext):
    """Обработка ввода текста feedback."""
    # Проверка на команду отмены
    if msg.text and msg.text.startswith("/cancel"):
        await state.clear()
        await msg.answer("❌ Отправка feedback отменена.")
        return
    
    # Валидация длины сообщения
    if len(msg.text.strip()) < 3:
        await msg.answer(
            "❌ <b>Слишком короткое сообщение</b>\n\n"
            "Пожалуйста, опишите ваше предложение более подробно (минимум 3 символа)."
        )
        return
    
    if len(msg.text.strip()) > 5000:
        await msg.answer(
            f"❌ <b>Слишком длинное сообщение</b>\n\n"
            f"Максимальная длина feedback: 5000 символов.\n"
            f"Ваше сообщение: {len(msg.text)} символов."
        )
        return
    
    try:
        # Получаем пользователя
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start для регистрации.")
                await state.clear()
                return
            r.raise_for_status()
            user = r.json()
        
        # Создаем feedback через API
        feedback_data = {
            "message": msg.text.strip(),
            "user_id": str(user['id'])
        }
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{API_BASE}/api/feedback/", json=feedback_data)
            r.raise_for_status()
            feedback = r.json()
        
        await msg.answer(
            "✅ <b>Feedback отправлен!</b>\n\n"
            "Спасибо за ваше сообщение. Мы рассмотрим его в ближайшее время.\n\n"
            f"ID: <code>{feedback['id']}</code>\n"
            f"Статус: {feedback['status']}",
            parse_mode="HTML"
        )
        
        logger.info(
            "Feedback created via bot",
            feedback_id=str(feedback['id']),
            user_id=str(user['id']),
            telegram_id=msg.from_user.id,
            message_length=len(msg.text.strip())
        )
        
        await state.clear()
        
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error creating feedback",
            status_code=e.response.status_code,
            response_text=e.response.text[:200] if hasattr(e.response, 'text') else None
        )
        await msg.answer("❌ <b>Ошибка отправки feedback</b>\n\nПопробуйте позже.")
        await state.clear()
    except Exception as e:
        logger.error("Error creating feedback", error=str(e))
        await msg.answer("❌ <b>Произошла ошибка</b>\n\nПопробуйте позже.")
        await state.clear()


@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    """Обработчик команды /admin для доступа к админ-панели."""
    try:
        # Проверяем, является ли пользователь админом
        async with httpx.AsyncClient(timeout=5) as client:
            # Получаем пользователя
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer(
                    "❌ <b>Пользователь не найден</b>\n\n"
                    "Используйте /start для регистрации."
                )
                return
            
            if r.status_code != 200:
                # Context7: Детальное логирование для диагностики
                logger.error(
                    "Failed to get user for admin check",
                    telegram_id=msg.from_user.id,
                    status_code=r.status_code,
                    response_text=r.text[:200] if hasattr(r, 'text') else str(r.content[:200])
                )
                await msg.answer(
                    f"❌ <b>Ошибка проверки прав доступа</b>\n\n"
                    f"Статус: {r.status_code}\n"
                    f"Попробуйте позже или обратитесь к администратору."
                )
                return
            
            user = r.json()
            
            # Проверяем роль админа
            user_role = user.get('role', 'user')
            is_admin = user_role == 'admin'
            tenant_id_value = str(user.get('tenant_id')) if user.get('tenant_id') else None
            webapp_url = _resolve_admin_webapp_url(tenant_id_value, msg.from_user.id)
            
            # Context7: Логирование для отладки без раскрытия токена
            logger.info(
                "Admin panel access requested",
                telegram_id=msg.from_user.id,
                user_role=user_role,
                is_admin=is_admin,
                has_token=bool(tenant_id_value)
            )
            
            if not is_admin:
                await msg.answer(
                    "❌ <b>Доступ запрещён</b>\n\n"
                    "Только администраторы могут использовать админ-панель."
                )
                return
            
            await msg.answer(
                "👑 <b>Админ-панель</b>\n\n"
                "Откройте Mini App для доступа к админ-панели.\n"
                "Доступ будет предоставлен только администраторам.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👑 Открыть админ-панель", web_app={"url": webapp_url})]
                ])
            )
            
    except Exception as e:
        logger.error("Error in cmd_admin", error=str(e))
        await msg.answer(
            "❌ <b>Ошибка</b>\n\n"
            "Произошла ошибка при открытии админ-панели.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="admin:retry")]
            ])
        )


# Callback handlers

@router.callback_query(F.data == "qr:start")
async def on_qr_start(cb: CallbackQuery):
    """Фолбэк: предлагаем открыть Mini App (QR живёт в Mini App)."""
    webapp_url = _resolve_qr_webapp_url(None, cb.from_user.id if cb.from_user else None)
    await cb.message.answer(
        "Откройте Mini App для сканирования QR-кода.",
        reply_markup=_kb_login(webapp_url)
    )
    await cb.answer()


@router.callback_query(F.data == "login:retry")
async def on_login_retry(cb: CallbackQuery):
    """Фолбэк: повторная попытка входа."""
    webapp_url = _resolve_qr_webapp_url(None, cb.from_user.id if cb.from_user else None)
    await cb.message.edit_text(
        "🔐 <b>Вход в систему</b>\n\n"
        "Для входа используйте команду:\n"
        "<code>/login INVITE_CODE</code>\n\n"
        "Или нажмите кнопку ниже для входа через Mini App:",
        reply_markup=_kb_login(webapp_url)
    )
    await cb.answer()


@router.callback_query(F.data == "admin:retry")
async def on_admin_retry(cb: CallbackQuery):
    """Фолбэк: повторная попытка открытия админ-панели."""
    webapp_url = _resolve_admin_webapp_url(None, cb.from_user.id)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 200:
                user = r.json()
                tenant_id_value = str(user.get('tenant_id')) if user.get('tenant_id') else None
                webapp_url = _resolve_admin_webapp_url(tenant_id_value, cb.from_user.id)
    except Exception as error:
        logger.warning(
            "Failed to resolve admin token on retry",
            error=str(error),
            user_id=cb.from_user.id,
        )

    await cb.message.edit_text(
        "👑 <b>Админ-панель</b>\n\n"
        "Откройте Mini App для доступа к админ-панели.\n"
        "Доступ будет предоставлен только администраторам.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Открыть админ-панель", web_app={"url": webapp_url})]
        ])
    )
    await cb.answer()


@router.callback_query(F.data == "menu:main")
async def on_menu_main(cb: CallbackQuery):
    """Обработчик главного меню."""
    await cb.message.edit_text(
        "🤖 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=_kb_main_menu()
    )
    await cb.answer()


@router.callback_query(F.data == "menu:channels")
async def on_menu_channels(cb: CallbackQuery):
    """Обработчик меню каналов."""
    await _show_channels_callback(cb)


@router.callback_query(F.data == "menu:add_channel")
async def on_menu_add_channel(cb: CallbackQuery):
    """Обработчик добавления канала."""
    await cb.message.edit_text(
        "➕ <b>Добавить канал</b>\n\n"
        "Отправьте команду:\n"
        "<code>/add_channel @channel_name</code>\n\n"
        "Пример: <code>/add_channel @durov</code>"
    )
    await cb.answer()


@router.callback_query(F.data == "menu:search")
async def on_menu_search(cb: CallbackQuery):
    """Обработчик поиска."""
    await cb.message.edit_text(
        "🔍 <b>Поиск</b>\n\n"
        "Отправьте команду:\n"
        "<code>/ask ваш вопрос</code>\n\n"
        "Пример: <code>/ask Что нового в AI?</code>"
    )
    await cb.answer()


@router.callback_query(F.data == "menu:subscription")
async def on_menu_subscription(cb: CallbackQuery):
    """Обработчик подписки."""
    await _show_subscription_callback(cb)


@router.callback_query(F.data.startswith("channel:view:"))
async def on_channel_view(cb: CallbackQuery):
    """Обработчик просмотра канала."""
    channel_id = cb.data.split(":")[2]
    await _show_channel_details(cb, channel_id)


@router.callback_query(F.data.startswith("channel:delete:"))
async def on_channel_delete(cb: CallbackQuery):
    """Обработчик удаления канала."""
    channel_id = cb.data.split(":")[2]
    await cb.message.edit_text(
        "🗑 <b>Удаление канала</b>\n\n"
        "Вы уверены, что хотите удалить этот канал?",
        reply_markup=_kb_confirm_delete(channel_id)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("channel:delete_confirm:"))
async def on_channel_delete_confirm(cb: CallbackQuery):
    """Обработчик подтверждения удаления канала."""
    channel_id = cb.data.split(":")[2]
    await _delete_channel_callback(cb, channel_id)


@router.callback_query(F.data.startswith("channel:digest:"))
async def on_channel_digest(cb: CallbackQuery):
    """Обработчик запроса дайджеста по каналу."""
    parts = cb.data.split(":")
    channel_id = parts[2]
    
    # Context7: Логируем параметры для диагностики
    logger.info(
        "Channel digest callback received",
        callback_data=cb.data,
        channel_id=channel_id,
        parts_count=len(parts),
        user_id=cb.from_user.id
    )
    
    # Если период не указан, показываем выбор периода
    if len(parts) == 3:
        await cb.message.edit_text(
            "📰 <b>Дайджест по каналу</b>\n\n"
            "Выберите период для генерации дайджеста:",
            reply_markup=_kb_channel_digest_period(channel_id)
        )
        await cb.answer()
        return
    
    # Период указан, генерируем дайджест
    period = int(parts[3])
    await _generate_channel_digest(cb, channel_id, period)


# Helper functions

async def _add_channel(msg: Message, channel_name: str):
    """Добавить канал."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Добавить канал
        # Context7: telegram_id будет получен автоматически через API при подписке
        # Если username указан, API сам найдет telegram_id через Telegram API
        channel_data = {
            "username": channel_name[1:],  # Убираем @
            "title": channel_name,
            "settings": {}
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{API_BASE}/api/channels/users/{user['id']}/subscribe", json=channel_data)
            r.raise_for_status()
            channel = r.json()
        
        await msg.answer(
            f"✅ <b>Канал добавлен</b>\n\n"
            f"📺 {channel['title']}\n"
            f"🆔 ID: {channel['id']}\n"
            f"📅 Добавлен: {channel['created_at'][:10]}"
        )
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            await msg.answer("❌ Канал уже добавлен")
        else:
            await msg.answer("❌ Ошибка добавления канала")
    except Exception as e:
        logger.error("Error adding channel", error=str(e))
        await msg.answer("❌ Произошла ошибка")


async def _show_channels(msg: Message):
    """Показать каналы пользователя."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить каналы
        url = f"{API_BASE}/api/channels/users/{user['id']}/list"
        logger.info(f"[BOT] CALL {url}")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
            logger.info(f"[BOT] RESPONSE {r.status_code} for {url}")
            r.raise_for_status()
            channels_data = r.json()
            channels = channels_data.get('channels', [])
        
        if not channels:
            await msg.answer(
                "📺 <b>Мои каналы</b>\n\n"
                "У вас пока нет добавленных каналов.\n"
                "Используйте /add_channel для добавления.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Добавить канал", callback_data="menu:add_channel")],
                    [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")]
                ])
            )
            return
        
        text = "📺 <b>Мои каналы</b>\n\n"
        # Context7: Ограничиваем количество каналов в тексте для предотвращения превышения лимита Telegram (4096 символов)
        MAX_CHANNELS_IN_TEXT = 50
        channels_to_show = channels[:MAX_CHANNELS_IN_TEXT]
        for channel in channels_to_show:
            status = "🟢" if channel['is_active'] else "🔴"
            text += f"{status} {channel['title']}\n"
        
        if len(channels) > MAX_CHANNELS_IN_TEXT:
            text += f"\n... и еще {len(channels) - MAX_CHANNELS_IN_TEXT} каналов"
        
        await msg.answer(
            text,
            reply_markup=_kb_channels_list(channels)
        )
        
    except Exception as e:
        logger.error("Error showing channels", error=str(e))
        await msg.answer("❌ Произошла ошибка")


async def _show_channels_callback(cb: CallbackQuery):
    """Показать каналы через callback."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 404:
                await cb.message.edit_text("❌ Пользователь не найден")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить каналы
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/channels/users/{user['id']}/list")
            r.raise_for_status()
            channels_data = r.json()
            channels = channels_data.get('channels', [])
        
        if not channels:
            await cb.message.edit_text(
                "📺 <b>Мои каналы</b>\n\n"
                "У вас пока нет добавленных каналов.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Добавить канал", callback_data="menu:add_channel")],
                    [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")]
                ])
            )
            return
        
        text = "📺 <b>Мои каналы</b>\n\n"
        # Context7: Ограничиваем количество каналов в тексте для предотвращения превышения лимита Telegram (4096 символов)
        MAX_CHANNELS_IN_TEXT = 50
        channels_to_show = channels[:MAX_CHANNELS_IN_TEXT]
        for channel in channels_to_show:
            status = "🟢" if channel['is_active'] else "🔴"
            text += f"{status} {channel['title']}\n"
        
        if len(channels) > MAX_CHANNELS_IN_TEXT:
            text += f"\n... и еще {len(channels) - MAX_CHANNELS_IN_TEXT} каналов"
        
        await cb.message.edit_text(
            text,
            reply_markup=_kb_channels_list(channels)
        )
        
    except Exception as e:
        logger.error("Error showing channels callback", error=str(e))
        await cb.message.edit_text("❌ Произошла ошибка")


async def _show_channel_details(cb: CallbackQuery, channel_id: str):
    """Показать детали канала."""
    await cb.message.edit_text(
        f"📺 <b>Канал #{channel_id}</b>\n\n"
        "Детали канала пока в разработке.",
        reply_markup=_kb_channel_actions(channel_id)
    )
    await cb.answer()


async def _delete_channel_callback(cb: CallbackQuery, channel_id: str):
    """Удалить канал через callback."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 404:
                await cb.message.edit_text("❌ Пользователь не найден")
                await cb.answer("Пользователь не найден", show_alert=True)
                return
            r.raise_for_status()
            user = r.json()
        
        # Удалить канал
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.delete(f"{API_BASE}/api/channels/users/{user['id']}/unsubscribe/{channel_id}")
            r.raise_for_status()
        
        await cb.message.edit_text("✅ Канал удален")
        await cb.answer("Канал удален")
        
    except httpx.HTTPStatusError as e:
        # Обработка HTTP ошибок с деталями
        error_detail = None
        try:
            if e.response.headers.get("content-type", "").startswith("application/json"):
                error_data = e.response.json()
                if isinstance(error_data, dict):
                    error_detail = error_data.get("detail")
        except Exception:
            pass
        
        logger.error(
            "HTTP error deleting channel",
            status_code=e.response.status_code,
            response_text=e.response.text[:200],
            error_detail=error_detail,
            channel_id=channel_id,
            user_id=cb.from_user.id
        )
        
        # Специфичные сообщения для разных статусов
        if e.response.status_code == 404:
            await cb.message.edit_text("❌ Канал не найден или уже удален")
            await cb.answer("Канал не найден", show_alert=True)
        elif e.response.status_code == 403:
            await cb.message.edit_text("❌ Нет доступа к удалению этого канала")
            await cb.answer("Нет доступа", show_alert=True)
        elif e.response.status_code >= 500:
            await cb.message.edit_text("❌ Ошибка сервера при удалении канала\n\nПопробуйте позже.")
            await cb.answer("Ошибка сервера", show_alert=True)
        else:
            await cb.message.edit_text(f"❌ Ошибка удаления канала (код: {e.response.status_code})")
            await cb.answer("Ошибка удаления", show_alert=True)
            
    except httpx.RequestError as e:
        # Обработка сетевых ошибок (таймауты, соединение и т.д.)
        logger.error(
            "Network error deleting channel",
            error=str(e),
            error_type=type(e).__name__,
            channel_id=channel_id,
            user_id=cb.from_user.id
        )
        await cb.message.edit_text("❌ Ошибка сети при удалении канала\n\nПроверьте подключение и попробуйте позже.")
        await cb.answer("Ошибка сети", show_alert=True)
        
    except Exception as e:
        # Обработка прочих ошибок
        logger.error(
            "Unexpected error deleting channel",
            error=str(e),
            error_type=type(e).__name__,
            channel_id=channel_id,
            user_id=cb.from_user.id
        )
        await cb.message.edit_text("❌ Неожиданная ошибка при удалении канала\n\nПопробуйте позже.")
        await cb.answer("Ошибка", show_alert=True)


async def _generate_channel_digest(cb: CallbackQuery, channel_id: str, period: int):
    """Генерация дайджеста по каналу."""
    try:
        # Показываем индикатор загрузки
        period_names = {1: "день", 7: "неделю", 30: "месяц"}
        period_name = period_names.get(period, f"{period} дней")
        
        if period >= 30:
            await cb.message.edit_text(
                f"📰 <b>Готовлю дайджест за {period_name}...</b>\n\n"
                "Это может занять минуту. Пожалуйста, подождите...",
                reply_markup=None
            )
        else:
            await cb.message.edit_text(
                f"📰 <b>Готовлю дайджест за {period_name}...</b>\n\n"
                "Пожалуйста, подождите...",
                reply_markup=None
            )
        
        await cb.answer()
        
        # Получаем пользователя
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 404:
                await cb.message.edit_text("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Context7: Логируем параметры перед вызовом API
        logger.info(
            "Channel digest API call",
            user_id=user['id'],
            user_id_type=type(user['id']).__name__,
            channel_id=channel_id,
            channel_id_type=type(channel_id).__name__,
            period=period
        )
        
        # Вызываем API для генерации дайджеста
        # Context7: Правильный путь endpoint'а: /api/channels/users/{user_id}/channels/{channel_id}/digest
        url = f"{API_BASE}/api/channels/users/{user['id']}/channels/{channel_id}/digest?period={period}"
        logger.info("Channel digest API URL", url=url)
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url)
            logger.info(f"[BOT] RESPONSE {r.status_code} for {url}")
            
            if r.status_code == 200:
                data = r.json()
                
                if data.get('status') == 'processing':
                    # Async операция (для месяца в будущем)
                    job_id = data.get('job_id')
                    await cb.message.edit_text(
                        f"⏳ <b>Дайджест генерируется</b>\n\n"
                        f"Job ID: {job_id}\n"
                        "Попробуйте запросить позже."
                    )
                else:
                    # Синхронный ответ
                    content = data.get('content', '')
                    posts_count = data.get('posts_count', 0)
                    
                    if not content:
                        await cb.message.edit_text(
                            f"📰 <b>Дайджест за {period_name}</b>\n\n"
                            "Не найдено постов за выбранный период."
                        )
                        return
                    
                    # Разбиваем длинный контент на части для Telegram
                    chunks = markdown_to_telegram_chunks(content, limit=4096)
                    
                    # Отправляем первую часть (заменяем сообщение "Готовлю дайджест...")
                    await cb.message.edit_text(chunks[0], parse_mode="HTML")
                    
                    # Отправляем остальные части
                    for chunk in chunks[1:]:
                        await cb.message.answer(chunk, parse_mode="HTML")
            elif r.status_code == 404:
                # Context7: Детальное логирование для диагностики
                logger.warning(
                    "Channel digest 404 - access denied",
                    user_id=str(user['id']),
                    channel_id=channel_id,
                    period=period,
                    url=url
                )
                await cb.message.edit_text(
                    "❌ Канал не найден или нет доступа.\n\n"
                    "Проверьте, что канал добавлен в ваши подписки.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📊 Мои каналы", callback_data="menu:channels")],
                        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")]
                    ])
                )
            elif r.status_code == 400:
                error_detail = r.json().get('detail', 'Неверный запрос')
                await cb.message.edit_text(f"❌ {error_detail}")
            else:
                await cb.message.edit_text(
                    "❌ Ошибка генерации дайджеста.\n\n"
                    "Попробуйте позже."
                )
    
    except httpx.TimeoutException:
        await cb.message.edit_text(
            f"⏱️ <b>Таймаут</b>\n\n"
            f"Генерация дайджеста за {period_name} заняла слишком много времени.\n"
            "Попробуйте выбрать меньший период или повторите позже."
        )
    except httpx.HTTPStatusError as e:
        error_detail = None
        try:
            if e.response.headers.get("content-type", "").startswith("application/json"):
                error_data = e.response.json()
                if isinstance(error_data, dict):
                    error_detail = error_data.get("detail")
        except Exception:
            pass
        
        logger.error(
            "HTTP error generating channel digest",
            status_code=e.response.status_code,
            error_detail=error_detail,
            channel_id=channel_id,
            period=period,
            user_id=cb.from_user.id
        )
        
        if e.response.status_code == 404:
            # Context7: Детальное логирование для диагностики
            logger.warning(
                "Channel view 404 - access denied",
                user_id=cb.from_user.id,
                channel_id=channel_id
            )
            await cb.message.edit_text(
                "❌ Канал не найден или нет доступа.\n\n"
                "Проверьте, что канал добавлен в ваши подписки.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Мои каналы", callback_data="menu:channels")],
                    [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")]
                ])
            )
        elif e.response.status_code >= 500:
            await cb.message.edit_text("❌ Ошибка сервера при генерации дайджеста\n\nПопробуйте позже.")
        else:
            await cb.message.edit_text(f"❌ Ошибка: {error_detail or 'Неизвестная ошибка'}")
    except Exception as e:
        logger.error(
            "Error generating channel digest",
            error=str(e),
            error_type=type(e).__name__,
            channel_id=channel_id,
            period=period,
            user_id=cb.from_user.id
        )
        await cb.message.edit_text("❌ Произошла ошибка при генерации дайджеста\n\nПопробуйте позже.")


async def _rag_query(msg: Message, question: str, intent_override: Optional[str] = None, voice_transcription: bool = False, audio_file_id: Optional[str] = None):
    """
    Выполнить RAG запрос через API.
    
    Args:
        msg: Telegram сообщение
        question: Текст вопроса
        intent_override: Принудительное намерение (опционально, для команд)
        voice_transcription: Флаг, что запрос пришел из голосового сообщения
    """
    try:
        # Показываем индикатор загрузки
        loading_msg = await msg.answer("🔍 <b>Обрабатываю запрос...</b>")
        
        # Получить пользователя
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await loading_msg.edit_text("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Выполнить RAG запрос через API
        query_data = {
            "query": question,
            "user_id": user['id']
        }
        
        # Добавляем intent_override если указан
        if intent_override:
            query_data["intent_override"] = intent_override
        
        # Добавляем данные о транскрибации если есть
        if voice_transcription:
            transcription_text = question  # question уже содержит транскрибированный текст
            query_data["audio_file_id"] = audio_file_id
            query_data["transcription_text"] = transcription_text
        
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{API_BASE}/api/rag/query", json=query_data)
            r.raise_for_status()
            result = r.json()
        
        # Проверяем наличие обязательных полей в ответе
        if 'result' not in result:
            logger.error(
                "Missing 'result' field in RAG response",
                response_keys=list(result.keys()),
                user_id=msg.from_user.id
            )
            await loading_msg.edit_text("❌ <b>Ошибка формата ответа</b>\n\nПопробуйте позже.")
            return
        
        if 'answer' not in result['result']:
            logger.error(
                "Missing 'answer' field in RAG response",
                result_keys=list(result['result'].keys()),
                user_id=msg.from_user.id
            )
            await loading_msg.edit_text("❌ <b>Ошибка формата ответа</b>\n\nПопробуйте позже.")
            return
        
        answer = result['result']['answer']
        sources = result['result'].get('sources', [])
        intent = result['result'].get('intent', 'ask')
        confidence = result['result'].get('confidence', 0.0)
        
        # Форматируем ответ в зависимости от намерения
        intent_emoji = {
            "ask": "🤖",
            "search": "🔍",
            "recommend": "🎯",
            "trend": "📈",
            "digest": "📰"
        }
        intent_labels = {
            "ask": "Ответ",
            "search": "Результаты поиска",
            "recommend": "Рекомендации",
            "trend": "Тренды",
            "digest": "Дайджест"
        }
        emoji = intent_emoji.get(intent, "🤖")
        label = intent_labels.get(intent, "Результат")
        
        # Конвертируем markdown ответ в Telegram HTML и разбиваем на чанки
        # Context7: Ссылки уже включены inline в ответ через промпты LLM
        answer_chunks = markdown_to_telegram_chunks(answer)
        answer_has_sources_section = "источ" in answer.lower() or "source" in answer.lower()
        
        # Context7: Улучшенное форматирование предупреждения о низкой уверенности
        confidence_text = ""
        if confidence < 0.5:
            confidence_text = "\n\n━━━━━━━━━━\n⚠️ <i>Уверенность в ответе низкая. Попробуйте уточнить запрос.</i>"
        
        def _shorten_source_snippet(value: Optional[str], limit: int = 160) -> str:
            if not value:
                return ""
            normalized = value.replace("\n", " ").strip()
            if len(normalized) <= limit:
                return normalized
            return normalized[:limit].rstrip() + "…"
        
        formatted_sources = []
        for source in sources[:5]:
            title = source.get("channel_title") or "Источник"
            safe_title = html.escape(title)
            snippet_preview = _shorten_source_snippet(source.get("content"))
            safe_preview = html.escape(snippet_preview) if snippet_preview else ""
            permalink = source.get("permalink")
            if permalink:
                entry = f"• <a href=\"{permalink}\">{safe_title}</a>"
            else:
                entry = f"• {safe_title}"
            if safe_preview:
                entry = f"{entry} — {safe_preview}"
            formatted_sources.append(entry)
        
        sources_block = ""
        if formatted_sources and not answer_has_sources_section:
            sources_block = "\n\n<b>Источники</b>\n" + "\n".join(formatted_sources)
        
        # Отправляем чанки с улучшенным форматированием
        for idx, chunk in enumerate(answer_chunks):
            is_last = idx == len(answer_chunks) - 1
            
            # Context7: Улучшенная структура заголовка для читабельности
            if idx == 0:
                # Первый чанк - с заголовком
                text = f"{emoji} <b>{label}</b>\n\n{chunk}"
            else:
                # Остальные чанки - без заголовка, только контент
                text = chunk
            
            # Добавляем предупреждение только в последний чанк
            if is_last:
                text += confidence_text + sources_block
            
            if idx == 0:
                # Первый чанк - редактируем сообщение загрузки
                await loading_msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
            else:
                # Остальные чанки - новые сообщения
                await msg.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        
    except httpx.TimeoutException:
        await loading_msg.edit_text("⏱️ <b>Превышено время ожидания</b>\n\nПопробуйте позже или упростите запрос.")
    except httpx.HTTPStatusError as e:
        # Пытаемся извлечь детали ошибки из ответа
        error_detail = None
        try:
            if e.response.headers.get("content-type", "").startswith("application/json"):
                error_data = e.response.json()
                if isinstance(error_data, dict):
                    error_detail = error_data.get("detail")
                    if isinstance(error_detail, dict):
                        error_message = error_detail.get("message")
                        if error_message:
                            error_detail = error_message
        except Exception:
            pass
        
        logger.error(
            "HTTP error in RAG query",
            status_code=e.response.status_code,
            response_text=e.response.text[:500],
            error_detail=error_detail,
            user_id=msg.from_user.id
        )
        
        # Специфичные сообщения для разных статусов
        if e.response.status_code == 503:
            # Индексация не готова
            if error_detail:
                await loading_msg.edit_text(f"⏳ <b>Индексация контента</b>\n\n{error_detail}")
            else:
                await loading_msg.edit_text(
                    "⏳ <b>Индексация контента еще не завершена</b>\n\n"
                    "Пожалуйста, подождите несколько минут и попробуйте снова."
                )
        elif e.response.status_code == 404:
            await loading_msg.edit_text("❌ <b>Ресурс не найден</b>\n\nПопробуйте позже.")
        elif e.response.status_code == 500:
            await loading_msg.edit_text("❌ <b>Внутренняя ошибка сервера</b>\n\nПопробуйте позже.")
        else:
            # Общее сообщение для других ошибок
            if error_detail:
                await loading_msg.edit_text(f"❌ <b>Ошибка обработки запроса</b>\n\n{error_detail}")
            else:
                await loading_msg.edit_text("❌ <b>Ошибка обработки запроса</b>\n\nПопробуйте позже.")
    except KeyError as e:
        # Ошибка при доступе к полям ответа
        logger.error(
            "Missing field in RAG response",
            error=str(e),
            response_keys=list(result.keys()) if 'result' in locals() else None,
            user_id=msg.from_user.id
        )
        await loading_msg.edit_text("❌ <b>Ошибка формата ответа</b>\n\nПопробуйте позже.")
    except Exception as e:
        logger.error(
            "Error in RAG query",
            error=str(e),
            error_type=type(e).__name__,
            user_id=msg.from_user.id,
            exc_info=True
        )
        await loading_msg.edit_text("❌ <b>Произошла ошибка при обработке запроса</b>\n\nПопробуйте позже.")


async def _show_subscription(msg: Message):
    """Показать информацию о подписке."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить информацию о подписке
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{user['id']}/subscription")
            r.raise_for_status()
            subscription = r.json()
        
        text = f"💎 <b>Подписка: {subscription['subscription_type'].upper()}</b>\n\n"
        text += f"📺 Каналов: {subscription['channels_limit']}\n"
        text += f"📝 Постов: {subscription['posts_limit']}\n"
        text += f"🔍 Запросов: {subscription['rag_queries_limit']}\n"
        
        if subscription['subscription_expires_at']:
            text += f"⏰ Истекает: {subscription['subscription_expires_at'][:10]}\n"
        
        await msg.answer(text)
        
    except Exception as e:
        logger.error("Error showing subscription", error=str(e))
        await msg.answer("❌ Произошла ошибка")


async def _show_subscription_callback(cb: CallbackQuery):
    """Показать информацию о подписке через callback."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 404:
                await cb.message.edit_text("❌ Пользователь не найден")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить информацию о подписке
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{API_BASE}/api/users/{user['id']}/subscription")
            r.raise_for_status()
            subscription = r.json()
        
        text = f"💎 <b>Подписка: {subscription['subscription_type'].upper()}</b>\n\n"
        text += f"📺 Каналов: {subscription['channels_limit']}\n"
        text += f"📝 Постов: {subscription['posts_limit']}\n"
        text += f"🔍 Запросов: {subscription['rag_queries_limit']}\n"
        
        if subscription['subscription_expires_at']:
            text += f"⏰ Истекает: {subscription['subscription_expires_at'][:10]}\n"
        
        await cb.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")]
            ])
        )
        
    except Exception as e:
        logger.error("Error showing subscription callback", error=str(e))
        await cb.message.edit_text("❌ Произошла ошибка")

# ============================================================================
# НОВЫЕ КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ КАНАЛАМИ
# ============================================================================

# Context7: Функция _extract_username_from_telegram_url перенесена в bot.utils
# для избежания дублирования кода
_extract_username_from_telegram_url = extract_username_from_telegram_url


@router.message(Command("add_channel"))
async def cmd_add_channel(msg: Message):
    """
    Команда добавления канала.
    
    Context7: Поддерживает прямой ввод username или ссылки:
    - /add_channel @channel_name
    - /add_channel https://t.me/channel_name
    - /add_channel channel_name
    """
    try:
        # Извлекаем аргументы из текста сообщения
        command_text = msg.text or ""
        args = command_text.replace("/add_channel", "").strip()
        
        if not args:
            await msg.answer(
                "Использование: /add_channel @channel_name\n\n"
                "Пример: /add_channel @durov\n"
                "Или: /add_channel https://t.me/durov"
            )
            return
        
        # Извлекаем username из аргументов (может быть ссылка или username)
        username = _extract_username_from_telegram_url(args)
        
        if not username:
            await msg.answer(
                "❌ Неверный формат канала!\n\n"
                "Используйте один из форматов:\n"
                "• <code>/add_channel @channel_name</code>\n"
                "• <code>/add_channel https://t.me/channel_name</code>\n"
                "• <code>/add_channel channel_name</code>",
                parse_mode="HTML"
            )
            return
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{API_BASE}/api/channels/users/{msg.from_user.id}/subscribe",
                    json={"username": username}
                )
                
                if resp.status_code == 201:
                    data = resp.json()
                    
                    # Получаем статистику пользователя для показа лимитов
                    try:
                        stats_resp = await client.get(
                            f"{API_BASE}/api/channels/users/{msg.from_user.id}/stats"
                        )
                        if stats_resp.status_code == 200:
                            stats = stats_resp.json()
                            await msg.answer(
                                f"✅ Канал {username} добавлен!\n\n"
                                f"📊 Статистика:\n"
                                f"• Каналов: {stats['total']}/{stats['max_allowed']}\n"
                                f"• Тариф: {stats['tier'].upper()}\n"
                                f"• Осталось слотов: {stats['remaining']}"
                            )
                        else:
                            await msg.answer(f"✅ Канал {username} добавлен!")
                    except Exception:
                        await msg.answer(f"✅ Канал {username} добавлен!")
                elif resp.status_code == 409:
                    await msg.answer("⚠️ Вы уже подписаны на этот канал")
                elif resp.status_code == 429:
                    data = resp.json()
                    reset_time = datetime.fromtimestamp(data['reset'])
                    await msg.answer(
                        f"⏳ Превышен лимит запросов\n"
                        f"Попробуйте после {reset_time.strftime('%H:%M:%S')}"
                    )
                elif resp.status_code == 403:
                    data = resp.json()
                    detail = data.get('detail', {})
                    await msg.answer(
                        f"🚫 <b>Достигнут лимит каналов</b>\n\n"
                        f"📊 Текущее использование: {detail.get('current', '?')}/{detail.get('max', '?')}\n"
                        f"💎 Тариф: FREE\n\n"
                        f"Для добавления новых каналов:\n"
                        f"• Удалите один из существующих каналов\n"
                        f"• Или улучшите тариф в Mini App"
                    )
                elif resp.status_code == 422:
                    await msg.answer("❌ Неверный формат канала. Используйте @channel_name")
                elif resp.status_code == 500:
                    # Попробуем получить детали ошибки из API
                    try:
                        error_data = resp.json()
                        if error_data.get('detail', {}).get('error') == 'tier_limit_exceeded':
                            await msg.answer(
                                f"🚫 <b>Достигнут лимит каналов</b>\n\n"
                                f"📊 Текущее использование: {error_data['detail'].get('current', '?')}/{error_data['detail'].get('max', '?')}\n"
                                f"💎 Тариф: FREE\n\n"
                                f"Для добавления новых каналов:\n"
                                f"• Удалите один из существующих каналов\n"
                                f"• Или улучшите тариф в Mini App"
                            )
                        else:
                            await msg.answer("❌ Внутренняя ошибка сервера. Попробуйте позже")
                    except:
                        await msg.answer("❌ Внутренняя ошибка сервера. Попробуйте позже")
                else:
                    await msg.answer(f"❌ Ошибка: {resp.status_code}")
        
        except httpx.TimeoutException:
            await msg.answer("⏱️ Превышено время ожидания. Попробуйте позже")
        except Exception as e:
            logger.error("Error in /add_channel", error=str(e))
            await msg.answer("❌ Произошла ошибка")
    
    except Exception as e:
        logger.error("Error in /add_channel command", error=str(e))
        await msg.answer("❌ Произошла ошибка")

# УДАЛЕНО: Дублирование команды /my_channels
# Команда /my_channels уже определена выше (строка 379) и использует функцию _show_channels()
# Этот обработчик был дублирован и удалён для предотвращения конфликтов регистрации


# ============================================================================
# УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ
# ============================================================================

@router.message(
    F.text & ~F.text.startswith("/"),
    ~StateFilter(DigestStates.waiting_topics),
    ~StateFilter(DigestStates.waiting_schedule_time),
    ~StateFilter(AddChannelStates.await_username),
    ~StateFilter(ChannelManagementStates.viewing_channel),
    ~StateFilter(ChannelManagementStates.confirming_delete),
    ~StateFilter(SearchStates.awaiting_query),
    ~StateFilter(FeedbackStates.waiting_message)
)
async def handle_text_message(msg: Message):
    """
    Универсальный обработчик текстовых сообщений с автоматическим определением намерения.
    
    Context7: Автоматически определяет намерение пользователя через IntentClassifier
    и обрабатывает запрос через RAG Service.
    
    Исключает сообщения в состояниях FSM (ввод тем дайджеста, добавление каналов и т.д.).
    """
    # Игнорируем очень короткие сообщения (возможно, случайные)
    if len(msg.text.strip()) < 3:
        await msg.answer("❌ <b>Слишком короткий запрос</b>\n\nПопробуйте задать более подробный вопрос.")
        return
    
    # Обрабатываем через RAG
    await _rag_query(msg, msg.text)


# ============================================================================
# ОБРАБОТЧИК ГОЛОСОВЫХ СООБЩЕНИЙ
# ============================================================================

@router.message(F.voice)
async def handle_voice_message(msg: Message):
    """
    Обработчик голосовых сообщений с транскрибацией через SaluteSpeech.
    
    Context7: Транскрибирует голосовое сообщение и автоматически обрабатывает через RAG.
    """
    try:
        # Проверяем, включена ли транскрибация
        if not settings.voice_transcription_enabled:
            await msg.answer(
                "❌ <b>Транскрибация голосовых сообщений отключена</b>\n\n"
                "Используйте текстовые сообщения для вопросов."
            )
            return
        
        # Проверяем длительность
        if msg.voice.duration > settings.voice_max_duration_sec:
            await msg.answer(
                f"❌ <b>Голосовое сообщение слишком длинное</b>\n\n"
                f"Максимальная длительность: {settings.voice_max_duration_sec} секунд.\n"
                f"Ваше сообщение: {msg.voice.duration} секунд."
            )
            return
        
        # Context7: Проверяем настройки SaluteSpeech перед обработкой
        if not settings.salutespeech_client_id or not settings.salutespeech_client_secret.get_secret_value():
            logger.warning(
                "SaluteSpeech not configured",
                has_client_id=bool(settings.salutespeech_client_id),
                has_client_secret=bool(settings.salutespeech_client_secret.get_secret_value())
            )
            await msg.answer(
                "❌ <b>Транскрибация недоступна</b>\n\n"
                "Сервис транскрибации не настроен. Используйте текстовые сообщения для вопросов."
            )
            return
        
        # Показываем индикатор обработки
        loading_msg = await msg.answer("🎤 <b>Обрабатываю голосовое сообщение...</b>")
        
        # Получаем пользователя
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await loading_msg.edit_text("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Скачиваем файл голосового сообщения
        redis_client = None
        try:
            # Context7: Используем глобальный bot из webhook, а не создаем новый
            try:
                from bot.webhook import bot as global_bot
                if not global_bot:
                    # Fallback: создаем временный bot если глобальный не инициализирован
                    from aiogram import Bot
                    global_bot = Bot(token=settings.telegram_bot_token)
            except ImportError:
                # Fallback: если не можем импортировать, создаем новый
                from aiogram import Bot
                global_bot = Bot(token=settings.telegram_bot_token)
            
            file = await global_bot.get_file(msg.voice.file_id)
            
            # Context7: Используем упрощенный метод download (aiogram best practice)
            # download возвращает BytesIO напрямую
            audio_bytes_io = await global_bot.download(file.file_id)
            audio_bytes = audio_bytes_io.read()
            
            # Context7: Используем SaluteSpeech Service с async Redis клиентом
            # Используем глобальный Redis клиент из webhook для переиспользования соединений
            from services.salutespeech_service import get_salutespeech_service
            try:
                from bot.webhook import redis_client as global_redis_client
                if global_redis_client:
                    redis_client = global_redis_client
                else:
                    # Fallback: создаем временный клиент
                    import redis.asyncio as redis
                    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            except (ImportError, AttributeError):
                # Fallback: создаем временный клиент
                import redis.asyncio as redis
                redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            
            salutespeech_service = get_salutespeech_service(redis_client=redis_client)
            
            transcription_result = await salutespeech_service.transcribe(
                audio_data=audio_bytes,
                audio_format="ogg_opus",
                language="ru"
            )
            
            transcription_text = transcription_result.get("text", "")
            
            if not transcription_text or len(transcription_text.strip()) < 3:
                await loading_msg.edit_text(
                    "❌ <b>Не удалось распознать речь</b>\n\n"
                    "Попробуйте записать сообщение заново или используйте текстовый ввод."
                )
                return
            
            # Показываем транскрипцию
            await loading_msg.edit_text(
                f"🎤 <b>Распознано:</b>\n\n{transcription_text}\n\n"
                f"🔍 <b>Обрабатываю запрос...</b>"
            )
            
            # Обрабатываем транскрибированный текст через RAG
            # Передаем audio_file_id для сохранения в историю
            # Получаем file_id из voice объекта
            audio_file_id = msg.voice.file_id if msg.voice else None
            
            await _rag_query(
                msg, 
                transcription_text, 
                voice_transcription=True,
                audio_file_id=audio_file_id
            )
        
        except httpx.TimeoutException as timeout_error:
            logger.error(
                "Timeout processing voice message",
                error=str(timeout_error),
                user_id=msg.from_user.id,
                voice_duration=msg.voice.duration if msg.voice else None,
                exc_info=True
            )
            await loading_msg.edit_text("⏱️ <b>Превышено время ожидания</b>\n\nПопробуйте позже.")
        except Exception as e:
            # Context7: Детальное логирование всех ошибок
            error_details = {
                "error": str(e),
                "error_type": type(e).__name__,
                "user_id": msg.from_user.id,
                "voice_duration": msg.voice.duration if msg.voice else None,
                "voice_file_id": msg.voice.file_id if msg.voice else None,
                "has_client_id": bool(settings.salutespeech_client_id),
                "has_client_secret": bool(settings.salutespeech_client_secret),
                "api_url": settings.salutespeech_url,
                "transcription_enabled": settings.voice_transcription_enabled
            }
            
            logger.error(
                "Error processing voice message",
                **error_details,
                exc_info=True
            )
            
            # Context7: Пользовательские сообщения об ошибках с детализацией
            error_msg = "❌ <b>Ошибка обработки голосового сообщения</b>\n\n"
            
            error_str_lower = str(e).lower()
            
            if "authorization" in error_str_lower or "401" in str(e):
                error_msg += (
                    "🔐 <b>Ошибка авторизации</b>\n\n"
                    "Проблема с настройками сервиса транскрибации.\n"
                    "Проверьте правильность Authorization key в настройках."
                )
            elif "token" in error_str_lower or "404" in str(e):
                error_msg += (
                    "🔑 <b>Ошибка получения токена</b>\n\n"
                    "Не удалось получить токен доступа к сервису транскрибации.\n"
                    "Проверьте настройки SaluteSpeech API."
                )
            elif "timeout" in error_str_lower:
                error_msg += (
                    "⏱️ <b>Превышено время ожидания</b>\n\n"
                    "Сервис транскрибации не ответил вовремя.\n"
                    "Попробуйте позже или используйте более короткое сообщение."
                )
            elif "empty" in error_str_lower or "распознать" in error_str_lower:
                error_msg += (
                    "🎤 <b>Не удалось распознать речь</b>\n\n"
                    "Попробуйте записать сообщение заново:\n"
                    "• Говорите четче\n"
                    "• Уменьшите фоновый шум\n"
                    "• Используйте текстовый ввод"
                )
            else:
                error_msg += (
                    "⚠️ <b>Внутренняя ошибка</b>\n\n"
                    "Попробуйте позже или используйте текстовый ввод.\n"
                    f"Код ошибки: {type(e).__name__}"
                )
            
            await loading_msg.edit_text(error_msg)
        finally:
            # Context7: Закрываем Redis клиент только если он был создан локально (не глобальный)
            # Глобальный клиент не закрываем, так как он используется другими частями системы
            if redis_client and hasattr(redis_client, '__module__'):
                # Проверяем, что это не глобальный клиент из webhook
                try:
                    from bot.webhook import redis_client as global_redis_client
                    if redis_client is not global_redis_client:
                        # Это локальный клиент - закрываем его
                        await redis_client.aclose()
                except (ImportError, AttributeError):
                    # Если не можем проверить, значит это локальный клиент - закрываем
                    try:
                        await redis_client.aclose()
                    except Exception as e:
                        logger.warning("Error closing Redis client", error=str(e))
    
    except Exception as e:
        logger.error("Error in voice handler", error=str(e))
        await msg.answer("❌ <b>Произошла ошибка при обработке голосового сообщения</b>")
