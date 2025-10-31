# Интеграция UnifiedSessionManager

## Обзор

`UnifiedSessionManager` интегрирован с локальной PostgreSQL базой данных через `LocalDBManager` для управления Telegram сессиями с Context7 best practices.

## Архитектура

```
UnifiedSessionManager
├── LocalDBManager (PostgreSQL)
├── Redis (координация, locks)
├── File System (SQLiteSession файлы)
└── SecretsManager (API ключи)
```

## Компоненты

### 1. LocalDBManager
- Прямые подключения к PostgreSQL через `asyncpg`
- Connection pooling для производительности
- CRUD операции с таблицей `telegram_sessions_v2`

### 2. UnifiedSessionManager
- Singleton pattern для управления сессиями
- State machine для переходов состояний
- Redis locks с heartbeat
- Интеграция с `LocalDBManager`

### 3. База данных
- Таблица: `telegram_sessions_v2`
- Составной PK: `(tenant_id, app_id)`
- RLS политики для безопасности
- Индексы для производительности

## Настройка

### 1. Переменные окружения

```bash
# База данных
DATABASE_URL=postgresql+asyncpg://postgres:password@supabase-db:5432/postgres

# Redis
REDIS_URL=redis://redis:6379

# Context7: UnifiedSessionManager
FEATURE_UNIFIED_SESSION_MANAGER=true
SESSIONS_BASE_PATH=/app/sessions
SESSION_FILE_PERMISSIONS=384
SESSION_DIR_PERMISSIONS=448
SESSION_LOCK_TTL=90
SESSION_LOCK_HEARTBEAT=30
QR_TICKET_TTL=600
MINIAPP_HMAC_SECRET=your_hmac_secret
MINIAPP_TIMESTAMP_TOLERANCE=300
SESSION_VALIDATION_INTERVAL=300
SESSION_STALE_THRESHOLD=3600
SUPABASE_URL=http://kong:8000
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_key
```

### 2. Docker Compose

Сервис `telethon-ingest` уже настроен в `docker-compose.yml`:

```yaml
telethon-ingest:
  build: ./telethon-ingest
  environment:
    # ... переменные окружения ...
  volumes:
    - telegram_sessions:/app/sessions
  depends_on:
    - supabase-db
    - redis
```

## Использование

### 1. Инициализация

```python
from services.local_db_client import LocalDBManager
from services.session.unified_session_manager_v2 import UnifiedSessionManager
import redis.asyncio as redis

# Инициализация
redis_client = redis.from_url("redis://localhost:6379")
db_manager = LocalDBManager()
await db_manager.initialize()

session_manager = UnifiedSessionManager(redis_client, db_manager)
```

### 2. Работа с сессиями

```python
# Получение клиента
async with session_manager.client_context(tenant_id, app_id) as client:
    # Работа с Telegram клиентом
    me = await client.get_me()
    print(f"Авторизован как: {me.username}")

# QR авторизация
qr_ticket = await session_manager.start_qr_auth(tenant_id, app_id)
if qr_ticket:
    print(f"QR код: {qr_ticket.qr_base64}")
    
    # После сканирования QR
    success = await session_manager.finalize_qr_auth(qr_ticket.ticket)
```

### 3. Health Check

```python
health = await session_manager.health_check()
print(f"Status: {health['status']}")
print(f"Redis: {health['redis']}")
print(f"Database: {health['database']['status']}")
```

## Тестирование

### 1. Запуск тестов

```bash
cd /opt/telegram-assistant/telethon-ingest
python test_integration.py
```

### 2. Проверка базы данных

```bash
# Подключение к PostgreSQL
docker compose exec supabase-db psql -U postgres -d postgres

# Проверка таблицы
\dt telegram_sessions_v2

# Проверка данных
SELECT * FROM telegram_sessions_v2 LIMIT 5;
```

### 3. Проверка Redis

```bash
# Подключение к Redis
docker compose exec redis redis-cli

# Проверка ключей
KEYS session_lock:*
KEYS qr_ticket:*
```

## Мониторинг

### 1. Метрики Prometheus

- `session_state_transitions_total`: Переходы состояний сессий
- `session_get_client_latency_seconds`: Время получения клиента
- `session_lock_acquired_total`: Полученные блокировки
- `qr_auth_start_total`: Запуски QR авторизации

### 2. Логи

```bash
# Просмотр логов
docker compose logs -f telethon-ingest

# Фильтрация по UnifiedSessionManager
docker compose logs telethon-ingest | grep "UnifiedSessionManager"
```

### 3. Health Check

```bash
# Проверка здоровья сервиса
curl http://localhost:8011/health
```

## Troubleshooting

### 1. Ошибки подключения к БД

```bash
# Проверка статуса БД
docker compose ps supabase-db

# Проверка логов БД
docker compose logs supabase-db
```

### 2. Ошибки Redis

```bash
# Проверка статуса Redis
docker compose ps redis

# Проверка подключения
docker compose exec redis redis-cli ping
```

### 3. Ошибки сессий

```bash
# Проверка файлов сессий
ls -la /app/sessions/

# Проверка прав доступа
ls -la /app/sessions/*/
```

## Миграция

### 1. Существующие сессии

Для миграции существующих сессий используйте:

```bash
python scripts/migrate_sessions.py
```

### 2. Очистка

Для очистки тестовых данных:

```sql
DELETE FROM telegram_sessions_v2 WHERE app_id = 'test_app';
```

## Безопасность

### 1. RLS политики

Таблица `telegram_sessions_v2` защищена RLS политиками:

- Анонимные пользователи: только чтение своих данных
- Аутентифицированные пользователи: полный доступ к своим данным
- Service role: полный доступ ко всем данным

### 2. API ключи

API ключи не хранятся в базе данных. Используется `api_key_alias` для ссылки на переменные окружения.

### 3. Файлы сессий

Файлы `.session` хранятся с правами `600` (только владелец).

## Производительность

### 1. Connection Pooling

`LocalDBManager` использует connection pool с настройками:
- Минимум: 1 соединение
- Максимум: 10 соединений
- Timeout: 30 секунд

### 2. Redis Locks

- TTL: 90 секунд
- Heartbeat: 30 секунд
- Автоматическое освобождение при сбоях

### 3. Индексы

Созданы индексы для:
- `tenant_id`
- `state`
- `telegram_user_id`
- `last_activity_at`
