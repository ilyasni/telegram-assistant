# Альтернативы хранения Telegram сессий

## Вариант 1: Поля в таблице users

```sql
ALTER TABLE users ADD COLUMN telegram_session_enc TEXT;
ALTER TABLE users ADD COLUMN telegram_session_key_id VARCHAR(64);
ALTER TABLE users ADD COLUMN telegram_auth_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE users ADD COLUMN telegram_auth_created_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE users ADD COLUMN telegram_auth_updated_at TIMESTAMP WITH TIME ZONE;
```

**Плюсы:**
- ✅ Простота - всё в одной таблице
- ✅ Быстрые запросы - нет JOIN'ов
- ✅ Меньше таблиц

**Минусы:**
- ❌ Нет истории авторизаций
- ❌ Нет аудита событий
- ❌ Сложно управлять несколькими сессиями
- ❌ Нарушение нормализации БД

## Вариант 2: JSON поле в users

```sql
ALTER TABLE users ADD COLUMN telegram_auth JSONB DEFAULT '{}'::jsonb;
```

**Пример данных:**
```json
{
  "sessions": [
    {
      "id": "uuid",
      "session_enc": "encrypted_string",
      "key_id": "key_123",
      "status": "authorized",
      "created_at": "2025-10-22T15:00:00Z",
      "device_info": "iPhone 15 Pro"
    }
  ],
  "last_auth": "2025-10-22T15:00:00Z",
  "auth_count": 1
}
```

**Плюсы:**
- ✅ Гибкость - можно хранить любые данные
- ✅ История в одном поле
- ✅ Простые запросы

**Минусы:**
- ❌ Сложные запросы по JSON
- ❌ Нет внешних ключей
- ❌ Сложно делать аналитику
- ❌ Проблемы с индексацией

## Вариант 3: Отдельная таблица (текущий)

```sql
CREATE TABLE telegram_sessions (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    user_id UUID REFERENCES users(id),
    session_string_enc TEXT NOT NULL,
    key_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE
);
```

**Плюсы:**
- ✅ Полный аудит и история
- ✅ Нормализованная структура
- ✅ Легко делать аналитику
- ✅ Внешние ключи и ограничения
- ✅ Индексы для производительности
- ✅ Возможность отзыва сессий

**Минусы:**
- ❌ Больше таблиц
- ❌ JOIN'ы в запросах
- ❌ Сложнее структура

## Рекомендация: Context7 Best Practice

**Для production системы рекомендуется Вариант 3 (отдельная таблица)** по следующим причинам:

### 1. Безопасность
- Отдельное хранение чувствительных данных
- Возможность ротации ключей шифрования
- Аудит доступа к сессиям

### 2. Управление
- Отзыв конкретных сессий
- Статусы сессий (pending, authorized, revoked, expired)
- История авторизаций

### 3. Масштабирование
- Один пользователь = несколько сессий
- Разные устройства и приложения
- Временные сессии

### 4. Аналитика
- Статистика авторизаций
- Отслеживание ошибок
- Мониторинг безопасности

## Упрощенная альтернатива

Если нужна простота, можно использовать **гибридный подход**:

```sql
-- Основные поля в users
ALTER TABLE users ADD COLUMN telegram_session_enc TEXT;
ALTER TABLE users ADD COLUMN telegram_auth_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE users ADD COLUMN telegram_auth_updated_at TIMESTAMP WITH TIME ZONE;

-- Аудит в отдельной таблице (опционально)
CREATE TABLE telegram_auth_logs (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    event VARCHAR(64) NOT NULL,
    at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

Это даст 80% функциональности при 50% сложности.
