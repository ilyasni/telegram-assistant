"""
Context7 best practice: Сохранение медиа-альбомов в БД
Сохраняет структуру альбомов в таблицы media_groups и media_group_items
"""

import hashlib
import json
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import structlog

logger = structlog.get_logger()


async def _emit_album_parsed_event(
    group_id: int,
    user_id: str,
    channel_id: str,
    grouped_id: int,
    tenant_id: str,
    album_kind: Optional[str],
    items_count: int,
    post_ids: List[str],
    caption_text: Optional[str],
    posted_at: Optional[datetime],
    cover_media_id: Optional[str],
    content_hash: str,
    trace_id: Optional[str],
    event_publisher: Optional[Any],
    redis_client: Optional[Any]
):
    """Эмиссия события albums.parsed после сохранения альбома."""
    # Формируем событие
    event_data = {
        "schema_version": "v1",
        "trace_id": trace_id or str(uuid_lib.uuid4()),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": f"{tenant_id}:{channel_id}:{grouped_id}",
        "user_id": user_id,
        "channel_id": channel_id,
        "album_id": group_id,
        "grouped_id": grouped_id,
        "tenant_id": tenant_id,
        "album_kind": album_kind,
        "items_count": items_count,
        "caption_text": caption_text,
        "posted_at": posted_at.isoformat() if posted_at else None,
        "post_ids": post_ids,
        "cover_media_id": cover_media_id,
        "content_hash": content_hash,
    }
    
    # Удаляем None значения для чистоты
    event_data = {k: v for k, v in event_data.items() if v is not None}
    
    # Публикуем событие
    if event_publisher:
        # Используем event_publisher, если доступен
        await event_publisher.publish_event('albums.parsed', event_data)
        logger.debug(
            "albums.parsed event published via event_publisher",
            group_id=group_id,
            grouped_id=grouped_id
        )
    elif redis_client:
        # Прямая публикация в Redis Streams
        stream_key = "stream:albums:parsed"
        
        # Сериализуем значения для Redis
        event_payload = {}
        for key, value in event_data.items():
            if value is None:
                continue
            elif isinstance(value, (dict, list)):
                event_payload[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, datetime):
                event_payload[key] = value.isoformat()
            else:
                event_payload[key] = str(value)
        
        await redis_client.xadd(stream_key, event_payload, maxlen=10000)
        logger.debug(
            "albums.parsed event published to Redis Streams",
            group_id=group_id,
            grouped_id=grouped_id
        )
    else:
        logger.warning(
            "No event_publisher or redis_client available for albums.parsed event",
            group_id=group_id
        )


