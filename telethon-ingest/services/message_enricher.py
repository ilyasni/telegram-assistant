"""
Message Enricher - извлечение деталей forwards/reactions/replies из Telegram сообщений.

Context7: Извлечение структурированных данных для сохранения в post_forwards, post_reactions, post_replies.
"""

import structlog
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logger = structlog.get_logger()


def extract_forwards_details(message) -> List[Dict[str, Any]]:
    """
    Извлечение деталей forwards из Telegram сообщения.
    
    Context7: Обработка message.fwd_from для извлечения информации о репостах.
    
    Args:
        message: Telegram Message объект
        
    Returns:
        Список словарей с данными о forwards
    """
    forwards = []
    
    try:
        if hasattr(message, 'fwd_from') and message.fwd_from:
            fwd_from = message.fwd_from
            
            forward_data = {
                'from_chat_id': None,
                'from_message_id': getattr(fwd_from, 'channel_post', None),
                'from_chat_title': None,
                'from_chat_username': None,
                'forwarded_at': fwd_from.date if hasattr(fwd_from, 'date') else datetime.now(timezone.utc)
            }
            
            # Извлечение информации о чате
            if hasattr(fwd_from, 'from_id'):
                from_id = fwd_from.from_id
                if hasattr(from_id, 'channel_id'):
                    forward_data['from_chat_id'] = from_id.channel_id
                elif hasattr(from_id, 'chat_id'):
                    forward_data['from_chat_id'] = from_id.chat_id
            
            # Извлечение названия и username (если доступны)
            if hasattr(fwd_from, 'from_name'):
                forward_data['from_chat_title'] = fwd_from.from_name
            
            # Обычно username доступен только через дополнительный запрос к API
            # Здесь оставляем None - может быть заполнено позже
            
            forwards.append(forward_data)
    
    except Exception as e:
        logger.warning("Failed to extract forwards details", error=str(e))
    
    return forwards


def extract_reactions_details(message) -> List[Dict[str, Any]]:
    """
    Извлечение деталей reactions из Telegram сообщения.
    
    Context7: Обработка message.reactions для извлечения информации о реакциях.
    
    Args:
        message: Telegram Message объект
        
    Returns:
        Список словарей с данными о reactions
    """
    reactions = []
    
    try:
        if hasattr(message, 'reactions') and message.reactions:
            # message.reactions может быть MessageReactions объектом
            if hasattr(message.reactions, 'results'):
                for reaction_result in message.reactions.results:
                    reaction_data = {
                        'reaction_type': 'emoji',
                        'reaction_value': None,
                        'user_tg_id': None,
                        'is_big': getattr(reaction_result, 'chosen', False)
                    }
                    
                    # Извлечение типа реакции
                    if hasattr(reaction_result, 'reaction'):
                        reaction = reaction_result.reaction
                        
                        # Emoji реакция
                        if hasattr(reaction, 'emoticon'):
                            reaction_data['reaction_type'] = 'emoji'
                            reaction_data['reaction_value'] = reaction.emoticon
                        
                        # Custom emoji реакция
                        elif hasattr(reaction, 'document_id'):
                            reaction_data['reaction_type'] = 'custom_emoji'
                            reaction_data['reaction_value'] = str(reaction.document_id)
                        
                        # Paid реакция (Premium)
                        elif hasattr(reaction, 'premium_emoji'):
                            reaction_data['reaction_type'] = 'paid'
                            reaction_data['reaction_value'] = reaction.premium_emoji
                    
                    # Извлечение информации о пользователе (если доступно)
                    if hasattr(reaction_result, 'peer_id'):
                        peer_id = reaction_result.peer_id
                        if hasattr(peer_id, 'user_id'):
                            reaction_data['user_tg_id'] = peer_id.user_id
                    
                    reactions.append(reaction_data)
            
            # Альтернативный формат: reactions может быть списком
            elif isinstance(message.reactions, (list, tuple)):
                for reaction in message.reactions:
                    # Упрощенная обработка списка реакций
                    reactions.append({
                        'reaction_type': 'emoji',
                        'reaction_value': str(reaction) if reaction else None,
                        'user_tg_id': None,
                        'is_big': False
                    })
    
    except Exception as e:
        logger.warning("Failed to extract reactions details", error=str(e))
    
    return reactions


def extract_replies_details(message, post_id: str) -> List[Dict[str, Any]]:
    """
    Извлечение деталей replies из Telegram сообщения.
    
    Context7: Обработка message.reply_to для извлечения информации об ответах.
    
    Args:
        message: Telegram Message объект
        post_id: UUID поста, к которому относится reply
        
    Returns:
        Список словарей с данными о replies
    """
    replies = []
    
    try:
        # Обработка reply_to (ответ на другое сообщение)
        if hasattr(message, 'reply_to') and message.reply_to:
            reply_to = message.reply_to
            
            reply_data = {
                'post_id': post_id,  # Пост, на который отвечают
                'reply_to_post_id': None,  # Пост, на который ответили (будет заполнено через связь)
                'reply_message_id': getattr(reply_to, 'reply_to_msg_id', None),
                'reply_chat_id': None,
                'reply_author_tg_id': None,
                'reply_author_username': None,
                'reply_content': None,  # Содержимое ответа - нужно получать отдельно
                'reply_posted_at': datetime.now(timezone.utc)  # Примерное время
            }
            
            # Извлечение информации о чате
            if hasattr(reply_to, 'reply_to_peer_id'):
                peer_id = reply_to.reply_to_peer_id
                if hasattr(peer_id, 'channel_id'):
                    reply_data['reply_chat_id'] = peer_id.channel_id
                elif hasattr(peer_id, 'chat_id'):
                    reply_data['reply_chat_id'] = peer_id.chat_id
                elif hasattr(peer_id, 'user_id'):
                    reply_data['reply_author_tg_id'] = peer_id.user_id
            
            replies.append(reply_data)
        
        # Обработка комментариев к посту (если доступны)
        # Обычно комментарии доступны через отдельный API вызов
        # Здесь оставляем базовую структуру
    
    except Exception as e:
        logger.warning("Failed to extract replies details", error=str(e))
    
    return replies

