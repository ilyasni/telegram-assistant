"""Контракты событий Redis Streams для worker."""

from typing import Dict, Tuple, List

# Имена потоков
STREAM_POST_CREATED = "events:post.created"
STREAM_POST_TAGGED = "events:post.tagged"
STREAM_POST_INDEXED = "events:post.indexed"
STREAM_GROUP_MESSAGE_CREATED = "stream:groups:messages"


def read_post_created(redis_client, last_id: str = "$", count: int = 10, block_ms: int = 1000) -> Tuple[str, Dict[str, str]]:
    """Читает одно событие post.created (blocking).

    Возвращает (msg_id, payload) или (None, None) если нет сообщений.
    """
    streams = redis_client.xread({STREAM_POST_CREATED: last_id}, count=count, block=block_ms)
    if not streams:
        return None, None

    _, msgs = streams[0]
    if not msgs:
        return None, None

    msg_id, fields = msgs[0]
    payload = {k.decode(): v.decode() for k, v in fields.items()}
    return msg_id.decode() if hasattr(msg_id, 'decode') else msg_id, payload


def publish_post_tagged(redis_client, payload: Dict[str, str]) -> str:
    return redis_client.xadd(STREAM_POST_TAGGED, payload)


def publish_post_indexed(redis_client, payload: Dict[str, str]) -> str:
    return redis_client.xadd(STREAM_POST_INDEXED, payload)


# --- Consumer Group helpers (надёжное чтение) ---

GROUP_POST_CREATED = "worker"
GROUP_GROUP_MESSAGE = "worker-group-messages"


def ensure_stream_group(redis_client, stream: str, group: str) -> None:
    try:
        redis_client.xgroup_create(stream, group, id="0-0", mkstream=True)
    except Exception as e:
        # BUSYGROUP is OK – группа уже существует
        if "BUSYGROUP" not in str(e):
            raise


def ensure_consumer(redis_client, stream: str, group: str, consumer: str) -> None:
    try:
        redis_client.xgroup_createconsumer(stream, group, consumer)
    except Exception:
        # ignore if exists
        pass


def read_post_created_group(
    redis_client,
    group: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 2000,
) -> List[Tuple[str, Dict[str, str]]]:
    """Читает сообщения из группы (pending + новые). Возвращает список (msg_id, payload)."""
    import structlog
    logger = structlog.get_logger()
    
    messages: List[Tuple[str, Dict[str, str]]] = []

    # Сначала прочитаем pending (XREADGROUP с id 0)
    for start_id in ("0", ">"):
        logger.debug("Reading from stream", start_id=start_id, group=group, consumer=consumer)
        try:
            items = redis_client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={STREAM_POST_CREATED: start_id},
                count=count,
                block=block_ms,
            )
            logger.debug("XREADGROUP result", items=items)
        except Exception as e:
            logger.error("XREADGROUP failed", error=str(e), start_id=start_id)
            continue
            
        if not items:
            logger.debug("No items from XREADGROUP", start_id=start_id)
            continue
            
        _, msgs = items[0]
        logger.info("Found messages", count=len(msgs), start_id=start_id)
        
        for msg_id, fields in msgs:
            payload = {k.decode(): v.decode() for k, v in fields.items()}
            messages.append((msg_id.decode() if hasattr(msg_id, 'decode') else msg_id, payload))
        # Если уже что-то нашли – выходим; следующий вызов из цикла продолжит
        if messages:
            break

    return messages


def ack_post_created(redis_client, group: str, msg_id: str) -> int:
    return redis_client.xack(STREAM_POST_CREATED, group, msg_id)


# --- Tagged stream helpers ---

def read_post_tagged_group(
    redis_client,
    group: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 2000,
):
    """Читает сообщения из группы для STREAM_POST_TAGGED (pending + новые)."""
    import structlog
    logger = structlog.get_logger()

    messages = []
    for start_id in ("0", ">"):
        try:
            items = redis_client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={STREAM_POST_TAGGED: start_id},
                count=count,
                block=block_ms,
            )
        except Exception as e:
            logger.error("XREADGROUP failed (tagged)", error=str(e), start_id=start_id)
            continue

        if not items:
            continue

        _, msgs = items[0]
        for msg_id, fields in msgs:
            payload = {k.decode(): v.decode() for k, v in fields.items()}
            messages.append((msg_id.decode() if hasattr(msg_id, 'decode') else msg_id, payload))
        if messages:
            break

    return messages


def ack_post_tagged(redis_client, group: str, msg_id: str) -> int:
    return redis_client.xack(STREAM_POST_TAGGED, group, msg_id)


def read_group_message_created_group(
    redis_client,
    group: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 2000,
):
    """Читает сообщения из группы для STREAM_GROUP_MESSAGE_CREATED."""
    import structlog

    logger = structlog.get_logger()
    messages = []

    for start_id in ("0", ">"):
        try:
            items = redis_client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={STREAM_GROUP_MESSAGE_CREATED: start_id},
                count=count,
                block=block_ms,
            )
        except Exception as e:
            logger.error("XREADGROUP failed (group messages)", error=str(e), start_id=start_id)
            continue

        if not items:
            continue

        _, msgs = items[0]
        for msg_id, fields in msgs:
            payload = {k.decode(): v.decode() for k, v in fields.items()}
            messages.append((msg_id.decode() if hasattr(msg_id, "decode") else msg_id, payload))
        if messages:
            break

    return messages


def ack_group_message_created(redis_client, group: str, msg_id: str) -> int:
    return redis_client.xack(STREAM_GROUP_MESSAGE_CREATED, group, msg_id)


