"""
Discussion Extractor - извлечение reply-цепочек для каналов с комментариями (Context7 P1).

Использует GetDiscussionMessage для получения комментариев к постам в каналах.

Context7 best practices:
- Кэширование проверки наличия комментариев (Redis, TTL 24 часа)
- Rate limiting для GetDiscussionMessage запросов
- Оптимизация повторных вызовов GetFullChannel
"""
import asyncio
import structlog
import json
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.tl import functions
from telethon.errors import MessageNotModifiedError, MessageIdInvalidError
import redis.asyncio as redis

logger = structlog.get_logger()

# Context7 P1: Кэш для проверки наличия комментариев (Redis ключи)
CACHE_KEY_PREFIX = "channel:has_comments:"
CACHE_TTL = 86400  # 24 часа

# Context7 P1: Rate limiting для GetDiscussionMessage (запросов в секунду)
DISCUSSION_RATE_LIMIT = 10  # Максимум 10 запросов в секунду
_last_discussion_request_time = {}  # {channel_id: timestamp}


async def get_discussion_message(
    client: TelegramClient,
    channel_entity,
    message_id: int
) -> Optional[Dict[str, Any]]:
    """
    Context7 P1: Получение discussion сообщения для канала с комментариями.
    
    Использует GetDiscussionMessage для получения связанного сообщения в discussion группе.
    Применяет rate limiting для предотвращения FloodWait.
    
    Args:
        client: TelegramClient
        channel_entity: Entity канала
        message_id: ID сообщения в канале
        
    Returns:
        Dict с данными discussion сообщения или None
    """
    try:
        # Context7 P1: Rate limiting для предотвращения FloodWait
        channel_id = getattr(channel_entity, 'id', None)
        if channel_id:
            await _rate_limit_discussion_request(channel_id)
        
        # Context7 P1: GetDiscussionMessage для получения discussion сообщения
        result = await client(functions.messages.GetDiscussionMessageRequest(
            peer=channel_entity,
            msg_id=message_id
        ))
        
        if not result:
            return None
        
        # Context7 P1: Извлечение данных из результата
        discussion_data = {
            'discussion_message_id': None,
            'discussion_chat_id': None,
            'discussion_chat_title': None,
            'discussion_chat_username': None,
            'messages': [],
            'chats': [],
            'users': []
        }
        
        # Извлечение discussion сообщения
        if hasattr(result, 'messages') and result.messages:
            for msg in result.messages:
                if hasattr(msg, 'id'):
                    discussion_data['discussion_message_id'] = msg.id
                    break
        
        # Извлечение информации о discussion чате
        if hasattr(result, 'chats') and result.chats:
            for chat in result.chats:
                if hasattr(chat, 'id'):
                    discussion_data['discussion_chat_id'] = chat.id
                    discussion_data['discussion_chat_title'] = getattr(chat, 'title', None)
                    discussion_data['discussion_chat_username'] = getattr(chat, 'username', None)
                    break
        
        # Сохранение всех сообщений для последующей обработки
        if hasattr(result, 'messages'):
            discussion_data['messages'] = result.messages
        
        if hasattr(result, 'chats'):
            discussion_data['chats'] = result.chats
        
        if hasattr(result, 'users'):
            discussion_data['users'] = result.users
        
        logger.debug("Discussion message retrieved",
                    channel_id=getattr(channel_entity, 'id', None),
                    message_id=message_id,
                    discussion_chat_id=discussion_data['discussion_chat_id'])
        
        return discussion_data
        
    except MessageIdInvalidError:
        logger.debug("Message ID invalid for discussion",
                    channel_id=getattr(channel_entity, 'id', None),
                    message_id=message_id)
        return None
    except Exception as e:
        logger.warning("Failed to get discussion message",
                     channel_id=getattr(channel_entity, 'id', None),
                     message_id=message_id,
                     error=str(e))
        return None


