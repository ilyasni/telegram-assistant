#!/usr/bin/env python3
"""
Скрипт для ручной установки команд Telegram бота.

Использование:
    python scripts/setup_bot_commands.py
    или
    docker compose exec api python scripts/setup_bot_commands.py
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "api"))

from bot.webhook import init_bot, set_bot_commands
from config import settings
import structlog

logger = structlog.get_logger()


async def main():
    """Основная функция для установки команд бота."""
    logger.info("Starting bot commands setup...")
    
    # Проверяем наличие токена
    token = os.getenv("TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set; cannot setup commands")
        sys.exit(1)
    
    # Инициализируем бота
    logger.info("Initializing bot...")
    init_bot()
    
    # Проверяем, что бот инициализирован
    from bot.webhook import bot
    if not bot:
        logger.error("Bot initialization failed")
        sys.exit(1)
    
    logger.info("Bot initialized successfully")
    
    # Устанавливаем команды
    logger.info("Setting bot commands...")
    try:
        await set_bot_commands()
        logger.info("Bot commands setup completed successfully")
        sys.exit(0)
    except Exception as e:
        logger.error("Failed to setup bot commands", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

