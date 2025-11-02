"""
Context7 best practice: Сохранение медиа-альбомов в БД
Сохраняет структуру альбомов в таблицы media_groups и media_group_items
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import structlog

logger = structlog.get_logger()


async def save_media_group(
    db_session: AsyncSession,
    user_id: str,
    channel_id: str,
    grouped_id: int,
    post_ids: List[str],  # List[post_id] в порядке альбома
    media_types: List[str],  # List[media_type] в порядке альбома
    media_sha256s: Optional[List[str]] = None,  # Optional List[sha256]
    media_bytes: Optional[List[int]] = None,  # Optional List[size_bytes]
    trace_id: Optional[str] = None
) -> Optional[int]:
    """
    Сохранение медиа-альбома в таблицы media_groups и media_group_items.
    
    Context7 best practice:
    - Идемпотентность через UNIQUE (user_id, channel_id, grouped_id)
    - Сохранение порядка через position
    - Определение album_kind (photo/video/mixed)
    - Автоматический пересчет items_count через триггер
    
    Args:
        db_session: SQLAlchemy AsyncSession
        user_id: UUID пользователя
        channel_id: UUID канала
        grouped_id: Telegram grouped_id
        post_ids: Список post_id в порядке альбома (должен совпадать с порядком сообщений)
        media_types: Список типов медиа (photo, video, document, ...)
        media_sha256s: Optional список SHA256 медиа файлов
        media_bytes: Optional список размеров медиа в байтах
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
        
        # Context7: UPSERT media_groups с идемпотентностью
        upsert_group_sql = text("""
            INSERT INTO media_groups (
                user_id, channel_id, grouped_id, album_kind,
                items_count, content_hash, created_at, updated_at
            ) VALUES (
                :user_id, :channel_id, :grouped_id, :album_kind,
                :items_count, :content_hash, NOW(), NOW()
            )
            ON CONFLICT (user_id, channel_id, grouped_id) 
            DO UPDATE SET
                album_kind = EXCLUDED.album_kind,
                content_hash = EXCLUDED.content_hash,
                updated_at = NOW()
            RETURNING id
        """)
        
        result = await db_session.execute(
            upsert_group_sql,
            {
                "user_id": user_id,
                "channel_id": channel_id,
                "grouped_id": grouped_id,
                "album_kind": album_kind,
                "items_count": len(post_ids),
                "content_hash": content_hash
            }
        )
        group_id = result.scalar_one()
        
        # Context7: Удаляем старые элементы (если альбом изменился)
        # Затем вставляем новые с правильным порядком
        delete_items_sql = text("""
            DELETE FROM media_group_items
            WHERE group_id = :group_id
        """)
        await db_session.execute(delete_items_sql, {"group_id": group_id})
        
        # Context7: Batch insert элементов альбома с порядком
        items_params = []
        for position, (post_id, media_type) in enumerate(zip(post_ids, media_types)):
            item_data = {
                "group_id": group_id,
                "post_id": post_id,
                "position": position,
                "media_type": media_type,
                "media_bytes": media_bytes[position] if media_bytes and position < len(media_bytes) else None,
                "media_sha256": media_sha256s[position] if media_sha256s and position < len(media_sha256s) else None,
                "meta": json.dumps({})
            }
            items_params.append(item_data)
        
        if items_params:
            insert_items_sql = text("""
                INSERT INTO media_group_items (
                    group_id, post_id, position, media_type,
                    media_bytes, media_sha256, meta
                ) VALUES (
                    :group_id, :post_id, :position, :media_type,
                    :media_bytes, :media_sha256, :meta::jsonb
                )
                ON CONFLICT (group_id, position) DO UPDATE SET
                    post_id = EXCLUDED.post_id,
                    media_type = EXCLUDED.media_type,
                    media_bytes = EXCLUDED.media_bytes,
                    media_sha256 = EXCLUDED.media_sha256
            """)
            
            await db_session.execute(insert_items_sql, items_params)
        
        logger.info(
            "Media group saved",
            group_id=group_id,
            grouped_id=grouped_id,
            items_count=len(post_ids),
            album_kind=album_kind,
            trace_id=trace_id
        )
        
        return group_id
        
    except Exception as e:
        logger.error(
            "Failed to save media group",
            grouped_id=grouped_id,
            error=str(e),
            trace_id=trace_id
        )
        return None

