#!/usr/bin/env python3
"""
Утилита для заполнения tg_channel_id в таблице channels.
Использует Telegram API для получения channel ID по username.
"""
import asyncio
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient
from telethon.errors import UsernameNotOccupiedError, FloodWaitError
import psycopg2
from psycopg2.extras import RealDictCursor
import structlog

from config import settings

logger = structlog.get_logger()


async def get_channel_id_from_telegram(client: TelegramClient, username: str) -> int | None:
    """
    Получение tg_channel_id из Telegram по username.
    
    Args:
        client: Telegram клиент
        username: Username канала (с @ или без)
        
    Returns:
        Channel ID (отрицательное число для публичных каналов) или None при ошибке
    """
    try:
        # Убираем @ если есть
        clean_username = username.lstrip('@')
        
        # Получаем entity из Telegram
        entity = await client.get_entity(clean_username)
        
        # Для каналов ID всегда отрицательный
        channel_id = -entity.id if entity.id > 0 else entity.id
        
        logger.info("Got channel ID from Telegram", 
                   username=username, 
                   channel_id=channel_id)
        
        return channel_id
        
    except (UsernameNotOccupiedError, ValueError) as e:
        logger.warning("Channel not found in Telegram", username=username, error=str(e))
        return None
    except FloodWaitError as e:
        logger.warning("Flood wait error", username=username, wait_seconds=e.seconds)
        await asyncio.sleep(e.seconds)
        return None
    except Exception as e:
        logger.error("Error getting channel ID", username=username, error=str(e))
        return None


async def populate_channel_ids():
    """
    Заполнение tg_channel_id для всех каналов с username.
    """
    logger.info("Starting channel ID population...")
    
    # Подключение к БД
    conn = psycopg2.connect(settings.database_url)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Получаем все каналы без tg_channel_id
        cursor.execute("""
            SELECT id, username, title
            FROM channels
            WHERE tg_channel_id IS NULL
              AND username IS NOT NULL
              AND username != ''
            ORDER BY created_at DESC
        """)
        
        channels = cursor.fetchall()
        logger.info(f"Found {len(channels)} channels without tg_channel_id")
        
        if not channels:
            logger.info("No channels to process")
            return
        
        # Инициализация Telegram клиента
        # Используем сессию из Redis (если есть)
        import redis.asyncio as redis
        redis_client = redis.from_url(settings.redis_url)
        
        # Ищем авторизованную сессию
        # Context7: Проверяем разные ключи для сессий
        session_string = None
        
        # Вариант 1: tg:qr:session:*
        keys = await redis_client.keys("tg:qr:session:*")
        for key in keys:
            session_data = await redis_client.hgetall(key)
            if session_data.get(b'status') == b'authorized':
                session_string = session_data.get(b'session_string', b'').decode('utf-8')
                if session_string:
                    logger.info("Found authorized session in tg:qr:session", key=key.decode())
                    break
        
        # Вариант 2: ingest:session:*
        if not session_string:
            keys = await redis_client.keys("ingest:session:*")
            for key in keys:
                session_data = await redis_client.hgetall(key)
                if session_data.get(b'status') == b'authorized':
                    session_string = session_data.get(b'session_string', b'').decode('utf-8')
                    if session_string:
                        logger.info("Found authorized session in ingest:session", key=key.decode())
                        break
        
        # Вариант 3: Получаем из Redis по ключу telegram:session:*
        if not session_string:
            keys = await redis_client.keys("telegram:session:*")
            for key in keys:
                # Пробуем получить session_string напрямую
                session_string_raw = await redis_client.get(key)
                if session_string_raw:
                    try:
                        session_string = session_string_raw.decode('utf-8')
                        logger.info("Found session string in telegram:session", key=key.decode())
                        break
                    except:
                        pass
        
        if not session_string:
            logger.error("No authorized Telegram session found in Redis or DB")
            return
        
        from telethon.sessions import StringSession
        session = StringSession(session_string)
        
        client = TelegramClient(
            session=session,
            api_id=settings.master_api_id,
            api_hash=settings.master_api_hash
        )
        
        await client.connect()
        logger.info("Connected to Telegram")
        
        # Обрабатываем каждый канал
        updated_count = 0
        failed_count = 0
        
        for channel in channels:
            logger.info("Processing channel", 
                       channel_id=channel['id'], 
                       username=channel['username'])
            
            # Получаем tg_channel_id из Telegram
            tg_channel_id = await get_channel_id_from_telegram(client, channel['username'])
            
            if tg_channel_id:
                # Обновляем БД
                cursor.execute("""
                    UPDATE channels
                    SET tg_channel_id = %s
                    WHERE id = %s
                """, (tg_channel_id, channel['id']))
                
                conn.commit()
                updated_count += 1
                logger.info("Updated channel", 
                           channel_id=channel['id'],
                           tg_channel_id=tg_channel_id)
            else:
                failed_count += 1
                logger.warning("Failed to get channel ID", 
                             channel_id=channel['id'],
                             username=channel['username'])
            
            # Небольшая задержка между запросами
            await asyncio.sleep(1)
        
        logger.info("Channel ID population completed", 
                   updated=updated_count, 
                   failed=failed_count)
        
        await client.disconnect()
        await redis_client.close()
        
    except Exception as e:
        logger.error("Error in populate_channel_ids", error=str(e))
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(populate_channel_ids())

