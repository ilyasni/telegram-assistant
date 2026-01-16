"""Обработчики команд для управления подборками каналов."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx
import structlog
import math
import os
from typing import Optional

logger = structlog.get_logger()
router = Router()

# API base URL
API_BASE = "http://api:8000"
THEMES_PAGE_SIZE = max(1, int(os.getenv("THEMES_PAGE_SIZE", "10")))


def _kb_themes_list(themes: list, subscribed_theme_ids: set, page: int = 0, page_size: int = THEMES_PAGE_SIZE):
    """Клавиатура со списком подборок с пагинацией."""
    builder = InlineKeyboardBuilder()
    
    # Пагинация
    total_themes = len(themes)
    total_pages = max(1, math.ceil(total_themes / page_size))
    current_page = max(0, min(page, total_pages - 1))
    start = current_page * page_size
    end = start + page_size
    page_themes = themes[start:end]
    
    # Кнопки подборок на текущей странице
    for theme in page_themes:
        theme_id = str(theme['id'])  # Context7: Явное преобразование в строку
        is_subscribed = theme_id in subscribed_theme_ids
        status_icon = "✅" if is_subscribed else "➕"
        channels_count = theme.get('channels_count', 0)
        builder.button(
            text=f"{status_icon} {theme['name']} ({channels_count} каналов)",
            callback_data=f"theme:view:{theme['slug']}"
        )
    
    # Навигация по страницам
    if total_pages > 1:
        nav_buttons = []
        if current_page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"themes:page:{current_page - 1}"
                )
            )
        if current_page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперёд ➡️",
                    callback_data=f"themes:page:{current_page + 1}"
                )
            )
        if nav_buttons:
            builder.row(*nav_buttons)
    
    # Дополнительные кнопки
    builder.button(text="🔄 Обновить список", callback_data="themes:refresh")
    builder.button(text="📚 Мои подборки", callback_data="themes:my_themes")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def _kb_theme_actions(theme_slug: str, is_subscribed: bool):
    """Клавиатура действий с подборкой."""
    buttons = []
    if is_subscribed:
        buttons.append([InlineKeyboardButton(text="❌ Отключить", callback_data=f"theme:unsubscribe:{theme_slug}")])
    else:
        buttons.append([InlineKeyboardButton(text="✅ Подключить", callback_data=f"theme:subscribe:{theme_slug}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к списку", callback_data="themes:list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _kb_confirm_unsubscribe(theme_slug: str):
    """Клавиатура подтверждения отключения подборки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, отключить", callback_data=f"theme:unsubscribe_confirm:{theme_slug}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"theme:view:{theme_slug}")],
    ])


@router.message(Command("themes"))
async def cmd_themes(msg: Message, page: int = 0):
    """Обработчик команды /themes — показывает список доступных подборок."""
    await _show_themes_list(msg, page)


