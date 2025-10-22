"""Telegram bot handlers with full functionality."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx
import structlog
import re
from typing import Optional
from config import settings

logger = structlog.get_logger()
router = Router()

# API base URL
API_BASE = "http://api:8000"


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
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="menu:search")],
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
        # 1) Попытка проверить/создать пользователя — но UX не блокируем
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
                if r.status_code == 404:
                    user_data = {
                        "telegram_id": msg.from_user.id,
                        "username": msg.from_user.username
                    }
                    await client.post(f"{API_BASE}/api/users/", json=user_data)
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


@router.message(Command("add_channel"))
async def cmd_add_channel(msg: Message):
    """Обработчик команды /add_channel."""
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer(
            "❌ <b>Неверный формат</b>\n\n"
            "Использование: <code>/add_channel @channel_name</code>\n"
            "Пример: <code>/add_channel @durov</code>"
        )
        return
    
    channel_name = args[1]
    if not channel_name.startswith('@'):
        await msg.answer("❌ Имя канала должно начинаться с @")
        return
    
    await _add_channel(msg, channel_name)


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
    await msg.answer("🔍 <b>Поиск</b>\n\nФункция поиска пока в разработке.")


@router.message(Command("recommend"))
async def cmd_recommend(msg: Message):
    """Обработчик команды /recommend."""
    await msg.answer("🎯 <b>Рекомендации</b>\n\nФункция рекомендаций пока в разработке.")


@router.message(Command("digest"))
async def cmd_digest(msg: Message):
    """Обработчик команды /digest."""
    await msg.answer("📰 <b>Дайджест</b>\n\nФункция дайджеста пока в разработке.")


@router.message(Command("subscription"))
async def cmd_subscription(msg: Message):
    """Обработчик команды /subscription."""
    await _show_subscription(msg)


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
            r = await client.post(f"{API_BASE}/api/channels/users/{user['id']}/channels", json=channel_data)
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
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/channels/users/{user['id']}/channels")
            r.raise_for_status()
            channels = r.json()
        
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
            r = await client.get(f"{API_BASE}/api/channels/users/{user['id']}/channels")
            r.raise_for_status()
            channels = r.json()
        
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
            r = await client.delete(f"{API_BASE}/api/channels/users/{user['id']}/channels/{channel_id}")
            r.raise_for_status()
        
        await cb.message.edit_text("✅ Канал удален")
        await cb.answer("Канал удален")
        
    except Exception as e:
        logger.error("Error deleting channel", error=str(e))
        await cb.message.edit_text("❌ Ошибка удаления канала")


async def _rag_query(msg: Message, question: str):
    """Выполнить RAG запрос."""
    try:
        # Получить пользователя
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{API_BASE}/api/users/{msg.from_user.id}")
            if r.status_code == 404:
                await msg.answer("❌ Пользователь не найден. Используйте /start")
                return
            r.raise_for_status()
            user = r.json()
        
        # Выполнить RAG запрос
        query_data = {
            "query": question,
            "user_id": user['id']
        }
        
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{API_BASE}/api/rag/query", json=query_data)
            r.raise_for_status()
            result = r.json()
        
        answer = result['result']['answer']
        sources = result['result']['sources']
        
        text = f"🤖 <b>Ответ на ваш вопрос:</b>\n\n{answer}\n\n"
        if sources:
            text += "<b>Источники:</b>\n"
            for source in sources[:3]:  # Показываем только первые 3
                text += f"• {source.get('title', 'Без названия')}\n"
        
        await msg.answer(text)
        
    except Exception as e:
        logger.error("Error in RAG query", error=str(e))
        await msg.answer("❌ Произошла ошибка при поиске")


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
