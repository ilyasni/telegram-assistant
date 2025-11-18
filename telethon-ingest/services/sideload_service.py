"""
Context7 P3: Sideloading Service для импорта личных диалогов и групп.

Использует Telethon для:
- Итерации по диалогам (iter_dialogs)
- Импорта сообщений из личных диалогов (DM)
- Импорта сообщений из групп
- Публикации событий persona_message_ingested, persona_graph_updated
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Set
import structlog
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel, Dialog
from telethon.utils import get_peer_id

from .telegram_client_manager import TelegramClientManager
from .floodwait_manager import FloodWaitManager
from .atomic_db_saver import AtomicDBSaver
from config import settings

logger = structlog.get_logger()


class SideloadService:
    """
    Context7 P3: Сервис для sideloading личных диалогов и групп.
    
    Features:
    - Импорт диалогов через iter_dialogs()
    - Импорт сообщений из DM и групп через iter_messages()
    - Сохранение в PostgreSQL с флагом source (dm/group/persona)
    - Публикация событий для Graph-RAG и аналитики
    """
    
    def __init__(
        self,
        telegram_client_manager: TelegramClientManager,
        db_session: AsyncSession,
        redis_client: Optional[Any] = None,
        event_publisher: Optional[Any] = None,
        floodwait_manager: Optional[FloodWaitManager] = None,
        account_id: Optional[str] = None
    ):
        self.client_manager = telegram_client_manager
        self.db_session = db_session
        self.redis_client = redis_client
        self.event_publisher = event_publisher
        self.floodwait_manager = floodwait_manager
        self.account_id = account_id
        self.atomic_saver = AtomicDBSaver()
        
        # Статистика импорта
        self.stats = {
            'dialogs_processed': 0,
            'messages_imported': 0,
            'dm_messages': 0,
            'group_messages': 0,
            'errors': 0,
            'skipped': 0
        }
    
    async def import_user_dialogs(
        self,
        user_id: str,
        tenant_id: str,
        dialog_types: Optional[List[str]] = None,
        limit_per_dialog: int = 100,
        since_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Импорт диалогов пользователя (DM и группы).
        
        Args:
            user_id: Telegram ID пользователя
            tenant_id: Tenant ID
            dialog_types: Типы диалогов для импорта ('dm', 'group', 'channel') или None (все)
            limit_per_dialog: Лимит сообщений на диалог
            since_date: Дата начала импорта (опционально)
            
        Returns:
            Статистика импорта
        """
        if dialog_types is None:
            dialog_types = ['dm', 'group']
        
        try:
            # Получаем Telegram клиент
            telegram_id = int(user_id)
            client = await self.client_manager.get_client(telegram_id)
            if not client:
                raise ValueError(f"Telegram client not available for user {user_id}")
            
            # Итерируемся по диалогам
            dialogs_imported = 0
            messages_imported = 0
            
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                dialog_type = self._classify_dialog(entity)
                
                # Фильтруем по типам
                if dialog_type not in dialog_types:
                    continue
                
                # Пропускаем каналы (они обрабатываются через channel_parser)
                if dialog_type == 'channel':
                    continue
                
                try:
                    # Импортируем сообщения из диалога
                    dialog_stats = await self._import_dialog_messages(
                        client=client,
                        dialog=dialog,
                        entity=entity,
                        dialog_type=dialog_type,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        limit=limit_per_dialog,
                        since_date=since_date
                    )
                    
                    dialogs_imported += 1
                    messages_imported += dialog_stats.get('messages_imported', 0)
                    
                    logger.info(
                        "Dialog imported",
                        dialog_type=dialog_type,
                        entity_id=getattr(entity, 'id', None),
                        title=getattr(entity, 'title', getattr(entity, 'first_name', 'Unknown')),
                        messages_count=dialog_stats.get('messages_imported', 0)
                    )
                    
                except Exception as e:
                    logger.error(
                        "Failed to import dialog",
                        dialog_type=dialog_type,
                        entity_id=getattr(entity, 'id', None),
                        error=str(e),
                        exc_info=True
                    )
                    self.stats['errors'] += 1
                    continue
            
            self.stats['dialogs_processed'] = dialogs_imported
            self.stats['messages_imported'] = messages_imported
            
            logger.info(
                "Sideloading completed",
                user_id=user_id,
                tenant_id=tenant_id,
                dialogs_imported=dialogs_imported,
                messages_imported=messages_imported
            )
            
            return {
                'success': True,
                'stats': self.stats.copy()
            }
            
        except Exception as e:
            logger.error(
                "Sideloading failed",
                user_id=user_id,
                tenant_id=tenant_id,
                error=str(e),
                exc_info=True
            )
            self.stats['errors'] += 1
            return {
                'success': False,
                'error': str(e),
                'stats': self.stats.copy()
            }
    
    def _classify_dialog(self, entity: Any) -> str:
        """Классификация типа диалога."""
        if isinstance(entity, User):
            return 'dm'
        elif isinstance(entity, Chat):
            return 'group'
        elif isinstance(entity, Channel):
            if getattr(entity, 'megagroup', False) or getattr(entity, 'gigagroup', False):
                return 'group'  # Supergroup
            else:
                return 'channel'
        else:
            return 'unknown'
    
    async def _import_dialog_messages(
        self,
        client: TelegramClient,
        dialog: Dialog,
        entity: Any,
        dialog_type: str,
        user_id: str,
        tenant_id: str,
        limit: int = 100,
        since_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Импорт сообщений из диалога.
        
        Args:
            client: TelegramClient
            dialog: Dialog объект
            entity: Entity (User/Chat/Channel)
            dialog_type: Тип диалога ('dm', 'group')
            user_id: Telegram ID пользователя
            tenant_id: Tenant ID
            limit: Лимит сообщений
            since_date: Дата начала импорта
            
        Returns:
            Статистика импорта
        """
        stats = {
            'messages_imported': 0,
            'skipped': 0,
            'errors': 0
        }
        
        messages_batch = []
        entity_id = getattr(entity, 'id', None)
        try:
            peer_id = get_peer_id(entity) if entity else None
        except Exception as e:
            logger.warning("Failed to get peer_id", entity_id=entity_id, error=str(e))
            peer_id = None
        
        try:
            # Итерируемся по сообщениям
            message_count = 0
            async for message in client.iter_messages(
                entity,
                limit=limit,
                offset_date=since_date,
                reverse=False  # От новых к старым
            ):
                # Пропускаем служебные сообщения
                if not message.message and not message.media:
                    continue
                
                try:
                    # Извлекаем данные сообщения
                    message_data = await self._extract_message_data(
                        message=message,
                        entity=entity,
                        dialog_type=dialog_type,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        peer_id=peer_id
                    )
                    
                    if message_data:
                        messages_batch.append(message_data)
                        message_count += 1
                    
                    # Сохраняем батчами
                    if len(messages_batch) >= 50:
                        saved = await self._save_messages_batch(
                            messages_batch,
                            dialog_type=dialog_type,
                            user_id=user_id,
                            tenant_id=tenant_id
                        )
                        stats['messages_imported'] += saved
                        messages_batch = []
                
                except Exception as e:
                    logger.warning(
                        "Failed to process message",
                        message_id=getattr(message, 'id', None),
                        error=str(e)
                    )
                    stats['errors'] += 1
                    continue
            
            # Сохраняем оставшиеся сообщения
            if messages_batch:
                saved = await self._save_messages_batch(
                    messages_batch,
                    dialog_type=dialog_type,
                    user_id=user_id,
                    tenant_id=tenant_id
                )
                stats['messages_imported'] += saved
            
            # Обновляем статистику
            if dialog_type == 'dm':
                self.stats['dm_messages'] += stats['messages_imported']
            elif dialog_type == 'group':
                self.stats['group_messages'] += stats['messages_imported']
            
            return stats
            
        except Exception as e:
            logger.error(
                "Failed to import dialog messages",
                dialog_type=dialog_type,
                entity_id=entity_id,
                error=str(e),
                exc_info=True
            )
            stats['errors'] += 1
            return stats
    
    async def _extract_message_data(
        self,
        message: Any,
        entity: Any,
        dialog_type: str,
        user_id: str,
        tenant_id: str,
        peer_id: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """Извлечение данных сообщения для сохранения."""
        try:
            content = message.message or ""
            posted_at = message.date.replace(tzinfo=timezone.utc) if message.date else datetime.now(timezone.utc)
            
            # Определяем sender
            sender_id = getattr(message, 'from_id', None)
            if sender_id:
                try:
                    sender_peer_id = get_peer_id(sender_id) if sender_id else None
                except Exception:
                    sender_peer_id = None
            else:
                sender_peer_id = None
            
            # Базовые данные
            base_data = {
                'telegram_message_id': message.id,
                'content': content,
                'posted_at': posted_at,
                'source': dialog_type,
                'created_at': datetime.now(timezone.utc),
                'has_media': bool(message.media)
            }
            
            if dialog_type == 'dm':
                # Для DM сохраняем в Post с source='dm'
                # Создаём/находим виртуальный "канал" для диалога
                # peer_id для DM = telegram_id собеседника
                if not peer_id:
                    # Получаем peer_id из entity (User)
                    try:
                        peer_id = get_peer_id(entity) if entity else None
                    except Exception:
                        peer_id = None
                
                if not peer_id:
                    logger.warning("Cannot determine peer_id for DM dialog", entity_id=getattr(entity, 'id', None))
                    return None
                
                channel_data = await self._get_or_create_dm_channel(
                    entity=entity,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    peer_id=peer_id
                )
                
                return {
                    **base_data,
                    'channel_id': channel_data['channel_id'],
                    'post_author': getattr(entity, 'first_name', None) or getattr(entity, 'username', None),
                    'sender_tg_id': sender_peer_id
                }
            
            elif dialog_type == 'group':
                # Для групп сохраняем в GroupMessage с source='group' или 'persona'
                group_data = await self._get_or_create_group(
                    entity=entity,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    peer_id=peer_id
                )
                
                return {
                    **base_data,
                    'group_id': group_data['group_id'],
                    'sender_tg_id': sender_peer_id,
                    'sender_username': getattr(message.sender, 'username', None) if hasattr(message, 'sender') else None
                }
            
            return None
            
        except Exception as e:
            logger.warning(
                "Failed to extract message data",
                message_id=getattr(message, 'id', None),
                error=str(e)
            )
            return None
    
    async def _get_or_create_dm_channel(
        self,
        entity: Any,
        user_id: str,
        tenant_id: str,
        peer_id: Optional[int]
    ) -> Dict[str, str]:
        """
        Создание/получение виртуального канала для DM диалога.
        
        Context7 P3: Используем специальный префикс для DM каналов (tg_channel_id < 0).
        """
        if not peer_id:
            raise ValueError("peer_id required for DM channel")
        
        # Используем отрицательный ID для DM каналов
        tg_channel_id = -abs(peer_id)
        
        # Проверяем существование канала
        result = await self.db_session.execute(
            text("SELECT id FROM channels WHERE tg_channel_id = :tg_channel_id"),
            {'tg_channel_id': tg_channel_id}
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            return {'channel_id': str(existing)}
        
        # Создаём новый канал для DM
        title = getattr(entity, 'first_name', None) or getattr(entity, 'username', None) or f"DM {peer_id}"
        channel_id = UUID()
        
        await self.db_session.execute(
            text("""
                INSERT INTO channels (id, tg_channel_id, title, is_active, created_at)
                VALUES (:id, :tg_channel_id, :title, :is_active, :created_at)
                ON CONFLICT (tg_channel_id) DO NOTHING
            """),
            {
                'id': str(channel_id),
                'tg_channel_id': tg_channel_id,
                'title': title,
                'is_active': True,
                'created_at': datetime.now(timezone.utc)
            }
        )
        await self.db_session.commit()
        
        return {'channel_id': str(channel_id)}
    
    async def _get_or_create_group(
        self,
        entity: Any,
        user_id: str,
        tenant_id: str,
        peer_id: Optional[int]
    ) -> Dict[str, str]:
        """Создание/получение группы."""
        if not peer_id:
            raise ValueError("peer_id required for group")
        
        # Проверяем существование группы
        result = await self.db_session.execute(
            text("SELECT id FROM groups WHERE tg_chat_id = :tg_chat_id AND tenant_id = :tenant_id::uuid"),
            {'tg_chat_id': peer_id, 'tenant_id': tenant_id}
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            return {'group_id': str(existing)}
        
        # Создаём новую группу
        title = getattr(entity, 'title', None) or f"Group {peer_id}"
        username = getattr(entity, 'username', None)
        group_id = UUID()
        
        await self.db_session.execute(
            text("""
                INSERT INTO groups (id, tenant_id, tg_chat_id, title, username, is_active, created_at)
                VALUES (:id, :tenant_id, :tg_chat_id, :title, :username, :is_active, :created_at)
                ON CONFLICT (tenant_id, tg_chat_id) DO NOTHING
            """),
            {
                'id': str(group_id),
                'tenant_id': tenant_id,
                'tg_chat_id': peer_id,
                'title': title,
                'username': username,
                'is_active': True,
                'created_at': datetime.now(timezone.utc)
            }
        )
        await self.db_session.commit()
        
        return {'group_id': str(group_id)}
    
    async def _save_messages_batch(
        self,
        messages_data: List[Dict[str, Any]],
        dialog_type: str,
        user_id: str,
        tenant_id: str
    ) -> int:
        """Сохранение батча сообщений в БД."""
        if not messages_data:
            return 0
        
        saved_count = 0
        
        try:
            if dialog_type == 'dm':
                # Сохраняем в Post с source='dm'
                for msg_data in messages_data:
                    try:
                        await self.db_session.execute(
                            text("""
                                INSERT INTO posts (
                                    id, channel_id, telegram_message_id, content, posted_at,
                                    source, created_at, has_media, post_author
                                )
                                VALUES (
                                    gen_random_uuid(), :channel_id::uuid, :telegram_message_id, :content,
                                    :posted_at, :source, :created_at, :has_media, :post_author
                                )
                                ON CONFLICT (channel_id, telegram_message_id) DO NOTHING
                            """),
                            {
                                'channel_id': msg_data['channel_id'],
                                'telegram_message_id': msg_data['telegram_message_id'],
                                'content': msg_data.get('content'),
                                'posted_at': msg_data['posted_at'],
                                'source': 'dm',
                                'created_at': msg_data['created_at'],
                                'has_media': msg_data.get('has_media', False),
                                'post_author': msg_data.get('post_author')
                            }
                        )
                        saved_count += 1
                    except Exception as e:
                        logger.warning("Failed to save DM message", error=str(e))
                        continue
            
            elif dialog_type == 'group':
                # Сохраняем в GroupMessage с source='group'
                for msg_data in messages_data:
                    try:
                        await self.db_session.execute(
                            text("""
                                INSERT INTO group_messages (
                                    id, group_id, tenant_id, tg_message_id, sender_tg_id,
                                    sender_username, content, posted_at, source, created_at, has_media
                                )
                                VALUES (
                                    gen_random_uuid(), :group_id::uuid, :tenant_id::uuid, :tg_message_id,
                                    :sender_tg_id, :sender_username, :content, :posted_at, :source, :created_at, :has_media
                                )
                                ON CONFLICT (group_id, tg_message_id) DO NOTHING
                            """),
                            {
                                'group_id': msg_data['group_id'],
                                'tenant_id': tenant_id,
                                'tg_message_id': msg_data['telegram_message_id'],
                                'sender_tg_id': msg_data.get('sender_tg_id'),
                                'sender_username': msg_data.get('sender_username'),
                                'content': msg_data.get('content'),
                                'posted_at': msg_data['posted_at'],
                                'source': 'group',
                                'created_at': msg_data['created_at'],
                                'has_media': msg_data.get('has_media', False)
                            }
                        )
                        saved_count += 1
                    except Exception as e:
                        logger.warning("Failed to save group message", error=str(e))
                        continue
            
            await self.db_session.commit()
            
            # Публикуем события
            await self._publish_persona_events(messages_data, dialog_type, user_id, tenant_id)
            
            return saved_count
            
        except Exception as e:
            logger.error("Failed to save messages batch", error=str(e), exc_info=True)
            await self.db_session.rollback()
            return 0
    
    async def _publish_persona_events(
        self,
        messages_data: List[Dict[str, Any]],
        dialog_type: str,
        user_id: str,
        tenant_id: str
    ):
        """Публикация событий persona_message_ingested."""
        if not self.redis_client or not messages_data:
            return
        
        try:
            stream_key = "stream:persona:messages:ingested"
            
            for msg_data in messages_data:
                # Context7 P3: Получаем message_id из БД после сохранения
                # Для DM: используем channel_id + telegram_message_id для поиска post_id
                # Для групп: используем group_id + tg_message_id для поиска group_message_id
                message_id_from_db = None
                if dialog_type == 'dm' and msg_data.get('channel_id'):
                    try:
                        result = await self.db_session.execute(
                            text("""
                                SELECT id FROM posts 
                                WHERE channel_id = :channel_id::uuid 
                                AND telegram_message_id = :telegram_message_id
                                LIMIT 1
                            """),
                            {
                                'channel_id': msg_data['channel_id'],
                                'telegram_message_id': msg_data['telegram_message_id']
                            }
                        )
                        row = result.fetchone()
                        if row:
                            message_id_from_db = str(row[0])
                    except Exception as e:
                        logger.debug("Failed to fetch message_id from DB", error=str(e))
                elif dialog_type == 'group' and msg_data.get('group_id'):
                    try:
                        result = await self.db_session.execute(
                            text("""
                                SELECT id FROM group_messages 
                                WHERE group_id = :group_id::uuid 
                                AND tg_message_id = :tg_message_id
                                LIMIT 1
                            """),
                            {
                                'group_id': msg_data['group_id'],
                                'tg_message_id': msg_data['telegram_message_id']
                            }
                        )
                        row = result.fetchone()
                        if row:
                            message_id_from_db = str(row[0])
                    except Exception as e:
                        logger.debug("Failed to fetch group_message_id from DB", error=str(e))
                
                event_payload = {
                    'user_id': user_id,
                    'tenant_id': tenant_id,
                    'dialog_type': dialog_type,
                    'message_id': message_id_from_db or str(msg_data.get('id', '')),
                    'telegram_message_id': str(msg_data.get('telegram_message_id', '')),
                    'content': msg_data.get('content', '')[:500],  # Ограничиваем длину
                    'posted_at': msg_data['posted_at'].isoformat() if isinstance(msg_data.get('posted_at'), datetime) else str(msg_data.get('posted_at', '')),
                    'source': msg_data.get('source', dialog_type),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'sender_tg_id': str(msg_data.get('sender_tg_id', '')) if msg_data.get('sender_tg_id') else None,
                    'sender_username': msg_data.get('sender_username')
                }
                
                # Публикуем в Redis Streams
                if hasattr(self.redis_client, 'xadd'):
                    await self.redis_client.xadd(stream_key, event_payload, maxlen=10000)
                elif hasattr(self.redis_client, 'execute_command'):
                    await self.redis_client.execute_command('XADD', stream_key, '*', *[str(k) for k, v in event_payload.items() for _ in [1, str(v)]])
            
            logger.debug(
                "Published persona events",
                count=len(messages_data),
                dialog_type=dialog_type
            )
            
        except Exception as e:
            logger.warning("Failed to publish persona events", error=str(e))