async def _show_themes_list(msg: Message, page: int = 0):
    """Показывает список подборок с пагинацией."""
    try:
        logger.info("Themes command received", user_id=msg.from_user.id, page=page)
        
        async with httpx.AsyncClient(timeout=10) as client:
            # Context7: Получаем все подборки с максимальным лимитом (500)
            # Context7: Если нужно больше, делаем несколько запросов с пагинацией
            themes_resp = await client.get(f"{API_BASE}/api/themes?limit=500")
            
            if themes_resp.status_code != 200:
                error_detail = ""
                try:
                    error_data = themes_resp.json()
                    error_detail = error_data.get("detail", "")
                except:
                    pass
                
                logger.error(
                    "Failed to load themes",
                    status_code=themes_resp.status_code,
                    error_detail=error_detail,
                    user_id=msg.from_user.id
                )
                
                await msg.answer(
                    "❌ <b>Ошибка</b>\n\n"
                    "Не удалось загрузить список подборок. Попробуйте позже.",
                    parse_mode="HTML"
                )
                return
            
            themes_data = themes_resp.json()
            themes = themes_data.get("themes", [])
            total_themes = themes_data.get("total", 0)
            
            # Context7: Если есть больше подборок, чем лимит, делаем дополнительные запросы
            # Context7: Используем пагинацию для получения всех подборок
            if total_themes > 500:
                logger.info(
                    "Fetching additional themes pages",
                    total=total_themes,
                    user_id=msg.from_user.id
                )
                # Запрашиваем остальные страницы
                for offset in range(500, total_themes, 500):
                    try:
                        additional_resp = await client.get(
                            f"{API_BASE}/api/themes?limit=500&offset={offset}",
                            timeout=10
                        )
                        if additional_resp.status_code == 200:
                            additional_data = additional_resp.json()
                            themes.extend(additional_data.get("themes", []))
                        else:
                            logger.warning(
                                "Failed to fetch additional themes page",
                                offset=offset,
                                status_code=additional_resp.status_code
                            )
                            break
                    except Exception as e:
                        logger.warning(
                            "Failed to fetch additional themes page",
                            offset=offset,
                            error=str(e)
                        )
                        break
            
            if not themes:
                await msg.answer(
                    "📚 <b>Подборки каналов</b>\n\n"
                    "Пока нет доступных подборок. Они появятся после синхронизации.",
                    parse_mode="HTML"
                )
                return
            
            # Получаем список подключенных подборок
            subscribed_theme_ids = set()
            try:
                subscribed_resp = await client.get(
                    f"{API_BASE}/api/themes/users/{msg.from_user.id}/subscribed"
                )
                if subscribed_resp.status_code == 200:
                    subscribed_data = subscribed_resp.json()
                    subscribed_themes = subscribed_data.get("themes", [])
                    # Context7: Явное преобразование theme_id в строку для сравнения
                    subscribed_theme_ids = {str(t["theme_id"]) for t in subscribed_themes}
                    logger.info(
                        "Loaded subscribed themes",
                        user_id=msg.from_user.id,
                        subscribed_count=len(subscribed_theme_ids),
                        subscribed_ids=list(subscribed_theme_ids),
                        subscribed_ids_types=[type(sid).__name__ for sid in list(subscribed_theme_ids)[:2]] if subscribed_theme_ids else []
                    )
            except Exception as e:
                logger.warning("Failed to load subscribed themes", error=str(e))
            
            # Context7: Пагинация для отображения в боте
            # Используем общее количество из API, а не длину списка (может быть неполным)
            total_themes_for_pagination = total_themes if total_themes > 0 else len(themes)
            total_pages = max(1, math.ceil(total_themes_for_pagination / THEMES_PAGE_SIZE))
            current_page = max(0, min(page, total_pages - 1))
            start = current_page * THEMES_PAGE_SIZE
            end = start + THEMES_PAGE_SIZE
            page_themes = themes[start:end]
            
            # Формируем сообщение
            text = "📚 <b>Подборки каналов</b>\n\n"
            text += "Выберите подборку для подключения:\n\n"
            
            for theme in page_themes:
                theme_id = str(theme['id'])  # Context7: Явное преобразование в строку
                is_subscribed = theme_id in subscribed_theme_ids
                status = "✅ Подключено" if is_subscribed else "➕ Доступно"
                channels_count = theme.get('channels_count', 0)
                text += f"{status} <b>{theme['name']}</b>\n"
                text += f"   📺 {channels_count} каналов\n\n"
                # Context7: Логирование для отладки сравнения ID
                if theme.get('slug') in ['diy-mens', 'startup']:
                    logger.info(
                        "Theme subscription check",
                        user_id=msg.from_user.id,
                        theme_id=theme_id,
                        theme_id_type=type(theme_id).__name__,
                        theme_slug=theme.get('slug'),
                        is_subscribed=is_subscribed,
                        subscribed_theme_ids=list(subscribed_theme_ids),
                        subscribed_theme_ids_types=[type(sid).__name__ for sid in list(subscribed_theme_ids)[:2]] if subscribed_theme_ids else []
                    )
            
            if total_pages > 1:
                text += f"\n<i>Страница {current_page + 1} из {total_pages}. Всего подборок: {total_themes_for_pagination}</i>"
            
            await msg.answer(
                text,
                parse_mode="HTML",
                reply_markup=_kb_themes_list(themes, subscribed_theme_ids, page=current_page)
            )
            
    except Exception as e:
        logger.error(
            "Error in /themes command",
            error=str(e),
            user_id=msg.from_user.id,
            exc_info=True
        )
        await msg.answer("❌ Произошла ошибка при загрузке подборок. Попробуйте позже.")


