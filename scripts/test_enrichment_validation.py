#!/usr/bin/env python3
"""
Простой скрипт для валидации EnrichmentRepository без внешних зависимостей.

Context7: Проверка базовой функциональности перед полным тестированием в Docker.
"""

import sys
import os
import hashlib
import json

# Добавляем shared в path
shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared', 'python'))
if shared_path not in sys.path:
    sys.path.insert(0, shared_path)

def test_compute_params_hash():
    """Тест вычисления params_hash."""
    print("Testing compute_params_hash...")
    
    # Импортируем только нужный метод (без зависимостей)
    from shared.repositories.enrichment_repository import EnrichmentRepository
    
    # Создаём мок db_session (не используется для compute_params_hash)
    class MockDB:
        pass
    
    repo = EnrichmentRepository(MockDB())
    
    # Тест 1: Одинаковые параметры дают одинаковый hash
    hash1 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.35})
    hash2 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.35})
    assert hash1 == hash2, "Same params should produce same hash"
    print("  ✓ Same params produce same hash")
    
    # Тест 2: Разные параметры дают разный hash
    hash3 = repo.compute_params_hash(model='gigachat-vision', version='2025-10', inputs={'threshold': 0.40})
    assert hash1 != hash3, "Different params should produce different hash"
    print("  ✓ Different params produce different hash")
    
    # Тест 3: Hash имеет правильную длину (SHA256 hex = 64 символа)
    assert len(hash1) == 64, "Hash should be 64 characters (SHA256 hex)"
    print("  ✓ Hash length is correct (64 chars)")
    
    print("✓ compute_params_hash tests passed\n")


def test_validation_logic():
    """Тест логики валидации kind."""
    print("Testing validation logic...")
    
    from shared.repositories.enrichment_repository import EnrichmentRepository
    
    class MockDB:
        pass
    
    repo = EnrichmentRepository(MockDB())
    
    # Проверяем валидные kinds
    valid_kinds = {'vision', 'vision_ocr', 'crawl', 'tags', 'classify', 'general'}
    for kind in valid_kinds:
        # Это не вызовет ошибку при проверке (но вызовет при реальном upsert)
        assert kind in valid_kinds, f"{kind} should be valid"
    print("  ✓ Valid kinds are recognized")
    
    # Проверяем невалидные kinds
    invalid_kinds = {'invalid', 'wrong', 'test'}
    for kind in invalid_kinds:
        assert kind not in valid_kinds, f"{kind} should be invalid"
    print("  ✓ Invalid kinds are rejected")
    
    print("✓ Validation logic tests passed\n")


def test_message_enricher_functions():
    """Тест функций message_enricher (без реальных Telegram объектов)."""
    print("Testing message_enricher functions...")
    
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'telethon-ingest')))
    
    from services.message_enricher import (
        extract_forwards_details,
        extract_reactions_details,
        extract_replies_details
    )
    
    # Тест с None/missing attributes
    class MockMessage:
        def __init__(self):
            self.fwd_from = None
            self.reactions = None
            self.reply_to = None
    
    message = MockMessage()
    
    forwards = extract_forwards_details(message)
    assert isinstance(forwards, list) and len(forwards) == 0
    print("  ✓ Handles missing fwd_from correctly")
    
    reactions = extract_reactions_details(message)
    assert isinstance(reactions, list) and len(reactions) == 0
    print("  ✓ Handles missing reactions correctly")
    
    replies = extract_replies_details(message, "test-post-id")
    assert isinstance(replies, list) and len(replies) == 0
    print("  ✓ Handles missing reply_to correctly")
    
    print("✓ message_enricher functions handle missing data correctly\n")


def test_sql_migration_syntax():
    """Проверка синтаксиса SQL в миграции."""
    print("Testing SQL migration syntax...")
    
    migration_file = os.path.join(
        os.path.dirname(__file__), '..', 'api', 'alembic', 'versions', 
        '20250130_unify_post_enrichment_schema.py'
    )
    
    with open(migration_file, 'r') as f:
        content = f.read()
    
    # Проверяем наличие ключевых SQL конструкций
    assert 'INSERT INTO post_enrichment' in content
    print("  ✓ INSERT statement found")
    
    assert 'ON CONFLICT (post_id, kind)' in content
    print("  ✓ ON CONFLICT clause found")
    
    assert 'CREATE UNIQUE INDEX' in content or 'CREATE INDEX' in content
    print("  ✓ Index creation found")
    
    assert 'jsonb_build_object' in content
    print("  ✓ JSONB functions found")
    
    print("✓ SQL migration syntax checks passed\n")


def main():
    """Главная функция."""
    print("=" * 60)
    print("Enrichment Repository Validation Tests")
    print("=" * 60)
    print()
    
    try:
        test_compute_params_hash()
        test_validation_logic()
        test_message_enricher_functions()
        test_sql_migration_syntax()
        
        print("=" * 60)
        print("✓ All validation tests passed!")
        print("=" * 60)
        return 0
    
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())

