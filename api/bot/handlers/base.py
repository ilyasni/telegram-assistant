"""Telegram bot handlers with full functionality."""

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Voice
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from bot.states import DigestStates, AddChannelStates, ChannelManagementStates, SearchStates
import html
import httpx
import structlog
import re
import io
from typing import Optional
from datetime import datetime
from config import settings
from utils.telegram_formatter import markdown_to_telegram_chunks

logger = structlog.get_logger()
router = Router()

# API base URL
API_BASE = "http://api:8000"

# Подключение роутеров из подмодулей
try:
    from bot.handlers.trends_handlers import router as trends_router
    router.include_router(trends_router)
    logger.info("Trends handlers router included")
except Exception as e:
    logger.warning("Failed to include trends handlers router", error=str(e))

try:
    from bot.handlers.digest_handlers import router as digest_router
    router.include_router(digest_router)
    logger.info("Digest handlers router included")
except Exception as e:
    logger.warning("Failed to include digest handlers router", error=str(e))

try:
    from bot.handlers.group_handlers import router as group_router
    router.include_router(group_router)
    logger.info("Group handlers router included")
except Exception as e:
    logger.warning("Failed to include group handlers router", error=str(e))


def _kb_login():
    """Клавиатура для авторизации: только Mini App (QR)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть Mini App (QR)", web_app={"url": "https://produman.studio/tg/app/"})]
    ])


def _kb_login_with_invite(invite_code: str):
    """Клавиатура для авторизации с инвайт-кодом."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть Mini App (QR)", web_app={"url": f"https://produman.studio/tg/app/?invite={invite_code}"})]
    ])


