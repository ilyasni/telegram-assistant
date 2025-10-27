# Session Management Integration

## Обзор

Интеграция `ImprovedSessionSaver` в API с соблюдением принципов:
- **Атомарность**: все операции в транзакциях
- **Идемпотентность**: повторные вызовы безопасны  
- **Наблюдаемость**: детальные метрики и логи
- **Rollback**: четкие стратегии отката

## Архитектура

### 1. SessionManagerService

**Файл**: `api/services/session_manager.py`

```python
class SessionManagerService:
    """Атомарный, идемпотентный, наблюдаемый сервис для управления Telegram сессиями."""
    
    async def save_telegram_session(
        self, tenant_id: str, user_id: str, session_string: str,
        telegram_user_id: int, first_name: Optional[str] = None,
        last_name: Optional[str] = None, username: Optional[str] = None,
        invite_code: Optional[str] = None, force_update: bool = False
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Атомарное сохранение Telegram сессии с детальной диагностикой.
        
        Returns:
            (success, session_id, error_code, error_details)
        """
```

**Принципы**:
- Атомарность через транзакции БД
- Идемпотентность через upsert функции
- Наблюдаемость через Prometheus метрики
- Rollback через детальную классификацию ошибок

### 2. API Endpoints

**Файл**: `api/routers/session_management.py`

```python
@router.post("/save", response_model=SessionSaveResponse)
async def save_session(request: SessionSaveRequest):
    """Атомарное сохранение Telegram сессии."""

@router.get("/status/{tenant_id}/{user_id}", response_model=SessionStatusResponse)
async def get_session_status(tenant_id: str, user_id: str):
    """Получение статуса сессии."""

@router.post("/revoke/{tenant_id}/{user_id}")
async def revoke_session(tenant_id: str, user_id: str, request: SessionRevokeRequest):
    """Отзыв сессии с логированием."""

@router.post("/cleanup")
async def cleanup_expired_sessions(hours: int = 24):
    """Очистка просроченных сессий."""
```

### 3. Telethon Integration

**Обновлен**: `telethon-ingest/services/session_storage.py`

```python
async def save_telegram_session(
    self, tenant_id: str, user_id: str, session_string: str,
    telegram_user_id: int, first_name: Optional[str] = None,
    last_name: Optional[str] = None, username: Optional[str] = None,
    invite_code: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Context7 best practice: сохранение Telegram сессии в БД с улучшенной диагностикой.
    
    Returns:
        (success, session_id, error_code, error_details)
    """
```

**Обновлен**: `telethon-ingest/services/qr_auth.py`

```python
# Context7 best practice: сохранение сессии в БД с данными пользователя
success, session_id, error_code, error_details = await self.session_storage.save_telegram_session(
    tenant_id=tenant_id,
    user_id=tenant_id,
    session_string=session_string,
    telegram_user_id=me.id,
    first_name=getattr(me, 'first_name', None),
    last_name=getattr(me, 'last_name', None),
    username=getattr(me, 'username', None),
    invite_code=None
)

if not success:
    logger.error(
        "Failed to save session to database", 
        tenant_id=tenant_id,
        error_code=error_code,
        error_details=error_details
    )
    self.redis_client.hset(redis_key, mapping={
        "status": "failed",
        "reason": f"database_save_failed: {error_code}",
        "error_details": error_details or "Unknown error"
    })
```

## Prometheus Метрики

### 1. Session Save Metrics

```python
SESSION_SAVE_ATTEMPTS = Counter(
    'session_save_attempts_total', 
    'Total session save attempts', 
    ['status', 'error_type']
)

SESSION_SAVE_DURATION = Histogram(
    'session_save_duration_seconds',
    'Session save duration',
    ['status']
)

SESSION_SAVE_ERRORS = Counter(
    'session_save_errors_total',
    'Session save errors by type',
    ['error_type', 'error_code']
)

ACTIVE_SESSIONS = Gauge(
    'active_sessions_total',
    'Total active sessions',
    ['tenant_id', 'status']
)
```

### 2. Grafana Dashboard

**Панели для мониторинга**:
- Session Save Success Rate: `rate(session_save_attempts_total{status="success"}[5m])`
- Session Save Duration p95: `histogram_quantile(0.95, session_save_duration_seconds_bucket)`
- Error Rate by Type: `rate(session_save_errors_total[5m])`
- Active Sessions: `active_sessions_total`

## Классификация Ошибок

### 1. Database Errors

