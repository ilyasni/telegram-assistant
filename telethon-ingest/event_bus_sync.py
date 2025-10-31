"""Context7 best practice: EventBus для синхронных операций в telethon-ingest.

Цель: Изолировать telethon-ingest от прямой записи в БД через публикацию событий.
Архитектура: telethon-ingest → Redis Streams → PostPersistenceWorker → БД
"""

import redis
import redis.exceptions
import json
import structlog
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import hashlib

logger = structlog.get_logger()


class EventBusSync:
    """Context7: Синхронный EventBus для telethon-ingest."""
    
    def __init__(self, redis_url: str):
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.stream_name = "stream:posts:parsed"
        self.consumer_group = "post_persist_workers"
        
    def _ensure_consumer_group(self):
        """Context7: Создание consumer group если не существует."""
        try:
            self.redis_client.xgroup_create(
                self.stream_name, 
                self.consumer_group, 
                id='0', 
                mkstream=True
            )
            logger.info("Consumer group created", group=self.consumer_group)
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug("Consumer group already exists", group=self.consumer_group)
            else:
                logger.error("Failed to create consumer group", error=str(e))
                raise
    
    def publish_post_created(self, post_data: Dict[str, Any]) -> str:
        """Context7: Публикация события создания поста."""
        try:
            # Context7: Идемпотентность через dedup_key
            dedup_key = self._generate_dedup_key(post_data)
            
            # Context7: Подготовка события
            event = {
                "event_type": "post.created",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": post_data,
                "dedup_key": dedup_key,
                "source": "telethon-ingest"
            }
            
            # Context7: Публикация в Redis Stream
            message_id = self.redis_client.xadd(
                self.stream_name,
                event,
                maxlen=10000,  # Context7: Ограничение размера stream
                approximate=True
            )
            
            logger.info(
                "Post created event published",
                message_id=message_id,
                dedup_key=dedup_key,
                channel_id=post_data.get('channel_id')
            )
            
            return message_id
            
        except Exception as e:
            logger.error("Failed to publish post created event", error=str(e))
            raise
    
    def publish_post_updated(self, post_data: Dict[str, Any]) -> str:
        """Context7: Публикация события обновления поста."""
        try:
            dedup_key = self._generate_dedup_key(post_data)
            
            event = {
                "event_type": "post.updated",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": post_data,
                "dedup_key": dedup_key,
                "source": "telethon-ingest"
            }
            
            message_id = self.redis_client.xadd(
                self.stream_name,
                event,
                maxlen=10000,
                approximate=True
            )
            
            logger.info(
                "Post updated event published",
                message_id=message_id,
                dedup_key=dedup_key,
                channel_id=post_data.get('channel_id')
            )
            
            return message_id
            
        except Exception as e:
            logger.error("Failed to publish post updated event", error=str(e))
            raise
    
    def publish_channel_updated(self, channel_data: Dict[str, Any]) -> str:
        """Context7: Публикация события обновления канала."""
        try:
            event = {
                "event_type": "channel.updated",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": channel_data,
                "source": "telethon-ingest"
            }
            
            message_id = self.redis_client.xadd(
                "stream:channels:updated",
                event,
                maxlen=1000,
                approximate=True
            )
            
            logger.info(
                "Channel updated event published",
                message_id=message_id,
                channel_id=channel_data.get('channel_id')
            )
            
            return message_id
            
        except Exception as e:
            logger.error("Failed to publish channel updated event", error=str(e))
            raise
    
    def _generate_dedup_key(self, post_data: Dict[str, Any]) -> str:
        """Context7: Генерация ключа дедупликации."""
        channel_id = post_data.get('channel_id')
        telegram_message_id = post_data.get('telegram_message_id')
        
        if not channel_id or not telegram_message_id:
            raise ValueError("channel_id and telegram_message_id are required for dedup_key")
        
        # Context7: SHA256 для стабильности
        content = f"{channel_id}:{telegram_message_id}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def get_stream_info(self) -> Dict[str, Any]:
        """Context7: Получение информации о stream для мониторинга."""
        try:
            info = self.redis_client.xinfo_stream(self.stream_name)
            return {
                "length": info.get("length", 0),
                "first_entry": info.get("first-entry"),
                "last_entry": info.get("last-entry"),
                "groups": info.get("groups", 0)
            }
        except Exception as e:
            logger.error("Failed to get stream info", error=str(e))
            return {"error": str(e)}
    
    def close(self):
        """Context7: Закрытие подключения."""
        if self.redis_client:
            self.redis_client.close()
            logger.info("EventBus sync connection closed")