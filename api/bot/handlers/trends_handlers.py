"""
Telegram Bot handlers для управления трендами.
Context7: Интеграция с Trends API через HTTP клиент
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx
import json
import structlog
from typing import Optional, List, Tuple, Dict, Any
from uuid import UUID

logger = structlog.get_logger()
router = Router()

# API base URL
API_BASE = "http://api:8000"


def _kb_trends_menu():
    """Клавиатура главного меню трендов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
        [InlineKeyboardButton(text="🔥 Горящие тренды", callback_data="trends:emerging")],
        [InlineKeyboardButton(text="🔍 Обнаружить тренды", callback_data="trends:detect")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:main")]
    ])


async def _get_user_id(telegram_id: int) -> Optional[UUID]:
    """Получить user_id по telegram_id."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{API_BASE}/api/users/{telegram_id}")
            if r.status_code == 200:
                # Context7: Безопасное чтение response body перед парсингом JSON
                try:
                    response_text = r.text
                    if response_text and response_text.strip():
                        try:
                            user_data = json.loads(response_text)
                            return UUID(user_data.get("id"))
                        except (json.JSONDecodeError, ValueError) as e:
                            logger.warning(
                                "Error parsing JSON from user response",
                                telegram_id=telegram_id,
                                error=str(e),
                                error_type=type(e).__name__
                            )
                            return None
                    else:
                        logger.warning("Empty response body for user", telegram_id=telegram_id)
                        return None
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                        httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
                    logger.warning(
                        "Error reading response body for user",
                        telegram_id=telegram_id,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    return None
                except Exception as e:
                    logger.warning(
                        "Unexpected error reading response body for user",
                        telegram_id=telegram_id,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    return None
            else:
                logger.warning("User not found", telegram_id=telegram_id, status_code=r.status_code)
                return None
    except Exception as e:
        logger.error("Error getting user_id", telegram_id=telegram_id, error=str(e))
        return None


def _extract_topics(text: Optional[str]) -> List[str]:
    if not text:
        return []
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return []
    topics_raw = parts[1]
    topics = [token.strip("# ").strip() for token in topics_raw.split(",")]
    return [topic for topic in topics if topic]


async def _update_trend_subscription(chat_id: int, frequency: str, topics: List[str], enable: bool = True) -> Tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if enable:
                response = await client.post(
                    f"{API_BASE}/api/trends/subscriptions",
                    json={
                        "chat_id": chat_id,
                        "frequency": frequency,
                        "topics": topics,
                    },
                )
            else:
                response = await client.delete(
                    f"{API_BASE}/api/trends/subscriptions/{chat_id}/{frequency}"
                )
        if response.status_code not in (200, 201):
            # Context7: Безопасное чтение response body для получения деталей ошибки
            try:
                response_text = response.text
                if response_text and response_text.strip():
                    try:
                        error_data = json.loads(response_text)
                        detail = error_data.get("detail", "Неизвестная ошибка")
                    except (json.JSONDecodeError, ValueError):
                        detail = f"HTTP {response.status_code}: {response_text[:200]}"
                else:
                    detail = f"HTTP {response.status_code}"
            except Exception as e:
                logger.warning(
                    "Error reading error response body",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=response.status_code
                )
                detail = f"HTTP {response.status_code}: Неизвестная ошибка"
            return False, detail
        return True, ""
    except Exception as exc:
        logger.error("Error updating trend subscription", error=str(exc))
        return False, str(exc)


async def _load_emerging_digest(client: httpx.AsyncClient, window: str = "3h", limit: int = 5, user_id: Optional[UUID] = None) -> Tuple[str, List[Dict[str, Any]]]:
    """Загрузить emerging тренды с обработкой ошибок."""
    try:
        # Context7: Ослабляем фильтры для показа трендов - снижаем пороги для лучшего UX
        params = {
            "min_sources": 0,  # Убираем минимальное требование по источникам
            "min_burst": 0.0,  # Убираем минимальное требование по burst score
            "page": 1,
            "page_size": max(limit * 2, 10),  # Запрашиваем больше, чтобы после фильтрации осталось достаточно
            "window": window,
            **({"user_id": str(user_id)} if user_id else {}),
        }
        response = await client.get(
            f"{API_BASE}/api/trends/emerging",
            params=params,
        )
        if response.status_code != 200:
            logger.warning(
                "Failed to load emerging trends",
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return window, []
        
        # Context7: Безопасное чтение response body перед парсингом JSON
        # Используем response.text для чтения тела ответа, чтобы избежать ошибок при закрытом соединении
        try:
            response_text = response.text
            if not response_text or not response_text.strip():
                logger.warning(
                    "Empty response body for emerging trends",
                    status_code=response.status_code,
                    user_id=str(user_id) if user_id else None
                )
                return window, []
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
            logger.error(
                "Error reading response body for emerging trends",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return window, []
        except Exception as e:
            logger.error(
                "Unexpected error reading response body for emerging trends",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return window, []
        
        # Парсинг JSON из уже прочитанного текста
        try:
            payload = json.loads(response_text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "Error parsing JSON from emerging trends response",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                response_preview=response_text[:200] if response_text else None,
                user_id=str(user_id) if user_id else None
            )
            return window, []
        except Exception as e:
            logger.error(
                "Unexpected error parsing JSON from emerging trends response",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return window, []
        
        clusters = payload.get("clusters", [])
        total = payload.get("total", 0)
        logger.info(
            "Loaded emerging trends",
            clusters_count=len(clusters),
            total=total,
            user_id=str(user_id) if user_id else None,
            window=window,
            params=params
        )
        
        # Context7: Fallback - если с персонализацией ничего не найдено, пробуем без персонализации
        if user_id and not clusters:
            logger.info("No personalized trends found, trying without personalization", user_id=str(user_id))
            fallback_params = {**params}
            fallback_params.pop("user_id", None)
            try:
                fallback_response = await client.get(
                    f"{API_BASE}/api/trends/emerging",
                    params=fallback_params,
                )
                if fallback_response.status_code == 200:
                    fallback_text = fallback_response.text
                    if fallback_text and fallback_text.strip():
                        fallback_payload = json.loads(fallback_text)
                        clusters = fallback_payload.get("clusters", [])
                        logger.info(
                            "Loaded emerging trends without personalization",
                            clusters_count=len(clusters),
                            user_id=str(user_id)
                        )
            except Exception as e:
                logger.warning(
                    "Error loading trends without personalization",
                    error=str(e),
                    user_id=str(user_id)
                )
        
        return payload.get("window") or window, clusters[:limit]  # Ограничиваем до limit
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, 
            httpx.RemoteProtocolError, httpx.LocalProtocolError) as e:
        logger.error(
            "Network error loading emerging trends",
            error=str(e),
            error_type=type(e).__name__,
            user_id=str(user_id) if user_id else None
        )
        return window, []
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error loading emerging trends",
            status_code=e.response.status_code,
            error=str(e),
            user_id=str(user_id) if user_id else None
        )
        return window, []
    except Exception as e:
        logger.error(
            "Unexpected error loading emerging trends",
            error=str(e),
            error_type=type(e).__name__,
            user_id=str(user_id) if user_id else None
        )
        return window, []


async def _load_stable_digest(client: httpx.AsyncClient, min_frequency: int = 0, limit: int = 5, user_id: Optional[UUID] = None) -> List[Dict[str, Any]]:
    """Загрузить stable тренды с обработкой ошибок."""
    try:
        # Context7: Ослабляем фильтры для показа трендов - убираем min_frequency для digest
        params = {
            "min_frequency": min_frequency,  # По умолчанию 0 - показываем все тренды
            "status": "stable",
            "page": 1,
            "page_size": max(limit * 2, 10),  # Запрашиваем больше, чтобы после фильтрации осталось достаточно
            **({"user_id": str(user_id)} if user_id else {}),
        }
        response = await client.get(f"{API_BASE}/api/trends/clusters", params=params)
        if response.status_code != 200:
            logger.warning(
                "Failed to load stable trends",
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return []
        
        # Context7: Безопасное чтение response body перед парсингом JSON
        # Используем response.text для чтения тела ответа, чтобы избежать ошибок при закрытом соединении
        try:
            response_text = response.text
            if not response_text or not response_text.strip():
                logger.warning(
                    "Empty response body for stable trends",
                    status_code=response.status_code,
                    user_id=str(user_id) if user_id else None
                )
                return []
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
            logger.error(
                "Error reading response body for stable trends",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return []
        except Exception as e:
            logger.error(
                "Unexpected error reading response body for stable trends",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return []
        
        # Парсинг JSON из уже прочитанного текста
        try:
            payload = json.loads(response_text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "Error parsing JSON from stable trends response",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                response_preview=response_text[:200] if response_text else None,
                user_id=str(user_id) if user_id else None
            )
            return []
        except Exception as e:
            logger.error(
                "Unexpected error parsing JSON from stable trends response",
                error=str(e),
                error_type=type(e).__name__,
                status_code=response.status_code,
                user_id=str(user_id) if user_id else None
            )
            return []
        
        clusters = payload.get("clusters", [])
        total = payload.get("total", 0)
        logger.info(
            "Loaded stable trends",
            clusters_count=len(clusters),
            total=total,
            user_id=str(user_id) if user_id else None,
            params=params
        )
        
        # Context7: Fallback - если с персонализацией ничего не найдено, пробуем без персонализации
        if user_id and not clusters:
            logger.info("No personalized stable trends found, trying without personalization", user_id=str(user_id))
            fallback_params = {**params}
            fallback_params.pop("user_id", None)
            try:
                fallback_response = await client.get(f"{API_BASE}/api/trends/clusters", params=fallback_params)
                if fallback_response.status_code == 200:
                    fallback_text = fallback_response.text
                    if fallback_text and fallback_text.strip():
                        fallback_payload = json.loads(fallback_text)
                        clusters = fallback_payload.get("clusters", [])
                        logger.info(
                            "Loaded stable trends without personalization",
                            clusters_count=len(clusters),
                            user_id=str(user_id)
                        )
            except Exception as e:
                logger.warning(
                    "Error loading stable trends without personalization",
                    error=str(e),
                    user_id=str(user_id)
                )
        
        if clusters:
            return clusters[:limit]  # Ограничиваем до limit
        
        # Fallback to emerging if no stable trends
        params["status"] = "emerging"
        try:
            fallback = await client.get(f"{API_BASE}/api/trends/clusters", params=params)
            if fallback.status_code != 200:
                logger.warning(
                    "Failed to load emerging trends as fallback",
                    status_code=fallback.status_code,
                    user_id=str(user_id) if user_id else None
                )
                return []
            
            # Context7: Безопасное чтение response body для fallback запроса
            try:
                fallback_text = fallback.text
                if not fallback_text or not fallback_text.strip():
                    logger.warning(
                        "Empty response body for fallback trends",
                        status_code=fallback.status_code,
                        user_id=str(user_id) if user_id else None
                    )
                    return []
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                    httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
                logger.error(
                    "Error reading response body for fallback trends",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=fallback.status_code,
                    user_id=str(user_id) if user_id else None
                )
                return []
            except Exception as e:
                logger.error(
                    "Unexpected error reading response body for fallback trends",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=fallback.status_code,
                    user_id=str(user_id) if user_id else None
                )
                return []
            
            # Парсинг JSON из уже прочитанного текста
            try:
                fallback_payload = json.loads(fallback_text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(
                    "Error parsing JSON from fallback trends response",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=fallback.status_code,
                    response_preview=fallback_text[:200] if fallback_text else None,
                    user_id=str(user_id) if user_id else None
                )
                return []
            except Exception as e:
                logger.error(
                    "Unexpected error parsing JSON from fallback trends response",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=fallback.status_code,
                    user_id=str(user_id) if user_id else None
                )
                return []
            
            fallback_clusters = fallback_payload.get("clusters", [])
            logger.info(
                "Loaded emerging trends as fallback for stable",
                clusters_count=len(fallback_clusters),
                user_id=str(user_id) if user_id else None
            )
            return fallback_clusters[:limit]  # Ограничиваем до limit
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                httpx.RemoteProtocolError, httpx.LocalProtocolError) as e:
            logger.error(
                "Network error in fallback trends request",
                error=str(e),
                error_type=type(e).__name__,
                user_id=str(user_id) if user_id else None
            )
            return []
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
            httpx.RemoteProtocolError, httpx.LocalProtocolError) as e:
        logger.error(
            "Network error loading stable trends",
            error=str(e),
            error_type=type(e).__name__,
            user_id=str(user_id) if user_id else None
        )
        return []
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error loading stable trends",
            status_code=e.response.status_code,
            error=str(e),
            user_id=str(user_id) if user_id else None
        )
        return []
    except Exception as e:
        logger.error(
            "Unexpected error loading stable trends",
            error=str(e),
            error_type=type(e).__name__,
            user_id=str(user_id) if user_id else None
        )
        return []


def _format_emerging_digest(window_label: str, clusters: List[Dict[str, Any]]) -> str:
    if not clusters:
        return f"🔥 <b>Горячие тренды за {window_label}</b>\n\nЗа последние {window_label} не было всплесков по вашим каналам. Это нормально, иногда новостное поле спокойное."
    lines = [f"🔥 <b>Горячие тренды за {window_label}</b>"]
    for idx, cluster in enumerate(clusters[:5], 1):
        card = cluster.get("card") or {}
        stats = card.get("stats") or {}
        label = card.get("title") or cluster.get("label") or cluster.get("primary_topic") or "Без названия"
        mentions = stats.get("mentions")
        baseline = stats.get("baseline")
        burst = stats.get("burst_score")
        burst_text = f"{burst:.1f}×" if isinstance(burst, (int, float)) else "—"
        sources = stats.get("sources") or cluster.get("source_diversity")
        why = card.get("why_important") or card.get("summary")
        examples = card.get("example_posts") or []
        lines.append(
            f"{idx}. <b>{label}</b>\n"
            f"   ⏱ {mentions or '—'} упоминаний vs {baseline or '—'}\n"
            f"   ⚡ Burst: {burst_text} | 🗞 Источники: {sources or '—'}"
        )
        if why:
            lines.append(f"   ❗ {why[:220]}")
        for example in examples[:2]:
            source = example.get("channel_title") or "Источник"
            snippet = example.get("content_snippet") or ""
            if snippet:
                sanitized = snippet.replace("\n", " ")
                lines.append(f"   • {source}: {sanitized[:160]}")
    return "\n".join(lines)


def _format_stable_digest(trends: List[Dict[str, Any]]) -> str:
    if not trends:
        return "🧊 <b>Устойчивые тренды за 7 дней</b>\n\nСейчас за 7 дней не нашлось трендов, которые проходят качество. Можно подключить больше каналов или уменьшить пороги."
    lines = ["🧊 <b>Устойчивые тренды за 7 дней</b>"]
    for idx, cluster in enumerate(trends[:5], 1):
        card = cluster.get("card") or {}
        stats = card.get("stats") or {}
        keyword = card.get("title") or cluster.get("label") or cluster.get("primary_topic") or "—"
        freq = stats.get("mentions") or cluster.get("window_mentions")
        burst = stats.get("burst_score")
        burst_text = f"{burst:.1f}×" if isinstance(burst, (int, float)) else "—"
        lines.append(f"{idx}. <b>{keyword}</b>\n   📊 Частота: {freq} | ⚡ Рост: {burst_text}")
    return "\n".join(lines)


@router.message(Command("trends"))
async def cmd_trends(msg: Message):
    """Обработчик команды /trends."""
    text = (
        "📈 <b>Тренды</b>\n\n"
        "Обнаружение и анализ трендов в ваших каналах.\n\n"
        "Выберите действие:"
    )
    await msg.answer(text, reply_markup=_kb_trends_menu(), parse_mode="HTML")


@router.callback_query(F.data == "trends:menu")
async def callback_trends_menu(callback: CallbackQuery):
    """Возврат в главное меню трендов."""
    await callback.message.edit_text(
        "📈 <b>Тренды</b>\n\n"
        "Обнаружение и анализ трендов в ваших каналах.\n\n"
        "Выберите действие:",
        reply_markup=_kb_trends_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(Command("trend_digest_1h"))
async def cmd_trend_digest_1h(message: Message):
    topics = _extract_topics(message.text)
    ok, error = await _update_trend_subscription(message.chat.id, "1h", topics, enable=True)
    if ok:
        topics_text = f" по темам: {', '.join(topics)}" if topics else ""
        await message.answer(
            f"✅ Подписка на hourly trending digest активирована{topics_text}.", parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ Не удалось включить подписку: {error}", parse_mode="HTML")


@router.message(Command("trend_digest_3h"))
async def cmd_trend_digest_3h(message: Message):
    topics = _extract_topics(message.text)
    ok, error = await _update_trend_subscription(message.chat.id, "3h", topics, enable=True)
    if ok:
        topics_text = f" по темам: {', '.join(topics)}" if topics else ""
        await message.answer(
            f"✅ Подписка на digest каждые 3 часа активирована{topics_text}.", parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ Не удалось включить подписку: {error}", parse_mode="HTML")


@router.message(Command("trend_digest_daily"))
async def cmd_trend_digest_daily(message: Message):
    topics = _extract_topics(message.text)
    ok, error = await _update_trend_subscription(message.chat.id, "daily", topics, enable=True)
    if ok:
        topics_text = f" по темам: {', '.join(topics)}" if topics else ""
        await message.answer(
            f"✅ Ежедневный digest трендов активирован{topics_text}.", parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ Не удалось включить подписку: {error}", parse_mode="HTML")


@router.message(Command("trend_digest_off"))
async def cmd_trend_digest_off(message: Message):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{API_BASE}/api/trends/subscriptions/{message.chat.id}")
        if response.status_code != 200:
            await message.answer("❌ Не удалось получить список подписок.", parse_mode="HTML")
            return
        data = response.json()
        subscriptions = data.get("subscriptions", [])
        if not subscriptions:
            await message.answer("ℹ️ Активных подписок не найдено.", parse_mode="HTML")
            return
        errors = []
        for sub in subscriptions:
            ok, error = await _update_trend_subscription(
                message.chat.id, sub.get("frequency"), sub.get("topics", []), enable=False
            )
            if not ok:
                errors.append(f"{sub.get('frequency')}: {error}")
        if errors:
            await message.answer(
                "⚠️ Частично отключено:\n" + "\n".join(errors),
                parse_mode="HTML",
            )
        else:
            await message.answer("✅ Все подписки на тренды отключены.", parse_mode="HTML")
    except Exception as exc:
        logger.error("Error disabling trend subscriptions", error=str(exc))
        await message.answer("❌ Ошибка при отключении подписок.", parse_mode="HTML")


@router.callback_query(F.data == "trends:list")
async def callback_trends_list(callback: CallbackQuery):
    """Показать список трендов."""
    try:
        status_used = "stable"
        user_uuid = await _get_user_id(callback.from_user.id)
        async with httpx.AsyncClient(timeout=10) as client:
            params = {"status": "stable", "page": 1, "page_size": 10}
            if user_uuid:
                params["user_id"] = str(user_uuid)
            r = await client.get(f"{API_BASE}/api/trends/clusters", params=params)
            if r.status_code != 200:
                await callback.answer("❌ Ошибка получения трендов", show_alert=True)
                return
            
            # Context7: Безопасное чтение response body перед парсингом JSON
            try:
                response_text = r.text
                if not response_text or not response_text.strip():
                    await callback.message.edit_text(
                        "📉 <b>Свежих трендов сейчас нет</b>\n\n"
                        "Запусти обнаружение или попробуй позже.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔍 Обнаружить", callback_data="trends:detect")],
                            [InlineKeyboardButton(text="🔙 Назад", callback_data="trends:menu")]
                        ]),
                        parse_mode="HTML"
                    )
                    await callback.answer()
                    return
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                    httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
                logger.error(
                    "Error reading response body for trends list",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code
                )
                await callback.answer("❌ Ошибка чтения ответа", show_alert=True)
                return
            except Exception as e:
                logger.error(
                    "Unexpected error reading response body for trends list",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code
                )
                await callback.answer("❌ Неожиданная ошибка", show_alert=True)
                return
            
            try:
                payload = json.loads(response_text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(
                    "Error parsing JSON from trends list response",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code,
                    response_preview=response_text[:200] if response_text else None
                )
                await callback.answer("❌ Ошибка обработки ответа", show_alert=True)
                return
            except Exception as e:
                logger.error(
                    "Unexpected error parsing JSON from trends list response",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code
                )
                await callback.answer("❌ Неожиданная ошибка", show_alert=True)
                return
            
            clusters = payload.get("clusters", [])
            if not clusters:
                params["status"] = "emerging"
                r = await client.get(f"{API_BASE}/api/trends/clusters", params=params)
                if r.status_code == 200:
                    # Context7: Безопасное чтение response body для fallback
                    try:
                        fallback_text = r.text
                        if fallback_text and fallback_text.strip():
                            try:
                                payload = json.loads(fallback_text)
                                clusters = payload.get("clusters", [])
                                if clusters:
                                    status_used = "emerging"
                            except (json.JSONDecodeError, ValueError) as e:
                                logger.warning(
                                    "Error parsing JSON from fallback trends list",
                                    error=str(e),
                                    error_type=type(e).__name__
                                )
                    except Exception as e:
                        logger.warning(
                            "Error reading fallback trends list response",
                            error=str(e),
                            error_type=type(e).__name__
                        )

        if not clusters:
                await callback.message.edit_text(
                "📉 <b>Свежих трендов сейчас нет</b>\n\n"
                "Запусти обнаружение или попробуй позже.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔍 Обнаружить", callback_data="trends:detect")],
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="trends:menu")]
                    ]),
                    parse_mode="HTML"
                )
                await callback.answer()
                return
            
        title_prefix = "🧊 <b>Устойчивые тренды за 7 дней</b>" if status_used == "stable" else "🔥 <b>Горячие тренды за последние часы</b>"
        text = f"{title_prefix}\n\n"
        builder = InlineKeyboardBuilder()
        for idx, cluster in enumerate(clusters[:10], 1):
            card = cluster.get("card") or {}
            stats = card.get("stats") or {}
            label = card.get("title") or cluster.get("label") or cluster.get("primary_topic") or "Без названия"
            mentions = stats.get("mentions")
            baseline = stats.get("baseline")
            burst = stats.get("burst_score")
            burst_text = f"{burst:.1f}×" if isinstance(burst, (int, float)) else "—"
            sources = stats.get("sources") or cluster.get("source_diversity")
            summary = card.get("summary") or cluster.get("summary") or ""
            why = card.get("why_important")
            text += (
                f"{idx}. <b>{label}</b>\n"
                f"   ⏱ {mentions or '—'} vs {baseline or '—'} | ⚡ {burst_text} | 🗞 Источники: {sources or '—'}\n"
            )
            if why:
                text += f"   ❗ {why}\n"
            elif summary:
                text += f"   📝 {summary[:200]}\n"
            examples = card.get("example_posts") or []
            for example in examples[:2]:
                source = example.get("channel_title") or "Источник"
                snippet = (example.get("content_snippet") or "").replace("\n", " ")
                if snippet:
                    text += f"   • {source}: {snippet[:140]}\n"
            text += "\n"
            cluster_id = cluster.get("id")
            if cluster_id:
                builder.button(text=f"ℹ️ {label[:26]}", callback_data=f"trend:cluster:{cluster_id}")
        
        # Context7: Применяем adjust(1) один раз после цикла для всех кнопок кластеров
        # Это гарантирует, что каждая кнопка кластера отображается на отдельной строке
        builder.adjust(1)
        
        # Context7: Кнопки навигации добавляем один раз после цикла
        builder.button(text="🔍 Обнаружить", callback_data="trends:detect")
        builder.button(text="🔙 Назад", callback_data="trends:menu")
        builder.adjust(1)
        
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()
    
    except Exception as e:
        logger.error("Error showing trends list", error=str(e))
        await callback.answer("❌ Ошибка загрузки трендов", show_alert=True)


@router.callback_query(F.data == "trends:emerging")
async def callback_trends_emerging(callback: CallbackQuery):
    """Показать emerging кластеры трендов."""
    try:
        user_uuid = await _get_user_id(callback.from_user.id)
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/api/trends/emerging",
                params={
                    "min_sources": 2,
                    "min_burst": 1.2,
                    "page": 1,
                    "page_size": 10,
                    **({"user_id": str(user_uuid)} if user_uuid else {}),
                }
            )
        if r.status_code != 200:
            await callback.answer("❌ Ошибка получения горящих трендов", show_alert=True)
            return

        # Context7: Безопасное чтение response body перед парсингом JSON
        try:
            response_text = r.text
            if not response_text or not response_text.strip():
                await callback.message.edit_text(
                    "🔥 <b>Горящих трендов сейчас нет</b>\n\n"
                    "Запусти обнаружение или попробуй позже.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔍 Обнаружить", callback_data="trends:detect")],
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="trends:menu")]
                    ]),
                    parse_mode="HTML"
                )
                await callback.answer()
                return
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
            logger.error(
                "Error reading response body for emerging trends",
                error=str(e),
                error_type=type(e).__name__,
                status_code=r.status_code
            )
            await callback.answer("❌ Ошибка чтения ответа", show_alert=True)
            return
        except Exception as e:
            logger.error(
                "Unexpected error reading response body for emerging trends",
                error=str(e),
                error_type=type(e).__name__,
                status_code=r.status_code
            )
            await callback.answer("❌ Неожиданная ошибка", show_alert=True)
            return
        
        try:
            payload = json.loads(response_text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "Error parsing JSON from emerging trends response",
                error=str(e),
                error_type=type(e).__name__,
                status_code=r.status_code,
                response_preview=response_text[:200] if response_text else None
            )
            await callback.answer("❌ Ошибка обработки ответа", show_alert=True)
            return
        except Exception as e:
            logger.error(
                "Unexpected error parsing JSON from emerging trends response",
                error=str(e),
                error_type=type(e).__name__,
                status_code=r.status_code
            )
            await callback.answer("❌ Неожиданная ошибка", show_alert=True)
            return
        
        clusters = payload.get("clusters", [])
        window_label = payload.get("window") or "3h"
        if not clusters:
            await callback.message.edit_text(
                f"🔥 <b>Горящих трендов нет за последние {window_label}</b>\n\n"
                "Попробуй запустить обнаружение вручную или вернись позже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 Обнаружить", callback_data="trends:detect")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="trends:menu")]
                ]),
                parse_mode="HTML"
            )
            await callback.answer()
            return

        text = f"🔥 <b>Горячие тренды за {window_label}</b>\n\n"
        builder = InlineKeyboardBuilder()
        for idx, cluster in enumerate(clusters, 1):
            card = cluster.get("card") or {}
            stats = card.get("stats") or {}
            label = card.get("title") or cluster.get("label") or cluster.get("primary_topic") or "Без названия"
            mentions = stats.get("mentions")
            baseline = stats.get("baseline")
            burst = stats.get("burst_score")
            sources = stats.get("sources") or cluster.get("source_diversity")
            channels = stats.get("channels") or sources
            duration = card.get("time_window", {}).get("duration_minutes")
            burst_text = f"{burst:.1f}×" if isinstance(burst, (int, float)) else "—"
            summary = card.get("summary") or cluster.get("summary") or ""
            why = card.get("why_important")
            text += (
                f"{idx}. <b>{label}</b>\n"
                f"   ⏱ За {duration or '—'} мин: {mentions or '—'} упоминаний (обычно {baseline or '—'})\n"
                f"   ⚡ Burst: {burst_text} | 🗞 Источники: {sources or '—'} | 📡 Каналы: {channels or '—'}\n"
            )
            if why:
                text += f"   ❗ {why}\n"
            elif summary:
                text += f"   📝 {summary[:160]}\n"
            text += "\n"
            cluster_id = cluster.get("id")
            if cluster_id:
                builder.button(
                    text=f"ℹ️ {label[:26]}",
                    callback_data=f"trend:cluster:{cluster_id}"
                )
        builder.button(text="🔙 Назад", callback_data="trends:menu")
        builder.adjust(1)

        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()

    except Exception as e:
        logger.error("Error showing emerging trends", error=str(e))
        await callback.answer("❌ Ошибка загрузки горящих трендов", show_alert=True)


@router.callback_query(F.data.startswith("trend:view:"))
async def callback_trend_view(callback: CallbackQuery):
    """Показать детали тренда."""
    trend_id = callback.data.split(":")[-1]
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API_BASE}/api/trends/{trend_id}")
            
            if r.status_code != 200:
                await callback.answer("❌ Ошибка получения тренда", show_alert=True)
                return
            
            # Context7: Безопасное чтение response body перед парсингом JSON
            try:
                response_text = r.text
                if not response_text or not response_text.strip():
                    await callback.answer("❌ Пустой ответ от сервера", show_alert=True)
                    return
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                    httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
                logger.error(
                    "Error reading response body for trend view",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code,
                    trend_id=trend_id
                )
                await callback.answer("❌ Ошибка чтения ответа", show_alert=True)
                return
            except Exception as e:
                logger.error(
                    "Unexpected error reading response body for trend view",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code,
                    trend_id=trend_id
                )
                await callback.answer("❌ Неожиданная ошибка", show_alert=True)
                return
            
            # Парсинг JSON из уже прочитанного текста
            try:
                trend = json.loads(response_text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(
                    "Error parsing JSON from trend view response",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code,
                    trend_id=trend_id,
                    response_preview=response_text[:200] if response_text else None
                )
                await callback.answer("❌ Ошибка обработки ответа", show_alert=True)
                return
            except Exception as e:
                logger.error(
                    "Unexpected error parsing JSON from trend view response",
                    error=str(e),
                    error_type=type(e).__name__,
                    status_code=r.status_code,
                    trend_id=trend_id
                )
                await callback.answer("❌ Неожиданная ошибка", show_alert=True)
                return
            
            keyword = trend.get("trend_keyword", "N/A")
            frequency = trend.get("frequency_count", 0)
            growth = trend.get("growth_rate")
            engagement = trend.get("engagement_score")
            first_mentioned = trend.get("first_mentioned_at")
            last_mentioned = trend.get("last_mentioned_at")
            channels = trend.get("channels_affected", [])
            posts_sample = trend.get("posts_sample", [])
            
            growth_text = f"{growth:.1%}" if growth else "—"
            engagement_text = f"{engagement:.1f}" if engagement else "—"
            
            text = (
                f"📌 <b>{keyword}</b>\n\n"
                f"📊 <b>Статистика:</b>\n"
                f"• Частота: {frequency}\n"
                f"• Рост: {growth_text}\n"
                f"• Engagement: {engagement_text}\n\n"
            )
            
            if first_mentioned:
                text += f"📅 Первое упоминание: {first_mentioned[:10]}\n"
            if last_mentioned:
                text += f"📅 Последнее упоминание: {last_mentioned[:10]}\n"
            
            if channels:
                text += f"\n📺 Каналов: {len(channels)}\n"
            
            if posts_sample:
                text += f"\n📝 <b>Примеры постов:</b>\n"
                for idx, post in enumerate(posts_sample[:3], 1):
                    post_text = post.get("content", "")[:100]
                    if len(post_text) > 100:
                        post_text = post_text[:100] + "..."
                    text += f"{idx}. {post_text}\n"
            
            builder = InlineKeyboardBuilder()
            builder.button(text="📋 Похожие тренды", callback_data=f"trend:similar:{trend_id}")
            builder.button(text="🗄 Архивировать", callback_data=f"trend:archive:{trend_id}")
            builder.button(text="🔙 Назад", callback_data="trends:list")
            builder.adjust(1)
            
            await callback.message.edit_text(
                text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
            await callback.answer()
    
    except Exception as e:
        logger.error("Error showing trend details", error=str(e))
        await callback.answer("❌ Ошибка загрузки тренда", show_alert=True)


@router.callback_query(F.data == "trends:detect")
async def callback_trends_detect(callback: CallbackQuery):
    """Запустить обнаружение трендов с улучшенной обработкой ошибок."""
    await callback.answer("⏳ Обнаружение запущено...")
    try:
        await callback.message.edit_text(
            "⏳ <b>Обнаруживаю тренды…</b>\nЭто может занять до 20 секунд.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    
    try:
        # Context7: Используем детальные таймауты для разных операций
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            user_uuid = await _get_user_id(callback.from_user.id)
            
            # Запрос на обнаружение трендов
            try:
                r = await client.post(
                    f"{API_BASE}/api/trends/detect",
                    params={
                        "days": 7,
                        "min_frequency": 10,
                        "min_growth": 0.2,
                        "min_engagement": 5.0
                    }
                )
                r.raise_for_status()  # Вызовет HTTPStatusError для 4xx/5xx
            except httpx.HTTPStatusError as e:
                error_detail = "Неизвестная ошибка"
                try:
                    error_detail = e.response.json().get("detail", f"HTTP {e.response.status_code}")
                except Exception:
                    error_detail = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                
                logger.error(
                    "HTTP error detecting trends",
                    status_code=e.response.status_code,
                    error=error_detail,
                    user_id=str(user_uuid) if user_uuid else None
                )
                await callback.message.edit_text(
                    f"❌ <b>Ошибка обнаружения трендов</b>\n\n{error_detail}",
                    parse_mode="HTML"
                )
                return
            except (httpx.ConnectError, httpx.ConnectTimeout, 
                    httpx.RemoteProtocolError, httpx.LocalProtocolError) as e:
                logger.error(
                    "Connection error detecting trends",
                    error=str(e),
                    error_type=type(e).__name__,
                    user_id=str(user_uuid) if user_uuid else None
                )
                await callback.message.edit_text(
                    "❌ <b>Ошибка подключения к серверу</b>\n\n"
                    "Не удалось установить соединение. Попробуйте позже.",
                    parse_mode="HTML"
                )
                return
            except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
                logger.error(
                    "Timeout detecting trends",
                    error=str(e),
                    error_type=type(e).__name__,
                    user_id=str(user_uuid) if user_uuid else None
                )
                await callback.message.edit_text(
                    "⏳ <b>Обнаружение трендов занимает больше времени</b>\n\n"
                    "Результаты будут доступны позже. Попробуйте проверить тренды через несколько минут.",
                    parse_mode="HTML"
                )
                return
            
            # Успешное обнаружение - загружаем результаты
            try:
                # Context7: Безопасное чтение response body перед парсингом JSON
                # Используем response.text для чтения тела ответа, чтобы избежать ошибок при закрытом соединении
                try:
                    response_text = r.text
                    if not response_text or not response_text.strip():
                        logger.warning(
                            "Empty response body for detect trends",
                            status_code=r.status_code,
                            user_id=str(user_uuid) if user_uuid else None
                        )
                        await callback.message.edit_text(
                            "⚠️ <b>Обнаружение завершено, но ответ пуст</b>\n\n"
                            "Попробуйте просмотреть тренды через меню.",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                                [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                            ]),
                            parse_mode="HTML"
                        )
                        return
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                        httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.NetworkError) as e:
                    logger.error(
                        "Error reading response body for detect trends",
                        error=str(e),
                        error_type=type(e).__name__,
                        status_code=r.status_code,
                        user_id=str(user_uuid) if user_uuid else None
                    )
                    await callback.message.edit_text(
                        "⚠️ <b>Обнаружение завершено, но не удалось прочитать ответ</b>\n\n"
                        "Проблема с сетью. Попробуйте просмотреть тренды через меню.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                            [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                        ]),
                        parse_mode="HTML"
                    )
                    return
                except Exception as e:
                    logger.error(
                        "Unexpected error reading response body for detect trends",
                        error=str(e),
                        error_type=type(e).__name__,
                        status_code=r.status_code,
                        user_id=str(user_uuid) if user_uuid else None
                    )
                    await callback.message.edit_text(
                        "⚠️ <b>Обнаружение завершено, но произошла ошибка</b>\n\n"
                        "Попробуйте просмотреть тренды через меню.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                            [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                        ]),
                        parse_mode="HTML"
                    )
                    return
                
                # Context7: Безопасный парсинг JSON из уже прочитанного текста
                try:
                    result = json.loads(response_text)
                except (json.JSONDecodeError, ValueError) as json_error:
                    logger.error(
                        "Error parsing JSON from detect trends response",
                        error=str(json_error),
                        error_type=type(json_error).__name__,
                        status_code=r.status_code,
                        response_preview=response_text[:200] if response_text else None,
                        user_id=str(user_uuid) if user_uuid else None
                    )
                    await callback.message.edit_text(
                        "⚠️ <b>Обнаружение завершено, но не удалось обработать ответ</b>\n\n"
                        "Попробуйте просмотреть тренды через меню.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                            [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                        ]),
                        parse_mode="HTML"
                    )
                    return
                except Exception as json_error:
                    logger.error(
                        "Unexpected error parsing JSON from detect trends response",
                        error=str(json_error),
                        error_type=type(json_error).__name__,
                        status_code=r.status_code,
                        user_id=str(user_uuid) if user_uuid else None
                    )
                    await callback.message.edit_text(
                        "⚠️ <b>Обнаружение завершено, но не удалось обработать ответ</b>\n\n"
                        "Попробуйте просмотреть тренды через меню.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                            [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                        ]),
                        parse_mode="HTML"
                    )
                    return
                
                trends_count = result.get("trends_count", 0)
                
                # Загружаем emerging и stable тренды (с обработкой ошибок внутри функций)
                # Context7: Используем ослабленные параметры для лучшего UX
                window_label, emerging_clusters = await _load_emerging_digest(client, user_id=user_uuid, limit=5)
                stable_trends = await _load_stable_digest(client, min_frequency=0, user_id=user_uuid, limit=5)
                
                logger.info(
                    "Trends loaded for digest",
                    emerging_count=len(emerging_clusters),
                    stable_count=len(stable_trends),
                    trends_count=trends_count,
                    user_id=str(user_uuid) if user_uuid else None
                )

                emerging_text = _format_emerging_digest(window_label, emerging_clusters)
                stable_text = _format_stable_digest(stable_trends)
                
                text = (
                    f"✅ <b>Обнаружение трендов завершено!</b>\n"
                    f"📊 Найдено трендов: {trends_count}\n\n"
                    f"{emerging_text}\n\n{stable_text}\n\n"
                    "Выбери действие:"
                )

                builder = InlineKeyboardBuilder()
                builder.button(text="🔥 Горячие", callback_data="trends:emerging")
                builder.button(text="🧊 Устойчивые", callback_data="trends:list")
                builder.button(text="🔙 Меню", callback_data="trends:menu")
                builder.adjust(1)

                await callback.message.edit_text(
                    text,
                    reply_markup=builder.as_markup(),
                    parse_mode="HTML",
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                    httpx.RemoteProtocolError, httpx.LocalProtocolError) as e:
                # Ошибка сети при загрузке результатов
                logger.error(
                    "Network error loading trend results",
                    error=str(e),
                    error_type=type(e).__name__,
                    user_id=str(user_uuid) if user_uuid else None
                )
                await callback.message.edit_text(
                    "⚠️ <b>Обнаружение завершено, но не удалось загрузить результаты</b>\n\n"
                    "Проблема с сетью. Попробуйте просмотреть тренды через меню.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                        [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                    ]),
                    parse_mode="HTML"
                )
            except Exception as e:
                # Ошибка при загрузке или форматировании результатов
                logger.error(
                    "Error loading trend results",
                    error=str(e),
                    error_type=type(e).__name__,
                    user_id=str(user_uuid) if user_uuid else None,
                    exc_info=True
                )
                await callback.message.edit_text(
                    "⚠️ <b>Обнаружение завершено, но не удалось загрузить результаты</b>\n\n"
                    "Попробуйте просмотреть тренды через меню.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                        [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                    ]),
                    parse_mode="HTML"
                )
    
    except httpx.TimeoutException as e:
        logger.error(
            "Timeout exception detecting trends",
            error=str(e),
            error_type=type(e).__name__
        )
        await callback.message.edit_text(
            "⏳ <b>Превышено время ожидания</b>\n\n"
            "Обнаружение трендов занимает больше времени, чем ожидалось. "
            "Результаты будут доступны позже.",
            parse_mode="HTML"
        )
    except (httpx.NetworkError, httpx.ProtocolError, httpx.TransportError, 
            httpx.ReadError, httpx.WriteError, httpx.DecodingError) as e:
        logger.error(
            "Network/Protocol error detecting trends",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        await callback.message.edit_text(
            "❌ <b>Ошибка сети</b>\n\n"
            "Не удалось выполнить запрос. Проверьте подключение и попробуйте позже.",
            parse_mode="HTML"
        )
    except Exception as e:
        # Логируем все детали для диагностики
        error_msg = str(e)
        error_type = type(e).__name__
        
        # Проверяем, не является ли это ошибкой "Server disconnected"
        if "Server disconnected" in error_msg or "disconnected" in error_msg.lower():
            logger.error(
                "Server disconnected error detecting trends",
                error=error_msg,
                error_type=error_type,
                exc_info=True
            )
            await callback.message.edit_text(
                "⚠️ <b>Сервер отключился во время обработки</b>\n\n"
                "Обнаружение трендов может быть завершено. Попробуйте просмотреть тренды через меню.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
                    [InlineKeyboardButton(text="🔙 Меню", callback_data="trends:menu")]
                ]),
                parse_mode="HTML"
            )
        else:
            logger.error(
                "Unexpected error detecting trends",
                error=error_msg,
                error_type=error_type,
                exc_info=True
            )
            await callback.message.edit_text(
                "❌ <b>Ошибка обнаружения трендов</b>\n\n"
                "Произошла непредвиденная ошибка. Попробуйте позже или обратитесь к администратору.",
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("trend:archive:"))
async def callback_trend_archive(callback: CallbackQuery):
    """Архивировать тренд."""
    trend_id = callback.data.split(":")[-1]
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{API_BASE}/api/trends/{trend_id}/archive")
            
            if r.status_code == 200:
                await callback.answer("✅ Тренд архивирован")
                await callback_trends_list(callback)  # Обновляем список
            else:
                error_detail = r.json().get("detail", "Неизвестная ошибка")
                await callback.answer(f"❌ Ошибка: {error_detail}", show_alert=True)
    
    except Exception as e:
        logger.error("Error archiving trend", error=str(e))
        await callback.answer("❌ Ошибка архивирования", show_alert=True)


@router.callback_query(F.data.startswith("trend:similar:"))
async def callback_trend_similar(callback: CallbackQuery):
    """Показать похожие тренды."""
    trend_id = callback.data.split(":")[-1]
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/api/trends/{trend_id}/similar",
                params={"limit": 5, "threshold": 0.7}
            )
            
            if r.status_code != 200:
                await callback.answer("❌ Ошибка получения похожих трендов", show_alert=True)
                return
            
            similar = r.json()
            
            if not similar:
                await callback.answer("📋 Похожих трендов не найдено", show_alert=True)
                return
            
            text = f"📋 <b>Похожие тренды</b>\n\n"
            for idx, trend in enumerate(similar[:5], 1):
                keyword = trend.get("trend_keyword", "N/A")
                similarity = trend.get("similarity", 0)
                text += f"{idx}. {keyword} (схожесть: {similarity:.1%})\n"
            
            await callback.message.answer(text, parse_mode="HTML")
            await callback.answer()
    
    except Exception as e:
        logger.error("Error showing similar trends", error=str(e))
        await callback.answer("❌ Ошибка загрузки похожих трендов", show_alert=True)


@router.callback_query(F.data.startswith("trend:cluster:"))
async def callback_trend_cluster(callback: CallbackQuery):
    """Показать детали кластера тренда."""
    cluster_id = callback.data.split(":")[-1]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            user_uuid = await _get_user_id(callback.from_user.id)
            params = {"user_id": str(user_uuid)} if user_uuid else None
            r = await client.get(f"{API_BASE}/api/trends/clusters/{cluster_id}", params=params)
        if r.status_code != 200:
            await callback.answer("❌ Кластер не найден", show_alert=True)
            return

        cluster = r.json()
        card = cluster.get("card") or {}
        stats = card.get("stats") or {}
        label = card.get("title") or cluster.get("label") or cluster.get("primary_topic") or "Без названия"
        summary = card.get("summary") or cluster.get("summary") or "Описание отсутствует."
        keywords = card.get("keywords") or cluster.get("keywords") or []
        topics = card.get("topics") or cluster.get("topics") or []
        time_window = card.get("time_window") or {}
        duration = time_window.get("duration_minutes")
        window_from = time_window.get("from")
        window_to = time_window.get("to")
        mentions = stats.get("mentions")
        baseline = stats.get("baseline")
        burst = stats.get("burst_score")
        sources = stats.get("sources") or cluster.get("source_diversity")
        channels = stats.get("channels") or sources
        novelty = cluster.get("novelty_score")
        why = card.get("why_important")
        example_posts = card.get("example_posts") or []

        text = f"ℹ️ <b>{label}</b>\n\n"
        if window_from and window_to:
            text += f"🕒 Окно: {window_from} — {window_to} (≈{duration or '?'} мин)\n"
        if why:
            text += f"❗ {why}\n"
        text += "\n"
        text += f"{summary}\n\n"
        text += f"📊 Упоминания: {mentions or '—'} (обычно {baseline or '—'})\n"
        if burst is not None:
            text += f"⚡ Burst: {burst:.1f}×\n"
        text += f"🗞 Источники: {sources or '—'} | 📡 Каналы: {channels or '—'}\n"
        if novelty is not None:
            text += f"🌀 Новизна: {novelty:.2f}\n"
        text += f"\n📎 Ключевые слова: {', '.join(keywords[:8]) or '—'}\n"
        if topics:
            text += f"🏷 Темы: {', '.join(topics[:6])}\n"
        if example_posts:
            text += "\n📝 Примеры постов:\n"
            for post in example_posts[:3]:
                channel_title = post.get("channel_title") or "Источник"
                posted_at = post.get("posted_at")
                snippet = (post.get("content_snippet") or "").replace("\n", " ")
                text += f"• {channel_title}"
                if posted_at:
                    text += f" ({posted_at})"
                if snippet:
                    text += f": {snippet[:160]}"
                text += "\n"

        builder = InlineKeyboardBuilder()
        builder.button(text="📈 Стабильные", callback_data="trends:list")
        builder.button(text="🔥 Горящие", callback_data="trends:emerging")
        builder.button(text="🔙 Меню", callback_data="trends:menu")
        builder.adjust(1)

        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()

    except Exception as e:
        logger.error("Error showing cluster detail", error=str(e))
        await callback.answer("❌ Ошибка загрузки кластера", show_alert=True)

