#!/usr/bin/env python3
"""
Скрипт для сброса UI кнопок Telegram бота (Menu Button и Main App).

Использование:
    python scripts/reset_bot_menu_button.py
    или
    docker compose exec api python scripts/reset_bot_menu_button.py
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "api"))

from aiogram import Bot
from aiogram.types import MenuButtonDefault
from config import settings
import structlog

logger = structlog.get_logger()


async def main():
    """Основная функция для сброса кнопок меню бота."""
    logger.info("Starting bot menu button reset...")
    
    # Проверяем наличие токена
    token = os.getenv("TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set; cannot reset menu button")
        sys.exit(1)
    
    bot = Bot(token=token)
    
    try:
        # Получаем текущую кнопку меню
        current_button = await bot.get_chat_menu_button()
        logger.info("Current menu button", button_type=type(current_button).__name__)
        
        # Сбрасываем кнопку меню на стандартную кнопку команд
        # В Telegram Bot API нельзя полностью удалить Menu Button,
        # можно только изменить его тип
        logger.info("Resetting menu button to default commands button...")
        from aiogram.types import MenuButtonCommands
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        
        # Проверяем результат
        new_button = await bot.get_chat_menu_button()
        logger.info("Menu button reset successfully", new_button_type=type(new_button).__name__)
        
        print("\n✅ Кнопка меню бота сброшена на стандартную кнопку команд")
        print(f"   Тип кнопки: {type(new_button).__name__}")
        print("\n💡 Примечание:")
        print("   - Menu Button теперь показывает стандартную кнопку команд")
        print("   - Для полного удаления Menu Button и Main App:")
        print("     1. Откройте настройки бота в Telegram")
        print("     2. Перейдите в раздел 'Mini Apps'")
        print("     3. Отключите 'Menu Button' и 'Main App'")
        print("   - Или используйте BotFather: /mybots → выберите бота → Bot Settings → Menu Button")
        
        sys.exit(0)
    except Exception as e:
        logger.error("Failed to reset menu button", error=str(e))
        print(f"\n❌ Ошибка при сбросе кнопки меню: {e}")
        sys.exit(1)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

