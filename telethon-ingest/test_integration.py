#!/usr/bin/env python3
"""
Тест интеграции UnifiedSessionManager с локальной базой данных.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

# Добавляем путь к модулям
sys.path.insert(0, str(Path(__file__).parent))

from services.session.local_db_client import LocalDBManager
from services.session.unified_session_manager_v2 import UnifiedSessionManager
import redis.asyncio as redis
from config import settings

async def test_integration():
    """Тест интеграции компонентов."""
    print("🧪 Тестирование интеграции UnifiedSessionManager...")
    
    # Инициализация Redis
    print("📡 Подключение к Redis...")
    redis_client = redis.from_url(settings.redis_url)
    try:
        await redis_client.ping()
        print("✅ Redis подключен")
    except Exception as e:
        print(f"❌ Ошибка подключения к Redis: {e}")
        return False
    
    # Инициализация LocalDBManager
    print("🗄️ Инициализация LocalDBManager...")
    db_manager = LocalDBManager()
    if not await db_manager.initialize():
        print("❌ Ошибка инициализации LocalDBManager")
        return False
    print("✅ LocalDBManager инициализирован")
    
    # Инициализация UnifiedSessionManager
    print("🔧 Инициализация UnifiedSessionManager...")
    session_manager = UnifiedSessionManager(redis_client, db_manager)
    print("✅ UnifiedSessionManager инициализирован")
    
    # Тест создания сессии
    print("📝 Тестирование создания сессии...")
    tenant_id = str(uuid.uuid4())
    app_id = "test_app"
    
    session_data = {
        "tenant_id": tenant_id,
        "app_id": app_id,
        "session_path": f"/app/sessions/{tenant_id}/{app_id}.session",
        "state": "ABSENT",
        "api_key_alias": "default"
    }
    
    result = await db_manager.create_session(session_data)
    if result:
        print("✅ Сессия создана")
        print(f"   Tenant ID: {result['tenant_id']}")
        print(f"   App ID: {result['app_id']}")
        print(f"   State: {result['state']}")
    else:
        print("❌ Ошибка создания сессии")
        return False
    
    # Тест получения сессии
    print("🔍 Тестирование получения сессии...")
    session = await db_manager.get_session(tenant_id, app_id)
    if session:
        print("✅ Сессия получена")
        print(f"   State: {session['state']}")
    else:
        print("❌ Ошибка получения сессии")
        return False
    
    # Тест обновления сессии
    print("🔄 Тестирование обновления сессии...")
    updates = {
        "state": "AUTHORIZED",
        "telegram_user_id": 123456789
    }
    
    if await db_manager.update_session(tenant_id, app_id, updates):
        print("✅ Сессия обновлена")
    else:
        print("❌ Ошибка обновления сессии")
        return False
    
    # Тест health check
    print("🏥 Тестирование health check...")
    health = await session_manager.health_check()
    print(f"   Status: {health['status']}")
    print(f"   Redis: {health['redis']}")
    print(f"   Database: {health['database']['status']}")
    
    # Очистка
    print("🧹 Очистка тестовых данных...")
    await db_manager.delete_session(tenant_id, app_id)
    print("✅ Тестовые данные удалены")
    
    # Закрытие соединений
    await db_manager.close()
    await redis_client.close()
    
    print("🎉 Все тесты пройдены успешно!")
    return True

async def main():
    """Главная функция."""
    print("🚀 Запуск тестов интеграции...")
    
    # Проверяем переменные окружения
    required_vars = ["DATABASE_URL", "REDIS_URL"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Отсутствуют переменные окружения: {', '.join(missing_vars)}")
        print("Установите переменные окружения и попробуйте снова.")
        return False
    
    try:
        success = await test_integration()
        return success
    except Exception as e:
        print(f"❌ Ошибка во время тестирования: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
