#!/usr/bin/env python3
"""
Скрипт для обновления tg_channel_id у каналов через Telegram API
"""

import asyncio
import sys
import os
sys.path.append('/opt/telegram-assistant')

from telethon import TelegramClient
from telethon.sessions import StringSession
import psycopg2
from psycopg2.extras import RealDictCursor

# Настройки из переменных окружения
API_ID = int(os.getenv('MASTER_API_ID', '0'))
API_HASH = os.getenv('MASTER_API_HASH', '')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@supabase-db:5432/postgres')

async def update_channel_ids():
    """Обновление tg_channel_id для каналов без него."""
    
    # Подключение к БД
    conn = psycopg2.connect(DATABASE_URL)
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Получаем каналы без tg_channel_id
            cursor.execute("""
                SELECT id, username, title 
                FROM channels 
                WHERE is_active = true 
                AND tg_channel_id IS NULL 
                AND username IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 20
            """)
            channels = cursor.fetchall()
            
            if not channels:
                print("Нет каналов для обновления")
                return
            
            print(f"Найдено {len(channels)} каналов для обновления")
            
            # Получаем активную сессию
            cursor.execute("""
                SELECT session_string_enc, key_id 
                FROM telegram_sessions 
                WHERE status = 'active' 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
            session_data = cursor.fetchone()
            
            if not session_data:
                print("Нет активных Telegram сессий")
                return
            
            # Расшифровка сессии (упрощенная версия)
            # В реальной системе нужно использовать правильную расшифровку
            session_string = session_data['session_string_enc']
            
            # Создание Telegram клиента
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            
            try:
                await client.start()
                print("Telegram клиент запущен")
                
                for channel in channels:
                    try:
                        print(f"Обрабатываем канал: {channel['username']}")
                        
                        # Context7: Нормализация username - убираем @ из начала
                        # Telethon ожидает username без @
                        clean_username = channel['username'].lstrip('@') if channel['username'] else None
                        if not clean_username:
                            print(f"  ❌ Пустой username для канала {channel['id']}")
                            continue
                        
                        # Получение entity канала
                        entity = await client.get_entity(clean_username)
                        
                        if hasattr(entity, 'id'):
                            tg_channel_id = entity.id
                            print(f"  Найден ID: {tg_channel_id}")
                            
                            # Обновление в БД
                            cursor.execute("""
                                UPDATE channels 
                                SET tg_channel_id = %s 
                                WHERE id = %s
                            """, (tg_channel_id, channel['id']))
                            
                            print(f"  ✅ Обновлен: {channel['username']} -> {tg_channel_id}")
                        else:
                            print(f"  ❌ Не удалось получить ID для {channel['username']}")
                            
                    except Exception as e:
                        print(f"  ❌ Ошибка для {channel['username']}: {e}")
                        continue
                
                conn.commit()
                print("Обновление завершено")
                
            finally:
                await client.disconnect()
                
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(update_channel_ids())



