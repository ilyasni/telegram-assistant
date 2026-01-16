"""
Утилита для получения tg_channel_id по username через Telegram API.
Context7: Используется для заполнения tg_channel_id при создании каналов.
"""

import asyncio
import structlog
from typing import Optional
from telethon import TelegramClient
from telethon.errors import UsernameNotOccupiedError, FloodWaitError, ChannelPrivateError, UsernameInvalidError
from telethon.sessions import StringSession
import redis.asyncio as redis

logger = structlog.get_logger()


async def resolve_channel_from_telegram(username: str) -> Optional[dict]:
    """
    Разрешение канала через Telegram API с получением полной информации.
    
    Context7: Получает tg_channel_id, title и канонический username из Telegram.
    Используется для заполнения данных канала при подписке на темы.
    
    Args:
        username: Username канала (с @ или без)
        
    Returns:
        Dict с ключами: tg_channel_id, title, username (канонический) или None при ошибке
    """
    try:
        # Убираем @ если есть
        clean_username = username.lstrip('@')
        
        # Получаем сессию из Redis
        session_string = await _get_session_from_redis()
        if not session_string:
            logger.warning("No Telegram session found in Redis - cannot resolve channel", 
                         username=username,
                         hint="Check if Telegram session is authorized in Redis")
            return None
        
        # Создаем клиент
        # Context7: Получаем API credentials из переменных окружения
        # Поддерживаем оба варианта: MASTER_API_* (для telethon-ingest) и TELEGRAM_API_* (для совместимости)
        import os
        master_api_id_env = os.getenv("MASTER_API_ID")
        telegram_api_id_env = os.getenv("TELEGRAM_API_ID")
        master_api_hash_env = os.getenv("MASTER_API_HASH")
        telegram_api_hash_env = os.getenv("TELEGRAM_API_HASH")
        
        api_id = int(master_api_id_env or telegram_api_id_env or "0")
        api_hash = master_api_hash_env or telegram_api_hash_env or ""
        
        if not api_id or not api_hash or api_id == 0:
            logger.warning("Telegram API credentials not configured, cannot resolve channel", 
                         username=username)
            return None
        
        session = StringSession(session_string)
        client = TelegramClient(
            session=session,
            api_id=api_id,
            api_hash=api_hash
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
                
                # Получаем title и канонический username
                channel_title = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or username
                canonical_username = getattr(entity, 'username', None)
                if canonical_username:
                    canonical_username = canonical_username.lstrip('@')
                
                result = {
                    "tg_channel_id": tg_channel_id,
                    "title": channel_title,
                    "username": canonical_username or clean_username  # Fallback на исходный username
                }
                
                logger.info("Resolved channel from Telegram", 
                           username=username,
                           tg_channel_id=tg_channel_id,
                           canonical_username=canonical_username,
                           title=channel_title)
                return result
            else:
                logger.warning("Entity has no valid ID", username=username)
                return None
                
        finally:
            await client.disconnect()
            
    except UsernameNotOccupiedError:
        logger.warning("Channel not found in Telegram (UsernameNotOccupiedError)", username=username)
        return None
    except ChannelPrivateError:
        logger.warning("Channel is private (ChannelPrivateError)", username=username)
        return None
    except UsernameInvalidError:
        logger.warning("Invalid username (UsernameInvalidError)", username=username)
        return None
    except FloodWaitError as e:
        logger.warning("Flood wait error", username=username, wait_seconds=e.seconds)
        await asyncio.sleep(e.seconds)
        return None
    except Exception as e:
        logger.error("Error resolving channel from Telegram", username=username, error=str(e), error_type=type(e).__name__)
        return None


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
        # Context7: Получаем API credentials из переменных окружения
        # Поддерживаем оба варианта: MASTER_API_* (для telethon-ingest) и TELEGRAM_API_* (для совместимости)
        import os
        master_api_id_env = os.getenv("MASTER_API_ID")
        telegram_api_id_env = os.getenv("TELEGRAM_API_ID")
        master_api_hash_env = os.getenv("MASTER_API_HASH")
        telegram_api_hash_env = os.getenv("TELEGRAM_API_HASH")
        
        api_id = int(master_api_id_env or telegram_api_id_env or "0")
        api_hash = master_api_hash_env or telegram_api_hash_env or ""
        
        if not api_id or not api_hash or api_id == 0:
            logger.warning("Telegram API credentials not configured, cannot get tg_channel_id", 
                         username=username)
            return None
        
        session = StringSession(session_string)
        client = TelegramClient(
            session=session,
            api_id=api_id,
            api_hash=api_hash
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

