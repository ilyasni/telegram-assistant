#!/usr/bin/env python3
"""
Скрипт для обновления tg_channel_id для каналов про пиво.
Использует известные ссылки для получения channel ID.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon import utils
from telethon.tl.types import PeerChannel
import psycopg2
from psycopg2.extras import RealDictCursor
import structlog
import redis.asyncio as redis
from config import settings

logger = structlog.get_logger()

# Известные каналы и их информация
BEER_CHANNELS = {
    'beer_for_all': {
        'url': 'https://t.me/beer_for_all',
        'title': 'Пиво🍺',
        'subscribers': 2656
    },
    'beer_by': {
        'url': 'https://t.me/beer_by',
        'title': 'beer_by',
        'subscribers': None
    },
    'prostopropivo': {
        'url': 'https://t.me/prostopropivo',
        'title': 'Просто Про Пиво',
        'subscribers': 31042
    }
}


async def get_channel_id_from_entity(client: TelegramClient, username: str) -> int | None:
    """
    Получение tg_channel_id через get_entity с разными вариантами.
    """
    variants = [
        username,
        f'@{username}',
        f'https://t.me/{username}',
        f't.me/{username}'
    ]
    
    for variant in variants:
        try:
            logger.info(f"Trying to get entity for {variant}")
            entity = await client.get_entity(variant)
            
            if hasattr(entity, 'id') and entity.id:
                if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
                    tg_channel_id = utils.get_peer_id(PeerChannel(entity.id))
                else:
                    tg_channel_id = entity.id
                
                logger.info(f"Got channel ID for {username}", 
                           variant=variant,
                           tg_channel_id=tg_channel_id)
                return tg_channel_id
        except Exception as e:
            logger.warning(f"Failed to get entity for {variant}", 
                         username=username,
                         variant=variant,
                         error=str(e))
            continue
    
    return None


async def update_beer_channels():
    """
    Обновление tg_channel_id для каналов про пиво.
    """
    logger.info("Starting beer channels update...")
    
    # Подключение к БД
    conn = psycopg2.connect(settings.database_url)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Получаем сессию из Redis
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        session_string = None
        
        keys = await redis_client.keys('telegram:session:*')
        for key in keys:
            session_string = await redis_client.get(key)
            if session_string:
                logger.info(f"Found session in {key}")
                break
        
        if not session_string:
            logger.error("No Telegram session found in Redis")
            await redis_client.close()
            return
        
        session = StringSession(session_string)
        client = TelegramClient(
            session=session,
            api_id=settings.master_api_id,
            api_hash=settings.master_api_hash
        )
        
        await client.connect()
        logger.info("Connected to Telegram")
        
        updated_count = 0
        failed_count = 0
        
        for username, channel_info in BEER_CHANNELS.items():
            logger.info(f"Processing channel {username}")
            
            # Получаем канал из БД
            cursor.execute("""
                SELECT id, username, title, tg_channel_id
                FROM channels
                WHERE username = %s
            """, (username,))
            
            channel_row = cursor.fetchone()
            if not channel_row:
                logger.warning(f"Channel {username} not found in DB")
                failed_count += 1
                continue
            
            channel_id = channel_row['id']
            current_tg_id = channel_row['tg_channel_id']
            
            if current_tg_id:
                logger.info(f"Channel {username} already has tg_channel_id: {current_tg_id}")
                continue
            
            # Пытаемся получить tg_channel_id
            tg_channel_id = await get_channel_id_from_entity(client, username)
            
            if tg_channel_id:
                # Обновляем БД
                cursor.execute("""
                    UPDATE channels
                    SET tg_channel_id = %s
                    WHERE id = %s
                """, (tg_channel_id, channel_id))
                
                conn.commit()
                updated_count += 1
                logger.info(f"Updated channel {username}", 
                           channel_id=channel_id,
                           tg_channel_id=tg_channel_id)
            else:
                failed_count += 1
                logger.warning(f"Failed to get tg_channel_id for {username}")
            
            # Небольшая задержка между запросами
            await asyncio.sleep(2)
        
        logger.info("Beer channels update completed", 
                   updated=updated_count, 
                   failed=failed_count)
        
        await client.disconnect()
        await redis_client.aclose()
        
    except Exception as e:
        logger.error("Error in update_beer_channels", error=str(e))
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(update_beer_channels())

