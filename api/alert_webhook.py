"""
Webhook receiver для AlertManager.

Context7: Обрабатывает webhook запросы от AlertManager и отправляет уведомления
в Telegram через существующего бота.
"""

from fastapi import APIRouter, Request, HTTPException
from typing import Dict, Any, List, Optional
import structlog
from datetime import datetime
import os

logger = structlog.get_logger()
router = APIRouter()

# ID чата для отправки уведомлений (из переменной окружения)
# Может быть числовым ID (например: -1001234567890) или username (например: @testgroupassistant)
ALERT_CHAT_ID = os.getenv("ALERT_TELEGRAM_CHAT_ID")


def format_alert_message(alerts: List[Dict[str, Any]]) -> str:
    """
    Форматирование сообщения об алерте для Telegram.
    
    Args:
        alerts: Список алертов от AlertManager
        
    Returns:
        Отформатированное сообщение в HTML
    """
    if not alerts:
        return "⚠️ Пустой список алертов"
    
    # Группируем алерты по статусу
    firing = [a for a in alerts if a.get('status') == 'firing']
    resolved = [a for a in alerts if a.get('status') == 'resolved']
    
    parts = []
    
    # Критические алерты
    if firing:
        parts.append("🚨 <b>КРИТИЧЕСКИЕ АЛЕРТЫ</b>\n")
        for alert in firing:
            labels = alert.get('labels', {})
            annotations = alert.get('annotations', {})
            
            alertname = labels.get('alertname', 'Unknown')
            severity = labels.get('severity', 'unknown')
            summary = annotations.get('summary', 'Нет описания')
            description = annotations.get('description', '')
            
            parts.append(f"<b>{alertname}</b> [{severity}]")
            parts.append(f"📋 {summary}")
            if description:
                parts.append(f"ℹ️ {description}")
            
            # Дополнительные метки
            component = labels.get('component', '')
            if component:
                parts.append(f"🔧 Компонент: {component}")
            
            parts.append("")  # Пустая строка между алертами
    
    # Разрешенные алерты
    if resolved:
        parts.append("✅ <b>РАЗРЕШЕННЫЕ АЛЕРТЫ</b>\n")
        for alert in resolved:
            labels = alert.get('labels', {})
            annotations = alert.get('annotations', {})
            
            alertname = labels.get('alertname', 'Unknown')
            summary = annotations.get('summary', 'Нет описания')
            
            parts.append(f"<b>{alertname}</b> - {summary}")
            parts.append("")
    
    return "\n".join(parts)


