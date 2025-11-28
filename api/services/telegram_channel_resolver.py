"""
Утилита для получения tg_channel_id по username через Telegram API.
Context7: Используется для заполнения tg_channel_id при создании каналов.
"""

import asyncio
import structlog
from typing import Optional
from telethon import TelegramClient
from telethon.errors import UsernameNotOccupiedError, FloodWaitError
from telethon.sessions import StringSession
import redis.asyncio as redis
from config import settings

logger = structlog.get_logger()


async def get_tg_channel_id_by_username(username: str) -> Optional[int]:
    """
    Получение tg_channel_id из Telegram по username.
    
    Context7: Использует существующую сессию из Redis для доступа к Telegram API.
    
    Args:
        username: Username канала (с @ или без)
        
    Returns:
        Channel ID (отрицательное число для публичных каналов) или None при ошибке
    """
    try:
        # Убираем @ если есть
        clean_username = username.lstrip('@')
        
        # Получаем сессию из Redis
        session_string = await _get_session_from_redis()
        if not session_string:
            logger.warning("No Telegram session found in Redis - cannot get tg_channel_id", 
                         username=username,
                         hint="Check if Telegram session is authorized in Redis")
            return None
        
        # Создаем клиент
        session = StringSession(session_string)
        client = TelegramClient(
            session=session,
            api_id=settings.master_api_id,
            api_hash=settings.master_api_hash
        )
        
        await client.connect()
        
        try:
            # Получаем entity из Telegram
            entity = await client.get_entity(clean_username)
            
            # Context7: Для каналов ID всегда отрицательный при сохранении в БД
            # Используем utils.get_peer_id для правильного преобразования
            from telethon import utils
            from telethon.tl.types import PeerChannel
            
            if hasattr(entity, 'id') and entity.id is not None:
                # Для каналов создаём PeerChannel и получаем правильный ID
                if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
                    tg_channel_id = utils.get_peer_id(PeerChannel(entity.id))
                else:
                    tg_channel_id = entity.id
                
                logger.info("Got channel ID from Telegram", 
                           username=username, 
                           tg_channel_id=tg_channel_id)
                return tg_channel_id
            else:
                logger.warning("Entity has no valid ID", username=username)
                return None
                
        finally:
            await client.disconnect()
            
    except UsernameNotOccupiedError:
        logger.warning("Channel not found in Telegram", username=username)
        return None
    except FloodWaitError as e:
        logger.warning("Flood wait error", username=username, wait_seconds=e.seconds)
        await asyncio.sleep(e.seconds)
        return None
    except Exception as e:
        logger.error("Error getting channel ID", username=username, error=str(e))
        return None


async def _get_session_from_redis() -> Optional[str]:
    """
    Получение сессии Telegram из Redis.
    
    Context7: Проверяет разные ключи для сессий в Redis.
    Добавлено логирование для диагностики проблем.
    """
    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        
        # Вариант 1: tg:qr:session:*
        keys = await redis_client.keys("tg:qr:session:*")
        logger.debug("Checking tg:qr:session keys", count=len(keys))
        for key in keys:
            session_data = await redis_client.hgetall(key)
            status = session_data.get('status')
            if status == 'authorized':
                session_string = session_data.get('session_string', '')
                if session_string:
                    logger.info("Found authorized session in tg:qr:session", key=key)
                    await redis_client.close()
                    return session_string
                else:
                    logger.warning("Session data found but session_string is empty", key=key)
            else:
                logger.debug("Session not authorized", key=key, status=status)
        
        # Вариант 2: ingest:session:*
        keys = await redis_client.keys("ingest:session:*")
        logger.debug("Checking ingest:session keys", count=len(keys))
        for key in keys:
            session_data = await redis_client.hgetall(key)
            status = session_data.get('status')
            if status == 'authorized':
                session_string = session_data.get('session_string', '')
                if session_string:
                    logger.info("Found authorized session in ingest:session", key=key)
                    await redis_client.close()
                    return session_string
                else:
                    logger.warning("Session data found but session_string is empty", key=key)
            else:
                logger.debug("Session not authorized", key=key, status=status)
        
        # Вариант 3: telegram:session:*
        keys = await redis_client.keys("telegram:session:*")
        logger.debug("Checking telegram:session keys", count=len(keys))
        for key in keys:
            session_string = await redis_client.get(key)
            if session_string:
                logger.info("Found session string in telegram:session", key=key)
                await redis_client.close()
                return session_string
        
        # Логируем все найденные ключи для диагностики
        all_keys = await redis_client.keys("*session*")
        logger.warning("No authorized Telegram session found in Redis", 
                      checked_patterns=["tg:qr:session:*", "ingest:session:*", "telegram:session:*"],
                      all_session_keys=all_keys[:10] if len(all_keys) > 10 else all_keys)  # Ограничиваем вывод
        
        await redis_client.close()
        return None
        
    except Exception as e:
        logger.error("Failed to get session from Redis", error=str(e), exc_info=True)
        return None

