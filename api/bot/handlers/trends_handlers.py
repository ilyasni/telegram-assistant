"""
Telegram Bot handlers для управления трендами.
Context7: Интеграция с Trends API через HTTP клиент
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx
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
                user_data = r.json()
                return UUID(user_data.get("id"))
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
            detail = response.json().get("detail", "Неизвестная ошибка")
            return False, detail
        return True, ""
    except Exception as exc:
        logger.error("Error updating trend subscription", error=str(exc))
        return False, str(exc)


async def _load_emerging_digest(client: httpx.AsyncClient, window: str = "3h", limit: int = 5, user_id: Optional[UUID] = None) -> Tuple[str, List[Dict[str, Any]]]:
    response = await client.get(
        f"{API_BASE}/api/trends/emerging",
        params={
            "min_sources": 1,
            "min_burst": 0.8,
            "page": 1,
            "page_size": max(limit, 1),
            "window": window,
            **({"user_id": str(user_id)} if user_id else {}),
        },
    )
    if response.status_code != 200:
        return window, []
    payload = response.json()
    return payload.get("window") or window, payload.get("clusters", [])


async def _load_stable_digest(client: httpx.AsyncClient, min_frequency: int = 10, limit: int = 5, user_id: Optional[UUID] = None) -> List[Dict[str, Any]]:
    params = {
        "min_frequency": min_frequency,
        "status": "stable",
        "page": 1,
        "page_size": max(limit, 1),
        **({"user_id": str(user_id)} if user_id else {}),
    }
    response = await client.get(f"{API_BASE}/api/trends/clusters", params=params)
    if response.status_code != 200:
        return []
    payload = response.json()
    clusters = payload.get("clusters", [])
    if clusters:
        return clusters
    params["status"] = "emerging"
    fallback = await client.get(f"{API_BASE}/api/trends/clusters", params=params)
    if fallback.status_code != 200:
        return []
    return fallback.json().get("clusters", [])


def _format_emerging_digest(window_label: str, clusters: List[Dict[str, Any]]) -> str:
    if not clusters:
        return f"🔥 <b>Горячие тренды за {window_label}</b>\n— пока пусто, запусти обнаружение позже."
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
        return "🧊 <b>Устойчивые тренды за 7 дней</b>\n— пока ничего интересного."
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
            payload = r.json()
            clusters = payload.get("clusters", [])
            if not clusters:
                params["status"] = "emerging"
                r = await client.get(f"{API_BASE}/api/trends/clusters", params=params)
                if r.status_code == 200:
                    payload = r.json()
                    clusters = payload.get("clusters", [])
                    if clusters:
                        status_used = "emerging"

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

        payload = r.json()
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
            
            trend = r.json()
            
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
    """Запустить обнаружение трендов."""
    await callback.answer("⏳ Обнаружение запущено...")
    try:
        await callback.message.edit_text(
            "⏳ <b>Обнаруживаю тренды…</b>\nЭто может занять до 20 секунд.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            user_uuid = await _get_user_id(callback.from_user.id)
            r = await client.post(
                f"{API_BASE}/api/trends/detect",
                params={
                    "days": 7,
                    "min_frequency": 10,
                    "min_growth": 0.2,
                    "min_engagement": 5.0
                }
            )
            
            if r.status_code == 200:
                result = r.json()
                trends_count = result.get("trends_count", 0)
                window_label, emerging_clusters = await _load_emerging_digest(client, user_id=user_uuid)
                stable_trends = await _load_stable_digest(client, user_id=user_uuid)

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
            else:
                error_detail = r.json().get("detail", "Неизвестная ошибка")
                await callback.message.edit_text(
                    f"❌ <b>Ошибка обнаружения трендов</b>\n\n{error_detail}",
                    parse_mode="HTML"
                )
    
    except httpx.TimeoutException:
        await callback.message.edit_text(
            "⏳ <b>Обнаружение трендов занимает больше времени</b>\n\n"
            "Результаты будут доступны позже.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error("Error detecting trends", error=str(e))
        await callback.message.edit_text(
            "❌ <b>Ошибка обнаружения трендов</b>\n\n"
            "Попробуйте позже.",
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

