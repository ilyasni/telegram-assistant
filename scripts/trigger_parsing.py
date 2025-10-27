#!/usr/bin/env python3
"""
Скрипт для запуска парсинга каналов за последние 24 часа
"""

import asyncio
import sys
import os
sys.path.append('/opt/telegram-assistant')

from telethon_ingest.services.telegram_client import TelegramIngestionService

async def main():
    print("Запуск парсинга каналов за последние 24 часа...")
    
    # Создаем сервис
    service = TelegramIngestionService()
    
    try:
        # Запускаем сервис
        await service.start()
        
        # Запускаем исторический парсинг
        await service._start_historical_parsing()
        
        print("Парсинг завершен")
        
    except Exception as e:
        print(f"Ошибка при парсинге: {e}")
    finally:
        # Останавливаем сервис
        await service.stop()

if __name__ == "__main__":
    asyncio.run(main())
