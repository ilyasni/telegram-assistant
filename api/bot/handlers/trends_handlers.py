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
from typing import Optional
from uuid import UUID

logger = structlog.get_logger()
router = Router()

# API base URL
API_BASE = "http://api:8000"


def _kb_trends_menu():
    """Клавиатура главного меню трендов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Список трендов", callback_data="trends:list")],
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


@router.callback_query(F.data == "trends:list")
async def callback_trends_list(callback: CallbackQuery):
    """Показать список трендов."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/api/trends/",
                params={
                    "min_frequency": 10,
                    "min_growth": 0.0,
                    "min_engagement": 0.0,
                    "status": "active",
                    "page": 1,
                    "page_size": 10
                }
            )
            
            if r.status_code != 200:
                await callback.answer("❌ Ошибка получения трендов", show_alert=True)
                return
            
            result = r.json()
            trends = result.get("trends", [])
            total = result.get("total", 0)
            
            if not trends:
                await callback.message.edit_text(
                    "📈 <b>Тренды</b>\n\n"
                    "Активных трендов не найдено.\n\n"
                    "Используйте кнопку '🔍 Обнаружить тренды' для поиска.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔍 Обнаружить", callback_data="trends:detect")],
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="trends:menu")]
                    ]),
                    parse_mode="HTML"
                )
                await callback.answer()
                return
            
            # Форматируем список трендов
            text = f"📈 <b>Активные тренды</b> (всего: {total})\n\n"
            
            for idx, trend in enumerate(trends[:10], 1):
                keyword = trend.get("trend_keyword", "N/A")
                frequency = trend.get("frequency_count", 0)
                growth = trend.get("growth_rate")
                engagement = trend.get("engagement_score")
                
                growth_text = f"📈 {growth:.1%}" if growth else "—"
                engagement_text = f"⭐ {engagement:.1f}" if engagement else "—"
                
                text += (
                    f"{idx}. <b>{keyword}</b>\n"
                    f"   📊 Частота: {frequency} | Рост: {growth_text} | Engagement: {engagement_text}\n\n"
                )
            
            builder = InlineKeyboardBuilder()
            for trend in trends[:5]:
                trend_id = trend.get("id")
                keyword = trend.get("trend_keyword", "N/A")[:30]
                builder.button(
                    text=f"📌 {keyword}",
                    callback_data=f"trend:view:{trend_id}"
                )
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
    await callback.answer("⏳ Обнаружаю тренды...")
    
    try:
        async with httpx.AsyncClient(timeout=120) as client:
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
                trends = result.get("trends", [])
                
                text = (
                    f"✅ <b>Обнаружение трендов завершено!</b>\n\n"
                    f"📊 Найдено трендов: {trends_count}\n\n"
                )
                
                if trends:
                    text += "<b>Топ тренды:</b>\n"
                    for idx, trend in enumerate(trends[:5], 1):
                        keyword = trend.get("keyword", "N/A")
                        frequency = trend.get("frequency", 0)
                        text += f"{idx}. {keyword} ({frequency} упоминаний)\n"
                
                await callback.message.answer(text, parse_mode="HTML")
                await callback.answer(f"✅ Найдено {trends_count} трендов")
                
                # Обновляем список трендов
                await callback_trends_list(callback)
            else:
                error_detail = r.json().get("detail", "Неизвестная ошибка")
                await callback.message.answer(
                    f"❌ <b>Ошибка обнаружения трендов</b>\n\n{error_detail}",
                    parse_mode="HTML"
                )
                await callback.answer("❌ Ошибка обнаружения", show_alert=True)
    
    except httpx.TimeoutException:
        await callback.message.answer(
            "⏳ <b>Обнаружение трендов занимает больше времени</b>\n\n"
            "Результаты будут доступны позже.",
            parse_mode="HTML"
        )
        await callback.answer("⏳ Превышено время ожидания", show_alert=True)
    except Exception as e:
        logger.error("Error detecting trends", error=str(e))
        await callback.message.answer(
            "❌ <b>Ошибка обнаружения трендов</b>\n\n"
            "Попробуйте позже.",
            parse_mode="HTML"
        )
        await callback.answer("❌ Ошибка обнаружения", show_alert=True)


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

