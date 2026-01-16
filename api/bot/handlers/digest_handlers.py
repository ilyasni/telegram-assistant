"""
Telegram Bot handlers для управления дайджестами.
Context7: Интеграция с Digest API через HTTP клиент
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import httpx
import structlog
import os
import redis.asyncio as redis
from typing import Optional
from uuid import UUID
from bot.states import DigestStates
from aiogram.filters import Command

logger = structlog.get_logger()
router = Router()

# API base URL
API_BASE = "http://api:8000"


def _kb_digest_menu():
    """Клавиатура главного меню дайджестов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="digest:settings")],
        [InlineKeyboardButton(text="📰 История", callback_data="digest:history")],
        [InlineKeyboardButton(text="🔄 Сгенерировать сейчас", callback_data="digest:generate")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")]
    ])


def _kb_digest_settings(enabled: bool):
    """Клавиатура настроек дайджеста."""
    builder = InlineKeyboardBuilder()
    
    # Переключатель включения/выключения
    status_text = "✅ Включен" if enabled else "❌ Выключен"
    builder.button(
        text=f"{'🔴' if enabled else '🟢'} {status_text}",
        callback_data="digest:toggle"
    )
    
    # Настройки
    builder.button(text="📝 Темы", callback_data="digest:edit_topics")
    builder.button(text="⏰ Время отправки", callback_data="digest:edit_time")
    builder.button(text="📅 Частота", callback_data="digest:edit_frequency")
    builder.button(text="🔙 Назад", callback_data="digest:menu")
    
    builder.adjust(1)
    return builder.as_markup()


def _kb_frequency_options():
    """Клавиатура выбора частоты."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Ежедневно", callback_data="digest:frequency:daily")],
        [InlineKeyboardButton(text="📆 Еженедельно", callback_data="digest:frequency:weekly")],
        [InlineKeyboardButton(text="📊 Ежемесячно", callback_data="digest:frequency:monthly")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="digest:settings")]
    ])


async def _get_user_id(telegram_id: int) -> Optional[UUID]:
    """Получить user_id по telegram_id."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{API_BASE}/api/users/{telegram_id}")
            if r.status_code == 200:
                user_data = r.json()
                return UUID(user_data.get("id"))
            else:
                logger.warning("User not found", telegram_id=telegram_id, status_code=r.status_code)
                return None
    except Exception as e:
        logger.error("Error getting user_id", telegram_id=telegram_id, error=str(e))
        return None


@router.message(Command("digest"))
async def cmd_digest(msg: Message):
    """Обработчик команды /digest."""
    user_id = await _get_user_id(msg.from_user.id)
    if not user_id:
        await msg.answer(
            "❌ <b>Пользователь не найден</b>\n\n"
            "Используйте /start для регистрации."
        )
        return
    
    text = (
        "📰 <b>Дайджесты</b>\n\n"
        "Персонализированные дайджесты новостей по вашим темам.\n\n"
        "Выберите действие:"
    )
    await msg.answer(text, reply_markup=_kb_digest_menu(), parse_mode="HTML")


