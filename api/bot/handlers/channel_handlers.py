"""
Telegram Bot Handlers для управления каналами
Поддерживает команды /add_channel, /my_channels с интеграцией через API
Использует FSM для многошаговых диалогов и Context7 best practices
"""

import asyncio
import logging
import re
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx

from bot.utils import extract_username_from_telegram_url

logger = logging.getLogger(__name__)

# Context7: Функция _extract_username_from_telegram_url перенесена в bot.utils
# для избежания дублирования кода. Определяем алиас на уровне модуля сразу после импорта.
_extract_username_from_telegram_url = extract_username_from_telegram_url

# ============================================================================
# FSM STATES
# ============================================================================

class ChannelStates(StatesGroup):
    """Состояния FSM для управления каналами."""
    waiting_username = State()
    waiting_telegram_id = State()
    waiting_confirmation = State()

# ============================================================================
# ROUTER SETUP
# ============================================================================

router = Router()

# ============================================================================
# API CLIENT
# ============================================================================

class ChannelAPIClient:
    """Клиент для работы с Channel Management API."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def subscribe_to_channel(
        self, 
        user_id: str, 
        username: Optional[str] = None,
        telegram_id: Optional[int] = None,
        title: Optional[str] = None
    ) -> Dict[str, Any]:
        """Подписка на канал через API."""
        try:
            payload = {}
            if username:
                payload["username"] = username
            if telegram_id:
                payload["telegram_id"] = telegram_id
            if title:
                payload["title"] = title
            
            response = await self.client.post(
                f"{self.base_url}/api/channels/users/{user_id}/subscribe",
                json=payload
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text}
                
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_user_channels(self, user_id: str) -> Dict[str, Any]:
        """Получение списка каналов пользователя."""
        try:
            response = await self.client.get(
                f"{self.base_url}/api/channels/users/{user_id}/list"
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text}
                
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def unsubscribe_from_channel(self, user_id: str, channel_id: str) -> Dict[str, Any]:
        """Отписка от канала."""
        try:
            response = await self.client.delete(
                f"{self.base_url}/api/channels/users/{user_id}/unsubscribe/{channel_id}"
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text}
                
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_subscription_stats(self, user_id: str) -> Dict[str, Any]:
        """Получение статистики подписок."""
        try:
            response = await self.client.get(
                f"{self.base_url}/api/channels/users/{user_id}/stats"
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text}
                
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def trigger_parsing(self, user_id: str, channel_id: str) -> Dict[str, Any]:
        """Триггер парсинга канала."""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/channels/{channel_id}/trigger-parsing",
                json={"user_id": user_id}
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text}
                
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def close(self):
        """Закрытие клиента."""
        await self.client.aclose()

# Глобальный экземпляр API клиента
api_client = ChannelAPIClient()

# ============================================================================
# COMMAND HANDLERS
# ============================================================================

@router.message(Command("add_channel"))
async def cmd_add_channel(msg: Message, state: FSMContext):
    """
    Команда добавления канала.
    
    Context7: Поддерживает прямой ввод username или ссылки:
    - /add_channel @channel_name
    - /add_channel https://t.me/channel_name
    - /add_channel channel_name
    
    Если аргументы не указаны, показывает интерактивное меню.
    """
    try:
        # Проверка прав доступа
        if not await _check_user_permissions(msg.from_user.id):
            await msg.answer("❌ У вас нет прав для добавления каналов")
            return
        
        # Извлекаем аргументы из команды
        command_text = msg.text or ""
        args = command_text.replace("/add_channel", "").strip()
        
        # Если есть аргументы, обрабатываем напрямую
        if args:
            username = _extract_username_from_telegram_url(args)
            
            if not username:
                await msg.answer(
                    "❌ Неверный формат!\n\n"
                    "Используйте один из форматов:\n"
                    "• <code>/add_channel @channel_name</code>\n"
                    "• <code>/add_channel https://t.me/channel_name</code>\n"
                    "• <code>/add_channel channel_name</code>\n\n"
                    "Username должен содержать только буквы, цифры и подчёркивания (5-32 символа).",
                    parse_mode="HTML"
                )
                return
            
            # Проверка лимитов перед добавлением
            stats_result = await api_client.get_subscription_stats(str(msg.from_user.id))
            if stats_result["success"]:
                stats = stats_result["data"]
                if not stats.get("can_add_more", True):
                    await msg.answer(
                        f"❌ Достигнут лимит подписок!\n"
                        f"Текущих каналов: {stats.get('total_channels', 0)}\n"
                        f"Лимит: {stats.get('subscription_limit', 0)}\n\n"
                        f"💡 Обновите подписку для увеличения лимита"
                    )
                    return
            
            # Показываем, что обрабатываем запрос
            processing_msg = await msg.answer(
                f"⏳ Обрабатываю запрос на добавление канала <code>@{username}</code>...",
                parse_mode="HTML"
            )
            
            # Подписка на канал
            result = await api_client.subscribe_to_channel(
                user_id=str(msg.from_user.id),
                username=username
            )
            
            if result["success"]:
                channel_data = result["data"]
                await processing_msg.edit_text(
                    f"✅ <b>Канал успешно добавлен!</b>\n\n"
                    f"📢 Канал: @{username}\n"
                    f"📝 Название: {channel_data.get('title', 'N/A')}\n\n"
                    f"🔄 Начинаю парсинг последних постов...",
                    parse_mode="HTML"
                )
                
                # Триггер парсинга в фоне
                asyncio.create_task(_trigger_background_parsing(msg.from_user.id, channel_data["id"]))
            else:
                error_msg = result.get("error", "Неизвестная ошибка")
                await processing_msg.edit_text(
                    f"❌ <b>Ошибка добавления канала</b>\n\n"
                    f"Не удалось добавить канал <code>@{username}</code>.\n"
                    f"Ошибка: {error_msg[:200]}",
                    parse_mode="HTML"
                )
            return
        
        # Если аргументов нет, показываем интерактивное меню
        # Получение статистики подписок
        stats_result = await api_client.get_subscription_stats(str(msg.from_user.id))
        
        if not stats_result["success"]:
            await msg.answer("❌ Ошибка получения статистики подписок")
            return
        
        stats = stats_result["data"]
        
        # Проверка лимитов
        if not stats.get("can_add_more", True):
            await msg.answer(
                f"❌ Достигнут лимит подписок!\n"
                f"Текущих каналов: {stats.get('total_channels', 0)}\n"
                f"Лимит: {stats.get('subscription_limit', 0)}\n\n"
                f"💡 Обновите подписку для увеличения лимита"
            )
            return
        
        # Создание клавиатуры с вариантами добавления
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="📝 По username (@channel_name или ссылка)",
            callback_data="add_by_username"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🔢 По Telegram ID",
            callback_data="add_by_id"
        ))
        keyboard.add(InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel_add"
        ))
        
        await msg.answer(
            f"📢 <b>Добавление канала</b>\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"• Каналов: {stats.get('total_channels', 0)}/{stats.get('subscription_limit', 0)}\n"
            f"• Постов сегодня: {stats.get('posts_today', 0)}\n"
            f"• Всего постов: {stats.get('total_posts', 0)}\n\n"
            f"💡 <i>Или просто отправьте:</i>\n"
            f"• <code>/add_channel @channel_name</code>\n"
            f"• <code>/add_channel https://t.me/channel_name</code>\n\n"
            f"Выберите способ добавления:",
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error in cmd_add_channel: {e}")
        await msg.answer("❌ Произошла ошибка при добавлении канала")

@router.message(Command("my_channels"))
async def cmd_my_channels(msg: Message):
    """Команда просмотра каналов пользователя."""
    try:
        # Получение списка каналов
        channels_result = await api_client.get_user_channels(str(msg.from_user.id))
        
        if not channels_result["success"]:
            await msg.answer("❌ Ошибка получения списка каналов")
            return
        
        channels_data = channels_result["data"]
        channels = channels_data.get("channels", [])
        
        if not channels:
            await msg.answer(
                "📭 <b>У вас пока нет подписок</b>\n\n"
                "Используйте /add_channel для добавления каналов",
                parse_mode="HTML"
            )
            return
        
        # Формирование сообщения со списком каналов
        text = f"📢 <b>Ваши каналы ({len(channels)})</b>\n\n"
        
        for i, channel in enumerate(channels, 1):
            status_emoji = "✅" if channel.get("is_active") else "⏸️"
            username = channel.get("username", "N/A")
            title = channel.get("title", "Без названия")
            posts_count = channel.get("posts_count", 0)
            
            text += f"{i}. {status_emoji} <b>@{username}</b>\n"
            text += f"   📝 {title}\n"
            text += f"   📊 Постов: {posts_count}\n\n"
        
        # Создание клавиатуры с действиями
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data="refresh_channels"
        ))
        keyboard.add(InlineKeyboardButton(
            text="➕ Добавить канал",
            callback_data="add_channel"
        ))
        
        await msg.answer(
            text,
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error in cmd_my_channels: {e}")
        await msg.answer("❌ Произошла ошибка при получении списка каналов")

@router.message(Command("channel_stats"))
async def cmd_channel_stats(msg: Message):
    """Команда статистики каналов."""
    try:
        # Получение статистики
        stats_result = await api_client.get_subscription_stats(str(msg.from_user.id))
        
        if not stats_result["success"]:
            await msg.answer("❌ Ошибка получения статистики")
            return
        
        stats = stats_result["data"]
        
        # Формирование сообщения со статистикой
        text = f"📊 <b>Статистика каналов</b>\n\n"
        text += f"📢 <b>Подписки:</b>\n"
        text += f"• Всего каналов: {stats.get('total_channels', 0)}\n"
        text += f"• Активных: {stats.get('active_channels', 0)}\n"
        text += f"• Лимит: {stats.get('subscription_limit', 0)}\n\n"
        text += f"📝 <b>Посты:</b>\n"
        text += f"• Всего: {stats.get('total_posts', 0)}\n"
        text += f"• Сегодня: {stats.get('posts_today', 0)}\n\n"
        
        can_add = stats.get("can_add_more", False)
        if can_add:
            text += "✅ Можно добавить больше каналов"
        else:
            text += "❌ Достигнут лимит подписок"
        
        await msg.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error in cmd_channel_stats: {e}")
        await msg.answer("❌ Произошла ошибка при получении статистики")

# ============================================================================
# CALLBACK HANDLERS
# ============================================================================

@router.callback_query(F.data == "add_by_username")
async def cb_add_by_username(callback: CallbackQuery, state: FSMContext):
    """Обработка добавления канала по username или ссылке."""
    await callback.message.edit_text(
        "📝 <b>Добавление по username</b>\n\n"
        "Отправьте username канала или ссылку:\n"
        "• <code>@channel_name</code>\n"
        "• <code>channel_name</code>\n"
        "• <code>https://t.me/channel_name</code>",
        parse_mode="HTML"
    )
    await state.set_state(ChannelStates.waiting_username)
    await callback.answer()

@router.callback_query(F.data == "add_by_id")
async def cb_add_by_id(callback: CallbackQuery, state: FSMContext):
    """Обработка добавления канала по Telegram ID."""
    await callback.message.edit_text(
        "🔢 <b>Добавление по Telegram ID</b>\n\n"
        "Отправьте Telegram ID канала (например: -1001234567890)",
        parse_mode="HTML"
    )
    await state.set_state(ChannelStates.waiting_telegram_id)
    await callback.answer()

@router.callback_query(F.data == "cancel_add")
async def cb_cancel_add(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления канала."""
    await callback.message.edit_text("❌ Добавление канала отменено")
    await state.clear()
    await callback.answer()