async def extract_reply_chain(
    client: TelegramClient,
    channel_entity,
    message_id: int,
    max_depth: int = 10,
    max_replies: int = 100
) -> List[Dict[str, Any]]:
    """
    Context7 P1: Извлечение reply-цепочки для поста в канале с комментариями.
    
    Получает discussion сообщение и извлекает все ответы (replies) к нему.
    
    Args:
        client: TelegramClient
        channel_entity: Entity канала
        message_id: ID сообщения в канале
        max_depth: Максимальная глубина цепочки ответов
        max_replies: Максимальное количество ответов для извлечения
        
    Returns:
        Список словарей с данными о replies
    """
    replies = []
    
    try:
        # Context7 P1: Получаем discussion сообщение
        discussion_data = await get_discussion_message(client, channel_entity, message_id)
        
        if not discussion_data or not discussion_data.get('discussion_chat_id'):
            logger.debug("No discussion data for message",
                        channel_id=getattr(channel_entity, 'id', None),
                        message_id=message_id)
            return replies
        
        discussion_chat_id = discussion_data['discussion_chat_id']
        discussion_message_id = discussion_data.get('discussion_message_id')
        
        if not discussion_message_id:
            logger.debug("No discussion message ID",
                        channel_id=getattr(channel_entity, 'id', None),
                        message_id=message_id)
            return replies
        
        # Context7 P1: Получаем discussion чат
        try:
            discussion_chat = await client.get_entity(discussion_chat_id)
        except Exception as e:
            logger.warning("Failed to get discussion chat entity",
                         discussion_chat_id=discussion_chat_id,
                         error=str(e))
            return replies
        
        # Context7 P1: Получаем ответы к discussion сообщению
        # Используем iter_messages для получения всех ответов
        reply_count = 0
        async for reply_message in client.iter_messages(
            discussion_chat,
            reply_to=discussion_message_id,
            limit=max_replies
        ):
            if reply_count >= max_replies:
                break
            
            reply_data = {
                'reply_message_id': reply_message.id,
                'reply_chat_id': discussion_chat_id,
                'reply_content': reply_message.text or reply_message.message or '',
                'reply_posted_at': reply_message.date.isoformat() if reply_message.date else None,
                'reply_author_tg_id': None,
                'reply_author_username': None,
                'thread_id': None,
                'reply_to_message_id': None
            }
            
            # Извлечение информации об авторе
            if hasattr(reply_message, 'from_id') and reply_message.from_id:
                from_id = reply_message.from_id
                if hasattr(from_id, 'user_id'):
                    reply_data['reply_author_tg_id'] = from_id.user_id
                elif hasattr(from_id, 'channel_id'):
                    reply_data['reply_author_tg_id'] = from_id.channel_id
            
            # Извлечение username автора
            if hasattr(reply_message, 'sender') and reply_message.sender:
                sender = reply_message.sender
                if hasattr(sender, 'username'):
                    reply_data['reply_author_username'] = sender.username
            
            # Извлечение reply_to (если это ответ на другой ответ)
            if hasattr(reply_message, 'reply_to') and reply_message.reply_to:
                reply_to = reply_message.reply_to
                if hasattr(reply_to, 'reply_to_msg_id'):
                    reply_data['reply_to_message_id'] = reply_to.reply_to_msg_id
                
                # Thread ID для вложенных ответов
                if hasattr(reply_to, 'reply_to_top_id'):
                    reply_data['thread_id'] = reply_to.reply_to_top_id
            
            replies.append(reply_data)
            reply_count += 1
        
        logger.info("Reply chain extracted",
                   channel_id=getattr(channel_entity, 'id', None),
                   message_id=message_id,
                   replies_count=len(replies),
                   discussion_chat_id=discussion_chat_id)
        
        return replies
        
    except Exception as e:
        logger.error("Failed to extract reply chain",
                    channel_id=getattr(channel_entity, 'id', None),
                    message_id=message_id,
                    error=str(e),
                    exc_info=True)
        return replies


