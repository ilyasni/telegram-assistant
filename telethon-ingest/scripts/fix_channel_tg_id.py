#!/usr/bin/env python3
"""
Context7 best practice: Скрипт для перепривязки канала - получения tg_channel_id по username.
Использует TelegramClientManager для безопасного получения entity канала.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor
import structlog

from config import settings
from services.telegram_client_manager import TelegramClientManager

logger = structlog.get_logger()


async def fix_channel_tg_id(channel_id: str):
    """
    Получение и обновление tg_channel_id для канала.
    
    Args:
        channel_id: UUID канала в БД
    """
    logger.info("Starting channel tg_channel_id fix", channel_id=channel_id)
    
    # Подключение к БД
    conn = psycopg2.connect(settings.database_url)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Получаем информацию о канале
        cursor.execute("""
            SELECT id, username, title, tg_channel_id
            FROM channels
            WHERE id = %s
        """, (channel_id,))
        
        channel = cursor.fetchone()
        if not channel:
            logger.error("Channel not found", channel_id=channel_id)
            return False
        
        username = channel['username']
        if not username:
            logger.error("Channel has no username", channel_id=channel_id)
            return False
        
        logger.info("Found channel", 
                   channel_id=channel_id, 
                   username=username,
                   current_tg_channel_id=channel['tg_channel_id'])
        
        # Инициализация TelegramClientManager
        import redis.asyncio as redis
        redis_client = redis.from_url(settings.redis_url)
        db_connection = psycopg2.connect(settings.database_url)
        client_manager = TelegramClientManager(redis_client, db_connection)
        
        # Получаем user_id из первой авторизованной сессии
        cursor.execute("""
            SELECT telegram_id
            FROM users
            WHERE telegram_auth_status = 'authorized'
            ORDER BY telegram_auth_created_at DESC
            LIMIT 1
        """)
        user_row = cursor.fetchone()
        if not user_row:
            logger.error("No authorized user found")
            return False
        
        user_id = str(user_row['telegram_id'])
        logger.info("Using user for Telegram client", user_id=user_id)
        
        # Получаем Telegram клиент
        telegram_client = await client_manager.get_client(user_id)
        if not telegram_client:
            logger.error("Failed to get Telegram client", user_id=user_id)
            return False
        
        # Получаем entity канала по username
        clean_username = username.lstrip('@')
        try:
            entity = await telegram_client.get_entity(clean_username)
            
            # Context7 best practice: Правильная конвертация ID канала
            from telethon import utils
            from telethon.tl.types import PeerChannel
            
            if hasattr(entity, 'id') and entity.id:
                # Для каналов используем get_peer_id для получения правильного отрицательного ID
                if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
                    tg_channel_id = utils.get_peer_id(PeerChannel(entity.id))
                else:
                    tg_channel_id = entity.id
                
                logger.info("Got channel entity", 
                           username=username,
                           entity_id=entity.id,
                           tg_channel_id=tg_channel_id)
                
                # Обновляем в БД
                cursor.execute("""
                    UPDATE channels
                    SET tg_channel_id = %s
                    WHERE id = %s
                """, (tg_channel_id, channel_id))
                
                conn.commit()
                
                logger.info("Successfully updated tg_channel_id",
                           channel_id=channel_id,
                           username=username,
                           tg_channel_id=tg_channel_id)
                
                return True
            else:
                logger.error("Entity has no valid ID", username=username)
                return False
                
        except Exception as e:
            logger.error("Failed to get channel entity", 
                        username=username, 
                        error=str(e),
                        error_type=type(e).__name__)
            return False
        
    except Exception as e:
        logger.error("Error in fix_channel_tg_id", error=str(e))
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    # Получаем channel_id из аргументов или используем @theworldisnoteasy
    if len(sys.argv) > 1:
        channel_id = sys.argv[1]
    else:
        # По умолчанию исправляем @theworldisnoteasy
        channel_id = "98fecd5f-1c22-4e86-a196-2b7444ae4ca0"
    
    result = asyncio.run(fix_channel_tg_id(channel_id))
    sys.exit(0 if result else 1)