@router.callback_query(F.data == "refresh_channels")
async def cb_refresh_channels(callback: CallbackQuery):
    """Обновление списка каналов."""
    await cmd_my_channels(callback.message)
    await callback.answer("🔄 Список обновлён")

@router.callback_query(F.data == "add_channel")
async def cb_add_channel(callback: CallbackQuery, state: FSMContext):
    """Переход к добавлению канала."""
    await cmd_add_channel(callback.message, state)
    await callback.answer()

# ============================================================================
# STATE HANDLERS
# ============================================================================

@router.message(ChannelStates.waiting_username)
async def process_username(msg: Message, state: FSMContext):
    """
    Обработка username канала или ссылки.
    
    Context7: Поддерживает различные форматы:
    - @channel_name
    - channel_name
    - https://t.me/channel_name
    """
    try:
        # Извлекаем username из текста (может быть ссылка или username)
        username = _extract_username_from_telegram_url(msg.text)
        
        if not username:
            await msg.answer(
                "❌ Неверный формат!\n\n"
                "Используйте один из форматов:\n"
                "• <code>@channel_name</code>\n"
                "• <code>channel_name</code>\n"
                "• <code>https://t.me/channel_name</code>\n\n"
                "Username должен содержать только буквы, цифры и подчёркивания (5-32 символа).",
                parse_mode="HTML"
            )
            return
        
        # Подписка на канал
        result = await api_client.subscribe_to_channel(
            user_id=str(msg.from_user.id),
            username=username
        )
        
        if result["success"]:
            channel_data = result["data"]
            await msg.answer(
                f"✅ <b>Канал успешно добавлен!</b>\n\n"
                f"📢 Канал: @{username}\n"
                f"📝 Название: {channel_data.get('title', 'N/A')}\n\n"
                f"🔄 Начинаю парсинг последних постов...",
                parse_mode="HTML"
            )
            
            # Триггер парсинга в фоне
            asyncio.create_task(_trigger_background_parsing(msg.from_user.id, channel_data["id"]))
        else:
            error_msg = result.get("error", "Неизвестная ошибка")
            await msg.answer(f"❌ Ошибка добавления канала: {error_msg}")
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error processing username: {e}")
        await msg.answer("❌ Произошла ошибка при обработке username")
        await state.clear()

