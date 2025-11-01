"""
Media Processor для Telethon Ingestion
Context7 best practice: SHA256 content-addressed storage, quota checks, S3 upload
"""

import asyncio
import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

import structlog
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

# Импорты для S3 и quota
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

from api.services.s3_storage import S3StorageService
from worker.services.storage_quota import StorageQuotaService
from worker.events.schemas.posts_vision_v1 import MediaFile, VisionUploadedEventV1
from api.services.url_canonicalizer import URLCanonicalizer

logger = structlog.get_logger()


class MediaProcessor:
    """
    Обработчик медиа файлов из Telegram сообщений.
    
    Features:
    - Скачивание медиа через Telethon
    - SHA256 вычисление для content-addressed storage
    - Проверка квот перед загрузкой
    - Загрузка в S3
    - Эмиссия VisionUploadedEventV1
    """
    
    def __init__(
        self,
        telegram_client: Optional[TelegramClient],
        s3_service: S3StorageService,
        storage_quota: StorageQuotaService,
        redis_client: Any,
        tenant_id: str = None
    ):
        self.telegram_client = telegram_client  # Может быть None, будет установлен при использовании
        self.s3_service = s3_service
        self.storage_quota = storage_quota
        self.redis_client = redis_client
        self.tenant_id = tenant_id or os.getenv("S3_DEFAULT_TENANT_ID", "877193ef-be80-4977-aaeb-8009c3d772ee")
        
        logger.info(
            "MediaProcessor initialized",
            tenant_id=self.tenant_id
        )
    
    async def process_message_media(
        self,
        message: Any,  # telethon.tl.types.Message
        post_id: str,
        trace_id: str,
        tenant_id: Optional[str] = None
    ) -> List[MediaFile]:
        """
        Обработка всех медиа файлов из сообщения.
        
        Args:
            message: Telegram message объект
            post_id: ID поста в системе
            trace_id: Trace ID для корреляции
            tenant_id: ID tenant (optional, использует default)
            
        Returns:
            Список MediaFile объектов
        """
        tenant_id = tenant_id or self.tenant_id
        media_files = []
        
        if not message.media:
            return media_files
        
        try:
            # Обработка разных типов медиа
            if isinstance(message.media, MessageMediaPhoto):
                media_file = await self._process_photo(message.media, message, tenant_id, trace_id)
                if media_file:
                    media_files.append(media_file)
            
            elif isinstance(message.media, MessageMediaDocument):
                media_file = await self._process_document(message.media, message, tenant_id, trace_id)
                if media_file:
                    media_files.append(media_file)
            
            logger.info(
                "Message media processed",
                post_id=post_id,
                media_count=len(media_files),
                trace_id=trace_id
            )
            
        except Exception as e:
            logger.error(
                "Failed to process message media",
                post_id=post_id,
                error=str(e),
                trace_id=trace_id
            )
        
        return media_files
    
    async def _process_photo(
        self,
        media: MessageMediaPhoto,
        message: Any,
        tenant_id: str,
        trace_id: str
    ) -> Optional[MediaFile]:
        """Обработка фото."""
        try:
            # Скачивание фото (получаем самое большое доступное)
            # Context7: Добавляем timeout для предотвращения зависаний при медленном соединении
            file_bytes = await asyncio.wait_for(
                self.telegram_client.download_media(message, file=bytes),
                timeout=120  # 2 минуты максимум на фото
            )
            
            if not file_bytes:
                logger.warning("Failed to download photo", trace_id=trace_id)
                return None
            
            # Определение MIME типа
            mime_type = "image/jpeg"  # Telegram фото обычно JPEG
            
            # Загрузка в S3
            return await self._upload_to_s3(
                content=file_bytes,
                mime_type=mime_type,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
        except asyncio.TimeoutError:
            logger.error("Photo download timeout after 120s", trace_id=trace_id)
            return None
        except Exception as e:
            logger.error("Failed to process photo", error=str(e), trace_id=trace_id)
            return None
    
    async def _process_document(
        self,
        media: MessageMediaDocument,
        message: Any,
        tenant_id: str,
        trace_id: str
    ) -> Optional[MediaFile]:
        """Обработка документа."""
        try:
            # Получение информации о документе
            document = media.document
            if not document:
                return None
            
            # Проверка размера (не более 40MB для документов GigaChat)
            file_size = getattr(document, 'size', 0)
            if file_size > 40 * 1024 * 1024:  # 40MB
                logger.warning(
                    "Document too large for Vision API",
                    size_bytes=file_size,
                    trace_id=trace_id
                )
                return None
            
            # Определение MIME типа
            mime_type = self._get_mime_from_document(document)
            
            # Проверка, поддерживается ли тип
            if not self._is_supported_mime(mime_type):
                logger.debug("Unsupported document type", mime_type=mime_type, trace_id=trace_id)
                return None
            
            # Скачивание документа
            # Context7: Добавляем timeout для предотвращения зависаний при медленном соединении
            file_bytes = await asyncio.wait_for(
                self.telegram_client.download_media(message, file=bytes),
                timeout=300  # 5 минут максимум на документы
            )
            
            if not file_bytes:
                logger.warning("Failed to download document", trace_id=trace_id)
                return None
            
            # Загрузка в S3
            return await self._upload_to_s3(
                content=file_bytes,
                mime_type=mime_type,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
        except asyncio.TimeoutError:
            logger.error("Document download timeout after 300s", trace_id=trace_id)
            return None
        except Exception as e:
            logger.error("Failed to process document", error=str(e), trace_id=trace_id)
            return None
    
    async def _upload_to_s3(
        self,
        content: bytes,
        mime_type: str,
        tenant_id: str,
        trace_id: str
    ) -> Optional[MediaFile]:
        """
        Загрузка медиа в S3 с проверкой квот.
        
        Returns:
            MediaFile объект или None при ошибке
        """
        try:
            # Проверка квоты перед загрузкой (async метод)
            quota_check = await self.storage_quota.check_quota_before_upload(
                tenant_id=tenant_id,
                size_bytes=len(content),
                content_type="media"
            )
            
            if not quota_check.allowed:
                logger.warning(
                    "Quota check failed",
                    reason=quota_check.reason,
                    size_bytes=len(content),
                    tenant_id=tenant_id,
                    trace_id=trace_id
                )
                return None
            
            # Загрузка в S3 (идемпотентная - возвращает существующий SHA256 если есть)
            # Note: put_media async метод
            sha256, s3_key, size_bytes = await self.s3_service.put_media(
                content=content,
                mime_type=mime_type,
                tenant_id=tenant_id
            )
            
            # Создание MediaFile объекта
            return MediaFile(
                sha256=sha256,
                s3_key=s3_key,
                mime_type=mime_type,
                size_bytes=size_bytes
            )
            
        except Exception as e:
            logger.error(
                "Failed to upload media to S3",
                error=str(e),
                mime_type=mime_type,
                size_bytes=len(content),
                trace_id=trace_id
            )
            return None
    
    async def emit_vision_uploaded_event(
        self,
        post_id: str,
        tenant_id: str,
        media_files: List[MediaFile],
        trace_id: str
    ):
        """
        Эмиссия события VisionUploadedEventV1 в stream:posts:vision.
        
        Args:
            post_id: ID поста
            tenant_id: ID tenant
            media_files: Список обработанных медиа файлов
            trace_id: Trace ID для корреляции
        """
        if not media_files:
            return
        
        try:
            # Фильтрация: только подходящие для Vision (изображения и документы)
            vision_files = [
                mf for mf in media_files
                if self._is_vision_suitable(mf.mime_type)
            ]
            
            if not vision_files:
                logger.debug("No vision-suitable media files", post_id=post_id)
                return
            
            # Создание события
            event = VisionUploadedEventV1(
                tenant_id=tenant_id,
                post_id=post_id,
                media_files=vision_files,
                requires_vision=True,
                idempotency_key=f"{tenant_id}:{post_id}:vision_upload",
                trace_id=trace_id
            )
            
            # Публикация в Redis Stream
            event_json = event.model_dump_json()
            await self.redis_client.xadd(
                "stream:posts:vision",
                {
                    "event": "posts.vision.uploaded",
                    "data": event_json
                }
            )
            
            logger.info(
                "Vision uploaded event emitted",
                post_id=post_id,
                media_count=len(vision_files),
                trace_id=trace_id
            )
            
        except Exception as e:
            logger.error(
                "Failed to emit vision uploaded event",
                post_id=post_id,
                error=str(e),
                trace_id=trace_id
            )
    
    def _get_mime_from_document(self, document) -> str:
        """Определение MIME типа из Telegram document."""
        # Получаем mime_type из атрибутов документа
        mime_type = getattr(document, 'mime_type', None)
        
        if mime_type:
            return mime_type
        
        # Fallback: определение по расширению
        file_name = getattr(document, 'file_name', '')
        if file_name:
            ext = file_name.split('.')[-1].lower()
            mime_map = {
                'pdf': 'application/pdf',
                'doc': 'application/msword',
                'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'txt': 'text/plain',
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'png': 'image/png',
            }
            return mime_map.get(ext, 'application/octet-stream')
        
        return 'application/octet-stream'
    
    def _is_supported_mime(self, mime_type: str) -> bool:
        """Проверка, поддерживается ли MIME тип для Vision API."""
        supported_images = [
            'image/jpeg',
            'image/png',
            'image/tiff',
            'image/bmp',
        ]
        
        supported_docs = [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'text/plain',
        ]
        
        return mime_type in (supported_images + supported_docs)
    
    def _is_vision_suitable(self, mime_type: str) -> bool:
        """Проверка, подходит ли медиа для Vision анализа."""
        return self._is_supported_mime(mime_type)