async def save_media_group(
    db_session: AsyncSession,
    user_id: str,
    channel_id: str,
    grouped_id: int,
    post_ids: List[str],  # List[post_id] в порядке альбома
    media_types: List[str],  # List[media_type] в порядке альбома
    media_sha256s: Optional[List[str]] = None,  # Optional List[sha256]
    media_bytes: Optional[List[int]] = None,  # Optional List[size_bytes]
    caption_text: Optional[str] = None,  # Текст альбома из первого сообщения
    posted_at: Optional[datetime] = None,  # Время публикации альбома
    cover_media_id: Optional[str] = None,  # UUID media_object для обложки
    media_kinds: Optional[List[str]] = None,  # List[media_kind] в порядке альбома (photo/video/document)
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,  # Tenant ID для события
    event_publisher: Optional[Any] = None,  # EventPublisher для эмиссии события albums.parsed
    redis_client: Optional[Any] = None  # Redis client для прямой публикации, если event_publisher=None
) -> Optional[int]:
    """
    Сохранение медиа-альбома в таблицы media_groups и media_group_items.
    
    Context7 best practice:
    - Идемпотентность через UNIQUE (user_id, channel_id, grouped_id)
    - Сохранение порядка через position
    - Определение album_kind (photo/video/mixed)
    - Автоматический пересчет items_count через триггер
    - Поддержка новых полей: caption_text, posted_at, cover_media_id, meta
    
    Args:
        db_session: SQLAlchemy AsyncSession
        user_id: UUID пользователя
        channel_id: UUID канала
        grouped_id: Telegram grouped_id
        post_ids: Список post_id в порядке альбома (должен совпадать с порядком сообщений)
        media_types: Список типов медиа (photo, video, document, ...)
        media_sha256s: Optional список SHA256 медиа файлов
        media_bytes: Optional список размеров медиа в байтах
        caption_text: Optional текст альбома из первого сообщения
        posted_at: Optional время публикации альбома
        cover_media_id: Optional UUID media_object для обложки (первое медиа)
        media_kinds: Optional список типов медиа (photo/video/document/audio)
        trace_id: Trace ID для логирования
        
    Returns:
        group_id (BIGINT) если успешно, None если ошибка
    """
    if not post_ids or not media_types:
        logger.warning("Empty album data", grouped_id=grouped_id, trace_id=trace_id)
        return None
    
    if len(post_ids) != len(media_types):
        logger.error(
            "Mismatched post_ids and media_types lengths",
            grouped_id=grouped_id,
            post_ids_count=len(post_ids),
            media_types_count=len(media_types),
            trace_id=trace_id
        )
        return None
    
    try:
        # Context7: Определение album_kind
        unique_types = set(media_types)
        if len(unique_types) == 1:
            album_kind = media_types[0]  # photo, video, document
        else:
            album_kind = "mixed"
        
        # Context7: Вычисление content_hash для отслеживания изменений
        # Hash по списку post_ids + media_types
        content_data = {
            "post_ids": post_ids,
            "media_types": media_types
        }
        if media_sha256s:
            content_data["sha256s"] = media_sha256s
        content_hash = hashlib.sha256(
            json.dumps(content_data, sort_keys=True).encode('utf-8')
        ).hexdigest()
        
        # Context7: Получаем media_object_id из media_objects по sha256 (если доступны)
        # Нужно для правильной ссылочной целостности cover_media_id и media_group_items.media_object_id
        media_object_ids: List[Optional[str]] = []
        if media_sha256s:
            for sha256 in media_sha256s:
                if sha256:
                    try:
                        get_media_object_sql = text("""
                            SELECT id FROM media_objects WHERE file_sha256 = :sha256 LIMIT 1
                        """)
                        result = await db_session.execute(
                            get_media_object_sql,
                            {"sha256": sha256}
                        )
                        row = result.fetchone()
                        media_object_ids.append(str(row.id) if row else None)
                    except Exception as e:
                        logger.debug(
                            "Failed to get media_object_id",
                            sha256=sha256,
                            error=str(e),
                            trace_id=trace_id
                        )
                        media_object_ids.append(None)
                else:
                    media_object_ids.append(None)
        
        # Определяем cover_media_id - первый доступный media_object_id или переданный явно
        final_cover_media_id = cover_media_id
        if not final_cover_media_id and media_object_ids:
            final_cover_media_id = next((mo_id for mo_id in media_object_ids if mo_id), None)
        
        # Context7: UPSERT media_groups с идемпотентностью и новыми полями
        upsert_group_sql = text("""
            INSERT INTO media_groups (
                user_id, channel_id, grouped_id, album_kind,
                items_count, content_hash, caption_text, posted_at, cover_media_id,
                created_at, updated_at
            ) VALUES (
                :user_id, :channel_id, :grouped_id, :album_kind,
                :items_count, :content_hash, :caption_text, :posted_at, :cover_media_id,
                NOW(), NOW()
            )
            ON CONFLICT (user_id, channel_id, grouped_id) 
            DO UPDATE SET
                album_kind = EXCLUDED.album_kind,
                content_hash = EXCLUDED.content_hash,
                caption_text = COALESCE(EXCLUDED.caption_text, media_groups.caption_text),
                posted_at = COALESCE(EXCLUDED.posted_at, media_groups.posted_at),
                cover_media_id = COALESCE(EXCLUDED.cover_media_id, media_groups.cover_media_id),
                updated_at = NOW()
            RETURNING id
        """)
        
        # Context7: Детальное логирование перед сохранением альбома
        logger.debug("Saving media group",
                    user_id=user_id,
                    channel_id=channel_id,
                    grouped_id=grouped_id,
                    post_ids_count=len(post_ids),
                    media_types_count=len(media_types),
                    album_kind=album_kind,
                    trace_id=trace_id)
        
        try:
            result = await db_session.execute(
                upsert_group_sql,
                {
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "grouped_id": grouped_id,
                    "album_kind": album_kind,
                    "items_count": len(post_ids),
                    "content_hash": content_hash,
                    "caption_text": caption_text,
                    "posted_at": posted_at,
                    "cover_media_id": final_cover_media_id
                }
            )
            group_id = result.scalar_one()
            
            logger.info("Media group upserted successfully",
                       group_id=group_id,
                       grouped_id=grouped_id,
                       items_count=len(post_ids),
                       trace_id=trace_id)
        except Exception as e:
            logger.error("Failed to upsert media_group",
                        user_id=user_id,
                        channel_id=channel_id,
                        grouped_id=grouped_id,
                        error=str(e),
                        error_type=type(e).__name__,
                        trace_id=trace_id,
                        exc_info=True)
            raise
        
        # Context7: Удаляем старые элементы (если альбом изменился)
        # Затем вставляем новые с правильным порядком
        delete_items_sql = text("""
            DELETE FROM media_group_items
            WHERE group_id = :group_id
        """)
        await db_session.execute(delete_items_sql, {"group_id": group_id})
        
        # Context7: Batch insert элементов альбома с порядком и новыми полями
        items_params = []
        for position, (post_id, media_type) in enumerate(zip(post_ids, media_types)):
            sha256 = media_sha256s[position] if media_sha256s and position < len(media_sha256s) else None
            media_object_id = media_object_ids[position] if position < len(media_object_ids) else None
            
            # Определяем media_kind (используем media_kinds если доступно, иначе media_type)
            media_kind = None
            if media_kinds and position < len(media_kinds):
                media_kind = media_kinds[position]
            else:
                # Маппинг media_type на media_kind
                media_kind = media_type if media_type in ['photo', 'video', 'document', 'audio'] else None
            
            # Context7: asyncpg требует JSON строку для JSONB при executemany
            # Используем json.dumps() для преобразования dict в JSON строку
            # Это аналогично подходу в enrichment_repository.py (строка 160)
            meta_value = json.dumps({}, ensure_ascii=False)
            
            item_data = {
                "group_id": group_id,
                "post_id": post_id,
                "position": position,
                "media_type": media_type,
                "media_bytes": media_bytes[position] if media_bytes and position < len(media_bytes) else None,
                "media_sha256": sha256,
                "sha256": sha256,  # Дублируем для поля sha256
                "media_object_id": media_object_id,
                "media_kind": media_kind,
                "meta": meta_value  # Context7: JSON строка для asyncpg JSONB
            }
            items_params.append(item_data)
        
        if items_params:
            # Context7: Исправлен SQL запрос - используем CAST(:meta AS jsonb) для asyncpg
            # При executemany через SQLAlchemy asyncpg требует явного CAST для JSON строк
            # Это аналогично подходу в enrichment_repository.py, но с именованными параметрами
            insert_items_sql = text("""
                INSERT INTO media_group_items (
                    group_id, post_id, position, media_type,
                    media_bytes, media_sha256, sha256,
                    media_object_id, media_kind, meta
                ) VALUES (
                    :group_id, :post_id, :position, :media_type,
                    :media_bytes, :media_sha256, :sha256,
                    :media_object_id, :media_kind, CAST(:meta AS jsonb)
                )
                ON CONFLICT (group_id, position) DO UPDATE SET
                    post_id = EXCLUDED.post_id,
                    media_type = EXCLUDED.media_type,
                    media_bytes = EXCLUDED.media_bytes,
                    media_sha256 = COALESCE(EXCLUDED.media_sha256, media_group_items.media_sha256),
                    sha256 = COALESCE(EXCLUDED.sha256, media_group_items.sha256),
                    media_object_id = COALESCE(EXCLUDED.media_object_id, media_group_items.media_object_id),
                    media_kind = COALESCE(EXCLUDED.media_kind, media_group_items.media_kind)
            """)
            
            # Context7: Выполняем вставку по одному элементу, так как executemany может не работать с CAST
            # Это менее эффективно, но гарантирует правильную обработку JSONB
            for item_param in items_params:
                await db_session.execute(insert_items_sql, item_param)
        
        logger.info(
            "Media group saved",
            group_id=group_id,
            grouped_id=grouped_id,
            items_count=len(post_ids),
            album_kind=album_kind,
            trace_id=trace_id
        )
        
        # Context7: Эмиссия события albums.parsed после успешного сохранения
        if tenant_id:
            try:
                await _emit_album_parsed_event(
                    group_id=group_id,
                    user_id=user_id,
                    channel_id=channel_id,
                    grouped_id=grouped_id,
                    tenant_id=tenant_id,
                    album_kind=album_kind,
                    items_count=len(post_ids),
                    post_ids=post_ids,
                    caption_text=caption_text,
                    posted_at=posted_at,
                    cover_media_id=final_cover_media_id,
                    content_hash=content_hash,
                    trace_id=trace_id,
                    event_publisher=event_publisher,
                    redis_client=redis_client
                )
            except Exception as e:
                logger.warning(
                    "Failed to emit albums.parsed event",
                    group_id=group_id,
                    error=str(e),
                    trace_id=trace_id
                )
                # Не прерываем выполнение - событие не критично
        
        return group_id
        
    except Exception as e:
        logger.error(
            "Failed to save media group",
            grouped_id=grouped_id,
            error=str(e),
            trace_id=trace_id
        )
        return None