@router.message(ChannelStates.waiting_telegram_id)
async def process_telegram_id(msg: Message, state: FSMContext):
    """Обработка Telegram ID канала."""
    try:
        telegram_id_text = msg.text.strip()
        
        # Валидация Telegram ID
        try:
            telegram_id = int(telegram_id_text)
            if telegram_id >= 0:
                await msg.answer("❌ Telegram ID канала должен быть отрицательным числом!")
                return
        except ValueError:
            await msg.answer("❌ Неверный формат Telegram ID!")
            return
        
        # Подписка на канал
        result = await api_client.subscribe_to_channel(
            user_id=str(msg.from_user.id),
            telegram_id=telegram_id
        )
        
        if result["success"]:
            channel_data = result["data"]
            await msg.answer(
                f"✅ <b>Канал успешно добавлен!</b>\n\n"
                f"🔢 ID: {telegram_id}\n"
                f"📝 Название: {channel_data.get('title', 'N/A')}\n\n"
                f"🔄 Начинаю парсинг последних постов...",
                parse_mode="HTML"
            )
            
            # Триггер парсинга в фоне
            asyncio.create_task(_trigger_background_parsing(msg.from_user.id, channel_data["id"]))
        else:
            error_msg = result.get("error", "Неизвестная ошибка")
            await msg.answer(f"❌ Ошибка добавления канала: {error_msg}")
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error processing telegram_id: {e}")
        await msg.answer("❌ Произошла ошибка при обработке Telegram ID")
        await state.clear()

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _check_user_permissions(user_id: int) -> bool:
    """Проверка прав пользователя."""
    # Здесь можно добавить логику проверки прав
    # Например, проверка в БД или кеше
    return True


