#!/usr/bin/env python3
"""
Context7: Создание user_channel для существующих каналов.

Скрипт создаёт user_channel связи для всех каналов, где они отсутствуют.
Это необходимо для корректной работы сохранения альбомов.
"""

import asyncio
import asyncpg
import os
import sys

async def create_user_channel_for_existing_channels():
    """Создание user_channel для существующих каналов."""
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@supabase-db:5432/postgres')
    conn = await asyncpg.connect(db_url)
    
    try:
        # Получаем все каналы без user_channel
        channels_without_user_channel = await conn.fetch('''
            SELECT DISTINCT c.id as channel_id, c.tg_channel_id
            FROM channels c
            WHERE NOT EXISTS (
                SELECT 1 FROM user_channel uc WHERE uc.channel_id = c.id
            )
            LIMIT 100
        ''')
        
        print(f'Найдено каналов без user_channel: {len(channels_without_user_channel)}')
        
        if not channels_without_user_channel:
            print('✅ Все каналы имеют user_channel')
            return
        
        created_count = 0
        error_count = 0
        
        for row in channels_without_user_channel:
            channel_id = row['channel_id']
            tg_channel_id = row['tg_channel_id']
            
            # Находим пользователя по telegram_id (берём первого доступного)
            # В реальном сценарии нужно использовать правильный user_id
            user = await conn.fetchrow('''
                SELECT id FROM users LIMIT 1
            ''')
            
            if not user:
                print(f'❌ Нет пользователей в БД для канала {channel_id}')
                error_count += 1
                continue
            
            user_id = user['id']
            
            # Создаём user_channel
            try:
                await conn.execute('''
                    INSERT INTO user_channel (user_id, channel_id, is_active, subscribed_at, settings)
                    VALUES ($1, $2, true, NOW(), '{}'::jsonb)
                    ON CONFLICT (user_id, channel_id) DO NOTHING
                ''', user_id, channel_id)
                
                created_count += 1
                print(f'✅ Создан user_channel для канала {channel_id} (tg_id: {tg_channel_id})')
            except Exception as e:
                error_count += 1
                print(f'❌ Ошибка создания user_channel для канала {channel_id}: {e}')
        
        print(f'\n{"="*70}')
        print(f'Итого: создано {created_count}, ошибок {error_count}')
        print(f'{"="*70}')
        
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(create_user_channel_for_existing_channels())

