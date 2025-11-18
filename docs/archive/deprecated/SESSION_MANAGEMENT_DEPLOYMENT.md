# Развертывание Session Management (Context7 P0.1)

**Дата**: 2025-01-20  
**Context7**: Инструкция по применению миграции и проверке функционала

## 1. Применение миграции БД

### Автоматический способ (рекомендуется)

```bash
# Применить миграцию через docker-compose
docker-compose exec api alembic upgrade head

# Или через docker compose (новый синтаксис)
docker compose exec api alembic upgrade head
```

### Ручной способ (если контейнер не запущен)

```bash
# Запустить контейнер API
docker-compose up -d api

# Подождать готовности
sleep 5

# Применить миграцию
docker-compose exec api alembic upgrade head
```

### Проверка миграции

```bash
# Проверить текущую версию миграции
docker-compose exec api alembic current

# Проверить созданные таблицы через SQL
docker-compose exec supabase-db psql -U postgres -d postgres -f /path/to/scripts/check_telegram_sessions.sql
```

## 2. Проверка функционала

### 2.1. Проверка структуры таблицы

```bash
# Через SQL скрипт
docker-compose exec supabase-db psql -U postgres -d postgres -f scripts/check_telegram_sessions.sql

# Или напрямую через psql
docker-compose exec supabase-db psql -U postgres -d postgres -c "
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'telegram_sessions';
"
```

### 2.2. Проверка индексов

```bash
docker-compose exec supabase-db psql -U postgres -d postgres -c "
SELECT indexname 
FROM pg_indexes 
WHERE tablename = 'telegram_sessions';
"
```

### 2.3. Проверка UNIQUE constraint

```bash
docker-compose exec supabase-db psql -U postgres -d postgres -c "
SELECT conname, pg_get_constraintdef(oid) 
FROM pg_constraint 
WHERE conrelid = 'telegram_sessions'::regclass 
AND contype = 'u';
"
```

### 2.4. Тест SessionManager

```bash
# Запустить тестовый скрипт (если зависимости установлены)
cd /opt/telegram-assistant
python3 scripts/test_session_management.py
```

## 3. Интеграция с существующим кодом

### 3.1. Использование SessionManager в telegram_client_manager

SessionManager уже интегрирован в `telegram_client_manager.py`. Для использования:

```python
from services.session_manager import SessionManager
from database import SyncDBManager

# Создание SessionManager
db_manager = SyncDBManager()
session_manager = SessionManager(
    db_connection_factory=lambda: db_manager.get_connection(),
    s3_client=None,  # Опционально
    s3_bucket=None
)

# Использование в TelegramClientManager
client_manager = TelegramClientManager(
    redis_client=redis_client,
    db_connection=db_connection,
    session_manager=session_manager  # Передаём SessionManager
)
```

### 3.2. Миграция существующих .session файлов

```bash
# Запустить скрипт миграции
cd /opt/telegram-assistant/telethon-ingest
python3 -m services.session_migration --dry-run  # Сначала dry-run
python3 -m services.session_migration  # Реальная миграция
```

## 4. Проверка работы

### 4.1. Проверка загрузки сессии

```python
# Пример использования SessionManager
from services.session_manager import SessionManager
from uuid import UUID

session_manager = SessionManager(...)

# Загрузка сессии
session = await session_manager.load_session(
    identity_id=UUID("..."),
    telegram_id=123456789,
    dc_id=2
)

if session:
    print("✅ Сессия загружена")
else:
    print("❌ Сессия не найдена")
```

### 4.2. Проверка сохранения сессии

```python
from telethon.sessions import StringSession

# Создание новой сессии
session = StringSession()

# Сохранение через SessionManager
await session_manager.save_session(
    identity_id=UUID("..."),
    telegram_id=123456789,
    session=session,
    dc_id=2
)
```

## 5. Troubleshooting

### Проблема: Таблица не создана

**Решение**: Примените миграцию:
```bash
docker-compose exec api alembic upgrade head
```

### Проблема: Ошибка "identity_id not found"

**Решение**: Убедитесь, что таблица `identities` существует и содержит записи:
```sql
SELECT COUNT(*) FROM identities;
```

### Проблема: Ошибка при загрузке сессии

**Решение**: Проверьте:
1. Существует ли identity для данного telegram_id
2. Правильно ли настроен ENCRYPTION_KEY
3. Есть ли активные сессии в таблице

## 6. Откат миграции (если нужно)

```bash
# Откатить миграцию
docker-compose exec api alembic downgrade -1

# Или откатить до конкретной версии
docker-compose exec api alembic downgrade 20251116_trend_agents
```

## 7. Мониторинг

### Метрики для отслеживания:

- Количество активных сессий: `SELECT COUNT(*) FROM telegram_sessions WHERE is_active = true;`
- Количество сессий по DC: `SELECT dc_id, COUNT(*) FROM telegram_sessions GROUP BY dc_id;`
- Последние использованные сессии: `SELECT * FROM telegram_sessions ORDER BY last_used_at DESC LIMIT 10;`

