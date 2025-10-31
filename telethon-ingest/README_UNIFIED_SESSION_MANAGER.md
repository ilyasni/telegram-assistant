# UnifiedSessionManager - Context7 Best Practices

## Обзор

`UnifiedSessionManager` - это единый менеджер для управления Telegram сессиями с Context7 best practices. Реализует Singleton pattern для централизованного управления сессиями с поддержкой multi-tenancy, безопасности и observability.

## Архитектура

### Single Source of Truth
- **SQLiteSession файлы** в `/app/sessions/<tenant>/<app>.session`
- **Supabase метаданные** с RLS политиками
- **Redis координация** для locks и QR flow

### State Machine
- `ABSENT` → `PENDING_QR` | `PENDING_CODE`
- `PENDING_QR` → `PENDING_PASSWORD` (2FA) → `AUTHORIZED`
- `AUTHORIZED` → `STALE` (validation fail) → `AUTHORIZED`
- `AUTHORIZED` → `REVOKED` (explicit)

### Security
- **SecretsManager** с fail-fast валидацией
- **HMAC подпись** для MiniApp callbacks
- **Replay защита** с timestamp tolerance
- **Fingerprint валидация** (SHA256:size:mtime)

## Использование

### 1. Инициализация

```python
from services.session.unified_session_manager import UnifiedSessionManager

# Создание менеджера
manager = UnifiedSessionManager(redis_client, db_connection)

# Инициализация
if await manager.initialize():
    print("UnifiedSessionManager initialized successfully")
else:
    print("Initialization failed")
```

### 2. Получение клиента

```python
# Простое получение клиента
client = await manager.get_client("tenant1", "app1")

# Через context manager (рекомендуется)
async with manager.client_context("tenant1", "app1") as client:
    if client:
        me = await client.get_me()
        print(f"User: {me.first_name}")
```

### 3. QR авторизация

```python
# Начало QR flow
qr_ticket = await manager.start_qr_flow("tenant1", "app1")
print(f"QR Code: {qr_ticket.qr_base64}")

# Финализация (после сканирования QR)
auth_state = await manager.finalize_qr(qr_ticket.ticket)

if auth_state.state == SessionState.AUTHORIZED:
    print("Authorization successful!")
elif auth_state.requires_password:
    # 2FA требуется
    auth_state = await manager.finalize_qr(qr_ticket.ticket, password="2fa_password")
```

### 4. Управление сессиями

```python
# Отзыв сессии
await manager.revoke_session("tenant1", "app1")

# Обнаружение существующих сессий
discovered_count = await manager.discover_existing_sessions()
print(f"Discovered {discovered_count} sessions")
```

## MiniApp API

### Endpoints

#### POST /api/qr/start
Начало QR авторизации.

**Request:**
```json
{
    "tenant_id": "tenant1",
    "app_id": "app1"
}
```

**Response:**
```json
{
    "ticket": "uuid-ticket",
    "qr_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
    "ttl": 600,
    "tenant_id": "tenant1",
    "app_id": "app1"
}
```

#### POST /api/qr/callback
Callback от Telegram после сканирования QR.

**Headers:**
- `X-Miniapp-Signature`: HMAC подпись
- `X-Timestamp`: Unix timestamp

**Request:**
```json
{
    "ticket": "uuid-ticket",
    "tg_user_id": 12345,
    "two_fa_required": false
}
```

**Response:**
```json
{
    "status": "success",
    "message": "Authorization completed successfully"
}
```

#### GET /api/qr/status
Получение статуса QR авторизации.

**Response:**
```json
{
    "state": "AUTHORIZED",
    "telegram_user_id": 12345,
    "requires_password": false
}
```

#### POST /api/qr/password
Отправка 2FA пароля.

**Request:**
```json
{
    "ticket": "uuid-ticket",
    "password": "2fa_password"
}
```

**Response:**
```json
{
    "status": "success",
    "message": "Password verified successfully"
}
```

## Конфигурация

### Environment Variables

```bash
# Supabase
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_ANON_KEY="your-anon-key"
export SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"

# Telegram API
export TELEGRAM_API_ID_MASTER="your-api-id"
export TELEGRAM_API_HASH_MASTER="your-api-hash"

# Redis
export REDIS_URL="redis://localhost:6379"

# Feature flags
export FEATURE_UNIFIED_SESSION_MANAGER="true"

# Session storage
export SESSIONS_BASE_PATH="/app/sessions"

# MiniApp security
export MINIAPP_HMAC_SECRET="your-hmac-secret"
export MINIAPP_TIMESTAMP_TOLERANCE="300"

# Backup (optional)
export ENABLE_SESSION_BACKUP="true"
export BACKUP_ENCRYPTION_KEY_PATH="/app/secrets/backup.key"
export BACKUP_RETENTION_DAYS="7"
```

### Settings

