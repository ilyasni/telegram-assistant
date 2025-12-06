#!/usr/bin/env python3
"""
Утилита для получения chat_id группы/канала для ALERT_TELEGRAM_CHAT_ID.

Использование:
    python scripts/get_alert_chat_id.py @testgroupassistant
    python scripts/get_alert_chat_id.py https://t.me/testgroupassistant
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "api"))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import structlog

logger = structlog.get_logger()


async def get_chat_id(username_or_url: str) -> None:
    """
    Получение chat_id для группы/канала по username или ссылке.
    
    Args:
        username_or_url: Username (например: @testgroupassistant) или ссылка (https://t.me/testgroupassistant)
    """
    # Получаем токен бота
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("❌ Ошибка: TELEGRAM_BOT_TOKEN не установлен")
        print("   Установите переменную окружения TELEGRAM_BOT_TOKEN")
        return
    
    # Извлекаем username из ссылки или @username
    username = username_or_url.replace('https://t.me/', '').replace('http://t.me/', '').lstrip('@')
    
    if not username:
        print("❌ Ошибка: Не удалось извлечь username")
        print("   Используйте формат: @username или https://t.me/username")
        return
    
    # Создаем бота
    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    try:
        print(f"🔍 Получение chat_id для @{username}...")
        
        # Получаем информацию о чате
        chat = await bot.get_chat(f"@{username}")
        
        print(f"\n✅ Chat ID найден!")
        print(f"   Username: @{username}")
        print(f"   Title: {chat.title}")
        print(f"   Type: {chat.type}")
        print(f"   Chat ID: {chat.id}")
        print(f"\n📋 Добавьте в .env файл:")
        print(f"   ALERT_TELEGRAM_CHAT_ID={chat.id}")
        print(f"\n   Или используйте username (будет автоматически разрешен):")
        print(f"   ALERT_TELEGRAM_CHAT_ID=@{username}")
        
    except Exception as e:
        print(f"❌ Ошибка при получении chat_id: {e}")
        print(f"\n💡 Возможные причины:")
        print(f"   1. Бот не добавлен в группу/канал")
        print(f"   2. Неверный username или ссылка")
        print(f"   3. Группа/канал не существует")
        print(f"\n📝 Инструкция:")
        print(f"   1. Добавьте бота в группу/канал как администратора")
        print(f"   2. Убедитесь, что бот имеет права на отправку сообщений")
        print(f"   3. Попробуйте снова")
        
    finally:
        await bot.session.close()


def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print(f"  python {sys.argv[0]} @username")
        print(f"  python {sys.argv[0]} https://t.me/username")
        print(f"\nПример:")
        print(f"  python {sys.argv[0]} @testgroupassistant")
        sys.exit(1)
    
    username_or_url = sys.argv[1]
    asyncio.run(get_chat_id(username_or_url))


if __name__ == "__main__":
    main()

