"""
Media Processor для Telethon Ingestion
Context7 best practice: SHA256 content-addressed storage, quota checks, S3 upload
"""

import asyncio
import hashlib
import io
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

import structlog
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

# Context7: MessageMediaGroup не существует в telethon 1.34.0, используем grouped_id для определения альбомов
# Импортируем опционально для обратной совместимости (если появится в будущих версиях)
try:
    from telethon.tl.types import MessageMediaGroup
    MESSAGE_MEDIA_GROUP_AVAILABLE = True
except ImportError:
    MessageMediaGroup = type(None)  # Заглушка для isinstance проверок
    MESSAGE_MEDIA_GROUP_AVAILABLE = False

# Импорты для S3 и quota
import sys
import os
# Context7: В Docker контейнере api и worker монтируются в /opt/telegram-assistant/
# Добавляем корень проекта в sys.path для импорта модулей
project_root = '/opt/telegram-assistant'
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Также добавляем локальный путь на случай dev окружения
local_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if local_root not in sys.path:
    sys.path.insert(0, local_root)

from api.services.s3_storage import S3StorageService
from worker.services.storage_quota import StorageQuotaService
from worker.events.schemas.posts_vision_v1 import MediaFile, VisionUploadedEventV1
from api.services.url_canonicalizer import URLCanonicalizer
from .metrics_utils import normalize_media_type, normalize_outcome, normalize_stage

# Context7: Prometheus метрики для мониторинга обработки медиа
try:
    from prometheus_client import Counter, Histogram, Gauge
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # Mock для случая отсутствия prometheus_client
    class Counter:
        def __init__(self, *args, **kwargs): pass
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
    class Histogram:
        def __init__(self, *args, **kwargs): pass
        def labels(self, *args, **kwargs): return self
        def observe(self, *args, **kwargs): pass
    class Gauge:
        def __init__(self, *args, **kwargs): pass
        def labels(self, *args, **kwargs): return self
        def set(self, *args, **kwargs): pass

logger = structlog.get_logger()

# Context7: Метрики Prometheus для обработки медиа
# Best practice: контроль кардинальности labels, нормализация значений
if PROMETHEUS_AVAILABLE:
    # Основные метрики обработки медиа
    # stage: parse (парсинг), vision (vision анализ), retag (ретеггинг)
    # media: нормализованные значения - photo, video, album, doc
    # outcome: ok (успех), err (ошибка)
    media_processing_total = Counter(
        'media_processing_total',
        'Total media files processed',
        ['stage', 'media', 'outcome']
    )
    
    # Суммарный объем обработанных медиа в байтах
    media_bytes_total = Counter(
        'media_bytes_total',
        'Total bytes processed',
        ['media']  # photo, video, album, doc
    )
    
    # Гистограмма размеров медиа с предопределенными buckets для SLO
    # Buckets: 50KB, 100KB, 500KB, 1MB, 5MB, 20MB
    media_size_bytes = Histogram(
        'media_size_bytes',
        'Media file size in bytes',
        ['media'],
        buckets=[50 * 1024, 100 * 1024, 500 * 1024, 1024 * 1024, 5 * 1024 * 1024, 20 * 1024 * 1024, float('inf')]
    )
    
    # Latency обработки медиа
    media_processing_duration_seconds = Histogram(
        'media_processing_duration_seconds',
        'Duration of media processing in seconds',
        ['stage', 'media', 'outcome']
    )
    
    # Альбомы обработаны
    media_albums_processed_total = Counter(
        'media_albums_processed_total',
        'Total media albums processed',
        ['status']  # success, failed, error
    )
    
    # Ошибки обработки медиа
    media_processing_failed_total = Counter(
        'media_processing_failed_total',
        'Total failed media processing attempts',
        ['reason']  # timeout, quota_exceeded, unsupported_format, download_error, album_item_error
    )
    
    # Здоровье экспорта метрик
    metrics_backend_up = Gauge(
        'metrics_backend_up',
        'Metrics backend availability',
        ['target']  # prometheus
    )
    
    # Инициализируем метрику доступности
    metrics_backend_up.labels(target='prometheus').set(1)
    
