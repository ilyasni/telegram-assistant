"""Контракты событий Redis Streams для telethon-ingest."""

from typing import Dict

# Имена потоков
STREAM_POST_CREATED = "events:post.created"
STREAM_POST_TAGGED = "events:post.tagged"
STREAM_POST_INDEXED = "events:post.indexed"
STREAM_USER_AUTHORIZED = "events:user.authorized"

# Наборы обязательных полей
POST_CREATED_REQUIRED_FIELDS = {
    "post_id",
    "tenant_id",
    "channel_id",
    "content",
    "created_at",
}

USER_AUTHORIZED_REQUIRED_FIELDS = {
    "telegram_id",
    "tenant_id",
    "session_string_encrypted",
}


def publish_post_created(redis_client, payload: Dict[str, str]) -> str:
    """Публикация события post.created с базовой валидацией.

    Возвращает ID сообщения в стриме.
    """
    missing = POST_CREATED_REQUIRED_FIELDS - set(payload.keys())
    if missing:
        raise ValueError(f"post.created missing fields: {sorted(missing)}")

    # XADD
    return redis_client.xadd(STREAM_POST_CREATED, payload)


def publish_user_authorized(redis_client, payload: Dict[str, str]) -> str:
    """Публикация события user.authorized для активации тарифа.
    
    Возвращает ID сообщения в стриме.
    """
    missing = USER_AUTHORIZED_REQUIRED_FIELDS - set(payload.keys())
    if missing:
        raise ValueError(f"user.authorized missing fields: {sorted(missing)}")
    
    # XADD
    return redis_client.xadd(STREAM_USER_AUTHORIZED, payload)


