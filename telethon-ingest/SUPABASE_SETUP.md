# Настройка Supabase для Telegram Assistant

## Созданный проект

- **Project ID**: `slcsgfwjtereplsaxyou`
- **URL**: `https://slcsgfwjtereplsaxyou.supabase.co`
- **Region**: `eu-central-1`
- **Status**: `ACTIVE_HEALTHY`

## Примененные миграции

✅ **Таблица `telegram_sessions` создана** с:
- Составным первичным ключом `(tenant_id, app_id)`
- RLS политиками для безопасности
- Индексами для производительности
- Триггерами для `updated_at`

## RLS Политики

1. **`anon_read_own_sessions`**: Анонимные пользователи могут читать только свои сессии
2. **`authenticated_full_access`**: Аутентифицированные пользователи имеют полный доступ к своим данным
3. **`service_role_full_access`**: Service role имеет полный доступ ко всем данным

## Переменные окружения

Создайте файл `.env` с следующими переменными:

```bash
# Supabase
SUPABASE_URL=https://slcsgfwjtereplsaxyou.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNsY3NnZndqdGVyZXBsc2F4eW91Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjE3MTY4OTMsImV4cCI6MjA3NzI5Mjg5M30.VKys_hsGZTMRp9xVVoytO7vjpN7ecfTrk2w5F2Aunts
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here

# Telegram API
MASTER_API_ID=your_api_id
MASTER_API_HASH=your_api_hash

# Redis
REDIS_URL=redis://localhost:6379/0

# Database (если используется дополнительно)
DATABASE_URL=postgresql://user:password@localhost:5432/telegram_assistant
```

## Получение Service Role Key

1. Перейдите в [Supabase Dashboard](https://supabase.com/dashboard)
2. Выберите проект `telegram-assistant`
3. Перейдите в Settings → API
4. Скопируйте `service_role` ключ
5. Добавьте его в `.env` файл

## Проверка подключения

```python
from services.supabase_client import SupabaseManager

# Инициализация
manager = SupabaseManager()

# Тест подключения
result = manager.test_connection()
print("Connection test:", result)

# Тест RLS
test_data = {
    "tenant_id": "123e4567-e89b-12d3-a456-426614174000",
    "app_id": "test_app",
    "session_path": "/app/sessions/test/test.session",
    "api_key_alias": "default"
}

# Создание тестовой записи
result = manager.create_session(**test_data)
print("Create session:", result)
```

## Следующие шаги

1. Настройте переменные окружения
2. Получите Service Role Key
3. Запустите тесты для проверки функциональности
4. Настройте мониторинг и алерты

## Безопасность

- ✅ RLS включен для всех таблиц
- ✅ Политики настроены для изоляции данных по `tenant_id`
- ✅ Service role имеет полный доступ для внутренних операций
- ✅ API ключи не хранятся в базе данных