@router.callback_query(F.data == "digest:menu")
async def callback_digest_menu(callback: CallbackQuery):
    """Возврат в главное меню дайджестов."""
    try:
        await callback.message.edit_text(
            "📰 <b>Дайджесты</b>\n\n"
            "Персонализированные дайджесты новостей по вашим темам.\n\n"
            "Выберите действие:",
            reply_markup=_kb_digest_menu(),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error("Error showing digest menu", error=str(e))
        await callback.answer("❌ Ошибка загрузки меню", show_alert=True)


@router.callback_query(F.data == "digest:settings")
async def callback_digest_settings(callback: CallbackQuery):
    """Показать настройки дайджеста."""
    user_id = await _get_user_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/api/digest/settings/{user_id}")
            if r.status_code != 200:
                await callback.answer("❌ Ошибка получения настроек", show_alert=True)
                return
            
            settings = r.json()
            
            # Форматируем текст настроек
            status_text = "✅ Включен" if settings.get("enabled") else "❌ Выключен"
            topics_text = ", ".join(settings.get("topics", [])) if settings.get("topics") else "Не указаны"
            
            text = (
                f"⚙️ <b>Настройки дайджеста</b>\n\n"
                f"Статус: {status_text}\n"
                f"📝 Темы: {topics_text}\n"
                f"⏰ Время: {settings.get('schedule_time', 'N/A')} ({settings.get('schedule_tz', 'N/A')})\n"
                f"📅 Частота: {settings.get('frequency', 'N/A')}\n"
                f"📊 Макс. постов: {settings.get('max_items_per_digest', 10)}\n\n"
                f"Выберите параметр для изменения:"
            )
            
            await callback.message.edit_text(
                text,
                reply_markup=_kb_digest_settings(settings.get("enabled", False)),
                parse_mode="HTML"
            )
            await callback.answer()
    
    except Exception as e:
        logger.error("Error showing digest settings", error=str(e))
        await callback.answer("❌ Ошибка загрузки настроек", show_alert=True)


@router.callback_query(F.data == "digest:toggle")
async def callback_digest_toggle(callback: CallbackQuery):
    """Переключить включение/выключение дайджеста."""
    user_id = await _get_user_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    try:
        # Получаем текущие настройки
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/api/digest/settings/{user_id}")
            if r.status_code != 200:
                await callback.answer("❌ Ошибка получения настроек", show_alert=True)
                return
            
            settings = r.json()
            new_enabled = not settings.get("enabled", False)
            
            # Проверяем наличие тем при включении
            if new_enabled and (not settings.get("topics") or len(settings.get("topics", [])) == 0):
                await callback.answer(
                    "⚠️ Для включения дайджеста необходимо указать хотя бы одну тему. "
                    "Используйте кнопку '📝 Темы'.",
                    show_alert=True
                )
                return
            
            # Обновляем настройки
            update_r = await client.put(
                f"{API_BASE}/api/digest/settings/{user_id}",
                json={"enabled": new_enabled}
            )
            
            if update_r.status_code == 200:
                await callback_digest_settings(callback)  # Обновляем отображение
                await callback.answer(f"✅ Дайджест {'включен' if new_enabled else 'выключен'}")
            else:
                error_detail = update_r.json().get("detail", "Неизвестная ошибка")
                await callback.answer(f"❌ Ошибка: {error_detail}", show_alert=True)
    
    except Exception as e:
        logger.error("Error toggling digest", error=str(e))
        await callback.answer("❌ Ошибка обновления настроек", show_alert=True)


@router.callback_query(F.data == "digest:edit_topics")
async def callback_digest_edit_topics(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование тем."""
    await state.set_state(DigestStates.waiting_topics)
    
    await callback.message.edit_text(
        "📝 <b>Редактирование тем</b>\n\n"
        "Укажите темы через запятую (например: AI, машинное обучение, нейросети).\n\n"
        "Отправьте список тем или /cancel для отмены:",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(DigestStates.waiting_topics)
async def process_topics(msg: Message, state: FSMContext):
    """Обработка ввода тем."""
    # Проверка на команду отмены
    if msg.text and msg.text.startswith("/cancel"):
        await state.clear()
        await msg.answer("❌ Редактирование тем отменено.")
        return
    
    user_id = await _get_user_id(msg.from_user.id)
    if not user_id:
        await msg.answer("❌ Пользователь не найден")
        await state.clear()
        return
    
    # Context7: Проверка на None перед использованием msg.text
    if not msg.text:
        await msg.answer("❌ Сообщение не содержит текста. Попробуйте еще раз:")
        return
    
    # Парсим темы (разделяем по запятой, очищаем от пробелов)
    topics = [t.strip() for t in msg.text.split(",") if t.strip()]
    
    if not topics:
        await msg.answer("❌ Список тем не может быть пустым. Попробуйте еще раз:")
        return
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{API_BASE}/api/digest/settings/{user_id}",
                json={"topics": topics}
            )
            
            if r.status_code == 200:
                await msg.answer(
                    f"✅ <b>Темы обновлены!</b>\n\n"
                    f"Добавлено тем: {len(topics)}\n"
                    f"Темы: {', '.join(topics[:5])}"
                    f"{'...' if len(topics) > 5 else ''}",
                    parse_mode="HTML"
                )
                await state.clear()
            else:
                error_detail = r.json().get("detail", "Неизвестная ошибка")
                await msg.answer(f"❌ Ошибка: {error_detail}")
    
    except Exception as e:
        logger.error("Error updating topics", error=str(e))
        await msg.answer("❌ Ошибка обновления тем")
    
    await state.clear()


@router.callback_query(F.data == "digest:edit_time")
async def callback_digest_edit_time(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование времени отправки."""
    await state.set_state(DigestStates.waiting_schedule_time)
    
    await callback.message.edit_text(
        "⏰ <b>Время отправки</b>\n\n"
        "Укажите время в формате HH:MM (например: 09:00 или 18:30).\n\n"
        "Отправьте время или /cancel для отмены:",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(DigestStates.waiting_schedule_time)
async def process_schedule_time(msg: Message, state: FSMContext):
    """Обработка ввода времени."""
    # Проверка на команду отмены
    if msg.text and msg.text.startswith("/cancel"):
        await state.clear()
        await msg.answer("❌ Редактирование времени отменено.")
        return
    
    user_id = await _get_user_id(msg.from_user.id)
    if not user_id:
        await msg.answer("❌ Пользователь не найден")
        await state.clear()
        return
    
    # Context7: Проверка на None перед использованием msg.text
    if not msg.text:
        await msg.answer("❌ Сообщение не содержит текста. Попробуйте еще раз:")
        return
    
    # Валидация формата времени
    time_pattern = r'^([0-1][0-9]|2[0-3]):[0-5][0-9]$'
    import re
    if not re.match(time_pattern, msg.text):
        await msg.answer("❌ Неверный формат времени. Используйте формат HH:MM (например: 09:00).")
        return
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{API_BASE}/api/digest/settings/{user_id}",
                json={"schedule_time": msg.text}
            )
            
            if r.status_code == 200:
                await msg.answer(f"✅ Время отправки обновлено: {msg.text}")
                await state.clear()
            else:
                error_detail = r.json().get("detail", "Неизвестная ошибка")
                await msg.answer(f"❌ Ошибка: {error_detail}")
    
    except Exception as e:
        logger.error("Error updating schedule time", error=str(e))
        await msg.answer("❌ Ошибка обновления времени")
    
    await state.clear()


@router.callback_query(F.data == "digest:edit_frequency")
async def callback_digest_edit_frequency(callback: CallbackQuery):
    """Показать выбор частоты."""
    await callback.message.edit_text(
        "📅 <b>Частота отправки</b>\n\n"
        "Выберите частоту отправки дайджестов:",
        reply_markup=_kb_frequency_options(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("digest:frequency:"))
async def callback_digest_frequency(callback: CallbackQuery):
    """Обработка выбора частоты."""
    user_id = await _get_user_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    frequency = callback.data.split(":")[-1]  # daily, weekly, monthly
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{API_BASE}/api/digest/settings/{user_id}",
                json={"frequency": frequency}
            )
            
            if r.status_code == 200:
                frequency_text = {
                    "daily": "📅 Ежедневно",
                    "weekly": "📆 Еженедельно",
                    "monthly": "📊 Ежемесячно"
                }.get(frequency, frequency)
                
                await callback.answer(f"✅ Частота обновлена: {frequency_text}")
                await callback_digest_settings(callback)  # Обновляем отображение
            else:
                error_detail = r.json().get("detail", "Неизвестная ошибка")
                await callback.answer(f"❌ Ошибка: {error_detail}", show_alert=True)
    
    except Exception as e:
        logger.error("Error updating frequency", error=str(e))
        await callback.answer("❌ Ошибка обновления частоты", show_alert=True)


@router.callback_query(F.data == "digest:history")
async def callback_digest_history(callback: CallbackQuery):
    """Показать историю дайджестов."""
    user_id = await _get_user_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/api/digest/history/{user_id}?limit=5")
            if r.status_code != 200:
                await callback.answer("❌ Ошибка получения истории", show_alert=True)
                return
            
            history = r.json()
            
            if not history:
                await callback.message.edit_text(
                    "📰 <b>История дайджестов</b>\n\n"
                    "История пуста. Сгенерируйте первый дайджест!",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Сгенерировать", callback_data="digest:generate")],
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="digest:menu")]
                    ]),
                    parse_mode="HTML"
                )
                await callback.answer()
                return
            
            # Форматируем историю
            text = "📰 <b>История дайджестов</b>\n\n"
            for idx, item in enumerate(history[:5], 1):
                status_icon = "✅" if item.get("status") == "sent" else "⏳" if item.get("status") == "pending" else "❌"
                text += (
                    f"{idx}. {status_icon} {item.get('digest_date', 'N/A')}\n"
                    f"   Постов: {item.get('posts_count', 0)}\n"
                    f"   Темы: {', '.join(item.get('topics', [])[:3])}\n\n"
                )
            
            builder = InlineKeyboardBuilder()
            builder.button(text="🔄 Сгенерировать новый", callback_data="digest:generate")
            builder.button(text="🔙 Назад", callback_data="digest:menu")
            builder.adjust(1)
            
            await callback.message.edit_text(
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
            await callback.answer()
    
    except Exception as e:
        logger.error("Error showing digest history", error=str(e))
        await callback.answer("❌ Ошибка загрузки истории", show_alert=True)


@router.callback_query(F.data == "digest:generate")
async def callback_digest_generate(callback: CallbackQuery):
    """Сгенерировать дайджест немедленно."""
    user_id = await _get_user_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Context7: Защита от двойного нажатия через Redis lock
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_client = None
    lock_key = f"digest:lock:{user_id}"
    lock_acquired = False
    redis_client_closed = False  # Context7: Флаг для отслеживания закрытия клиента
    
    try:
        # Context7: Инициализируем redis_client в начале try блока для корректной обработки ошибок
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        
        # Пытаемся получить lock (TTL 5 минут)
        lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=300)
        if not lock_acquired:
            await callback.answer("⏳ Дайджест уже генерируется, подождите...", show_alert=True)
            # Context7: Закрываем клиент перед ранним возвратом
            if redis_client and not redis_client_closed:
                try:
                    await redis_client.close()
                    redis_client_closed = True  # Context7: Отмечаем, что клиент закрыт
                except Exception as e:
                    logger.warning("Failed to close Redis client on early return", error=str(e))
                    redis_client_closed = True  # Context7: Отмечаем как закрытый даже при ошибке
            return
        
        # Context7: Код генерации дайджеста перемещен в основной try блок для корректной работы
        # Показываем индикатор загрузки
        await callback.answer("⏳ Генерирую дайджест...")
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{API_BASE}/api/digest/generate/{user_id}")
            
            if r.status_code == 200:
                result = r.json()
                content = result.get("content")
                posts_count = result.get("posts_count", 0)
                topics = result.get("topics", [])
                status = result.get("status")
                digest_id = result.get("digest_id")

                if not content:
                    queued_at = result.get("queued_at")
                    queued_message = (
                        "🕒 <b>Дайджест поставлен в очередь</b>\n\n"
                        f"ID: <code>{digest_id}</code>\n"
                        f"Статус: <b>{status or 'scheduled'}</b>\n"
                    )
                    if queued_at:
                        queued_message += f"Поставлен: {queued_at}\n"
                    queued_message += "\nМы пришлём готовый дайджест отдельным сообщением."
                    await callback.message.answer(queued_message, parse_mode="HTML")
                    await callback.answer("✅ Дайджест поставлен в очередь")
                    # Context7: Не освобождаем lock здесь - finally блок освободит его автоматически
                    return
                
                from utils.telegram_formatter import markdown_to_telegram_chunks
                chunks = markdown_to_telegram_chunks(content)
                
                for idx, chunk in enumerate(chunks):
                    prefix = (
                        f"📰 <b>Дайджест готов!</b>\n\n"
                        f"📊 Постов: {posts_count}\n"
                        f"📝 Темы: {', '.join(topics[:5])}\n\n"
                        if idx == 0
                        else ""
                    )
                    await callback.message.answer(prefix + chunk, parse_mode="HTML")
                
                await callback.answer("✅ Дайджест сгенерирован")
            else:
                error_detail = r.json().get("detail", "Неизвестная ошибка")
                await callback.message.answer(
                    f"❌ <b>Ошибка генерации дайджеста</b>\n\n{error_detail}",
                    parse_mode="HTML"
                )
                await callback.answer("❌ Ошибка генерации", show_alert=True)
        except httpx.TimeoutException:
            await callback.message.answer(
                "⏳ <b>Генерация дайджеста занимает больше времени</b>\n\n"
                "Попробуйте позже или проверьте настройки дайджеста.",
                parse_mode="HTML"
            )
            await callback.answer("⏳ Превышено время ожидания", show_alert=True)
        except Exception as e:
            logger.error("Error generating digest", error=str(e))
            await callback.message.answer(
                "❌ <b>Ошибка генерации дайджеста</b>\n\n"
                "Проверьте настройки дайджеста (темы должны быть указаны).",
                parse_mode="HTML"
            )
            await callback.answer("❌ Ошибка генерации", show_alert=True)
        finally:
            # Освобождаем lock только если он был получен
            if redis_client and lock_acquired:
                try:
                    await redis_client.delete(lock_key)
                except Exception as e:
                    logger.warning("Failed to release digest lock", error=str(e), lock_key=lock_key)
    except Exception as redis_init_error:
        # Context7: Обрабатываем ошибки инициализации Redis клиента
        logger.error("Failed to initialize Redis client", error=str(redis_init_error))
        await callback.answer("❌ Ошибка подключения к Redis. Попробуйте позже.", show_alert=True)
        # Context7: redis_client может быть None, если инициализация не удалась
        redis_client = None
        redis_client_closed = True
        return
    finally:
        # Закрываем Redis client в любом случае, если он еще не закрыт
        if redis_client and not redis_client_closed:
            try:
                await redis_client.close()
                redis_client_closed = True
            except Exception as e:
                logger.warning("Failed to close Redis client", error=str(e))
                redis_client_closed = True