def _kb_main_menu():
    """Главное меню бота."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Мои каналы", callback_data="menu:channels")],
        [InlineKeyboardButton(text="➕ Добавить канал", callback_data="menu:add_channel")],
        [InlineKeyboardButton(text="👥 Мои группы", callback_data="menu:groups")],
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="menu:search")],
        [InlineKeyboardButton(text="📰 Дайджесты", callback_data="digest:menu")],
        [InlineKeyboardButton(text="📈 Тренды", callback_data="trends:menu")],
        [InlineKeyboardButton(text="💎 Подписка", callback_data="menu:subscription")],
    ])


def _kb_channels_list(channels: list):
    """Клавиатура со списком каналов."""
    builder = InlineKeyboardBuilder()
    for channel in channels:
        builder.button(
            text=f"📺 {channel['title']}",
            callback_data=f"channel:view:{channel['id']}"
        )
    builder.button(text="➕ Добавить канал", callback_data="menu:add_channel")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def _kb_channel_actions(channel_id: str):
    """Клавиатура действий с каналом."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"channel:delete:{channel_id}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"channel:refresh:{channel_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu:channels")],
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
                    await client.post(f"{API_BASE}/api/users/", json=user_data)
                elif r.status_code == 200:
                    # Пользователь существует - обновляем данные
                    await client.put(f"{API_BASE}/api/users/{msg.from_user.id}", json=user_data)
        except Exception as e:
            logger.warning("User bootstrap failed (non-blocking)", error=str(e))

        # 2) Всегда показываем приветствие и Mini App кнопку (baseline-first UX)
        await msg.answer(
            "Ассистент.\nИспользуйте кнопку ниже для входа.",
            reply_markup=_kb_login()
        )
        
    except Exception as e:
        logger.error("Error in cmd_start (fallback path)", error=str(e))
        # Даже при ошибке показываем Mini App, чтобы не блокировать вход
        await msg.answer(
            "Ассистент.\nИспользуйте кнопку ниже для входа.",
            reply_markup=_kb_login()
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
    help_text = """🤖 <b>Помощь по командам бота</b>

<b>🚀 Основные команды</b>
/start — Начать работу с ботом
/help — Показать эту справку
/login [INVITE_CODE] — Войти в систему (с инвайт-кодом или без)

<b>📺 Управление каналами</b>
/add_channel @channel_name — Добавить канал для отслеживания
Пример: <code>/add_channel @durov</code>

/my_channels — Показать список ваших подписанных каналов

<b>🔍 Поиск и вопросы</b>
/ask <i>ваш вопрос</i> — Задать вопрос ассистенту
Пример: <code>/ask Что нового в AI?</code>

/search <i>запрос</i> — Поиск по содержимому каналов
Пример: <code>/search машинное обучение</code>

<b>👥 Группы</b>
/groups — Показать подключённые группы
/group_discovery — Найти доступные чаты и подключить новые

/recommend <i>запрос</i> — Получить рекомендации
Пример: <code>/recommend интересные новости про AI</code>

<b>💬 Текстовые и голосовые сообщения</b>
Вы можете просто написать вопрос текстом — бот автоматически обработает запрос через RAG.

Также поддерживаются голосовые сообщения — бот распознает речь и ответит на ваш вопрос.

<b>💎 Подписка</b>
/subscription — Информация о вашей подписке и лимитах

<b>👑 Администрирование</b>
/admin — Открыть админ-панель (только для администраторов)

<b>💡 Советы</b>
• Задавайте вопросы естественным языком
• Используйте голосовые сообщения для быстрого ввода
• Команды работают без аргументов — просто отправьте текст
• Результаты поиска включают ссылки на источники

<b>📝 Примечание</b>
Для входа в систему используйте Mini App через кнопку внизу или команду /login."""
    
    await msg.answer(
        help_text,
        parse_mode="HTML",
        reply_markup=_kb_login()
    )


@router.message(Command("login"))
async def cmd_login(msg: Message):
    """Обработчик команды /login с поддержкой инвайт-кодов."""
    args = msg.text.split()
    
    # Если передан инвайт-код, валидируем его
    if len(args) > 1:
        invite_code = args[1]
        logger.info("Login with invite code", user_id=msg.from_user.id, invite_code=invite_code)
        
        # Валидация инвайт-кода
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                # Проверяем инвайт-код через API
                response = await client.get(f"{API_BASE}/api/admin/invites/{invite_code}")
                
                if response.status_code == 200:
                    invite_data = response.json()
                    logger.info("Valid invite code", invite_code=invite_code, tenant_id=invite_data.get('tenant_id'))
                    
                    # Открываем Mini App с валидным инвайтом
                    await msg.answer(
                        f"✅ <b>Инвайт-код принят</b>\n\n"
                        f"Открываем Mini App для авторизации...",
                        reply_markup=_kb_login_with_invite(invite_code)
                    )
                elif response.status_code == 404:
                    await msg.answer(
                        "❌ <b>Неверный инвайт-код</b>\n\n"
                        "Проверьте правильность кода и попробуйте снова.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="login:retry")]
                        ])
                    )
                elif response.status_code == 410:
                    await msg.answer(
                        "❌ <b>Инвайт-код истёк</b>\n\n"
                        "Срок действия кода истёк. Обратитесь к администратору.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="login:retry")]
                        ])
                    )
                else:
                    await msg.answer(
                        "❌ <b>Ошибка проверки инвайт-кода</b>\n\n"
                        "Попробуйте позже или обратитесь к поддержке.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="login:retry")]
                        ])
                    )
                    
        except httpx.TimeoutException:
            logger.warning("Timeout checking invite code", user_id=msg.from_user.id, invite_code=invite_code)
            await msg.answer(
                "⏱️ <b>Таймаут проверки</b>\n\n"
                "Сервер не отвечает. Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="login:retry")]
                ])
            )
        except Exception as e:
            logger.error("Error checking invite code", user_id=msg.from_user.id, invite_code=invite_code, error=str(e))
            await msg.answer(
                "❌ <b>Ошибка системы</b>\n\n"
                "Попробуйте позже или обратитесь к поддержке.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="login:retry")]
                ])
            )
    else:
        # Обычный логин без инвайт-кода
        await msg.answer(
            "🔐 <b>Вход в систему</b>\n\n"
            "Для входа используйте команду:\n"
            "<code>/login INVITE_CODE</code>\n\n"
            "Или нажмите кнопку ниже для входа через Mini App:",
            reply_markup=_kb_login()
        )


# Удалена дублированная функция - используется версия ниже


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
            
            # Context7: Логирование для отладки
            webapp_url = "https://produman.studio/tg/app/"
            logger.info(
                "Admin panel access requested",
                telegram_id=msg.from_user.id,
                user_role=user_role,
                is_admin=is_admin,
                webapp_url=webapp_url
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
    await cb.message.answer(
        "Откройте Mini App для сканирования QR-кода.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть Mini App (QR)", web_app={"url": "https://produman.studio/tg/app/"})]
        ])
    )
    await cb.answer()


@router.callback_query(F.data == "login:retry")
async def on_login_retry(cb: CallbackQuery):
    """Фолбэк: повторная попытка входа."""
    await cb.message.edit_text(
        "🔐 <b>Вход в систему</b>\n\n"
        "Для входа используйте команду:\n"
        "<code>/login INVITE_CODE</code>\n\n"
        "Или нажмите кнопку ниже для входа через Mini App:",
        reply_markup=_kb_login()
    )
    await cb.answer()


