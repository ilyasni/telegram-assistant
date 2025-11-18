"""
Обработчики Telegram-бота для управления группами и discovery-пайплайном.
Следует Context7: защищённые вызовы API, явное логирование, UX-паттерны.
"""

from __future__ import annotations

import asyncio
import html
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import httpx
import structlog
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = structlog.get_logger()
router = Router()

# Context7: базовый URL API централизован в одном месте
API_BASE = "http://api:8000"
DISCOVERY_PAGE_SIZE = max(1, int(os.getenv("GROUP_DISCOVERY_PAGE_SIZE", "6")))
GROUP_DIGEST_PAGE_SIZE = max(1, int(os.getenv("GROUP_DIGEST_PAGE_SIZE", "5")))
GROUP_DIGEST_WINDOWS: Tuple[int, ...] = (4, 6, 12, 24)
_default_window_env = os.getenv("GROUP_DIGEST_DEFAULT_WINDOW", "12")
try:
    _default_group_window = int(_default_window_env)
except ValueError:
    _default_group_window = 12
if _default_group_window not in GROUP_DIGEST_WINDOWS:
    _default_group_window = 12
DEFAULT_GROUP_DIGEST_WINDOW = _default_group_window


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ КЛИЕНТЫ
# ============================================================================

async def _get_user_context(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает словарь с user_id и tenant_id для Telegram пользователя."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{API_BASE}/api/users/{telegram_id}")
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Failed to fetch user context",
                telegram_id=telegram_id,
                status_code=resp.status_code,
                response=resp.text[:200],
            )
    except Exception as exc:
        logger.error("User context fetch failed", telegram_id=telegram_id, error=str(exc))
    return None


async def _fetch_groups(tenant_id: str) -> Optional[Dict[str, Any]]:
    """Загружает группы арендатора."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API_BASE}/api/groups/",
                params={"tenant_id": tenant_id, "limit": 50, "offset": 0},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Failed to fetch tenant groups",
                tenant_id=tenant_id,
                status_code=resp.status_code,
                response=resp.text[:200],
            )
    except Exception as exc:
        logger.error("Tenant groups fetch failed", tenant_id=tenant_id, error=str(exc))
    return None


async def _fetch_all_groups(tenant_id: str, page_size: int = 50) -> List[Dict[str, Any]]:
    """
    Возвращает все группы арендатора без ограничения пагинацией.
    Context7: мягкий backoff между запросами, чтобы не перегружать API.
    """
    collected: List[Dict[str, Any]] = []
    offset = 0
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                resp = await client.get(
                    f"{API_BASE}/api/groups/",
                    params={
                        "tenant_id": tenant_id,
                        "limit": page_size,
                        "offset": offset,
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Failed to fetch tenant groups page",
                        tenant_id=tenant_id,
                        status_code=resp.status_code,
                        offset=offset,
                        response=resp.text[:200],
                    )
                    break

                payload = resp.json()
                batch = payload.get("groups") or []
                collected.extend(batch)

                total = int(payload.get("total", len(collected)))
                offset += page_size

                if offset >= total:
                    break

                await asyncio.sleep(0.05)
    except Exception as exc:
        logger.error(
            "Tenant groups fetch failed (paginated)",
            tenant_id=tenant_id,
            error=str(exc),
        )
    return collected


def _resolve_group_default_window(group: Dict[str, Any]) -> int:
    """Определяет окно дайджеста для группы с учётом настроек."""
    settings = group.get("settings") or {}
    digest_settings = settings.get("digest") or {}
    raw_window = digest_settings.get("default_window_hours")
    if isinstance(raw_window, int) and raw_window in GROUP_DIGEST_WINDOWS:
        return raw_window
    return DEFAULT_GROUP_DIGEST_WINDOW


async def _load_group_digest_groups(
    state: FSMContext,
    tenant_id: str,
    force_refresh: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Возвращает список групп и карту по id из FSM-состояния,
    при необходимости перезапрашивает из API.
    """
    data = await state.get_data()
    groups: Optional[List[Dict[str, Any]]] = None
    groups_map: Optional[Dict[str, Dict[str, Any]]] = None

    if not force_refresh:
        groups = data.get("group_digest_groups")
        groups_map = data.get("group_digest_groups_map")

    if groups is None or groups_map is None or force_refresh:
        groups = await _fetch_all_groups(tenant_id)
        groups_map = {str(group.get("id")): group for group in groups}
        await state.update_data(
            group_digest_groups=groups,
            group_digest_groups_map=groups_map,
        )

    return groups, groups_map


async def _create_discovery_request(tenant_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Создаёт discovery-запрос для поиска групп."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{API_BASE}/api/groups/discovery",
                json={"tenant_id": tenant_id, "user_id": user_id},
            )
            if resp.status_code in (200, 202):
                return resp.json()
            logger.warning(
                "Failed to create discovery request",
                tenant_id=tenant_id,
                user_id=user_id,
                status_code=resp.status_code,
                response=resp.text[:200],
            )
    except Exception as exc:
        logger.error("Discovery request creation failed", tenant_id=tenant_id, user_id=user_id, error=str(exc))
    return None


