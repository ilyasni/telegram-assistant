"""
Context7 best practice: Retry обвязка для Telethon API с правильной классификацией ошибок.

Retry только для retriable ошибок:
- FloodWaitError (строго ждать)
- RpcError/RpcCallFailError/ServerError/TimeoutError/OSError/asyncio.TimeoutError

Не retry для:
- UnauthorizedError, PhoneCodeInvalidError, PhoneNumberBannedError, AuthKeyError
"""

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import List, Optional, Any
import structlog
import redis.asyncio as redis
from telethon import TelegramClient, errors
from telethon.tl.types import Message, Channel, Chat
from prometheus_client import Histogram, Gauge

logger = structlog.get_logger()

# Context7: Метрики без высокой кардинальности (импортируем из main.py)
# telegram_floodwait_seconds определен в main.py

cooldown_channels_total = Gauge(
    'cooldown_channels_total',
    'Channels in cooldown'
)

# Context7: Классификация ошибок для retry
RETRIABLE_ERRORS = (
    errors.FloodWaitError,
    errors.RpcCallFailError, 
    errors.ServerError,
    TimeoutError,
    OSError,
    asyncio.TimeoutError
)

NON_RETRIABLE_ERRORS = (
    errors.UnauthorizedError,
    errors.PhoneCodeInvalidError,
    errors.PhoneNumberBannedError,
    errors.AuthKeyError,
    errors.SessionPasswordNeededError,
    errors.PhoneNumberUnoccupiedError
)

# Константы
MAX_FLOOD_WAIT = 60  # Максимальный FloodWait для cooldown
MAX_RETRIES = 5


async def fetch_messages_with_retry(
    client: TelegramClient,
    channel,
    limit: int = 50,
    max_retries: int = MAX_RETRIES,
    redis_client: Optional[redis.Redis] = None
) -> List[Message]:
    """
    Context7: Retry с FloodWait и cooldown управлением.
    
    Args:
        client: TelegramClient
        channel: Канал для парсинга
        limit: Количество сообщений
        max_retries: Максимальное количество попыток
        redis_client: Redis клиент для cooldown
        
    Returns:
        List[Message] или пустой список при ошибке
    """
    backoff = 0.5
    
    for attempt in range(max_retries):
        try:
            # Проверяем cooldown перед запросом
            # Context7: Безопасная проверка типа channel.id
            channel_id = getattr(channel, 'id', None)
            if redis_client and channel_id and await is_channel_in_cooldown(redis_client, channel_id):
                logger.info("Channel in cooldown, skipping", 
                          channel_id=channel_id)
                return []
            
            # Выполняем запрос
            messages = await client.get_messages(channel, limit=limit)
            
            # Сброс backoff после успешного запроса
            backoff = 0.5
            
            logger.debug("Messages fetched successfully", 
                        channel_id=channel_id,
                        count=len(messages))
            return messages
            
        except errors.FloodWaitError as e:
            # telegram_floodwait_seconds.observe(e.seconds)  # Метрика определена в main.py
            
            logger.warning("FloodWait error", 
                          channel_id=channel_id,
                          seconds=e.seconds, 
                          attempt=attempt)
            
            if e.seconds > MAX_FLOOD_WAIT:
                # Перевести канал в cooldown
                if redis_client:
                    await set_channel_cooldown(redis_client, channel_id, e.seconds)
                logger.warning("Channel moved to cooldown", 
                              channel_id=channel_id,
                              seconds=e.seconds)
                return []
                
            # Ждем FloodWait + 1 секунда
            await asyncio.sleep(e.seconds + 1)
            
        except NON_RETRIABLE_ERRORS as e:
            logger.error("Non-retriable error", 
                        channel_id=channel_id,
                        error=str(e),
                        error_type=type(e).__name__)
            return []
            
        except RETRIABLE_ERRORS as e:
            if attempt == max_retries - 1:
                logger.error("Max retries exceeded", 
                           channel_id=channel_id,
                           error=str(e),
                           error_type=type(e).__name__)
                return []
                
            # Экспоненциальный backoff с джиттером
            delay = min(backoff * (0.8 + random.random() * 0.4), 30)
            logger.warning("Retriable error, retrying", 
                          channel_id=channel_id,
                          error=str(e),
                          error_type=type(e).__name__,
                          delay=delay,
                          attempt=attempt + 1)
            await asyncio.sleep(delay)
            backoff *= 2
            
        except Exception as e:
            logger.error("Unexpected error", 
                        channel_id=channel_id,
                        error=str(e),
                        error_type=type(e).__name__)
            return []
            
    return []


async def set_channel_cooldown(
    redis_client: redis.Redis, 
    channel_id: int, 
    seconds: int
) -> None:
    """
    Context7: Cooldown в Redis с TTL.
    
    Args:
        redis_client: Redis клиент
        channel_id: ID канала
        seconds: Время cooldown в секундах
    """
    try:
        cooldown_key = f"channel:cooldown:{channel_id}"
        cooldown_data = {
            "started_at": datetime.utcnow().isoformat(),
            "duration_seconds": seconds
        }
        
        # Устанавливаем TTL и данные
        # Context7: setex() - синхронная функция в redis-py
        redis_client.setex(
            cooldown_key,
            seconds,
            str(cooldown_data)
        )
        
        cooldown_channels_total.inc()
        
        logger.info("Channel cooldown set", 
                   channel_id=channel_id,
                   seconds=seconds)
                   
    except Exception as e:
        logger.error("Failed to set channel cooldown", 
                    channel_id=channel_id,
                    error=str(e))


