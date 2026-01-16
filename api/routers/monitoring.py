"""
Мониторинг и диагностика системы.

Context7: Endpoints для мониторинга контейнеров, пайплайна и системы.
"""

from fastapi import APIRouter
from typing import Dict, Any, List
import structlog

from utils.container_monitor import ContainerMonitor
# Context7: Импорт webhook receiver для AlertManager
# Используем относительный импорт из родительского каталога
import sys
from pathlib import Path
# Добавляем корень api/ в путь для импорта alert_webhook
api_root = Path(__file__).parent.parent
if str(api_root) not in sys.path:
    sys.path.insert(0, str(api_root))
from alert_webhook import router as alert_webhook_router

router = APIRouter()
logger = structlog.get_logger()

# Подключаем роутер для AlertManager webhook
router.include_router(alert_webhook_router)

# Глобальный экземпляр мониторинга контейнеров
_container_monitor: ContainerMonitor = None


def get_container_monitor() -> ContainerMonitor:
    """Получить экземпляр мониторинга контейнеров."""
    global _container_monitor
    if _container_monitor is None:
        _container_monitor = ContainerMonitor()
    return _container_monitor


@router.get("/containers")
async def get_containers_status():
    """
    Получить статус всех критичных контейнеров.
    
    Context7: Мониторинг перезапусков и uptime контейнеров.
    """
    try:
        monitor = get_container_monitor()
        containers = await monitor.get_all_containers_status()
        recent_restarts = await monitor.get_recent_restarts(minutes=30)
        
        return {
            "containers": containers,
            "recent_restarts": recent_restarts,
            "critical_services": monitor.critical_services,
            "total_services": len(containers),
            "unhealthy_count": sum(1 for c in containers.values() if c.get('unhealthy')),
            "restarting_count": sum(1 for c in containers.values() if c.get('restarting'))
        }
    except Exception as e:
        logger.error("Failed to get containers status", error=str(e))
        return {
            "error": str(e),
            "containers": {},
            "recent_restarts": []
        }


@router.get("/containers/{service_name}")
async def get_container_status(service_name: str):
    """
    Получить статус конкретного контейнера.
    
    Args:
        service_name: Имя сервиса (api, worker, etc.)
    """
    try:
        monitor = get_container_monitor()
        status = await monitor.get_container_status(service_name)
        
        if status is None:
            return {"error": f"Container '{service_name}' not found"}
        
        return status
    except Exception as e:
        logger.error("Failed to get container status", service=service_name, error=str(e))
        return {"error": str(e)}


@router.post("/bot/setup-commands")
async def setup_bot_commands():
    """
    Ручная установка команд Telegram бота.
    
    Context7: Endpoint для ручного запуска установки команд бота.
    Полезно, если команды не установились автоматически при старте.
    """
    try:
        from bot.webhook import init_bot, set_bot_commands, bot
        
        # Инициализируем бота, если еще не инициализирован
        if not bot:
            logger.info("Bot not initialized, initializing...")
            init_bot()
        
        if not bot:
            return {
                "success": False,
                "error": "Bot not configured. Check TELEGRAM_BOT_TOKEN."
            }
        
        # Устанавливаем команды
        await set_bot_commands()
        
        # Проверяем результат
        registered_commands = await bot.get_my_commands()
        
        return {
            "success": True,
            "message": "Bot commands set successfully",
            "commands_count": len(registered_commands),
            "commands": [
                {"command": cmd.command, "description": cmd.description}
                for cmd in registered_commands
            ]
        }
    except Exception as e:
        logger.error("Failed to setup bot commands", error=str(e), exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/bot/reset-menu-button")
async def reset_bot_menu_button():
    """
    Сброс UI кнопок Telegram бота (Menu Button и Main App).
    
    Context7: Endpoint для сброса Menu Button на дефолтное состояние.
    Main App нужно отключать через интерфейс Telegram или BotFather.
    """
    try:
        from bot.webhook import init_bot, bot
        from aiogram.types import MenuButtonDefault
        
        # Инициализируем бота, если еще не инициализирован
        if not bot:
            logger.info("Bot not initialized, initializing...")
            init_bot()
        
        if not bot:
            return {
                "success": False,
                "error": "Bot not configured. Check TELEGRAM_BOT_TOKEN."
            }
        
        # Получаем текущую кнопку
        current_button = await bot.get_chat_menu_button()
        
        # Сбрасываем на стандартную кнопку команд
        # В Telegram Bot API нельзя полностью удалить Menu Button,
        # можно только изменить его тип
        from aiogram.types import MenuButtonCommands
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        
        # Проверяем результат
        new_button = await bot.get_chat_menu_button()
        
        return {
            "success": True,
            "message": "Menu button reset to default commands button",
            "previous_button_type": type(current_button).__name__,
            "new_button_type": type(new_button).__name__,
            "note": "Для полного удаления Menu Button и Main App используйте интерфейс Telegram или BotFather"
        }
    except Exception as e:
        logger.error("Failed to reset menu button", error=str(e), exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/bot/reset-user-keyboard/{telegram_user_id}")
async def reset_user_keyboard(telegram_user_id: int):
    """
    Сброс клавиатуры ответа (Reply Keyboard) у конкретного пользователя.
    
    Context7: Endpoint для удаления кастомной клавиатуры у пользователя
    и восстановления стандартной клавиатуры Telegram.
    """
    try:
        from bot.webhook import init_bot, bot
        from aiogram.types import ReplyKeyboardRemove
        
        # Инициализируем бота, если еще не инициализирован
        if not bot:
            logger.info("Bot not initialized, initializing...")
            init_bot()
        
        if not bot:
            return {
                "success": False,
                "error": "Bot not configured. Check TELEGRAM_BOT_TOKEN."
            }
        
        # Отправляем сообщение с удалением клавиатуры
        await bot.send_message(
            chat_id=telegram_user_id,
            text="⌨️ Клавиатура сброшена. Стандартная клавиатура восстановлена.",
            reply_markup=ReplyKeyboardRemove(remove_keyboard=True)
        )
        
        return {
            "success": True,
            "message": f"Keyboard reset for user {telegram_user_id}",
            "telegram_user_id": telegram_user_id
        }
    except Exception as e:
        logger.error(
            "Failed to reset user keyboard",
            error=str(e),
            telegram_user_id=telegram_user_id,
            exc_info=True
        )
        return {
            "success": False,
            "error": str(e),
            "telegram_user_id": telegram_user_id
        }

