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
    Извлечение деталей forwards из Telegram сообщения (Context7 P1.1).
    
    Context7: Обработка message.fwd_from (MessageFwdHeader) для извлечения всех данных о репостах.
    Поддерживает все поля из MessageFwdHeader:
    - from_id (peer ID источника)
    - from_name (имя автора)
    - date (дата оригинального сообщения)
    - channel_post (ID сообщения в канале)
    - post_author (подпись автора)
    - saved_from_peer, saved_from_msg_id (сохранённые форварды)
    - psa_type (публичное объявление)
    
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
                'forwarded_at': fwd_from.date if hasattr(fwd_from, 'date') and fwd_from.date else datetime.now(timezone.utc)
            }
            
            # Context7 P1.1: Извлечение полного peer ID как JSONB
            if hasattr(fwd_from, 'from_id') and fwd_from.from_id:
                from_id = fwd_from.from_id
                peer_id_data = {}
                
                if hasattr(from_id, 'user_id'):
                    peer_id_data['user_id'] = from_id.user_id
                elif hasattr(from_id, 'channel_id'):
                    peer_id_data['channel_id'] = from_id.channel_id
                    forward_data['from_chat_id'] = from_id.channel_id
                elif hasattr(from_id, 'chat_id'):
                    peer_id_data['chat_id'] = from_id.chat_id
                    forward_data['from_chat_id'] = from_id.chat_id
                
                forward_data['from_id'] = peer_id_data
            
            # Извлечение названия автора
            if hasattr(fwd_from, 'from_name') and fwd_from.from_name:
                forward_data['from_chat_title'] = fwd_from.from_name
                forward_data['from_name'] = fwd_from.from_name
            
            # Подпись автора (для каналов)
            if hasattr(fwd_from, 'post_author') and fwd_from.post_author:
                forward_data['post_author_signature'] = fwd_from.post_author
            
            # Сохранённые форварды (saved_from_peer, saved_from_msg_id)
            if hasattr(fwd_from, 'saved_from_peer') and fwd_from.saved_from_peer:
                saved_peer = fwd_from.saved_from_peer
                saved_peer_data = {}
                
                if hasattr(saved_peer, 'user_id'):
                    saved_peer_data['user_id'] = saved_peer.user_id
                elif hasattr(saved_peer, 'channel_id'):
                    saved_peer_data['channel_id'] = saved_peer.channel_id
                elif hasattr(saved_peer, 'chat_id'):
                    saved_peer_data['chat_id'] = saved_peer.chat_id
                
                forward_data['saved_from_peer'] = saved_peer_data
            
            if hasattr(fwd_from, 'saved_from_msg_id') and fwd_from.saved_from_msg_id:
                forward_data['saved_from_msg_id'] = fwd_from.saved_from_msg_id
            
            # Тип публичного объявления
            if hasattr(fwd_from, 'psa_type') and fwd_from.psa_type:
                forward_data['psa_type'] = fwd_from.psa_type
            
            forwards.append(forward_data)
    
    except Exception as e:
        logger.warning("Failed to extract forwards details", error=str(e), exc_info=True)
    
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
    Извлечение деталей replies из Telegram сообщения (Context7 P1.1).
    
    Context7: Обработка message.reply_to для извлечения информации об ответах.
    Поддерживает thread_id для каналов с комментариями.
    
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
                'reply_posted_at': datetime.now(timezone.utc),  # Примерное время
                'thread_id': None  # Context7 P1.1: ID треда для каналов с комментариями
            }
            
            # Context7 P1.1: Извлечение thread_id (для каналов с комментариями)
            if hasattr(reply_to, 'reply_to_top_id') and reply_to.reply_to_top_id:
                reply_data['thread_id'] = reply_to.reply_to_top_id
            
            # Извлечение информации о чате
            if hasattr(reply_to, 'reply_to_peer_id') and reply_to.reply_to_peer_id:
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
        logger.warning("Failed to extract replies details", error=str(e), exc_info=True)
    
    return replies

