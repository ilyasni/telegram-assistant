"""
Integration тесты для сохранения forwards/reactions/replies.

Context7: Проверка сохранения деталей в post_forwards, post_reactions, post_replies.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from telethon_ingest.services.atomic_db_saver import AtomicDBSaver
from telethon_ingest.services.message_enricher import (
    extract_forwards_details,
    extract_reactions_details,
    extract_replies_details,
)


@pytest.mark.asyncio
async def test_save_forwards_reactions_replies():
    """Проверка сохранения forwards/reactions/replies через AtomicDBSaver."""
    mock_db_session = AsyncMock()
    mock_execute = AsyncMock()
    mock_db_session.execute = mock_execute

    saver = AtomicDBSaver()

    post_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    forwards_data = [
        {
            "from_chat_id": 12345,
            "from_message_id": 67890,
            "from_chat_title": "Test Channel",
            "from_chat_username": "testchannel",
            "forwarded_at": now,
        }
    ]
    reactions_data = [
        {
            "reaction_type": "emoji",
            "reaction_value": "👍",
            "user_tg_id": 11111,
            "is_big": False,
        }
    ]
    replies_data = [
        {
            "reply_to_post_id": None,
            "reply_message_id": 22222,
            "reply_chat_id": 33333,
            "reply_author_tg_id": 44444,
            "reply_content": "Test reply",
            "reply_posted_at": now,
        }
    ]

    await saver.save_forwards_reactions_replies(
        db_session=mock_db_session,
        post_id=post_id,
        forwards_data=forwards_data,
        reactions_data=reactions_data,
        replies_data=replies_data,
    )

    assert mock_execute.call_count == 3
    sql_texts = [str(call[0][0]) for call in mock_execute.call_args_list]
    assert any("post_forwards" in sql for sql in sql_texts)
    assert any("post_reactions" in sql for sql in sql_texts)
    assert any("post_replies" in sql for sql in sql_texts)


@pytest.mark.asyncio
async def test_save_forwards_reactions_replies_empty_data():
    """Проверка обработки пустых данных."""
    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock()

    saver = AtomicDBSaver()
    post_id = str(uuid.uuid4())

    await saver.save_forwards_reactions_replies(
        db_session=mock_db_session,
        post_id=post_id,
        forwards_data=None,
        reactions_data=None,
        replies_data=None,
    )

    assert mock_db_session.execute.call_count == 0


@pytest.mark.asyncio
async def test_save_forwards_reactions_replies_partial_data():
    """Проверка сохранения только части данных."""
    mock_db_session = AsyncMock()
    mock_execute = AsyncMock()
    mock_db_session.execute = mock_execute

    saver = AtomicDBSaver()
    post_id = str(uuid.uuid4())
    reactions_data = [
        {
            "reaction_type": "emoji",
            "reaction_value": "❤️",
            "user_tg_id": 99999,
            "is_big": True,
        }
    ]

    await saver.save_forwards_reactions_replies(
        db_session=mock_db_session,
        post_id=post_id,
        forwards_data=None,
        reactions_data=reactions_data,
        replies_data=None,
    )

    assert mock_execute.call_count == 1
    assert "post_reactions" in str(mock_execute.call_args[0][0])


@pytest.mark.asyncio
async def test_save_forwards_reactions_replies_error_handling():
    """Проверка обработки ошибок при сохранении."""
    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock(side_effect=Exception("Database error"))

    saver = AtomicDBSaver()
    post_id = str(uuid.uuid4())

    try:
        await saver.save_forwards_reactions_replies(
            db_session=mock_db_session,
            post_id=post_id,
            forwards_data=[{"from_chat_id": 12345}],
        )
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"Method should handle errors gracefully, but raised: {exc}")


def test_message_enricher_integration():
    """Интеграционный тест: извлечение + подготовка данных для сохранения."""
    message = MagicMock()

    message.fwd_from = MagicMock()
    message.fwd_from.date = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    message.fwd_from.channel_post = 12345
    message.fwd_from.from_id = MagicMock()
    message.fwd_from.from_id.channel_id = 67890

    message.reactions = MagicMock()
    reaction_result = MagicMock()
    reaction_result.reaction = MagicMock()
    reaction_result.reaction.emoticon = "👍"
    message.reactions.results = [reaction_result]

    message.reply_to = MagicMock()
    message.reply_to.reply_to_msg_id = 11111
    message.reply_to.reply_to_peer_id = MagicMock()
    message.reply_to.reply_to_peer_id.channel_id = 22222

    post_id = "test-post-id"

    forwards = extract_forwards_details(message)
    reactions = extract_reactions_details(message)
    replies = extract_replies_details(message, post_id)

    assert forwards and reactions and replies
    assert forwards[0]["from_chat_id"] == 67890
    assert reactions[0]["reaction_type"] == "emoji"
    assert replies[0]["reply_chat_id"] == 22222


if __name__ == '__main__':
    print("Running integration tests for forwards/reactions/replies...")
    print("Note: Full tests require pytest and async test framework")
    print("Basic integration test:")
    test_message_enricher_integration()
    print("✓ Integration test passed!")