async def is_channel_in_cooldown(
    redis_client: redis.Redis, 
    channel_id: int
) -> bool:
    """
    Проверка cooldown перед парсингом.
    
    Args:
        redis_client: Redis клиент
        channel_id: ID канала
        
    Returns:
        True если канал в cooldown
    """
    try:
        cooldown_key = f"channel:cooldown:{channel_id}"
        # Context7: exists() - синхронная функция в redis-py
        exists = redis_client.exists(cooldown_key)
        return bool(exists)
        
    except Exception as e:
        logger.error("Failed to check channel cooldown", 
                    channel_id=channel_id,
                    error=str(e))
        return False


async def get_channel_cooldown_info(
    redis_client: redis.Redis, 
    channel_id: int
) -> Optional[dict]:
    """
    Получение информации о cooldown канала.
    
    Args:
        redis_client: Redis клиент
        channel_id: ID канала
        
    Returns:
        Dict с информацией о cooldown или None
    """
    try:
        cooldown_key = f"channel:cooldown:{channel_id}"
        # Context7: get() - синхронная функция в redis-py
        data = redis_client.get(cooldown_key)
        
        if data:
            # Парсим данные cooldown
            import json
            return json.loads(data)
        return None
        
    except Exception as e:
        logger.error("Failed to get channel cooldown info", 
                    channel_id=channel_id,
                    error=str(e))
        return None


async def clear_channel_cooldown(
    redis_client: redis.Redis, 
    channel_id: int
) -> bool:
    """
    Очистка cooldown канала.
    
    Args:
        redis_client: Redis клиент
        channel_id: ID канала
        
    Returns:
        True если cooldown был очищен
    """
    try:
        cooldown_key = f"channel:cooldown:{channel_id}"
        # Context7: delete() - синхронная функция в redis-py
        deleted = redis_client.delete(cooldown_key)
        
        if deleted:
            cooldown_channels_total.dec()
            logger.info("Channel cooldown cleared", 
                       channel_id=channel_id)
            
        return bool(deleted)
        
    except Exception as e:
        logger.error("Failed to clear channel cooldown", 
                    channel_id=channel_id,
                    error=str(e))
        return False


async def get_all_cooldown_channels(
    redis_client: redis.Redis
) -> List[dict]:
    """
    Получение всех каналов в cooldown.
    
    Args:
        redis_client: Redis клиент
        
    Returns:
        List[dict] с информацией о каналах в cooldown
    """
    try:
        pattern = "channel:cooldown:*"
        # Context7: keys() - синхронная функция в redis-py
        keys = redis_client.keys(pattern)
        
        cooldown_channels = []
        for key in keys:
            try:
                # Извлекаем channel_id из ключа
                channel_id = int(key.decode('utf-8').split(':')[-1])
                
                # Получаем данные cooldown
                # Context7: get() - синхронная функция в redis-py
                data = redis_client.get(key)
                if data:
                    cooldown_info = {
                        "channel_id": channel_id,
                        "cooldown_data": data.decode('utf-8')
                    }
                    cooldown_channels.append(cooldown_info)
                    
            except (ValueError, UnicodeDecodeError) as e:
                logger.warning("Invalid cooldown key", key=key, error=str(e))
                continue
                
        return cooldown_channels
        
    except Exception as e:
        logger.error("Failed to get cooldown channels", error=str(e))
        return []


# Context7: Утилиты для работы с каналами
async def is_channel_accessible(
    client: TelegramClient,
    channel
) -> bool:
    """
    Проверка доступности канала.
    
    Args:
        client: TelegramClient
        channel: Канал для проверки
        
    Returns:
        True если канал доступен
    """
    try:
        # Простой запрос для проверки доступности
        await client.get_entity(channel)
        return True
        
    except errors.ChannelPrivateError:
        logger.warning("Channel is private", channel_id=channel.id)
        return False
        
    except errors.ChannelInvalidError:
        logger.warning("Channel is invalid", channel_id=channel.id)
        return False
        
    except Exception as e:
        logger.warning("Channel accessibility check failed", 
                      channel_id=channel.id,
                      error=str(e))
        return False


async def get_channel_info(
    client: TelegramClient,
    channel
) -> Optional[dict]:
    """
    Получение информации о канале.
    
    Args:
        client: TelegramClient
        channel: Канал
        
    Returns:
        Dict с информацией о канале или None
    """
    try:
        entity = await client.get_entity(channel)
        
        return {
            "id": entity.id,
            "title": getattr(entity, 'title', ''),
            "username": getattr(entity, 'username', ''),
            "participants_count": getattr(entity, 'participants_count', 0),
            "is_broadcast": getattr(entity, 'broadcast', False),
            "is_megagroup": getattr(entity, 'megagroup', False)
        }
        
    except Exception as e:
        logger.error("Failed to get channel info", 
                    channel_id=channel.id,
                    error=str(e))
        return None