async def check_channel_has_comments(
    client: TelegramClient,
    channel_entity,
    redis_client: Optional[redis.Redis] = None
) -> bool:
    """
    Context7 P1: Проверка, есть ли у канала включённые комментарии.
    
    Проверяет наличие discussion группы у канала с кэшированием в Redis.
    
    Args:
        client: TelegramClient
        channel_entity: Entity канала
        redis_client: Опциональный Redis клиент для кэширования
        
    Returns:
        True если у канала есть комментарии, False иначе
    """
    channel_id = getattr(channel_entity, 'id', None)
    if not channel_id:
        return False
    
    # Context7 P1: Проверка кэша в Redis
    if redis_client:
        try:
            cache_key = f"{CACHE_KEY_PREFIX}{channel_id}"
            cached_value = await redis_client.get(cache_key)
            
            if cached_value is not None:
                # Кэш найден - возвращаем результат
                result = cached_value.decode('utf-8') == '1' if isinstance(cached_value, bytes) else cached_value == '1'
                logger.debug("Channel comments check from cache",
                            channel_id=channel_id,
                            has_comments=result)
                return result
        except Exception as e:
            logger.debug("Failed to check cache for channel comments",
                        channel_id=channel_id,
                        error=str(e))
    
    # Context7 P1: Получаем полную информацию о канале (если кэш не найден)
    try:
        full_channel = await client(functions.channels.GetFullChannelRequest(channel_entity))
        
        if not full_channel:
            # Кэшируем отрицательный результат
            if redis_client:
                try:
                    cache_key = f"{CACHE_KEY_PREFIX}{channel_id}"
                    await redis_client.setex(cache_key, CACHE_TTL, "0")
                except Exception:
                    pass
            return False
        
        # Context7 P1: Проверяем наличие linked_chat (discussion группа)
        has_comments = False
        
        if hasattr(full_channel, 'linked_chat_id') and full_channel.linked_chat_id:
            logger.debug("Channel has comments enabled",
                        channel_id=channel_id,
                        linked_chat_id=full_channel.linked_chat_id)
            has_comments = True
        else:
            # Альтернативная проверка через chats в full_channel
            if hasattr(full_channel, 'chats') and full_channel.chats:
                for chat in full_channel.chats:
                    # Если есть мегагруппа, связанная с каналом - это discussion группа
                    if hasattr(chat, 'megagroup') and chat.megagroup:
                        logger.debug("Channel has comments (megagroup found)",
                                    channel_id=channel_id,
                                    chat_id=chat.id)
                        has_comments = True
                        break
        
        # Context7 P1: Кэшируем результат в Redis
        if redis_client:
            try:
                cache_key = f"{CACHE_KEY_PREFIX}{channel_id}"
                await redis_client.setex(cache_key, CACHE_TTL, "1" if has_comments else "0")
            except Exception as e:
                logger.debug("Failed to cache channel comments check",
                            channel_id=channel_id,
                            error=str(e))
        
        return has_comments
        
    except Exception as e:
        logger.warning("Failed to check channel comments",
                     channel_id=channel_id,
                     error=str(e))
        return False


async def _rate_limit_discussion_request(channel_id: int) -> bool:
    """
    Context7 P1: Rate limiting для GetDiscussionMessage запросов.
    
    Args:
        channel_id: ID канала
        
    Returns:
        True если запрос разрешён, False если нужно подождать
    """
    global _last_discussion_request_time
    
    current_time = time.time()
    last_request_time = _last_discussion_request_time.get(channel_id, 0)
    
    # Минимальный интервал между запросами (100ms для 10 req/s)
    min_interval = 1.0 / DISCUSSION_RATE_LIMIT
    
    if current_time - last_request_time < min_interval:
        # Слишком частый запрос - нужно подождать
        wait_time = min_interval - (current_time - last_request_time)
        await asyncio.sleep(wait_time)
    
    # Обновляем время последнего запроса
    _last_discussion_request_time[channel_id] = time.time()
    return True