async def _fetch_discovery(request_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """Получает состояние discovery по request_id."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API_BASE}/api/groups/discovery/{request_id}",
                params={"tenant_id": tenant_id},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Failed to fetch discovery state",
                request_id=request_id,
                tenant_id=tenant_id,
                status_code=resp.status_code,
                response=resp.text[:200],
            )
    except Exception as exc:
        logger.error("Discovery state fetch failed", request_id=request_id, tenant_id=tenant_id, error=str(exc))
    return None


async def _fetch_latest_discovery(tenant_id: str) -> Optional[Dict[str, Any]]:
    """Получает последний discovery-запрос по арендатора."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API_BASE}/api/groups/discovery/latest",
                params={"tenant_id": tenant_id},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Failed to fetch latest discovery",
                tenant_id=tenant_id,
                status_code=resp.status_code,
                response=resp.text[:200],
            )
    except Exception as exc:
        logger.error("Latest discovery fetch failed", tenant_id=tenant_id, error=str(exc))
    return None


async def _connect_group(
    tenant_id: str,
    candidate: Dict[str, Any],
    requested_by: str,
) -> Tuple[bool, str]:
    """Подключает найденную группу через Groups API."""
    payload = {
        "tenant_id": tenant_id,
        "tg_chat_id": candidate.get("tg_chat_id"),
        "title": candidate.get("title") or "Без названия",
        "username": candidate.get("username"),
        "invite_link": candidate.get("invite_link"),
        "settings": {
            "source": "bot",
            "discovery": {
                "request_id": candidate.get("request_id"),
                "requested_by": requested_by,
                "category": candidate.get("category"),
                "is_channel": candidate.get("is_channel"),
                "is_broadcast": candidate.get("is_broadcast"),
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{API_BASE}/api/groups/", json=payload)
            if resp.status_code in (200, 201):
                return True, "Группа подключена"
            if resp.status_code == 409:
                return True, "Группа уже подключена"
            message = resp.text.strip() or resp.reason_phrase or "Неизвестная ошибка API"
            try:
                error_payload = resp.json()
                if isinstance(error_payload, dict):
                    detail = error_payload.get("detail") or error_payload.get("message")
                    if detail:
                        message = str(detail)
            except Exception:
                pass
            logger.warning(
                "Group connect API returned error",
                tenant_id=tenant_id,
                status_code=resp.status_code,
                response=resp.text[:500],
            )
            return False, message
    except Exception as exc:
        logger.error(
            "Group connect failed",
            tenant_id=tenant_id,
            tg_chat_id=candidate.get("tg_chat_id"),
            error=str(exc),
        )
        return False, str(exc)


# ============================================================================
# UX-ХЕЛПЕРЫ
# ============================================================================

def _render_groups_text(groups: List[Dict[str, Any]]) -> str:
    if not groups:
        return (
            "👥 <b>Группы не подключены</b>\n\n"
            "Запусти поиск, чтобы увидеть доступные чаты и выбрать нужные."
        )

    lines = ["👥 <b>Мои группы</b>\n"]
    for idx, group in enumerate(groups, start=1):
        title = html.escape(group.get("title") or "Без названия")
        username = group.get("username")
        status = "🟢" if group.get("is_active") else "⚪️"
        lines.append(f"{idx}. {status} <b>{title}</b>")
        if username:
            lines.append(f"    @{html.escape(username)}")
        if group.get("settings"):
            default_window = group["settings"].get("digest", {}).get("default_window_hours")
            if default_window:
                lines.append(f"    ⏱ Окно дайджеста: {default_window} ч.")
    lines.append("\nВыбери действие ниже, чтобы запустить discovery или обновить список.")
    return "\n".join(lines)


def _groups_menu_keyboard(has_groups: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Найти новые группы", callback_data="groups:discover")
    builder.button(text="🔄 Обновить список", callback_data="groups:refresh")
    if has_groups:
        builder.button(text="📰 Дайджесты групп", callback_data="group_digest:menu")
    builder.button(text="🔙 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def _render_group_digest_menu(
    groups: List[Dict[str, Any]],
    page: int = 0,
    page_size: int = GROUP_DIGEST_PAGE_SIZE,
) -> str:
    if not groups:
        return (
            "📰 <b>Групповые дайджесты</b>\n\n"
            "Пока не подключено ни одной группы. Добавь группы через discovery, "
            "а затем вернись к этому разделу для генерации дайджестов."
        )

    total = len(groups)
    total_pages = max(1, math.ceil(total / page_size))
    current_page = max(0, min(page, total_pages - 1))
    start = current_page * page_size
    end = start + page_size
    page_items = groups[start:end]

    lines = ["📰 <b>Групповые дайджесты</b>\n"]
    for idx, group in enumerate(page_items, start=start + 1):
        title = html.escape(group.get("title") or "Без названия")
        username = group.get("username")
        status = "🟢" if group.get("is_active") else "⚪️"
        default_window = _resolve_group_default_window(group)
        lines.append(f"{idx}. {status} <b>{title}</b>")
        if username:
            lines.append(f"    @{html.escape(username)}")
        lines.append(f"    ⏱ Окно по умолчанию: {default_window} ч.")

    lines.append(
        f"\nСтраница {current_page + 1} из {total_pages}. "
        f"Групп в списке: {total}."
    )
    lines.append("Выбери группу ниже, чтобы настроить окно и запустить дайджест.")
    return "\n".join(lines)


def _group_digest_menu_keyboard(
    groups: List[Dict[str, Any]],
    page: int = 0,
    page_size: int = GROUP_DIGEST_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(groups)
    total_pages = max(1, math.ceil(total / page_size))
    current_page = max(0, min(page, total_pages - 1))
    start = current_page * page_size
    end = start + page_size
    page_items = groups[start:end]

    for group in page_items:
        group_id = str(group.get("id"))
        title = group.get("title") or group.get("username") or "Группа"
        short_title = title[:40]
        builder.button(text=f"📰 {short_title}", callback_data=f"gdigest:view:{group_id}")
    if page_items:
        builder.adjust(1)

    if total_pages > 1:
        nav_buttons: List[InlineKeyboardButton] = []
        if current_page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"group_digest:page:{current_page - 1}",
                )
            )
        if current_page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперёд ➡️",
                    callback_data=f"group_digest:page:{current_page + 1}",
                )
            )
        if nav_buttons:
            builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="group_digest:refresh"))
    builder.row(InlineKeyboardButton(text="📋 Мои группы", callback_data="menu:groups"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


def _render_group_digest_detail(
    group: Dict[str, Any],
    selected_window: int,
    last_result: Optional[Dict[str, Any]] = None,
) -> str:
    title = html.escape(group.get("title") or "Без названия")
    username = group.get("username")
    tg_chat_id = group.get("tg_chat_id")
    default_window = _resolve_group_default_window(group)

    lines = ["📰 <b>Дайджест группы</b>\n"]
    lines.append(f"<b>{title}</b>")
    if username:
        lines.append(f"@{html.escape(username)}")
    if tg_chat_id:
        lines.append(f"ID чата: <code>{tg_chat_id}</code>")
    lines.append("")
    lines.append(f"Выбранное окно: {selected_window} ч.")
    if default_window != selected_window:
        lines.append(f"Окно по умолчанию: {default_window} ч.")
    lines.append("Настрой окно и нажми «🚀 Сгенерировать», чтобы получить дайджест.")

    if last_result:
        lines.append("\n<b>Последний запуск</b>")
        status = last_result.get("status", "queued")
        lines.append(f"Статус: {status}")
        if last_result.get("requested_at"):
            lines.append(f"Запрошено: {last_result['requested_at']}")
        msg_count = last_result.get("message_count")
        if msg_count is not None:
            lines.append(f"Сообщений в окне: {msg_count}")
        participants = last_result.get("participant_count")
        if participants is not None:
            lines.append(f"Участников: {participants}")
        history_id = last_result.get("history_id")
        if history_id:
            lines.append(f"History ID: <code>{history_id}</code>")
        window_id = last_result.get("group_window_id")
        if window_id:
            lines.append(f"Window ID: <code>{window_id}</code>")

    return "\n".join(lines)


def _group_digest_detail_keyboard(group_id: str, selected_window: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for window in GROUP_DIGEST_WINDOWS:
        prefix = "✅" if window == selected_window else "🕒"
        builder.button(
            text=f"{prefix} {window} ч.",
            callback_data=f"gdigest:window:{group_id}:{window}",
        )
    builder.adjust(2, 2)
    builder.row(
        InlineKeyboardButton(
            text="🚀 Сгенерировать",
            callback_data=f"gdigest:trigger:{group_id}:{selected_window}",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="group_digest:menu"))
    return builder.as_markup()


def _render_discovery_text(
    data: Dict[str, Any],
    page: int = 0,
    page_size: int = DISCOVERY_PAGE_SIZE,
) -> str:
    status = data.get("status")
    header = "🔍 <b>Результаты поиска групп</b>\n"
    if status == "pending":
        return header + "\n⏳ Поиск в очереди. Подожди немного и обнови статус."
    if status == "processing":
        return header + "\n⚙️ Поиск выполняется. Я пришлю результат, как только он будет готов."
    if status == "failed":
        reason = html.escape(data.get("error") or "не указана")
        return header + f"\n❌ Поиск завершился ошибкой.\nПричина: {reason}"

    results = data.get("results") or []
    if not results:
        return header + "\nℹ️ Группы не найдены. Проверь, что бот добавлен в нужные чаты."

    lines = [header]
    total_results = len(results)
    total_pages = max(1, math.ceil(total_results / page_size))
    current_page = max(0, min(page, total_pages - 1))
    start = current_page * page_size
    end = start + page_size
    page_items = results[start:end]

    for idx, item in enumerate(page_items, start=start + 1):
        title = html.escape(item.get("title") or "Без названия")
        username = item.get("username")
        connected = item.get("is_connected")
        status_emoji = "✅" if connected else "➕"
        privacy = "🔒" if item.get("is_private") else "🌐"
        category = item.get("category") or ("channel" if item.get("is_channel") else "group")
        category_emoji = "📣" if category == "channel" else "👥"
        lines.append(f"{idx}. {status_emoji} {privacy}{category_emoji} <b>{title}</b>")
        if username:
            lines.append(f"    @{html.escape(username)}")
        participants = item.get("participants_count")
        if participants:
            lines.append(f"    👥 {participants} участников")
        if category == "channel" and item.get("is_broadcast"):
            lines.append("    📡 Формат: канал (только админы пишут)")
        if connected:
            lines.append("    Уже подключена к системе")
    lines.append(
        f"\nСтраница {current_page + 1} из {total_pages}. "
        f"Всего доступных чатов: {total_results}."
    )
    lines.append("Выбери группу в списке ниже, чтобы подключить её.")
    return "\n".join(lines)


def _discovery_keyboard(
    data: Dict[str, Any],
    request_id: str,
    page: int = 0,
    page_size: int = DISCOVERY_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    results = data.get("results") or []

    total_results = len(results)
    total_pages = max(1, math.ceil(total_results / page_size))
    current_page = max(0, min(page, total_pages - 1))
    start = current_page * page_size
    end = start + page_size
    page_items = results[start:end]

    for item in page_items:
        if item.get("is_connected"):
            continue
        title = item.get("title") or item.get("username") or "Группа"
        category = item.get("category") or ("channel" if item.get("is_channel") else "group")
        prefix = "📣" if category == "channel" else "👥"
        short_title = title[:32]
        callback_data = f"gconn:{request_id}:{item.get('tg_chat_id')}"
        builder.button(text=f"➕ {prefix} {short_title}", callback_data=callback_data)
    builder.adjust(1)

    if total_pages > 1:
        nav_buttons: List[InlineKeyboardButton] = []
        if current_page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"gdisc:page:{request_id}:{current_page - 1}",
                )
            )
        if current_page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперёд ➡️",
                    callback_data=f"gdisc:page:{request_id}:{current_page + 1}",
                )
            )
        if nav_buttons:
            builder.row(*nav_buttons)

    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"gdisc:refresh:{request_id}:{current_page}",
        )
    )
    builder.row(InlineKeyboardButton(text="📋 Мои группы", callback_data="groups:refresh"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main"))
    return builder.as_markup()


# ============================================================================
# КОМАНДЫ
# ============================================================================

@router.message(Command("groups"))
@router.message(Command("my_groups"))
async def cmd_groups(msg: Message, state: FSMContext):
    """Показывает список подключённых групп и действия."""
    user_ctx = await _get_user_context(msg.from_user.id)
    if not user_ctx:
        await msg.answer("❌ Пользователь не найден. Используй /start для регистрации.")
        return

    tenant_id = str(user_ctx["tenant_id"])
    groups_payload = await _fetch_groups(tenant_id)
    groups = groups_payload.get("groups", []) if groups_payload else []

    await msg.answer(
        _render_groups_text(groups),
        parse_mode="HTML",
        reply_markup=_groups_menu_keyboard(bool(groups)),
    )


@router.message(Command("group_digest"))
async def cmd_group_digest(msg: Message, state: FSMContext):
    """Открывает меню генерации групповых дайджестов."""
    user_ctx = await _get_user_context(msg.from_user.id)
    if not user_ctx:
        await msg.answer("❌ Пользователь не найден. Используй /start для регистрации.")
        return

    tenant_id = str(user_ctx["tenant_id"])
    user_id = str(user_ctx["id"])

    groups = await _fetch_all_groups(tenant_id)
    groups_map = {str(group.get("id")): group for group in groups}

    await state.update_data(
        group_digest_tenant_id=tenant_id,
        group_digest_user_id=user_id,
        group_digest_groups=groups,
        group_digest_groups_map=groups_map,
        group_digest_current_page=0,
    )

    text = _render_group_digest_menu(groups, page=0)
    keyboard = _group_digest_menu_keyboard(groups, page=0)

    await msg.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("group_discovery"))
@router.message(Command("groups_discovery"))
async def cmd_group_discovery(msg: Message, state: FSMContext):
    """Запускает discovery по пользовательским чатам."""
    user_ctx = await _get_user_context(msg.from_user.id)
    if not user_ctx:
        await msg.answer("❌ Пользователь не найден. Используй /start для регистрации.")
        return

    tenant_id = str(user_ctx["tenant_id"])
    user_id = str(user_ctx["id"])

    discovery = await _create_discovery_request(tenant_id, user_id)
    if not discovery:
        await msg.answer("❌ Не удалось запустить discovery. Попробуй позже.")
        return

    request_id = str(discovery["id"])
    await state.update_data(
        last_group_discovery_id=request_id,
        last_group_tenant_id=tenant_id,
    )

    await msg.answer(
        "🔍 Запустил поиск групп.\nЯ пришлю результат, как только он будет готов.",
        reply_markup=_groups_menu_keyboard(True),
    )

    asyncio.create_task(
        _poll_discovery_results(
            bot=msg.bot,
            chat_id=msg.chat.id,
            request_id=request_id,
            tenant_id=tenant_id,
        )
    )


@router.message(Command("groups_discovery_status"))
async def cmd_group_discovery_status(msg: Message, state: FSMContext):
    """Показывает статус последнего discovery-запроса."""
    data = await state.get_data()
    tenant_id = data.get("last_group_tenant_id")
    request_id = data.get("last_group_discovery_id")

    if not tenant_id:
        user_ctx = await _get_user_context(msg.from_user.id)
        if not user_ctx:
            await msg.answer("❌ Пользователь не найден. Используй /start для регистрации.")
            return
        tenant_id = str(user_ctx["tenant_id"])

    discovery = None
    if request_id:
        discovery = await _fetch_discovery(request_id, tenant_id)
    if not discovery:
        discovery = await _fetch_latest_discovery(tenant_id)
        if discovery:
            await state.update_data(
                last_group_discovery_id=str(discovery["id"]),
                last_group_tenant_id=tenant_id,
            )

    if not discovery:
        await msg.answer("ℹ️ Активных discovery-запросов нет. Используй /group_discovery.")
        return

    text = _render_discovery_text(discovery, page=0)
    keyboard = None
    if discovery.get("status") == "completed":
        keyboard = _discovery_keyboard(discovery, str(discovery["id"]), page=0)

    await msg.answer(text, parse_mode="HTML", reply_markup=keyboard)


# ============================================================================
# CALLBACK HANDLERS
# ============================================================================

@router.callback_query(F.data == "menu:groups")
async def cb_menu_groups(callback: CallbackQuery, state: FSMContext):
    """Меню групп через inline-кнопку."""
    user_ctx = await _get_user_context(callback.from_user.id)
    if not user_ctx:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    tenant_id = str(user_ctx["tenant_id"])
    groups_payload = await _fetch_groups(tenant_id)
    groups = groups_payload.get("groups", []) if groups_payload else []

    await callback.message.edit_text(
        _render_groups_text(groups),
        parse_mode="HTML",
        reply_markup=_groups_menu_keyboard(bool(groups)),
    )
    await callback.answer()


@router.callback_query(F.data == "groups:refresh")
async def cb_groups_refresh(callback: CallbackQuery, state: FSMContext):
    """Обновляет список подключённых групп."""
    await cb_menu_groups(callback, state)


@router.callback_query(F.data == "group_digest:menu")
async def cb_group_digest_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает меню групповых дайджестов."""
    user_ctx = await _get_user_context(callback.from_user.id)
    if not user_ctx:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    tenant_id = str(user_ctx["tenant_id"])
    user_id = str(user_ctx["id"])

    groups, _ = await _load_group_digest_groups(state, tenant_id, force_refresh=True)
    await state.update_data(
        group_digest_tenant_id=tenant_id,
        group_digest_user_id=user_id,
        group_digest_current_page=0,
    )

    text = _render_group_digest_menu(groups, page=0)
    keyboard = _group_digest_menu_keyboard(groups, page=0)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to edit group digest menu message", error=str(exc))
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "group_digest:refresh")
async def cb_group_digest_refresh(callback: CallbackQuery, state: FSMContext):
    """Обновляет список групп для дайджеста."""
    data = await state.get_data()
    tenant_id = data.get("group_digest_tenant_id")
    user_id = data.get("group_digest_user_id")

    if not tenant_id or not user_id:
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        tenant_id = str(user_ctx["tenant_id"])
        user_id = str(user_ctx["id"])
        await state.update_data(
            group_digest_tenant_id=tenant_id,
            group_digest_user_id=user_id,
        )

    groups, _ = await _load_group_digest_groups(state, tenant_id, force_refresh=True)
    current_page = int(data.get("group_digest_current_page", 0))
    max_page = max(0, math.ceil(len(groups) / GROUP_DIGEST_PAGE_SIZE) - 1)
    current_page = max(0, min(current_page, max_page))
    await state.update_data(group_digest_current_page=current_page)

    text = _render_group_digest_menu(groups, page=current_page)
    keyboard = _group_digest_menu_keyboard(groups, page=current_page)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to refresh group digest menu", error=str(exc))
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer("🔄 Список обновлён")


@router.callback_query(F.data.startswith("group_digest:page:"))
async def cb_group_digest_page(callback: CallbackQuery, state: FSMContext):
    """Переключает страницу в меню групповых дайджестов."""
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return

    requested_page = int(parts[2])
    data = await state.get_data()
    tenant_id = data.get("group_digest_tenant_id")

    if not tenant_id:
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        tenant_id = str(user_ctx["tenant_id"])
        await state.update_data(group_digest_tenant_id=tenant_id, group_digest_user_id=str(user_ctx["id"]))

    groups, _ = await _load_group_digest_groups(state, tenant_id, force_refresh=False)
    max_page = max(0, math.ceil(len(groups) / GROUP_DIGEST_PAGE_SIZE) - 1)
    current_page = max(0, min(requested_page, max_page))
    await state.update_data(group_digest_current_page=current_page)

    text = _render_group_digest_menu(groups, page=current_page)
    keyboard = _group_digest_menu_keyboard(groups, page=current_page)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to switch group digest page", error=str(exc))
    await callback.answer()


@router.callback_query(F.data.startswith("gdigest:view:"))
async def cb_group_digest_view(callback: CallbackQuery, state: FSMContext):
    """Показывает детали выбранной группы для генерации дайджеста."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    group_id = parts[2]
    data = await state.get_data()
    tenant_id = data.get("group_digest_tenant_id")

    if not tenant_id:
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        tenant_id = str(user_ctx["tenant_id"])
        await state.update_data(group_digest_tenant_id=tenant_id, group_digest_user_id=str(user_ctx["id"]))

    groups, groups_map = await _load_group_digest_groups(state, tenant_id, force_refresh=False)
    group = groups_map.get(group_id)
    if not group:
        groups, groups_map = await _load_group_digest_groups(state, tenant_id, force_refresh=True)
        group = groups_map.get(group_id)

    if not group:
        await callback.answer("❌ Группа не найдена", show_alert=True)
        return

    selected_windows = dict(data.get("group_digest_selected_windows") or {})
    selected_window = selected_windows.get(group_id) or _resolve_group_default_window(group)
    selected_windows[group_id] = selected_window

    last_results = data.get("group_digest_last_results") or {}
    last_result = last_results.get(group_id)

    await state.update_data(
        group_digest_selected_windows=selected_windows,
        group_digest_current_group_id=group_id,
    )

    text = _render_group_digest_detail(group, selected_window, last_result=last_result)
    keyboard = _group_digest_detail_keyboard(group_id, selected_window)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to show group digest detail", error=str(exc))
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("gdigest:window:"))
async def cb_group_digest_window(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор окна для группового дайджеста."""
    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer()
        return

    group_id = parts[2]
    requested_window = int(parts[3])
    if requested_window not in GROUP_DIGEST_WINDOWS:
        await callback.answer("❌ Недопустимое окно", show_alert=True)
        return

    data = await state.get_data()
    tenant_id = data.get("group_digest_tenant_id")
    if not tenant_id:
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        tenant_id = str(user_ctx["tenant_id"])
        await state.update_data(group_digest_tenant_id=tenant_id, group_digest_user_id=str(user_ctx["id"]))

    _, groups_map = await _load_group_digest_groups(state, tenant_id, force_refresh=False)
    group = groups_map.get(group_id)
    if not group:
        await callback.answer("❌ Группа не найдена", show_alert=True)
        return

    selected_windows = dict(data.get("group_digest_selected_windows") or {})
    selected_windows[group_id] = requested_window
    await state.update_data(group_digest_selected_windows=selected_windows)

    last_results = data.get("group_digest_last_results") or {}
    last_result = last_results.get(group_id)

    text = _render_group_digest_detail(group, requested_window, last_result=last_result)
    keyboard = _group_digest_detail_keyboard(group_id, requested_window)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to update group digest window view", error=str(exc))
    await callback.answer(f"⏱ Окно: {requested_window} ч.")


@router.callback_query(F.data.startswith("gdigest:trigger:"))
async def cb_group_digest_trigger(callback: CallbackQuery, state: FSMContext):
    """Запускает генерацию группового дайджеста через API."""
    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer("❌ Некорректный формат", show_alert=True)
        return

    group_id = parts[2]
    window_size = int(parts[3])
    if window_size not in GROUP_DIGEST_WINDOWS:
        await callback.answer("❌ Недопустимое окно", show_alert=True)
        return

    data = await state.get_data()
    tenant_id = data.get("group_digest_tenant_id")
    user_id = data.get("group_digest_user_id")

    # Context7: Детальное логирование для диагностики
    logger.debug(
        "Group digest trigger - initial state check",
        telegram_id=callback.from_user.id,
        tenant_id=tenant_id,
        user_id=user_id,
        tenant_id_type=type(tenant_id).__name__ if tenant_id is not None else "None",
        user_id_type=type(user_id).__name__ if user_id is not None else "None",
    )

    # Context7: Проверка на None и пустые строки
    if tenant_id is None or user_id is None or not str(tenant_id).strip() or not str(user_id).strip():
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        # Context7: Валидация tenant_id и user_id перед использованием
        tenant_id_raw = user_ctx.get("tenant_id")
        user_id_raw = user_ctx.get("id")
        
        if not tenant_id_raw or not user_id_raw:
            logger.error(
                "Missing tenant_id or user_id in user context",
                telegram_id=callback.from_user.id,
                has_tenant_id=tenant_id_raw is not None,
                has_user_id=user_id_raw is not None,
            )
            await callback.answer("❌ Ошибка: отсутствует tenant_id или user_id", show_alert=True)
            return
        
        # Context7: Проверка формата UUID перед конвертацией в строку
        try:
            # Валидируем, что это валидный UUID
            UUID(str(tenant_id_raw))
            UUID(str(user_id_raw))
        except (ValueError, TypeError) as e:
            logger.error(
                "Invalid UUID format for tenant_id or user_id",
                telegram_id=callback.from_user.id,
                tenant_id=tenant_id_raw,
                user_id=user_id_raw,
                error=str(e),
            )
            await callback.answer("❌ Ошибка: невалидный формат tenant_id или user_id", show_alert=True)
            return
        
        tenant_id = str(tenant_id_raw)
        user_id = str(user_id_raw)
        await state.update_data(
            group_digest_tenant_id=tenant_id,
            group_digest_user_id=user_id,
        )

    # Context7: Дополнительная валидация перед отправкой запроса
    # Проверяем на None и пустые строки после всех преобразований
    tenant_id_str = str(tenant_id).strip() if tenant_id is not None else ""
    user_id_str = str(user_id).strip() if user_id is not None else ""
    
    if not tenant_id_str or not user_id_str:
        logger.error(
            "tenant_id or user_id is empty before request",
            telegram_id=callback.from_user.id,
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_id_str=tenant_id_str,
            user_id_str=user_id_str,
        )
        await callback.answer("❌ Ошибка: tenant_id или user_id пуст", show_alert=True)
        return
    
    # Context7: Проверка формата UUID перед отправкой
    try:
        tenant_uuid = UUID(tenant_id_str)
        user_uuid = UUID(user_id_str)
        # Обновляем значения на валидированные UUID строки
        tenant_id = str(tenant_uuid)
        user_id = str(user_uuid)
    except (ValueError, TypeError) as e:
        logger.error(
            "Invalid UUID format before API request",
            telegram_id=callback.from_user.id,
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_id_str=tenant_id_str,
            user_id_str=user_id_str,
            error=str(e),
            error_type=type(e).__name__,
        )
        await callback.answer("❌ Ошибка: невалидный формат tenant_id или user_id", show_alert=True)
        return

    _, groups_map = await _load_group_digest_groups(state, tenant_id, force_refresh=False)
    group = groups_map.get(group_id)
    if not group:
        _, groups_map = await _load_group_digest_groups(state, tenant_id, force_refresh=True)
        group = groups_map.get(group_id)
    if not group:
        await callback.answer("❌ Группа не найдена", show_alert=True)
        return

    # Context7: Финальная проверка перед формированием payload
    if not tenant_id or not user_id:
        logger.error(
            "tenant_id or user_id is empty before payload creation",
            telegram_id=callback.from_user.id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        await callback.answer("❌ Ошибка: tenant_id или user_id пуст", show_alert=True)
        return
    
    # Context7: Убеждаемся, что значения - это строки UUID (не None, не пустые)
    tenant_id_final = str(tenant_id).strip()
    user_id_final = str(user_id).strip()
    
    if not tenant_id_final or not user_id_final:
        logger.error(
            "tenant_id or user_id is empty after string conversion",
            telegram_id=callback.from_user.id,
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_id_final=tenant_id_final,
            user_id_final=user_id_final,
        )
        await callback.answer("❌ Ошибка: tenant_id или user_id пуст после конвертации", show_alert=True)
        return
    
    # Context7: Проверяем формат UUID еще раз перед отправкой
    try:
        UUID(tenant_id_final)
        UUID(user_id_final)
    except (ValueError, TypeError) as e:
        logger.error(
            "Invalid UUID format in final check",
            telegram_id=callback.from_user.id,
            tenant_id_final=tenant_id_final,
            user_id_final=user_id_final,
            error=str(e),
        )
        await callback.answer("❌ Ошибка: невалидный формат tenant_id или user_id", show_alert=True)
        return
    
    payload = {
        "tenant_id": tenant_id_final,
        "user_id": user_id_final,
        "window_size_hours": window_size,
        "delivery_channel": "telegram",
        "delivery_format": "telegram_html",
        "trigger": "bot_manual_group",
    }

    # Context7: Детальное логирование payload перед отправкой
    logger.info(
        "Group digest trigger - sending request",
        telegram_id=callback.from_user.id,
        group_id=group_id,
        tenant_id=tenant_id_final,
        user_id=user_id_final,
        window_size=window_size,
        payload_tenant_id=payload.get("tenant_id"),
        payload_user_id=payload.get("user_id"),
        payload_tenant_id_type=type(payload.get("tenant_id")).__name__,
        payload_user_id_type=type(payload.get("user_id")).__name__,
        payload_json=str(payload),
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{API_BASE}/api/groups/{group_id}/digest", json=payload)
    except Exception as exc:
        logger.error(
            "Group digest trigger request failed",
            tenant_id=tenant_id,
            group_id=group_id,
            window_size=window_size,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await callback.answer("❌ Ошибка запроса к API", show_alert=True)
        return

    if resp.status_code not in (200, 202):
        message = resp.text.strip() or resp.reason_phrase or "Не удалось запустить дайджест"
        
        # Context7: Улучшенная обработка ошибок валидации (422)
        if resp.status_code == 422:
            try:
                error_payload = resp.json()
                if isinstance(error_payload, dict):
                    detail = error_payload.get("detail")
                    if isinstance(detail, list) and len(detail) > 0:
                        # Извлекаем первую ошибку валидации
                        first_error = detail[0]
                        if isinstance(first_error, dict):
                            error_msg = first_error.get("msg", "")
                            error_loc = first_error.get("loc", [])
                            if error_loc:
                                field_name = " → ".join(str(loc) for loc in error_loc)
                                message = f"Ошибка валидации {field_name}: {error_msg}"
                            else:
                                message = error_msg
                    elif isinstance(detail, str):
                        message = detail
            except Exception:
                pass
        
        # Context7: Обработка других типов ошибок
        if resp.status_code != 422:
            try:
                error_payload = resp.json()
                if isinstance(error_payload, dict):
                    detail = error_payload.get("detail") or error_payload.get("message")
                    if detail:
                        message = str(detail)
            except Exception:
                pass
        
        logger.warning(
            "Group digest API returned error",
            tenant_id=tenant_id,
            group_id=group_id,
            window_size=window_size,
            status_code=resp.status_code,
            response=resp.text[:500],
            error_message=message,
        )
        await callback.answer(f"❌ {message}", show_alert=True)
        return

    result = resp.json()
    requested_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    last_results = dict(data.get("group_digest_last_results") or {})
    last_results[group_id] = {
        "status": result.get("status", "queued"),
        "history_id": result.get("history_id"),
        "group_window_id": result.get("group_window_id"),
        "message_count": result.get("message_count"),
        "participant_count": result.get("participant_count"),
        "window_size_hours": window_size,
        "requested_at": requested_at,
    }

    selected_windows = dict(data.get("group_digest_selected_windows") or {})
    selected_windows[group_id] = window_size

    await state.update_data(
        group_digest_last_results=last_results,
        group_digest_selected_windows=selected_windows,
    )

    detail_text = _render_group_digest_detail(group, window_size, last_result=last_results[group_id])
    keyboard = _group_digest_detail_keyboard(group_id, window_size)

    try:
        await callback.message.edit_text(detail_text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to update group digest detail after trigger", error=str(exc))

    summary = (
        "🕒 <b>Дайджест поставлен в очередь</b>\n"
        f"Группа: {html.escape(group.get('title') or group.get('username') or 'Без названия')}\n"
        f"Окно: {window_size} ч.\n"
        f"Сообщений: {result.get('message_count', 0)}\n"
        f"Участников: {result.get('participant_count', 0)}\n"
        f"History ID: <code>{result.get('history_id')}</code>"
    )
    await callback.message.answer(summary, parse_mode="HTML")

    logger.info(
        "Group digest requested from bot",
        tenant_id=tenant_id,
        user_id=user_id,
        group_id=group_id,
        window_size=window_size,
        history_id=result.get("history_id"),
    )
    await callback.answer("✅ Дайджест поставлен в очередь")


@router.callback_query(F.data == "groups:discover")
async def cb_groups_discover(callback: CallbackQuery, state: FSMContext):
    """Запускает discovery через inline-меню."""
    user_ctx = await _get_user_context(callback.from_user.id)
    if not user_ctx:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    tenant_id = str(user_ctx["tenant_id"])
    user_id = str(user_ctx["id"])

    discovery = await _create_discovery_request(tenant_id, user_id)
    if not discovery:
        await callback.answer("❌ Не удалось запустить поиск", show_alert=True)
        return

    request_id = str(discovery["id"])
    await state.update_data(
        last_group_discovery_id=request_id,
        last_group_tenant_id=tenant_id,
    )

    await callback.message.edit_text(
        "🔍 Запустил поиск групп. Я пришлю результаты, как только они появятся.",
        reply_markup=_groups_menu_keyboard(True),
    )
    await callback.answer("Поиск запущен")

    asyncio.create_task(
        _poll_discovery_results(
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            request_id=request_id,
            tenant_id=tenant_id,
        )
    )


@router.callback_query(F.data.startswith("gdisc:refresh:"))
async def cb_discovery_refresh(callback: CallbackQuery, state: FSMContext):
    """Обновляет сообщение с результатами discovery."""
    parts = callback.data.split(":")
    if len(parts) not in (3, 4):
        await callback.answer()
        return
    request_id = parts[2]
    page = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else 0

    data = await state.get_data()
    tenant_id = data.get("last_group_tenant_id")
    if not tenant_id:
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        tenant_id = str(user_ctx["tenant_id"])

    discovery = await _fetch_discovery(request_id, tenant_id)
    if not discovery:
        await callback.answer("⚠️ Нет данных. Запусти поиск заново.", show_alert=True)
        return

    text = _render_discovery_text(discovery, page=page)
    keyboard = None
    if discovery.get("status") == "completed":
        keyboard = _discovery_keyboard(discovery, request_id, page=page)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to edit discovery message", error=str(exc))
    await callback.answer("🔄 Обновлено")


@router.callback_query(F.data.startswith("gdisc:page:"))
async def cb_discovery_page(callback: CallbackQuery, state: FSMContext):
    """Переключает страницы в результатах discovery."""
    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer()
        return

    request_id = parts[2]
    page = int(parts[3])

    data = await state.get_data()
    tenant_id = data.get("last_group_tenant_id")
    if not tenant_id:
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        tenant_id = str(user_ctx["tenant_id"])

    discovery = await _fetch_discovery(request_id, tenant_id)
    if not discovery:
        await callback.answer("⚠️ Данные недоступны. Запусти поиск заново.", show_alert=True)
        return

    text = _render_discovery_text(discovery, page=page)
    keyboard = None
    if discovery.get("status") == "completed":
        keyboard = _discovery_keyboard(discovery, request_id, page=page)

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        logger.warning("Failed to switch discovery page", error=str(exc))
    await callback.answer()


@router.callback_query(F.data.startswith("gconn:"))
async def cb_group_connect(callback: CallbackQuery, state: FSMContext):
    """Подключает выбранную группу из результатов discovery."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    request_id, tg_chat_id = parts[1], parts[2]

    data = await state.get_data()
    tenant_id = data.get("last_group_tenant_id")
    if not tenant_id:
        user_ctx = await _get_user_context(callback.from_user.id)
        if not user_ctx:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        tenant_id = str(user_ctx["tenant_id"])
    else:
        user_ctx = await _get_user_context(callback.from_user.id)

    discovery = await _fetch_discovery(request_id, tenant_id)
    if not discovery or discovery.get("status") != "completed":
        await callback.answer("⚠️ Результаты устарели, запусти поиск заново.", show_alert=True)
        return

    candidate = None
    for item in discovery.get("results", []):
        if str(item.get("tg_chat_id")) == tg_chat_id:
            candidate = dict(item)
            break

    if not candidate:
        await callback.answer("⚠️ Группа не найдена в результатах", show_alert=True)
        return

    if candidate.get("is_connected"):
        await callback.answer("✅ Группа уже подключена")
        return

    candidate["request_id"] = request_id
    success, message = await _connect_group(
        tenant_id=tenant_id,
        candidate=candidate,
        requested_by=str(user_ctx["id"]) if user_ctx else "",
    )

    if success:
        await callback.answer("✅ Группа подключена")
        await callback.message.answer(
            f"✅ <b>{html.escape(candidate.get('title') or 'Группа')}</b> подключена.",
            parse_mode="HTML",
        )
        # Обновляем список групп
        await cb_menu_groups(callback, state)
    else:
        await callback.answer(f"❌ {message}", show_alert=True)


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

async def _poll_discovery_results(
    bot: Bot,
    chat_id: int,
    request_id: str,
    tenant_id: str,
    poll_interval: int = 5,
    max_attempts: int = 24,
):
    """Периодически опрашивает API и отправляет результат discovery пользователю."""
    try:
        attempt = 0
        async with httpx.AsyncClient(timeout=15) as client:
            while attempt < max_attempts:
                attempt += 1
                resp = await client.get(
                    f"{API_BASE}/api/groups/discovery/{request_id}",
                    params={"tenant_id": tenant_id},
                )
                if resp.status_code != 200:
                    await asyncio.sleep(poll_interval)
                    continue
                data = resp.json()
                status = data.get("status")
                if status == "completed":
                    text = _render_discovery_text(data, page=0)
                    keyboard = _discovery_keyboard(data, request_id, page=0)
                    await bot.send_message(
                        chat_id,
                        text,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                    return
                if status == "failed":
                    text = _render_discovery_text(data, page=0)
                    await bot.send_message(chat_id, text, parse_mode="HTML")
                    return
                await asyncio.sleep(poll_interval)
        await bot.send_message(
            chat_id,
            "⏳ Поиск групп ещё выполняется. Используй /groups_discovery_status для проверки.",
        )
    except Exception as exc:
        logger.error(
            "Discovery polling task failed",
            request_id=request_id,
            tenant_id=tenant_id,
            error=str(exc),
        )
        try:
            await bot.send_message(
                chat_id,
                "⚠️ Не удалось получить результат discovery. Проверь статус вручную.",
            )
        except Exception:
            pass


# Context7 best practices:
# - [C7-ID: BOT-GROUPS-001] — Все сетевые вызовы защищены try/except с логированием.
# - [C7-ID: BOT-GROUPS-002] — Inline UX повторяет прошлую сборку: выводим все группы и даём выбрать подключаемые.
# - [C7-ID: BOT-GROUPS-003] — Взаимодействие с API разделено на мелкие функции для unit-тестирования.
# - [C7-ID: BOT-GROUPS-004] — Фоновый polling реализован без блокировки event loop.

