"""
Entity Extractor - извлечение entity-level metadata из Telethon (Context7 P1.2).

Извлекает детальную информацию о Telegram сущностях:
- peer ID, access_hash
- avatar_hash (хеш аватара)
- bio (описание)
- restrictions (ограничения)
- admin status и права
"""
import structlog
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from telethon.tl.types import User, Channel, Chat, PeerUser, PeerChannel, PeerChat
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import ChannelFull, UserFull, ChatFull

logger = structlog.get_logger()


async def extract_entity_metadata(
    client,
    entity,
    peer_id: Optional[int] = None,
    peer_type: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Извлечение entity-level metadata из Telethon сущности (Context7 P1.2).
    
    Args:
        client: Telethon TelegramClient
        entity: Telethon entity (User, Channel, Chat)
        peer_id: Telegram peer ID (если известен)
        peer_type: Тип сущности (если известен)
        
    Returns:
        Словарь с метаданными сущности или None при ошибке
    """
    try:
        # Определение peer_id и peer_type
        if not peer_id or not peer_type:
            peer_id, peer_type = _extract_peer_info(entity)
        
        if not peer_id or not peer_type:
            logger.warning("Could not extract peer_id/peer_type from entity", entity=type(entity).__name__)
            return None
        
        metadata = {
            'peer_id': peer_id,
            'peer_type': peer_type,
            'last_seen_at': datetime.now(timezone.utc)
        }
        
        # Извлечение базовой информации в зависимости от типа сущности
        if isinstance(entity, User):
            metadata.update(_extract_user_metadata(entity, client))
        elif isinstance(entity, Channel):
            metadata.update(_extract_channel_metadata(entity, client))
        elif isinstance(entity, Chat):
            metadata.update(_extract_chat_metadata(entity, client))
        else:
            logger.warning("Unknown entity type", entity_type=type(entity).__name__)
            return None
        
        # Получение полной информации через GetFull* методы
        try:
            full_metadata = await _fetch_full_entity_metadata(client, entity, peer_type)
            if full_metadata:
                metadata.update(full_metadata)
        except Exception as e:
            logger.warning("Failed to fetch full entity metadata", 
                         peer_id=peer_id, peer_type=peer_type, error=str(e))
            # Продолжаем без полной информации
        
        return metadata
        
    except Exception as e:
        logger.error("Failed to extract entity metadata", 
                    peer_id=peer_id, peer_type=peer_type, error=str(e), exc_info=True)
        return None


def _extract_peer_info(entity) -> tuple[Optional[int], Optional[str]]:
    """Извлечение peer_id и peer_type из сущности."""
    if isinstance(entity, User):
        return entity.id, 'user'
    elif isinstance(entity, Channel):
        return entity.id, 'channel' if entity.broadcast else 'supergroup'
    elif isinstance(entity, Chat):
        return entity.id, 'chat'
    elif isinstance(entity, PeerUser):
        return entity.user_id, 'user'
    elif isinstance(entity, PeerChannel):
        return entity.channel_id, 'channel'  # Точный тип определится позже
    elif isinstance(entity, PeerChat):
        return entity.chat_id, 'chat'
    else:
        return None, None


def _extract_user_metadata(user: User, client) -> Dict[str, Any]:
    """Извлечение метаданных пользователя."""
    metadata = {
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_verified': user.verified,
        'is_premium': getattr(user, 'premium', False),
        'is_scam': user.scam,
        'is_fake': user.fake,
        'is_bot': user.bot,
        'is_restricted': user.restricted,
        'is_min': user.min,
        'dc_id': user.photo.dc_id if user.photo else None,
    }
    
    # Извлечение access_hash
    if hasattr(user, 'access_hash'):
        metadata['access_hash'] = user.access_hash
    
    # Извлечение avatar_hash
    if user.photo:
        photo = user.photo
        if hasattr(photo, 'photo_id'):
            # Генерируем хеш из photo_id и dc_id
            photo_str = f"{photo.photo_id}_{photo.dc_id}"
            metadata['avatar_hash'] = hashlib.sha256(photo_str.encode()).hexdigest()[:64]
    
    return metadata


def _extract_channel_metadata(channel: Channel, client) -> Dict[str, Any]:
    """Извлечение метаданных канала."""
    metadata = {
        'username': channel.username,
        'title': channel.title,
        'access_hash': channel.access_hash,
        'is_verified': channel.verified,
        'is_scam': channel.scam,
        'is_fake': channel.fake,
        'is_broadcast': channel.broadcast,
        'is_megagroup': channel.megagroup,
        'is_restricted': channel.restricted,
        'is_min': channel.min,
        'dc_id': channel.photo.dc_id if channel.photo else None,
    }
    
    # Извлечение avatar_hash
    if channel.photo:
        photo = channel.photo
        if hasattr(photo, 'photo_id'):
            photo_str = f"{photo.photo_id}_{photo.dc_id}"
            metadata['avatar_hash'] = hashlib.sha256(photo_str.encode()).hexdigest()[:64]
    
    return metadata


def _extract_chat_metadata(chat: Chat, client) -> Dict[str, Any]:
    """Извлечение метаданных чата."""
    metadata = {
        'title': chat.title,
        'is_creator': chat.creator,
        'is_kicked': chat.kicked,
        'is_left': chat.left,
        'is_deactivated': chat.deactivated,
        'participants_count': chat.participants_count,
        'dc_id': chat.photo.dc_id if chat.photo else None,
    }
    
    # Извлечение avatar_hash
    if chat.photo:
        photo = chat.photo
        if hasattr(photo, 'photo_id'):
            photo_str = f"{photo.photo_id}_{photo.dc_id}"
            metadata['avatar_hash'] = hashlib.sha256(photo_str.encode()).hexdigest()[:64]
    
    return metadata


async def _fetch_full_entity_metadata(
    client,
    entity,
    peer_type: str
) -> Optional[Dict[str, Any]]:
    """Получение полной информации через GetFull* методы."""
    try:
        if peer_type == 'user' and isinstance(entity, User):
            full_user = await client(GetFullUserRequest(entity))
            if isinstance(full_user, UserFull):
                return {
                    'bio': full_user.about,
                    'restrictions': _extract_restrictions(full_user.restricted),
                    'entity_metadata': {
                        'common_chats_count': full_user.common_chats_count,
                        'settings': _extract_user_settings(full_user.settings) if full_user.settings else None
                    }
                }
        elif peer_type in ('channel', 'supergroup') and isinstance(entity, Channel):
            full_channel = await client(GetFullChannelRequest(entity))
            if isinstance(full_channel.full_chat, ChannelFull):
                full_chat = full_channel.full_chat
                return {
                    'bio': full_chat.about,
                    'participants_count': full_chat.participants_count if hasattr(full_chat, 'participants_count') else None,
                    'members_count': full_chat.participants_count if hasattr(full_chat, 'participants_count') else None,
                    'admins_count': full_chat.admins_count if hasattr(full_chat, 'admins_count') else None,
                    'restrictions': _extract_restrictions(full_chat.restricted),
                    'entity_metadata': {
                        'migrated_from_chat_id': full_chat.migrated_from_chat_id if hasattr(full_chat, 'migrated_from_chat_id') else None,
                        'migrated_from_max_id': full_chat.migrated_from_max_id if hasattr(full_chat, 'migrated_from_max_id') else None,
                    }
                }
        elif peer_type == 'chat' and isinstance(entity, Chat):
            full_chat = await client(GetFullChatRequest(entity.id))
            if isinstance(full_chat, ChatFull):
                return {
                    'participants_count': full_chat.participants_count,
                    'entity_metadata': {
                        'chat_photo': _extract_chat_photo_info(full_chat.chat_photo) if full_chat.chat_photo else None
                    }
                }
    except Exception as e:
        logger.warning("Failed to fetch full entity metadata", 
                      peer_type=peer_type, error=str(e))
        return None
    
    return None


def _extract_restrictions(restricted: Optional[Any]) -> Optional[Dict[str, Any]]:
    """Извлечение информации об ограничениях."""
    if not restricted:
        return None
    
    restrictions = {}
    if hasattr(restricted, 'platform'):
        restrictions['platform'] = restricted.platform
    if hasattr(restricted, 'reason'):
        restrictions['reason'] = restricted.reason
    if hasattr(restricted, 'text'):
        restrictions['text'] = restricted.text
    
    return restrictions if restrictions else None


def _extract_user_settings(settings: Any) -> Optional[Dict[str, Any]]:
    """Извлечение настроек пользователя."""
    if not settings:
        return None
    
    user_settings = {}
    if hasattr(settings, 'phone_calls_available'):
        user_settings['phone_calls_available'] = settings.phone_calls_available
    if hasattr(settings, 'phone_calls_private'):
        user_settings['phone_calls_private'] = settings.phone_calls_private
    
    return user_settings if user_settings else None


def _extract_chat_photo_info(photo: Any) -> Optional[Dict[str, Any]]:
    """Извлечение информации о фото чата."""
    if not photo:
        return None
    
    photo_info = {}
    if hasattr(photo, 'dc_id'):
        photo_info['dc_id'] = photo.dc_id
    if hasattr(photo, 'photo_id'):
        photo_info['photo_id'] = photo.photo_id
    
    return photo_info if photo_info else None


async def extract_admins_metadata(
    client,
    entity,
    peer_type: str
) -> List[Dict[str, Any]]:
    """
    Извлечение метаданных администраторов сущности (Context7 P1.2).
    
    Args:
        client: Telethon TelegramClient
        entity: Telethon entity (Channel, Chat)
        peer_type: Тип сущности ('channel', 'supergroup', 'chat')
        
    Returns:
        Список словарей с метаданными администраторов
    """
    admins = []
    
    try:
        if peer_type in ('channel', 'supergroup') and isinstance(entity, Channel):
            # Для каналов/супергрупп используем GetFullChannel
            full_channel = await client(GetFullChannelRequest(entity))
            if isinstance(full_channel.full_chat, ChannelFull):
                full_chat = full_channel.full_chat
                
                # Получение списка администраторов
                if hasattr(full_chat, 'admins'):
                    for admin in full_chat.admins:
                        admin_metadata = _extract_admin_metadata(admin, peer_type)
                        if admin_metadata:
                            admins.append(admin_metadata)
        elif peer_type == 'chat' and isinstance(entity, Chat):
            # Для обычных чатов информация об администраторах может быть ограничена
            full_chat = await client(GetFullChatRequest(entity.id))
            if isinstance(full_chat, ChatFull):
                # В обычных чатах информация об админах может быть ограничена
                pass
    except Exception as e:
        logger.warning("Failed to extract admins metadata", 
                      peer_type=peer_type, error=str(e))
    
    return admins


def _extract_admin_metadata(admin: Any, peer_type: str) -> Optional[Dict[str, Any]]:
    """Извлечение метаданных одного администратора."""
    try:
        admin_metadata = {}
        
        # Извлечение peer_id администратора
        if hasattr(admin, 'user_id'):
            admin_metadata['admin_peer_id'] = admin.user_id
            admin_metadata['admin_peer_type'] = 'user'
        elif hasattr(admin, 'bot_id'):
            admin_metadata['admin_peer_id'] = admin.bot_id
            admin_metadata['admin_peer_type'] = 'bot'
        else:
            return None
        
        # Извлечение роли
        if hasattr(admin, 'rank'):
            admin_metadata['rank'] = admin.rank
        
        # Извлечение прав
        if hasattr(admin, 'admin_rights'):
            rights = admin.admin_rights
            admin_metadata['can_edit'] = getattr(rights, 'change_info', False)
            admin_metadata['can_delete'] = getattr(rights, 'delete_messages', False)
            admin_metadata['can_ban'] = getattr(rights, 'ban_users', False)
            admin_metadata['can_invite'] = getattr(rights, 'invite_users', False)
            admin_metadata['can_change_info'] = getattr(rights, 'change_info', False)
            admin_metadata['can_post_messages'] = getattr(rights, 'post_messages', False) if peer_type == 'channel' else None
            admin_metadata['can_edit_messages'] = getattr(rights, 'edit_messages', False) if peer_type == 'channel' else None
            
            # Сохраняем полные права как JSON
            rights_dict = {}
            for attr in dir(rights):
                if not attr.startswith('_') and not callable(getattr(rights, attr)):
                    rights_dict[attr] = getattr(rights, attr)
            admin_metadata['rights'] = rights_dict
        
        if hasattr(admin, 'promoted_by'):
            admin_metadata['promoted_by'] = admin.promoted_by
        
        if hasattr(admin, 'is_self'):
            admin_metadata['is_self'] = admin.is_self
        
        return admin_metadata
        
    except Exception as e:
        logger.warning("Failed to extract admin metadata", error=str(e))
        return None