@router.message(Command("my_themes"))
async def cmd_my_themes(msg: Message):
    """Обработчик команды /my_themes — показывает подключенные подборки."""
    try:
        logger.info("My themes command received", user_id=msg.from_user.id)
        
        async with httpx.AsyncClient(timeout=10) as client:
            url = f"{API_BASE}/api/themes/users/{msg.from_user.id}/subscribed"
            logger.info("Requesting subscribed themes", user_id=msg.from_user.id, url=url)
            
            resp = await client.get(url)
            
            logger.info(
                "API response received",
                user_id=msg.from_user.id,
                status_code=resp.status_code,
                response_text=resp.text[:500] if len(resp.text) > 0 else "empty"
            )
            
            # Обрабатываем случай, когда подборок нет (200 с пустым списком - это нормально)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as e:
                    logger.error(
                        "Failed to parse JSON response",
                        user_id=msg.from_user.id,
                        error=str(e),
                        response_text=resp.text[:500]
                    )
                    await msg.answer("❌ Ошибка при обработке ответа. Попробуйте позже.")
                    return
                
                themes = data.get("themes", [])
                
                # Context7: Явная проверка типа и содержимого
                logger.info(
                    "My themes API response parsed",
                    user_id=msg.from_user.id,
                    status_code=resp.status_code,
                    themes_count=len(themes),
                    themes_type=type(themes).__name__,
                    themes_is_empty=not themes,
                    themes_bool=bool(themes),
                    themes_data=themes if len(themes) <= 2 else f"{len(themes)} themes",
                    raw_data_keys=list(data.keys()) if data else [],
                    data_total=data.get("total", "N/A"),
                    data_themes_key_exists="themes" in data if data else False
                )
                
                # Context7: Дополнительная проверка - может быть themes это не список?
                if themes is None:
                    logger.warning("Themes is None, treating as empty", user_id=msg.from_user.id)
                    themes = []
                
                if not themes or len(themes) == 0:
                    # Показываем нормальное сообщение с кнопкой для просмотра доступных подборок
                    builder = InlineKeyboardBuilder()
                    builder.button(text="📚 Все подборки", callback_data="themes:list")
                    builder.button(text="🔙 Главное меню", callback_data="menu:main")
                    builder.adjust(1)
                    
                    await msg.answer(
                        "📚 <b>Мои подборки</b>\n\n"
                        "У вас пока нет подключенных подборок.\n\n"
                        "Используйте /themes для просмотра доступных подборок и их подключения.",
                        parse_mode="HTML",
                        reply_markup=builder.as_markup()
                    )
                    return
                
                # Если есть подборки - показываем их
                text = "📚 <b>Мои подборки</b>\n\n"
                for theme in themes:
                    text += f"✅ <b>{theme['theme_name']}</b>\n"
                    text += f"   Подключено: {theme['subscribed_at'][:10]}\n\n"
                
                builder = InlineKeyboardBuilder()
                for theme in themes:
                    builder.button(
                        text=f"📚 {theme['theme_name']}",
                        callback_data=f"theme:view:{theme['theme_slug']}"
                    )
                builder.button(text="📚 Все подборки", callback_data="themes:list")
                builder.button(text="🔙 Главное меню", callback_data="menu:main")
                builder.adjust(1)
                
                await msg.answer(
                    text,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
                return
            
            # Только для реальных ошибок показываем сообщение об ошибке
            if resp.status_code == 404:
                # Пользователь не найден - это тоже нормально, показываем пустой список
                builder = InlineKeyboardBuilder()
                builder.button(text="📚 Все подборки", callback_data="themes:list")
                builder.button(text="🔙 Главное меню", callback_data="menu:main")
                builder.adjust(1)
                
                await msg.answer(
                    "📚 <b>Мои подборки</b>\n\n"
                    "У вас пока нет подключенных подборок.\n\n"
                    "Используйте /themes для просмотра доступных подборок и их подключения.",
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
                return
            
            # Для других ошибок показываем сообщение об ошибке
            logger.warning(
                "Failed to load subscribed themes",
                user_id=msg.from_user.id,
                status_code=resp.status_code
            )
            await msg.answer(
                "❌ <b>Ошибка</b>\n\n"
                "Не удалось загрузить ваши подборки. Попробуйте позже.",
                parse_mode="HTML"
            )
            return
            
    except Exception as e:
        logger.error(
            "Error in /my_themes command",
            error=str(e),
            user_id=msg.from_user.id,
            exc_info=True
        )
        await msg.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data.startswith("themes:"))
async def handle_themes_callback(cb: CallbackQuery):
    """Обработчик callback для управления подборками."""
    try:
        parts = cb.data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        
        if action == "list":
            # Показываем список подборок (первая страница)
            await _show_themes_list(cb.message, page=0)
            await cb.answer()
        elif action == "page":
            # Переключение страницы
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            await _show_themes_list(cb.message, page=page)
            await cb.answer()
        elif action == "refresh":
            # Обновляем список
            await cb.answer("🔄 Обновление...")
            await _show_themes_list(cb.message, page=0)
        elif action == "my_themes":
            # Показываем подключенные подборки
            await cmd_my_themes(cb.message)
            await cb.answer()
        else:
            await cb.answer("❌ Неизвестное действие")
            
    except Exception as e:
        logger.error(
            "Error in themes callback",
            error=str(e),
            callback_data=cb.data,
            exc_info=True
        )
        await cb.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("theme:view:"))
