"""Telegram клиент для парсинга каналов."""

import asyncio
import logging
from typing import List, Optional
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, AuthKeyError
import structlog
import redis
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime

from config import settings
from services.events import publish_post_created

logger = structlog.get_logger()


class TelegramIngestionService:
    """Сервис для парсинга Telegram каналов."""
    
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.redis_client = redis.from_url(settings.redis_url)
        self.db_connection = None
        self.is_running = False
        
    async def start(self):
        """Запуск сервиса."""
        try:
            # Подключение к БД
            self.db_connection = psycopg2.connect(settings.database_url)
            
            # Инициализация Telegram клиента
            self.client = TelegramClient(
                settings.session_name,
                settings.master_api_id,
                settings.master_api_hash
            )
            
            await self.client.start()
            logger.info("Telegram client started", user_id=self.client.get_me().id)
            
            # Регистрация обработчиков событий
            self._register_handlers()
            
            # Загрузка активных каналов
            await self._load_active_channels()
            
            self.is_running = True
            logger.info("Telegram ingestion service started")
            
        except Exception as e:
            logger.error("Failed to start telegram service", error=str(e))
            raise
    
    def _register_handlers(self):
        """Регистрация обработчиков событий."""
        
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            """Обработка новых сообщений."""
            try:
                await self._process_message(event)
            except Exception as e:
                logger.error("Error processing message", error=str(e), message_id=event.message.id)
    
    async def _load_active_channels(self):
        """Загрузка активных каналов из БД."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, tenant_id, telegram_id, username, title 
                    FROM channels 
                    WHERE is_active = true
                """)
                channels = cursor.fetchall()
                
                for channel in channels:
                    try:
                        # Получение объекта канала
                        entity = await self.client.get_entity(channel['telegram_id'])
                        logger.info("Loaded channel", 
                                  channel_id=channel['id'], 
                                  username=channel['username'],
                                  title=channel['title'])
                    except Exception as e:
                        logger.warning("Failed to load channel", 
                                     channel_id=channel['id'], 
                                     error=str(e))
                        
        except Exception as e:
            logger.error("Failed to load channels", error=str(e))
    
    async def _process_message(self, event):
        """Обработка нового сообщения."""
        try:
            message = event.message
            channel = await event.get_chat()
            
            # Получение информации о канале из БД
            channel_info = await self._get_channel_info(channel.id)
            if not channel_info:
                logger.warning("Channel not found in database", telegram_id=channel.id)
                return
            
            # Подготовка данных сообщения
            message_data = {
                'tenant_id': channel_info['tenant_id'],
                'channel_id': channel_info['id'],
                'telegram_message_id': message.id,
                'content': message.text or '',
                'media_urls': await self._extract_media_urls(message),
                'created_at': message.date.isoformat(),
                'is_processed': False
            }
            
            # Сохранение в БД
            post_id = await self._save_message(message_data)
            
            # Публикация события в Redis Streams по контракту
            publish_post_created(self.redis_client, {
                'post_id': post_id,
                'tenant_id': str(channel_info['tenant_id']),
                'channel_id': str(channel_info['id']),
                'content': message_data['content'],
                'created_at': message_data['created_at']
            })
            
            logger.info("Message processed", 
                       post_id=post_id, 
                       channel_id=channel_info['id'],
                       message_id=message.id)
            
        except FloodWaitError as e:
            logger.warning("FloodWait error", seconds=e.seconds)
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error("Error processing message", error=str(e))
    
    async def _get_channel_info(self, telegram_id: int) -> Optional[dict]:
        """Получение информации о канале из БД."""
        try:
            with self.db_connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, tenant_id, telegram_id, username, title 
                    FROM channels 
                    WHERE telegram_id = %s AND is_active = true
                """, (telegram_id,))
                return cursor.fetchone()
        except Exception as e:
            logger.error("Failed to get channel info", error=str(e))
            return None
    
    async def _save_message(self, message_data: dict) -> str:
        """Сохранение сообщения в БД."""
        try:
            with self.db_connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO posts (tenant_id, channel_id, telegram_message_id, content, media_urls, created_at, is_processed)
                    VALUES (%(tenant_id)s, %(channel_id)s, %(telegram_message_id)s, %(content)s, %(media_urls)s, %(created_at)s, %(is_processed)s)
                    RETURNING id
                """, message_data)
                post_id = cursor.fetchone()[0]
                self.db_connection.commit()
                return post_id
        except Exception as e:
            logger.error("Failed to save message", error=str(e))
            raise
    
    async def _extract_media_urls(self, message) -> List[str]:
        """Извлечение URL медиафайлов из сообщения."""
        media_urls = []
        if message.photo:
            media_urls.append(f"photo:{message.photo.id}")
        if message.video:
            media_urls.append(f"video:{message.video.id}")
        if message.document:
            media_urls.append(f"document:{message.document.id}")
        return media_urls
    
    # Публикация перенесена в services.events
    
    async def stop(self):
        """Остановка сервиса."""
        self.is_running = False
        if self.client:
            await self.client.disconnect()
        if self.db_connection:
            self.db_connection.close()
        logger.info("Telegram ingestion service stopped")