def _is_valid_username(username: str) -> bool:
    """Валидация username канала."""
    if not username:
        return False
    
    # Username должен содержать только буквы, цифры и подчёркивания
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False
    
    # Длина от 5 до 32 символов
    if len(username) < 5 or len(username) > 32:
        return False
    
    return True

async def _trigger_background_parsing(user_id: int, channel_id: str):
    """Фоновый триггер парсинга канала."""
    try:
        # Небольшая задержка для завершения подписки
        await asyncio.sleep(2)
        
        result = await api_client.trigger_parsing(str(user_id), channel_id)
        
        if result["success"]:
            logger.info(f"Background parsing triggered for user {user_id}, channel {channel_id}")
        else:
            logger.warning(f"Failed to trigger background parsing: {result.get('error')}")
            
    except Exception as e:
        logger.error(f"Error in background parsing trigger: {e}")

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@router.message()
async def handle_unknown_message(msg: Message, state: FSMContext):
    """Обработка неизвестных сообщений."""
    current_state = await state.get_state()
    
    if current_state in [ChannelStates.waiting_username, ChannelStates.waiting_telegram_id]:
        await msg.answer(
            "❌ Неверный формат!\n\n"
            "Используйте команду /add_channel для начала заново"
        )
        await state.clear()
    else:
        await msg.answer(
            "🤖 Не понимаю эту команду\n\n"
            "Доступные команды:\n"
            "• /add_channel - добавить канал\n"
            "• /my_channels - мои каналы\n"
            "• /channel_stats - статистика"
        )

# ============================================================================
# CLEANUP
# ============================================================================

async def cleanup_api_client():
    """Очистка ресурсов при завершении."""
    await api_client.close()

# ============================================================================
# CONTEXT7 BEST PRACTICES
# ============================================================================

# [C7-ID: BOT-HANDLERS-001] - FSM для многошаговых диалогов
# [C7-ID: BOT-HANDLERS-002] - Валидация пользовательского ввода
# [C7-ID: BOT-HANDLERS-003] - Обработка ошибок API
# [C7-ID: BOT-HANDLERS-004] - Фоновые задачи для триггеров
# [C7-ID: BOT-HANDLERS-005] - Inline клавиатуры для UX
# [C7-ID: BOT-HANDLERS-006] - Логирование всех операций
# [C7-ID: BOT-HANDLERS-007] - Graceful cleanup ресурсов