async def handle_theme_view(cb: CallbackQuery):
    """Просмотр информации о подборке."""
    try:
        theme_slug = cb.data.split(":", 2)[2]
        
        async with httpx.AsyncClient(timeout=10) as client:
            # Получаем информацию о подборке
            theme_resp = await client.get(f"{API_BASE}/api/themes/{theme_slug}")
            
            if theme_resp.status_code != 200:
                await cb.answer("❌ Подборка не найдена", show_alert=True)
                return
            
            theme = theme_resp.json()
            
            # Проверяем, подключена ли подборка
            subscribed_resp = await client.get(
                f"{API_BASE}/api/themes/users/{cb.from_user.id}/subscribed"
            )
            is_subscribed = False
            if subscribed_resp.status_code == 200:
                subscribed_data = subscribed_resp.json()
                subscribed_themes = subscribed_data.get("themes", [])
                is_subscribed = any(t["theme_id"] == theme["id"] for t in subscribed_themes)
            
            text = f"📚 <b>{theme['name']}</b>\n\n"
            if theme.get('description'):
                text += f"{theme['description']}\n\n"
            text += f"📺 Каналов: {theme.get('channels_count', 0)}\n"
            text += f"Статус: {'✅ Подключено' if is_subscribed else '➕ Доступно'}\n\n"
            
            if is_subscribed:
                text += "ℹ️ При отключении подборки каналы из неё будут деактивированы, "
                text += "но каналы, подключённые вручную, останутся активными."
            else:
                text += "ℹ️ При подключении подборки все каналы из неё будут добавлены в ваши подписки."
            
            await cb.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=_kb_theme_actions(theme_slug, is_subscribed)
            )
            await cb.answer()
            
    except Exception as e:
        logger.error(
            "Error viewing theme",
            error=str(e),
            theme_slug=theme_slug if 'theme_slug' in locals() else None,
            exc_info=True
        )
        await cb.answer("❌ Ошибка при загрузке подборки", show_alert=True)


