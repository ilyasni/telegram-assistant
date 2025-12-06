#!/usr/bin/env python3
"""
Скрипт для сброса клавиатуры ответа (Reply Keyboard) у пользователя Telegram бота.

Использование:
    python scripts/reset_user_keyboard.py <telegram_user_id>
    или
    docker compose exec api python scripts/reset_user_keyboard.py <telegram_user_id>
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "api"))

from aiogram import Bot
from aiogram.types import ReplyKeyboardRemove
from config import settings
import structlog

logger = structlog.get_logger()


async def main():
    """Основная функция для сброса клавиатуры пользователя."""
    if len(sys.argv) < 2:
        print("Использование: python scripts/reset_user_keyboard.py <telegram_user_id>")
        print("\nПример:")
        print("  python scripts/reset_user_keyboard.py 123456789")
        sys.exit(1)
    
    try:
        telegram_user_id = int(sys.argv[1])
    except ValueError:
        print(f"❌ Ошибка: '{sys.argv[1]}' не является числом")
        sys.exit(1)
    
    logger.info("Starting keyboard reset for user", telegram_user_id=telegram_user_id)
    
    # Проверяем наличие токена
    token = os.getenv("TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set; cannot reset keyboard")
        sys.exit(1)
    
    bot = Bot(token=token)
    
    try:
        # Отправляем сообщение с удалением клавиатуры
        logger.info("Sending message to remove keyboard...")
        await bot.send_message(
            chat_id=telegram_user_id,
            text="⌨️ Клавиатура сброшена. Стандартная клавиатура восстановлена.",
            reply_markup=ReplyKeyboardRemove(remove_keyboard=True)
        )
        
        logger.info("Keyboard reset successfully")
        print(f"\n✅ Клавиатура пользователя {telegram_user_id} сброшена")
        print("   Пользователь получит сообщение с подтверждением")
        
        sys.exit(0)
    except Exception as e:
        logger.error("Failed to reset keyboard", error=str(e), telegram_user_id=telegram_user_id)
        print(f"\n❌ Ошибка при сбросе клавиатуры: {e}")
        print("\nВозможные причины:")
        print("  - Пользователь не запускал бота (/start)")
        print("  - Неверный telegram_user_id")
        print("  - Пользователь заблокировал бота")
        sys.exit(1)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