else:
    # Mock метрики если prometheus недоступен
    if not PROMETHEUS_AVAILABLE:
        logger.warning("Prometheus client not available, using mock metrics. Some metrics will not be exported.")
    
    class MockMetric:
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass
        def set(self, *args, **kwargs): pass
    
    media_processing_total = MockMetric()
    media_bytes_total = MockMetric()
    media_size_bytes = MockMetric()
    media_processing_duration_seconds = MockMetric()
    media_albums_processed_total = MockMetric()
    media_processing_failed_total = MockMetric()
    metrics_backend_up = MockMetric()


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
        tenant_id: Optional[str] = None,
        channel_id: Optional[str] = None
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
        start_time = time.time()
        media_type = "unknown"
        
        if not message.media:
            return media_files
        
        try:
            # Context7: Обработка разных типов медиа с нормализованными метриками
            stage = "parse"
            raw_media_type = "unknown"
            
            if isinstance(message.media, MessageMediaPhoto):
                raw_media_type = "photo"
                media_file = await self._process_photo(message.media, message, tenant_id, trace_id)
                if media_file:
                    media_files.append(media_file)
                    # Context7: Нормализация и метрики
                    normalized_media = normalize_media_type(raw_media_type)
                    outcome = normalize_outcome(True)
                    media_processing_total.labels(stage=stage, media=normalized_media, outcome=outcome).inc()
                    media_bytes_total.labels(media=normalized_media).inc(media_file.size_bytes)
                    media_size_bytes.labels(media=normalized_media).observe(media_file.size_bytes)
            elif isinstance(message.media, MessageMediaDocument):
                raw_media_type = "document"
                media_file = await self._process_document(message.media, message, tenant_id, trace_id)
                if media_file:
                    media_files.append(media_file)
                    normalized_media = normalize_media_type(raw_media_type)
                    outcome = normalize_outcome(True)
                    media_processing_total.labels(stage=stage, media=normalized_media, outcome=outcome).inc()
                    media_bytes_total.labels(media=normalized_media).inc(media_file.size_bytes)
                    media_size_bytes.labels(media=normalized_media).observe(media_file.size_bytes)
                else:
                    normalized_media = normalize_media_type(raw_media_type)
                    outcome = normalize_outcome(False)
                    media_processing_total.labels(stage=stage, media=normalized_media, outcome=outcome).inc()
            # Context7: Проверка на альбом через grouped_id (MessageMediaGroup не существует в telethon 1.34.0)
            elif hasattr(message, 'grouped_id') and message.grouped_id is not None:
                # Context7: Обработка медиа-альбомов (Telegram albums)
                raw_media_type = "album"
                channel_entity = getattr(message, 'peer_id', None)
                album_files = await self._process_media_group(
                    message, tenant_id, trace_id, channel_entity=channel_entity, channel_id=channel_id
                )
                media_files.extend(album_files)
                normalized_media = normalize_media_type(raw_media_type)
                if album_files:
                    media_albums_processed_total.labels(status="success").inc()
                    # Метрики по элементам альбома
                    for mf in album_files:
                        media_bytes_total.labels(media=normalized_media).inc(mf.size_bytes)
                        media_size_bytes.labels(media=normalized_media).observe(mf.size_bytes)
                else:
                    media_albums_processed_total.labels(status="failed").inc()
            
            # Context7: Latency метрика с нормализацией
            duration = time.time() - start_time
            normalized_media = normalize_media_type(raw_media_type)
            outcome = normalize_outcome(len(media_files) > 0)
            
            # Context7: Добавляем exemplar с trace_id (если поддерживается библиотекой)
            try:
                # Prometheus client может поддерживать exemplars
                media_processing_duration_seconds.labels(
                    stage=stage,
                    media=normalized_media,
                    outcome=outcome
                ).observe(duration)
            except TypeError:
                # Если exemplars не поддерживаются, используем обычный observe
                media_processing_duration_seconds.labels(
                    stage=stage,
                    media=normalized_media,
                    outcome=outcome
                ).observe(duration)
            
            logger.info(
                "Message media processed",
                post_id=post_id,
                media_count=len(media_files),
                media_type=normalized_media,
                is_album=hasattr(message, 'grouped_id') and message.grouped_id is not None,
                duration_seconds=round(duration, 3),
                trace_id=trace_id
            )
            
        except Exception as e:
            duration = time.time() - start_time
            normalized_media = normalize_media_type(raw_media_type)
            outcome = normalize_outcome(False)
            media_processing_duration_seconds.labels(stage=stage, media=normalized_media, outcome=outcome).observe(duration)
            media_processing_failed_total.labels(reason="exception").inc()
            
            logger.error(
                "Failed to process message media",
                post_id=post_id,
                error=str(e),
                media_type=normalized_media,
                trace_id=trace_id
            )
        
        return media_files
    
    async def _process_media_group(
        self,
        message: Any,
        tenant_id: str,
        trace_id: str,
        channel_entity: Any = None,
        channel_id: Optional[str] = None
    ) -> List[MediaFile]:
        """
        Обработка медиа-альбома (MessageMediaGroup).
        
        Context7 best practice: 
        - Negative cache для избежания повторных get_messages()
        - Использование iter_messages() с окном по времени вместо пагинации min_id/max_id
        - Параллельное скачивание всех медиа через asyncio.gather()
        - Сохранение порядка элементов через message.id
        
        Args:
            message: Первое сообщение из альбома (или любое с grouped_id)
            tenant_id: ID tenant
            trace_id: Trace ID для корреляции
            channel_entity: Telegram канал для получения всех сообщений альбома
            channel_id: ID канала в системе (для Redis cache)
            
        Returns:
            Список MediaFile объектов с сохранением порядка
        """
        if not self.telegram_client:
            logger.warning("TelegramClient not available for media group processing", trace_id=trace_id)
            return []
        
        try:
            # Получаем grouped_id из сообщения
            grouped_id = getattr(message, 'grouped_id', None)
            if not grouped_id:
                logger.warning("Media group without grouped_id", trace_id=trace_id)
                return []
            
            # Context7: Negative cache для избежания повторных get_messages()
            # Если альбом уже в БД, пропускаем запрос к Telegram API
            if channel_id and self.redis_client:
                cache_key = f"album_seen:{channel_id}:{grouped_id}"
                try:
                    if await self.redis_client.exists(cache_key):
                        # Альбом уже собран, пропускаем get_messages()
                        # Восстановить message_ids можно из БД через media_group_items
                        logger.debug(
                            "Album already seen, skipping get_messages",
                            grouped_id=grouped_id,
                            channel_id=channel_id,
                            trace_id=trace_id
                        )
                        return []  # Возвращаем пустой список, так как альбом уже обработан
                except Exception as e:
                    logger.warning(
                        "Failed to check Redis cache",
                        grouped_id=grouped_id,
                        error=str(e),
                        trace_id=trace_id
                    )
                    # Продолжаем обработку при ошибке кеша
            
            # Context7: Получаем все сообщения альбома через iter_messages()
            # Telethon возвращает сообщения с одинаковым grouped_id, но нужно получить их все
            # Используем channel_entity или peer из сообщения
            peer = getattr(message, 'peer_id', None) or channel_entity
            
            if not peer:
                logger.warning(
                    "Cannot determine peer for media group",
                    grouped_id=grouped_id,
                    trace_id=trace_id
                )
                return []
            
            # Context7: Использование iter_messages() с расширенным окном по времени
            # Context7 best practice: увеличенное окно для больших альбомов
            # Настраивается через ALBUM_SEARCH_WINDOW_MINUTES (по умолчанию 10 минут)
            import os
            window_minutes = int(os.getenv("ALBUM_SEARCH_WINDOW_MINUTES", "10"))
            search_limit = int(os.getenv("ALBUM_SEARCH_LIMIT", "50"))
            
            # Telegram ограничивает медиагруппы до 10 элементов, но для надежности используем большее окно
            # Стратегия: limit=50, offset_date=msg.date±10 минут, фильтрация по grouped_id
            # Прерывание: если собрали цепочку и следующее сообщение не из альбома
            
            current_date = message.date
            offset_date_min = current_date - timedelta(minutes=window_minutes)
            offset_date_max = current_date + timedelta(minutes=window_minutes)
            
            album_messages = []
            try:
                # Context7: Telethon iter_messages around message date с расширенным окном
                async for msg in self.telegram_client.iter_messages(
                    peer,
                    limit=search_limit,
                    offset_date=current_date,
                    reverse=False
                ):
                    # Проверяем окно по дате
                    if msg.date < offset_date_min or msg.date > offset_date_max:
                        continue
                    
                    # Фильтруем по grouped_id
                    if getattr(msg, 'grouped_id', None) == grouped_id:
                        album_messages.append(msg)
                        
                        # Context7: Telegram ограничение - максимум 10 элементов
                        if len(album_messages) >= 10:
                            break
                    
                    # Если уже есть элементы и встретили сообщение без grouped_id или с другим
                    # - прерываем (цепочка прервалась)
                    if album_messages and getattr(msg, 'grouped_id', None) != grouped_id:
                        break
                
                # Сортируем по message.id для сохранения порядка альбома
                album_messages.sort(key=lambda m: m.id)
                
                if not album_messages:
                    logger.warning(
                        "No messages found for media group",
                        grouped_id=grouped_id,
                        current_msg_id=message.id,
                        trace_id=trace_id
                    )
                    return []
                
                # Context7: Проверка полноты альбома
                # Если найдено 10 элементов, возможно альбом больше - логируем предупреждение
                if len(album_messages) >= 10:
                    logger.warning(
                        "Album may be incomplete - reached Telegram limit of 10 items",
                        grouped_id=grouped_id,
                        items_found=len(album_messages),
                        window_minutes=window_minutes,
                        search_limit=search_limit,
                        trace_id=trace_id
                    )
                else:
                    logger.debug(
                        "Album messages found",
                        grouped_id=grouped_id,
                        items_count=len(album_messages),
                        window_minutes=window_minutes,
                        trace_id=trace_id
                    )
                
                logger.debug(
                    "Found album messages",
                    grouped_id=grouped_id,
                    total_messages=len(album_messages),
                    message_ids=[m.id for m in album_messages],
                    trace_id=trace_id
                )
                
                # Context7: После успешного получения сообщений - кешируем факт обработки
                if channel_id and self.redis_client:
                    cache_key = f"album_seen:{channel_id}:{grouped_id}"
                    try:
                        await self.redis_client.setex(cache_key, 21600, "1")  # 6 часов TTL
                    except Exception as e:
                        logger.warning(
                            "Failed to set Redis cache",
                            grouped_id=grouped_id,
                            error=str(e),
                            trace_id=trace_id
                        )
                
            except Exception as e:
                logger.error(
                    "Failed to fetch album messages",
                    grouped_id=grouped_id,
                    error=str(e),
                    trace_id=trace_id
                )
                # Fallback: обрабатываем только текущее сообщение
                album_messages = [message]
            
            # Context7: Параллельная обработка всех медиа из альбома
            processing_tasks = []
            for msg in album_messages:
                if not msg.media:
                    continue
                
                if isinstance(msg.media, MessageMediaPhoto):
                    task = self._process_photo(msg.media, msg, tenant_id, trace_id)
                    processing_tasks.append(('photo', task))
                elif isinstance(msg.media, MessageMediaDocument):
                    task = self._process_document(msg.media, msg, tenant_id, trace_id)
                    processing_tasks.append(('document', task))
                else:
                    logger.debug(
                        "Unsupported media type in album message",
                        media_type=type(msg.media).__name__,
                        message_id=msg.id,
                        trace_id=trace_id
                    )
            
            if not processing_tasks:
                logger.warning("No processable media in album", grouped_id=grouped_id, trace_id=trace_id)
                return []
            
            # Параллельное скачивание всех медиа
            results = await asyncio.gather(
                *[task for _, task in processing_tasks],
                return_exceptions=True
            )
            
            # Фильтрация успешных результатов с сохранением порядка
            media_files = []
            for idx, ((media_type, _), result) in enumerate(zip(processing_tasks, results)):
                if isinstance(result, Exception):
                    logger.warning(
                        "Failed to process media item in album",
                        position=idx,
                        media_type=media_type,
                        error=str(result),
                        trace_id=trace_id
                    )
                    media_processing_failed_total.labels(reason="album_item_error").inc()
                elif result:
                    media_files.append(result)
            
            logger.info(
                "Media group processed",
                grouped_id=grouped_id,
                total_items=len(album_messages),
                processed_count=len(media_files),
                trace_id=trace_id
            )
            
            if media_files:
                media_albums_processed_total.labels(status="success").inc()
            else:
                media_albums_processed_total.labels(status="failed").inc()
            
            return media_files
            
        except Exception as e:
            logger.error(
                "Failed to process media group",
                error=str(e),
                trace_id=trace_id,
                exc_info=True
            )
            media_albums_processed_total.labels(status="error").inc()
            return []
    
    async def _process_photo(
        self,
        media: MessageMediaPhoto,
        message: Any,
        tenant_id: str,
        trace_id: str
    ) -> Optional[MediaFile]:
        """Обработка фото."""
        try:
            # Context7: Извлечение Telegram-специфичных метаданных
            tg_file_id = None
            tg_file_unique_id = None
            width = None
            height = None
            
            if hasattr(media, 'photo') and media.photo:
                photo = media.photo
                # Context7: Telethon предоставляет file_unique_id для дедупликации
                if hasattr(photo, 'file_unique_id'):
                    tg_file_unique_id = str(photo.file_unique_id)
                if hasattr(photo, 'id'):
                    tg_file_id = str(photo.id)
                
                # Извлечение размеров из PhotoSize (берем самый большой)
                if hasattr(photo, 'sizes') and photo.sizes:
                    largest_size = max(photo.sizes, key=lambda s: getattr(s, 'w', 0) * getattr(s, 'h', 0))
                    width = getattr(largest_size, 'w', None)
                    height = getattr(largest_size, 'h', None)
            
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
            media_file = await self._upload_to_s3(
                content=file_bytes,
                mime_type=mime_type,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            # Context7: Сохранение метаданных в MediaFile (если схема расширена)
            # Пока сохраняем только базовые поля, метаданные будут в post_media_map
            if media_file:
                logger.debug(
                    "Photo processed with metadata",
                    sha256=media_file.sha256[:8],
                    width=width,
                    height=height,
                    tg_file_unique_id=tg_file_unique_id,
                    trace_id=trace_id
                )
            
            return media_file
            
        except asyncio.TimeoutError:
            media_processing_failed_total.labels(reason="timeout").inc()
            logger.error("Photo download timeout after 120s", trace_id=trace_id)
            return None
        except Exception as e:
            media_processing_failed_total.labels(reason="download_error").inc()
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
                media_processing_failed_total.labels(reason="unsupported_format").inc()
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
            media_processing_failed_total.labels(reason="timeout").inc()
            logger.error("Document download timeout after 300s", trace_id=trace_id)
            return None
        except Exception as e:
            media_processing_failed_total.labels(reason="download_error").inc()
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
        Context7: детальное логирование ошибок с trace_id и контекстом.
        
        Returns:
            MediaFile объект или None при ошибке
        """
        try:
            # Context7: Проверка квоты перед загрузкой (async метод)
            quota_check = await self.storage_quota.check_quota_before_upload(
                tenant_id=tenant_id,
                size_bytes=len(content),
                content_type="media"
            )
            
            if not quota_check.allowed:
                media_processing_failed_total.labels(reason="quota_exceeded").inc()
                logger.warning(
                    "Quota check failed - blocking S3 upload",
                    reason=quota_check.reason,
                    size_bytes=len(content),
                    size_mb=len(content) / (1024 ** 2),
                    tenant_id=tenant_id,
                    current_usage_gb=getattr(quota_check, 'current_usage_gb', None),
                    trace_id=trace_id
                )
                return None
            
            # Context7: Загрузка в S3 (идемпотентная - возвращает существующий SHA256 если есть)
            sha256, s3_key, size_bytes = await self.s3_service.put_media(
                content=content,
                mime_type=mime_type,
                tenant_id=tenant_id
            )
            
            logger.debug(
                "Media uploaded to S3 successfully",
                sha256=sha256[:16] + "...",
                s3_key=s3_key,
                size_bytes=size_bytes,
                mime_type=mime_type,
                trace_id=trace_id
            )
            
            # Создание MediaFile объекта
            return MediaFile(
                sha256=sha256,
                s3_key=s3_key,
                mime_type=mime_type,
                size_bytes=size_bytes
            )
            
        except Exception as e:
            # Context7: Детальное логирование ошибок S3 с полным контекстом
            error_type = type(e).__name__
            error_message = str(e)
            
            # Извлекаем дополнительные детали из исключения если доступны
            error_details = {}
            if hasattr(e, 'response'):
                # Boto3 ClientError
                error_details['error_code'] = e.response.get('Error', {}).get('Code', 'Unknown')
                error_details['request_id'] = e.response.get('ResponseMetadata', {}).get('RequestId', '')
            
            media_processing_failed_total.labels(reason="s3_upload_error").inc()
            logger.error(
                "Failed to upload media to S3",
                error=error_message,
                error_type=error_type,
                mime_type=mime_type,
                size_bytes=len(content),
                size_mb=len(content) / (1024 ** 2),
                tenant_id=tenant_id,
                trace_id=trace_id,
                **error_details
            )
            # Context7: Не блокируем создание события - медиа может быть загружено позже через retry
            # Возвращаем None чтобы вызывающий код мог обработать это gracefully
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
            
            # Context7: Унифицированное имя stream - stream:posts:vision
            stream_name = "stream:posts:vision"
            
            # Публикация в Redis Stream
            event_json = event.model_dump_json()
            await self.redis_client.xadd(
                stream_name,
                {
                    "event": "posts.vision.uploaded",
                    "data": event_json
                }
            )
            
            logger.info(
                "Vision uploaded event emitted",
                stream=stream_name,
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