```python
from config import settings

# Проверка настроек
print(f"Feature enabled: {settings.feature_unified_session_manager}")
print(f"Sessions path: {settings.sessions_base_path}")
print(f"QR TTL: {settings.qr_ticket_ttl}")
print(f"Lock TTL: {settings.session_lock_ttl}")
```

## Мониторинг

### Prometheus метрики

```bash
# Проверка метрик
curl http://localhost:9090/metrics | grep session_

# Основные метрики
session_state_transitions_total
session_states_gauge
qr_auth_start_total
qr_auth_callback_total
miniapp_webhook_total
rls_denied_total
session_lock_acquired_total
session_lock_failed_total
fingerprint_computation_duration_seconds
session_conversion_total
session_backup_total
```

### Grafana Dashboard

```bash
# Импорт dashboard
curl -X POST http://grafana:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @grafana/dashboards/session_manager.json
```

### Алерты

```bash
# Применение алертов
kubectl apply -f grafana/alerts/session_manager.yml
```

## Бэкапы

### Автоматические бэкапы

```bash
# Запуск планировщика
python scripts/backup_scheduler.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  --supabase-url "https://your-project.supabase.co" \
  --supabase-key "your-service-key"
```

### Ручные бэкапы

```bash
# Бэкап конкретной сессии
python scripts/backup_scheduler.py \
  --manual-backup "tenant1:app1" \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379"

# Список бэкапов
python scripts/backup_scheduler.py \
  --list-backups "tenant1:app1" \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379"
```

## CLI

### Управление сессиями

```bash
# Список сессий
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  list

# Статус сессии
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  status --tenant-id tenant1 --app-id app1

# QR авторизация
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  qr-login --tenant-id tenant1 --app-id app1

# Финализация QR
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  finalize --ticket your-ticket --password 2fa_password

# Health check
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  health
```

### Миграция сессий

```bash
# Миграция существующих сессий
python scripts/migrate_sessions.py \
  --sessions-dir /app/sessions \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379"

# Dry run
python scripts/migrate_sessions.py \
  --sessions-dir /app/sessions \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  --dry-run
```

## Тестирование

### Unit тесты

```bash
# Запуск unit тестов
pytest tests/test_unified_session_manager.py -v

# С покрытием
pytest tests/test_unified_session_manager.py --cov=services.session --cov-report=html
```

### Integration тесты

```bash
# Запуск integration тестов
pytest tests/integration/test_miniapp_api.py -v
```

### E2E тестирование

```bash
# Тест QR авторизации
python scripts/session_cli.py qr-login --tenant-id tenant1 --app-id app1
# Сканируйте QR код
python scripts/session_cli.py finalize --ticket your-ticket
```

## Troubleshooting

### Проблема: Сессии не мигрируются

**Решение:**
1. Проверьте права доступа к каталогу `/app/sessions`
2. Убедитесь, что Redis и БД доступны
3. Проверьте логи миграции

```bash
# Проверка прав
ls -la /app/sessions

# Проверка подключений
python -c "import redis; r=redis.Redis.from_url('redis://localhost:6379'); print(r.ping())"
python -c "import psycopg2; conn=psycopg2.connect('postgresql://user:pass@localhost/db'); print('OK')"
```

### Проблема: RLS политики блокируют доступ

**Решение:**
1. Убедитесь, что используется service role для внутренних операций
2. Проверьте JWT claims в RLS политиках

```sql
-- Проверка RLS
SELECT * FROM pg_policies WHERE tablename = 'telegram_sessions';

-- Тест с service role
SET ROLE service_role;
SELECT * FROM telegram_sessions LIMIT 1;
```

### Проблема: QR авторизация не работает

**Решение:**
1. Проверьте HMAC secret в конфигурации
2. Убедитесь, что timestamp в пределах tolerance
3. Проверьте Redis для QR tickets

```bash
# Проверка QR tickets в Redis
redis-cli KEYS "qr:*"

# Проверка конфигурации
python -c "from config import settings; print(settings.miniapp_hmac_secret)"
```

## Context7 Best Practices

1. **Observability**: Comprehensive metrics и logging
2. **Idempotency**: Safe retry mechanisms
3. **Circuit Breaker**: Protection against API failures
4. **Distributed Locks**: Coordinated access control
5. **Security**: HMAC signatures и replay protection
6. **Fingerprint Validation**: Tamper detection
7. **State Machine**: Explicit state transitions
8. **Error Handling**: Graceful degradation
9. **Monitoring**: Proactive alerting
10. **Documentation**: Clear state descriptions

## Лицензия

MIT License - см. LICENSE файл для деталей.

## Поддержка

При возникновении проблем:

1. Проверьте логи: `docker logs telethon-ingest`
2. Проверьте метрики: `curl http://localhost:9090/metrics`
3. Проверьте статус: `python scripts/session_cli.py health`
4. Создайте issue с логами и метриками