@router.callback_query(F.data.startswith("theme:subscribe:"))
async def handle_theme_subscribe(cb: CallbackQuery):
    """Подключение подборки."""
    theme_slug = None
    try:
        theme_slug = cb.data.split(":", 2)[2]
        endpoint = f"{API_BASE}/api/themes/{theme_slug}/subscribe/{cb.from_user.id}"
        
        # Context7: Логирование перед запросом
        logger.info(
            "Subscribing to theme via bot",
            theme_slug=theme_slug,
            user_id=cb.from_user.id,
            endpoint=endpoint
        )
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(endpoint)
            
            # Context7: Логирование ответа
            logger.info(
                "Theme subscription API response",
                theme_slug=theme_slug,
                user_id=cb.from_user.id,
                status_code=resp.status_code
            )
            
            if resp.status_code == 201:
                data = resp.json()
                
                # Context7: Обработка структурированного ответа
                channels_info = data.get("channels", {})
                channels_added = channels_info.get("added", 0)
                channels_reactivated = channels_info.get("reactivated", 0)
                channels_expected = channels_info.get("expected", 0)
                channels_failed = channels_info.get("failed", 0)
                channels_skipped_manual = channels_info.get("skipped_manual", 0)
                errors = data.get("errors")
                
                # Формируем понятное сообщение для пользователя
                total_processed = channels_added + channels_reactivated
                if total_processed == channels_expected:
                    # Все каналы успешно подключены
                    message = f"✅ Подборка подключена!\n"
                    if channels_added > 0:
                        message += f"Добавлено: {channels_added} каналов\n"
                    if channels_reactivated > 0:
                        message += f"Реактивировано: {channels_reactivated} каналов\n"
                    if channels_skipped_manual > 0:
                        message += f"Пропущено (уже подключены вручную): {channels_skipped_manual}"
                elif total_processed > 0:
                    # Частично подключено
                    message = f"⚠️ Подборка подключена частично:\n"
                    message += f"Подключено: {total_processed} из {channels_expected} каналов\n"
                    if channels_failed > 0:
                        message += f"Ошибок: {channels_failed}"
                else:
                    # Ничего не подключено
                    message = f"❌ Не удалось подключить подборку\n"
                    if channels_skipped_manual == channels_expected:
                        message = "ℹ️ Все каналы уже подключены вручную"
                    elif errors:
                        error_summary = errors[0].get("error", "Неизвестная ошибка")[:100]
                        message += f"Ошибка: {error_summary}"
                
                await cb.answer(message, show_alert=True)
                
                # Логируем детали для диагностики
                if errors:
                    logger.warning(
                        "Theme subscription completed with errors",
                        theme_slug=theme_slug,
                        user_id=cb.from_user.id,
                        errors_count=len(errors),
                        errors=errors[:3]  # Первые 3 ошибки для лога
                    )
                
                # Обновляем информацию о подборке
                await handle_theme_view(cb)
            elif resp.status_code == 409:
                await cb.answer("⚠️ Вы уже подключены к этой подборке", show_alert=True)
            elif resp.status_code == 404:
                error_data = resp.json() if resp.content else {}
                detail = error_data.get('detail', 'Подборка не найдена')
                await cb.answer(f"❌ {detail}", show_alert=True)
            elif resp.status_code in (500, 502, 503, 504):
                # Временная ошибка сервера
                error_data = resp.json() if resp.content else {}
                detail = error_data.get('detail', 'Временная ошибка сервера')
                logger.error(
                    "Theme subscription server error",
                    theme_slug=theme_slug,
                    user_id=cb.from_user.id,
                    status_code=resp.status_code,
                    error_detail=detail
                )
                await cb.answer("❌ Временная ошибка, повторите позже", show_alert=True)
            else:
                # Другие ошибки
                error_data = resp.json() if resp.content else {}
                error_body = resp.text[:500] if resp.text else "No error body"
                detail = error_data.get('detail', f'Ошибка {resp.status_code}')
                
                logger.warning(
                    "Theme subscription failed",
                    theme_slug=theme_slug,
                    user_id=cb.from_user.id,
                    status_code=resp.status_code,
                    error_detail=detail,
                    error_body=error_body
                )
                await cb.answer(f"❌ Ошибка: {detail}", show_alert=True)
                
    except httpx.TimeoutException:
        logger.error(
            "Theme subscription timeout",
            theme_slug=theme_slug,
            user_id=cb.from_user.id if 'cb' in locals() else None
        )
        await cb.answer("❌ Превышено время ожидания, повторите позже", show_alert=True)
    except Exception as e:
        logger.error(
            "Error subscribing to theme",
            theme_slug=theme_slug,
            user_id=cb.from_user.id if 'cb' in locals() else None,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        await cb.answer("❌ Ошибка при подключении подборки", show_alert=True)


@router.callback_query(F.data.startswith("theme:unsubscribe:"))
async def handle_theme_unsubscribe(cb: CallbackQuery):
    """Подтверждение отключения подборки."""
    try:
        theme_slug = cb.data.split(":", 2)[2]
        
        # Показываем подтверждение
        text = (
            "❌ <b>Отключить подборку?</b>\n\n"
            "При отключении подборки каналы из неё будут деактивированы.\n"
            "Каналы, подключённые вручную, останутся активными."
        )
        
        await cb.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=_kb_confirm_unsubscribe(theme_slug)
        )
        await cb.answer()
        
    except Exception as e:
        logger.error(
            "Error in unsubscribe confirmation",
            error=str(e),
            exc_info=True
        )
        await cb.answer("❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("theme:unsubscribe_confirm:"))
async def handle_theme_unsubscribe_confirm(cb: CallbackQuery):
    """Отключение подборки после подтверждения."""
    try:
        theme_slug = cb.data.split(":", 2)[2]
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{API_BASE}/api/themes/{theme_slug}/unsubscribe/{cb.from_user.id}"
            )
            
            if resp.status_code == 200:
                data = resp.json()
                channels_deactivated = data.get("channels_deactivated", 0)
                await cb.answer(f"✅ Подборка отключена. Деактивировано каналов: {channels_deactivated}")
                # Обновляем информацию о подборке
                await handle_theme_view(cb)
            elif resp.status_code == 404:
                await cb.answer("❌ Подборка не найдена или не подключена", show_alert=True)
            else:
                error_data = resp.json() if resp.content else {}
                await cb.answer(f"❌ Ошибка: {error_data.get('detail', resp.status_code)}", show_alert=True)
                
    except Exception as e:
        logger.error(
            "Error unsubscribing from theme",
            error=str(e),
            theme_slug=theme_slug if 'theme_slug' in locals() else None,
            exc_info=True
        )
        await cb.answer("❌ Ошибка при отключении подборки", show_alert=True)


async def _get_user_id(telegram_id: int) -> Optional[str]:
    """Получает user_id (UUID) для Telegram пользователя."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_BASE}/api/users/{telegram_id}")
            if resp.status_code == 200:
                user_data = resp.json()
                return user_data.get("id")
            return None
    except Exception as e:
        logger.error("Failed to get user_id", telegram_id=telegram_id, error=str(e))
        return None