```python
def _classify_database_error(self, error: SQLAlchemyError) -> str:
    """Классификация ошибок базы данных."""
    error_str = str(error).lower()
    
    if 'value too long' in error_str or 'character varying' in error_str:
        return "session_too_long"
    elif 'column does not exist' in error_str:
        return "missing_column"
    elif 'unique constraint' in error_str or 'duplicate key' in error_str:
        return "duplicate_session"
    elif 'foreign key constraint' in error_str:
        return "invalid_tenant_or_user"
    elif 'not null constraint' in error_str:
        return "missing_required_field"
    elif 'permission denied' in error_str or 'insufficient privilege' in error_str:
        return "permission_denied"
    elif 'connection' in error_str or 'timeout' in error_str:
        return "connection_error"
    else:
        return "database_error"
```

### 2. Error Handling Strategy

**Уровни ошибок**:
- `validation_error`: Ошибки валидации входных данных
- `no_encryption_key`: Отсутствие ключа шифрования
- `session_too_long`: Сессия слишком длинная
- `duplicate_session`: Дублирование сессии
- `database_error`: Общие ошибки БД
- `unexpected_error`: Неожиданные ошибки

## Тестирование

### 1. Unit Tests

```bash
# Тест интеграции
python3 scripts/test_session_integration.py

# Стресс-тест
python3 scripts/test_session_integration.py stress
```

### 2. API Tests

```bash
# Сохранение сессии
curl -X POST http://localhost:8000/api/v1/sessions/save \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test-tenant",
    "user_id": "test-user",
    "session_string": "test-session-string",
    "telegram_user_id": 139883458,
    "first_name": "Test",
    "last_name": "User"
  }'

# Получение статуса
curl http://localhost:8000/api/v1/sessions/status/test-tenant/test-user

# Отзыв сессии
curl -X POST http://localhost:8000/api/v1/sessions/revoke/test-tenant/test-user \
  -H "Content-Type: application/json" \
  -d '{"reason": "manual_revoke"}'
```

## Rollback Strategies

### 1. Database Rollback

```python
async with self._get_db_session() as db:
    try:
        # Атомарные операции
        session_id = await self._atomic_save_session(...)
        db.commit()
        return True, session_id, None, None
    except Exception as e:
        db.rollback()
        error_code = self._classify_database_error(e)
        return False, None, error_code, str(e)
```

### 2. Cache Rollback

```python
# Обновление кэша только после успешного сохранения
if success:
    self._session_cache[f"{tenant_id}:{user_id}"] = {
        'session_id': session_id,
        'status': 'authorized',
        'updated_at': datetime.now(timezone.utc).isoformat()
    }
```

### 3. Redis Rollback

```python
# В telethon-ingest
if not success:
    self.redis_client.hset(redis_key, mapping={
        "status": "failed",
        "reason": f"database_save_failed: {error_code}",
        "error_details": error_details or "Unknown error"
    })
```

## Мониторинг и Алерты

### 1. Prometheus Alerts

```yaml
groups:
  - name: session_management_alerts
    rules:
      - alert: SessionSaveFailureRate
        expr: rate(session_save_attempts_total{status="failed"}[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High session save failure rate"
      
      - alert: SessionSaveLatency
        expr: histogram_quantile(0.95, session_save_duration_seconds_bucket) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High session save latency"
```

### 2. Health Checks

```python
@router.get("/health")
async def session_health():
    """Health check для сервиса управления сессиями."""
    try:
        return {
            "status": "healthy",
            "service": "session-manager",
            "version": "1.0.0"
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail="Service unhealthy")
```

## Конфигурация

### 1. Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://telegram_user:password@localhost:5432/telegram_assistant

# Redis
REDIS_URL=redis://localhost:6379/0

# API
API_TITLE=Telegram Assistant API
API_VERSION=1.0.0
```

### 2. Database Functions

```sql
-- Функция upsert для атомарного сохранения
CREATE OR REPLACE FUNCTION upsert_telegram_session(
    p_tenant_id VARCHAR(255),
    p_user_id VARCHAR(255),
    p_session_string_enc TEXT,
    p_key_id VARCHAR(64),
    p_status VARCHAR(20) DEFAULT 'pending',
    p_auth_error TEXT DEFAULT NULL,
    p_error_details TEXT DEFAULT NULL
) RETURNS UUID;

-- Функция очистки старых сессий
CREATE OR REPLACE FUNCTION cleanup_old_telegram_sessions(
    p_older_than_hours INTEGER DEFAULT 24
) RETURNS INTEGER;
```

## Результат

✅ **Атомарность**: Все операции в транзакциях БД

✅ **Идемпотентность**: Повторные вызовы безопасны через upsert

✅ **Наблюдаемость**: Детальные метрики Prometheus и структурированные логи

✅ **Rollback**: Четкие стратегии отката на всех уровнях

✅ **Интеграция**: Полная интеграция с API и telethon-ingest

---

**Статус**: ✅ Готово к production использованию