@router.callback_query(F.data == "admin:retry")
async def on_admin_retry(cb: CallbackQuery):
    """Фолбэк: повторная попытка открытия админ-панели."""
    await cb.message.edit_text(
        "👑 <b>Админ-панель</b>\n\n"
        "Откройте Mini App для доступа к админ-панели.\n"
        "Доступ будет предоставлен только администраторам.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Открыть админ-панель", web_app={"url": "https://produman.studio/tg/app/"})]
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


# Helper functions

async def _add_channel(msg: Message, channel_name: str):
    """Добавить канал."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Добавить канал
        channel_data = {
            "telegram_id": -1001234567890,  # TODO: Получить реальный ID канала
            "username": channel_name[1:],  # Убираем @
            "title": channel_name,
            "settings": {}
        }
        
        async with httpx.AsyncClient() as client:
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
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить каналы
        url = f"{API_BASE}/api/channels/users/{user['id']}/list"
        logger.info(f"[BOT] CALL {url}")
        async with httpx.AsyncClient() as client:
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
        for channel in channels:
            status = "🟢" if channel['is_active'] else "🔴"
            text += f"{status} {channel['title']}\n"
        
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
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 404:
                await cb.message.edit_text("❌ Пользователь не найден")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить каналы
        async with httpx.AsyncClient() as client:
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
        for channel in channels:
            status = "🟢" if channel['is_active'] else "🔴"
            text += f"{status} {channel['title']}\n"
        
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
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 404:
                await cb.message.edit_text("❌ Пользователь не найден")
                return
            r.raise_for_status()
            user = r.json()
        
        # Удалить канал
        async with httpx.AsyncClient() as client:
            r = await client.delete(f"{API_BASE}/api/channels/users/{user['id']}/unsubscribe/{channel_id}")
            r.raise_for_status()
        
        await cb.message.edit_text("✅ Канал удален")
        await cb.answer("Канал удален")
        
    except Exception as e:
        logger.error("Error deleting channel", error=str(e))
        await cb.message.edit_text("❌ Ошибка удаления канала")


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
        
        answer = result['result']['answer']
        sources = result['result']['sources']
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
        logger.error("HTTP error in RAG query", status_code=e.response.status_code, response_text=e.response.text[:200])
        await loading_msg.edit_text("❌ <b>Ошибка обработки запроса</b>\n\nПопробуйте позже.")
    except Exception as e:
        logger.error("Error in RAG query", error=str(e))
        await loading_msg.edit_text("❌ <b>Произошла ошибка при обработке запроса</b>\n\nПопробуйте позже.")


async def _show_subscription(msg: Message):
    """Показать информацию о подписке."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить информацию о подписке
        async with httpx.AsyncClient() as client:
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
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/users/{cb.from_user.id}")
            if r.status_code == 404:
                await cb.message.edit_text("❌ Пользователь не найден")
                return
            r.raise_for_status()
            user = r.json()
        
        # Получить информацию о подписке
        async with httpx.AsyncClient() as client:
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

@router.message(Command("add_channel"))
async def cmd_add_channel(msg: Message):
    """Команда добавления канала."""
    try:
        # Извлекаем аргументы из текста сообщения
        command_text = msg.text or ""
        args = command_text.replace("/add_channel", "").strip()
        
        if not args:
            await msg.answer(
                "Использование: /add_channel @channel_name\n\n"
                "Пример: /add_channel @durov"
            )
            return
        
        username = args
        
        # Валидация username
        if not re.match(r'^@?[a-zA-Z0-9_]{5,32}$', username):
            await msg.answer("❌ Неверный формат канала. Используйте @channel_name")
            return
        
        # Добавление @ если отсутствует
        if not username.startswith('@'):
            username = '@' + username
        
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

@router.message(Command("my_channels"))
async def cmd_my_channels(msg: Message):
    """Команда просмотра каналов пользователя."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{API_BASE}/api/channels/users/{msg.from_user.id}/list"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                channels = data['channels']
                
                if not channels:
                    await msg.answer("📺 У вас нет подписанных каналов")
                    return
                
                # Inline кнопки для каждого канала
                builder = InlineKeyboardBuilder()
                for ch in channels[:10]:  # Первые 10
                    builder.button(
                        text=f"📺 {ch['title']}",
                        callback_data=f"channel:view:{ch['id']}"
                    )
                builder.adjust(1)
                
                await msg.answer(
                    f"📋 Ваши каналы ({data['total']}):",
                    reply_markup=builder.as_markup()
                )
            else:
                await msg.answer("❌ Не удалось загрузить список каналов")
    
    except Exception as e:
        logger.error("Error in /my_channels", error=str(e))
        await msg.answer("❌ Произошла ошибка")


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
    ~StateFilter(SearchStates.awaiting_query)
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
