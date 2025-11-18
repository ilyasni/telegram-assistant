#!/usr/bin/env python3
"""
Context7 P3: Тестовый скрипт для проверки функционала Sideloading.

Проверяет:
1. Поле source в таблицах posts и group_messages
2. Базовая структура SideloadService
3. Проверка импортов и зависимостей
"""
import sys
import asyncio
from pathlib import Path

# Добавляем пути для импорта
sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).parent.parent / "telethon-ingest"))

async def test_database_schema():
    """Проверка схемы БД: поле source."""
    print("🔍 Проверка схемы БД...")
    
    try:
        from sqlalchemy import create_engine, inspect, text
        import os
        
        # Получаем URL БД из переменных окружения или используем дефолт
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/postgres"
        )
        
        engine = create_engine(db_url)
        inspector = inspect(engine)
        
        # Проверка поля source в posts
        posts_columns = {col['name']: col for col in inspector.get_columns('posts')}
        if 'source' in posts_columns:
            source_col = posts_columns['source']
            print(f"✅ Поле source найдено в posts:")
            print(f"   Тип: {source_col['type']}")
            print(f"   Default: {source_col.get('default', 'None')}")
            print(f"   Nullable: {source_col.get('nullable', True)}")
        else:
            print("❌ Поле source НЕ найдено в posts")
            return False
        
        # Проверка поля source в group_messages
        try:
            group_messages_columns = {col['name']: col for col in inspector.get_columns('group_messages')}
            if 'source' in group_messages_columns:
                source_col = group_messages_columns['source']
                print(f"✅ Поле source найдено в group_messages:")
                print(f"   Тип: {source_col['type']}")
                print(f"   Default: {source_col.get('default', 'None')}")
                print(f"   Nullable: {source_col.get('nullable', True)}")
            else:
                print("❌ Поле source НЕ найдено в group_messages")
                return False
        except Exception as e:
            print(f"⚠️  Таблица group_messages не найдена или недоступна: {e}")
        
        # Проверка индексов
        indexes = inspector.get_indexes('posts')
        source_indexes = [idx for idx in indexes if 'source' in str(idx.get('column_names', []))]
        if source_indexes:
            print(f"✅ Найдены индексы для source: {[idx['name'] for idx in source_indexes]}")
        else:
            print("⚠️  Индексы для source не найдены (может быть нормально)")
        
        engine.dispose()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при проверке схемы БД: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_sideload_service_imports():
    """Проверка импортов SideloadService."""
    print("\n🔍 Проверка импортов SideloadService...")
    
    try:
        from services.sideload_service import SideloadService
        print("✅ SideloadService импортирован успешно")
        
        # Проверка наличия основных методов
        required_methods = [
            'import_user_dialogs',
            '_import_dialog_messages',
            '_classify_dialog',
            '_extract_message_data',
            '_save_messages_batch',
            '_publish_persona_events',
            '_get_or_create_dm_channel',
            '_get_or_create_group'
        ]
        
        missing_methods = []
        for method in required_methods:
            if not hasattr(SideloadService, method):
                missing_methods.append(method)
        
        if missing_methods:
            print(f"❌ Отсутствуют методы: {missing_methods}")
            return False
        else:
            print(f"✅ Все необходимые методы присутствуют: {required_methods}")
            return True
            
    except ImportError as e:
        print(f"❌ Ошибка импорта SideloadService: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ Ошибка при проверке SideloadService: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_persona_event_schemas():
    """Проверка схем событий Persona."""
    print("\n🔍 Проверка схем событий Persona...")
    
    try:
        from worker.events.schemas.persona_messages_v1 import (
            PersonaMessageIngestedEventV1,
            PersonaGraphUpdatedEventV1
        )
        print("✅ Схемы событий Persona импортированы успешно")
        
        # Проверка полей PersonaMessageIngestedEventV1
        from pydantic import ValidationError
        try:
            # Тестовый экземпляр
            test_event = PersonaMessageIngestedEventV1(
                idempotency_key="test-key",
                user_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440000",
                dialog_type="dm",
                message_id="550e8400-e29b-41d4-a716-446655440000",
                telegram_message_id=12345,
                dialog_entity_id="550e8400-e29b-41d4-a716-446655440000",
                telegram_dialog_id=67890,
                content_snippet="Test message",
                posted_at="2025-01-21T12:00:00Z",
                source="dm"
            )
            print("✅ PersonaMessageIngestedEventV1 создан успешно")
            print(f"   Поля: {list(test_event.model_dump().keys())}")
        except ValidationError as e:
            print(f"❌ Ошибка валидации PersonaMessageIngestedEventV1: {e}")
            return False
        
        return True
        
    except ImportError as e:
        print(f"❌ Ошибка импорта схем событий: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ Ошибка при проверке схем событий: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_neo4j_client_methods():
    """Проверка методов Neo4jClient для Persona."""
    print("\n🔍 Проверка методов Neo4jClient для Persona...")
    
    try:
        from worker.integrations.neo4j_client import Neo4jClient
        print("✅ Neo4jClient импортирован успешно")
        
        # Проверка наличия методов для Persona
        required_methods = [
            'create_persona_node',
            'create_dialogue_node',
            'create_persona_message_relationship'
        ]
        
        missing_methods = []
        for method in required_methods:
            if not hasattr(Neo4jClient, method):
                missing_methods.append(method)
        
        if missing_methods:
            print(f"❌ Отсутствуют методы: {missing_methods}")
            return False
        else:
            print(f"✅ Все необходимые методы присутствуют: {required_methods}")
            return True
            
    except ImportError as e:
        print(f"❌ Ошибка импорта Neo4jClient: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ Ошибка при проверке Neo4jClient: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_graph_writer_persona():
    """Проверка GraphWriter для Persona."""
    print("\n🔍 Проверка GraphWriter для Persona...")
    
    try:
        from worker.services.graph_writer import GraphWriter, STREAM_PERSONA_MESSAGES_INGESTED
        print("✅ GraphWriter импортирован успешно")
        print(f"✅ STREAM_PERSONA_MESSAGES_INGESTED = {STREAM_PERSONA_MESSAGES_INGESTED}")
        
        # Проверка наличия методов для Persona
        required_methods = [
            '_process_persona_batch',
            '_process_persona_message_event',
            'start_consuming_persona'
        ]
        
        missing_methods = []
        for method in required_methods:
            if not hasattr(GraphWriter, method):
                missing_methods.append(method)
        
        if missing_methods:
            print(f"❌ Отсутствуют методы: {missing_methods}")
            return False
        else:
            print(f"✅ Все необходимые методы присутствуют: {required_methods}")
            return True
            
    except ImportError as e:
        print(f"❌ Ошибка импорта GraphWriter: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ Ошибка при проверке GraphWriter: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Основная функция тестирования."""
    print("=" * 60)
    print("🧪 Тестирование P3 — Sideloading")
    print("=" * 60)
    
    results = []
    
    # Тест 1: Схема БД
    results.append(("Схема БД (поле source)", await test_database_schema()))
    
    # Тест 2: Импорты SideloadService
    results.append(("SideloadService импорты", test_sideload_service_imports()))
    
    # Тест 3: Схемы событий
    results.append(("Схемы событий Persona", test_persona_event_schemas()))
    
    # Тест 4: Neo4jClient методы
    results.append(("Neo4jClient методы", test_neo4j_client_methods()))
    
    # Тест 5: GraphWriter Persona
    results.append(("GraphWriter Persona", test_graph_writer_persona()))
    
    # Итоги
    print("\n" + "=" * 60)
    print("📊 Итоги тестирования:")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print("=" * 60)
    print(f"Всего тестов: {len(results)}")
    print(f"✅ Успешно: {passed}")
    print(f"❌ Провалено: {failed}")
    print("=" * 60)
    
    if failed == 0:
        print("🎉 Все тесты пройдены успешно!")
        return 0
    else:
        print("⚠️  Некоторые тесты провалились. Проверьте вывод выше.")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

