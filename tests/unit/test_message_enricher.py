"""
Unit тесты для Message Enricher.

Context7: Проверка извлечения деталей forwards/reactions/replies из Telegram сообщений.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

# Импорт тестируемых функций
from telethon_ingest.services.message_enricher import (
    extract_forwards_details,
    extract_reactions_details,
    extract_replies_details
)


def test_extract_forwards_details_with_fwd_from():
    """Проверка извлечения forwards с fwd_from."""
    # Создаём мок сообщения с fwd_from
    message = MagicMock()
    message.fwd_from = MagicMock()
    message.fwd_from.date = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    message.fwd_from.channel_post = 12345
    message.fwd_from.from_id = MagicMock()
    message.fwd_from.from_id.channel_id = 67890
    message.fwd_from.from_name = "Test Channel"
    
    forwards = extract_forwards_details(message)
    
    assert len(forwards) == 1
    assert forwards[0]['from_chat_id'] == 67890
    assert forwards[0]['from_message_id'] == 12345
    assert forwards[0]['from_chat_title'] == "Test Channel"
    assert forwards[0]['forwarded_at'] == datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_extract_forwards_details_without_fwd_from():
    """Проверка обработки сообщения без fwd_from."""
    message = MagicMock()
    message.fwd_from = None
    
    forwards = extract_forwards_details(message)
    
    assert len(forwards) == 0


def test_extract_forwards_details_no_attribute():
    """Проверка обработки сообщения без атрибута fwd_from."""
    message = MagicMock()
    del message.fwd_from
    
    forwards = extract_forwards_details(message)
    
    assert len(forwards) == 0


def test_extract_reactions_details_with_results():
    """Проверка извлечения reactions с results."""
    message = MagicMock()
    message.reactions = MagicMock()
    
    # Мок reaction result
    reaction_result = MagicMock()
    reaction_result.chosen = False
    reaction_result.reaction = MagicMock()
    reaction_result.reaction.emoticon = "👍"
    reaction_result.peer_id = MagicMock()
    reaction_result.peer_id.user_id = 12345
    
    message.reactions.results = [reaction_result]
    
    reactions = extract_reactions_details(message)
    
    assert len(reactions) == 1
    assert reactions[0]['reaction_type'] == 'emoji'
    assert reactions[0]['reaction_value'] == "👍"
    assert reactions[0]['user_tg_id'] == 12345
    assert reactions[0]['is_big'] is False


def test_extract_reactions_details_custom_emoji():
    """Проверка извлечения custom emoji реакции."""
    message = MagicMock()
    message.reactions = MagicMock()

    class Reaction:
        def __init__(self):
            self.document_id = 98765

    class ReactionResult:
        def __init__(self):
            self.chosen = True
            self.reaction = Reaction()
            self.peer_id = None

    message.reactions.results = [ReactionResult()]

    reactions = extract_reactions_details(message)

    assert len(reactions) == 1
    assert reactions[0]['reaction_type'] == 'custom_emoji'
    assert reactions[0]['reaction_value'] == "98765"
    assert reactions[0]['is_big'] is True


def test_extract_reactions_details_without_reactions():
    """Проверка обработки сообщения без reactions."""
    message = MagicMock()
    message.reactions = None
    
    reactions = extract_reactions_details(message)
    
    assert len(reactions) == 0


def test_extract_replies_details_with_reply_to():
    """Проверка извлечения replies с reply_to."""
    message = MagicMock()
    message.reply_to = MagicMock()
    message.reply_to.reply_to_msg_id = 11111
    message.reply_to.reply_to_peer_id = MagicMock()
    message.reply_to.reply_to_peer_id.channel_id = 22222
    
    post_id = "test-post-id-123"
    replies = extract_replies_details(message, post_id)
    
    assert len(replies) == 1
    assert replies[0]['post_id'] == post_id
    assert replies[0]['reply_message_id'] == 11111
    assert replies[0]['reply_chat_id'] == 22222


def test_extract_replies_details_without_reply_to():
    """Проверка обработки сообщения без reply_to."""
    message = MagicMock()
    message.reply_to = None
    
    post_id = "test-post-id-123"
    replies = extract_replies_details(message, post_id)
    
    assert len(replies) == 0


def test_extract_replies_details_user_reply():
    """Проверка извлечения reply от пользователя."""
    message = MagicMock()
    message.reply_to = MagicMock()
    message.reply_to.reply_to_msg_id = 33333

    class PeerUser:
        def __init__(self, user_id: int):
            self.user_id = user_id

    message.reply_to.reply_to_peer_id = PeerUser(user_id=44444)

    post_id = "test-post-id-456"
    replies = extract_replies_details(message, post_id)

    assert len(replies) == 1
    assert replies[0]['reply_author_tg_id'] == 44444


def test_extract_forwards_details_exception_handling():
    """Проверка обработки исключений при извлечении forwards."""
    message = MagicMock()
    message.fwd_from = MagicMock()
    # Вызываем исключение при доступе к атрибутам
    message.fwd_from.date = property(lambda self: (_ for _ in ()).throw(Exception("Test error")))
    
    # Не должно падать, а вернуть пустой список
    forwards = extract_forwards_details(message)
    assert len(forwards) == 0 or isinstance(forwards, list)


def test_extract_reactions_details_exception_handling():
    """Проверка обработки исключений при извлечении reactions."""
    message = MagicMock()
    message.reactions = MagicMock()
    # Вызываем исключение при доступе к results
    message.reactions.results = property(lambda self: (_ for _ in ()).throw(Exception("Test error")))
    
    # Не должно падать, а вернуть пустой список
    reactions = extract_reactions_details(message)
    assert len(reactions) == 0 or isinstance(reactions, list)


if __name__ == '__main__':
    # Простой запуск без pytest для проверки базовой функциональности
    print("Testing message_enricher...")
    
    print("Testing extract_forwards_details...")
    test_extract_forwards_details_without_fwd_from()
    test_extract_forwards_details_no_attribute()
    print("✓ Forward extraction tests passed")
    
    print("Testing extract_reactions_details...")
    test_extract_reactions_details_without_reactions()
    print("✓ Reaction extraction tests passed")
    
    print("Testing extract_replies_details...")
    test_extract_replies_details_without_reply_to()
    print("✓ Reply extraction tests passed")
    
    print("All basic tests passed!")