async def send_telegram_alert(message: str) -> bool:
    """
    Отправка уведомления в Telegram через бота.
    
    Args:
        message: Текст сообщения (HTML)
        
    Returns:
        True если отправлено успешно, False иначе
    """
    if not ALERT_CHAT_ID:
        logger.warning("ALERT_TELEGRAM_CHAT_ID not set, skipping Telegram alert")
        return False
    
    try:
        from bot.webhook import bot, init_bot
        
        # Context7: Инициализируем бота, если он не инициализирован
        if not bot:
            logger.info("Bot not initialized, attempting to initialize...")
            init_bot()
            from bot.webhook import bot as bot_after_init
            if not bot_after_init:
                logger.warning("Bot not initialized, cannot send alert")
                return False
            bot = bot_after_init
        
        # Определяем chat_id: если это username (начинается с @), получаем ID через get_chat
        chat_id = ALERT_CHAT_ID
        if ALERT_CHAT_ID.startswith('@') or ALERT_CHAT_ID.startswith('https://t.me/'):
            # Извлекаем username из ссылки или @username
            username = ALERT_CHAT_ID.replace('https://t.me/', '').lstrip('@')
            try:
                # Пытаемся получить chat через username
                chat = await bot.get_chat(f"@{username}")
                chat_id = chat.id
                chat_type = chat.type if hasattr(chat, 'type') else 'unknown'
                logger.info(
                    "Resolved username to chat_id",
                    username=username,
                    chat_id=chat_id,
                    chat_type=chat_type,
                    title=getattr(chat, 'title', 'N/A')
                )
                
                # Context7: Для каналов проверяем, является ли бот администратором
                if chat_type in ['channel', 'supergroup']:
                    try:
                        member = await bot.get_chat_member(chat_id, bot.id)
                        member_status = member.status if hasattr(member, 'status') else 'unknown'
                        logger.info(
                            "Bot member status in channel",
                            chat_id=chat_id,
                            status=member_status
                        )
                        if member_status not in ['administrator', 'creator']:
                            logger.warning(
                                "Bot is not administrator in channel",
                                chat_id=chat_id,
                                status=member_status,
                                hint="Bot must be administrator to send messages to channel"
                            )
                    except Exception as member_error:
                        logger.warning(
                            "Failed to check bot member status",
                            chat_id=chat_id,
                            error=str(member_error)
                        )
            except Exception as e:
                logger.error(
                    "Failed to resolve username to chat_id",
                    username=username,
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True
                )
                return False
        
        # Отправляем сообщение
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="HTML"
            )
            logger.info("Alert sent to Telegram", chat_id=chat_id, original=ALERT_CHAT_ID)
            return True
        except Exception as send_error:
            logger.error(
                "Failed to send message to Telegram",
                chat_id=chat_id,
                error=str(send_error),
                error_type=type(send_error).__name__,
                exc_info=True
            )
            return False
        
    except Exception as e:
        logger.error("Failed to send Telegram alert", error=str(e), exc_info=True)
        return False


@router.post("/alertmanager/webhook")
async def alertmanager_webhook(request: Request):
    """
    Webhook endpoint для получения алертов от AlertManager.
    
    Context7: Обрабатывает POST запросы от AlertManager в формате AlertManager webhook.
    Формат: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
    """
    try:
        data = await request.json()
        
        # Проверяем формат данных AlertManager
        version = data.get('version', '4')
        if version not in ['3', '4']:
            logger.warning("Unsupported AlertManager webhook version", version=version)
            raise HTTPException(status_code=400, detail=f"Unsupported version: {version}")
        
        # Извлекаем алерты
        alerts = data.get('alerts', [])
        if not alerts:
            logger.debug("Empty alerts list received")
            return {"status": "ok", "message": "No alerts"}
        
        # Фильтруем только critical алерты (должно быть настроено в AlertManager, но проверяем на всякий случай)
        critical_alerts = [
            a for a in alerts 
            if a.get('labels', {}).get('severity') == 'critical'
        ]
        
        if not critical_alerts:
            logger.debug("No critical alerts in webhook payload")
            return {"status": "ok", "message": "No critical alerts"}
        
        # Форматируем сообщение
        message = format_alert_message(critical_alerts)
        
        # Отправляем в Telegram
        success = await send_telegram_alert(message)
        
        if success:
            logger.info(
                "AlertManager webhook processed",
                alerts_count=len(critical_alerts),
                status="sent"
            )
            return {"status": "ok", "message": "Alerts sent to Telegram"}
        else:
            logger.warning(
                "AlertManager webhook processed but failed to send",
                alerts_count=len(critical_alerts),
                status="failed"
            )
            # Возвращаем 200, чтобы AlertManager не повторял запрос
            return {"status": "ok", "message": "Failed to send, but acknowledged"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error processing AlertManager webhook", error=str(e), exc_info=True)
        # Возвращаем 200, чтобы AlertManager не повторял запрос при ошибках
        return {"status": "error", "message": str(e)}


@router.get("/alertmanager/health")
async def alertmanager_webhook_health():
    """
    Health check для webhook endpoint.
    """
    return {
        "status": "ok",
        "chat_id_configured": bool(ALERT_CHAT_ID),
        "bot_available": False  # Будет проверено при первом запросе
    }

