# Truth Detector Guide

## Цель

"Сыворотка правды" для диагностики `database_save_failed` - быстро доказывает, ЧТО именно падает в момент ошибки.

## Проблема

После всех исправлений `database_save_failed` всё равно всплывает, что означает:

1. **Неверная классификация исключений** - любая ошибка мапится на один ярлык
2. **Скрытый edge-case** в новом upsert-пути
3. **Маскировка не-БД ошибок** под database_save_failed

## Решение: Детектор Правды

### 1. Точечная Телеметрия

**Файл**: `api/middleware/auth_diagnostics.py`

```python
class AuthDiagnostics:
    """Детектор правды для диагностики auth finalize ошибок."""
    
    def log_step_failure(
        self, 
        step: str, 
        error: Exception, 
        duration: float,
        error_type: str = "unknown"
    ) -> None:
        """Логирование ошибки шага с детальной диагностикой."""
        sqlstate, pgerror, statement, params = self._extract_db_cause(error)
        
        logger.exception(
            f"Auth finalize step failed: {step}",
            correlation_id=self.correlation_id,
            user_id=self.user_id,
            step=step,
            duration=duration,
            error_type=error_type,
            sqlstate=sqlstate,
            pgerror=pgerror,
            statement=statement[:200] if statement else None,
            params_length=len(params) if params else 0,
            error_class=type(error).__name__,
            error_message=str(error)
        )
```

### 2. Разделение Ошибок

**Вместо одного `database_save_failed`**:

```python
# Детектор правды: разделение ошибок по типам
if error_code == "db_integrity":
    failure_reason = "session_store_integrity_failed"
elif error_code == "db_operational":
    failure_reason = "session_store_operational_failed"
elif error_code == "db_generic":
    failure_reason = "session_store_database_failed"
elif error_code == "session_saver_failed":
    failure_reason = "session_saver_failed"
else:
    failure_reason = "session_store_unexpected_failed"
```

### 3. Feature Flags для Тестирования

**Файл**: `api/config/feature_flags.py`

```python
class FeatureFlags:
    def __init__(self):
        # Auth finalize bypass для диагностики
        self._flags['AUTH_FINALIZE_DB_BYPASS'] = os.getenv('AUTH_FINALIZE_DB_BYPASS', 'off').lower() == 'on'
        
        # Детальная диагностика
        self._flags['AUTH_DETAILED_DIAGNOSTICS'] = os.getenv('AUTH_DETAILED_DIAGNOSTICS', 'on').lower() == 'on'
        
        # Retry на OperationalError
        self._flags['AUTH_RETRY_OPERATIONAL_ERRORS'] = os.getenv('AUTH_RETRY_OPERATIONAL_ERRORS', 'on').lower() == 'on'
        
        # Мягкая деградация
        self._flags['AUTH_SOFT_DEGRADATION'] = os.getenv('AUTH_SOFT_DEGRADATION', 'off').lower() == 'on'
```

## Быстрый Запуск

### 1. Включение Детектора

```bash
# Включить все feature flags
./scripts/enable_truth_detector.sh

# Или вручную в .env
echo "AUTH_FINALIZE_DB_BYPASS=on" >> .env
echo "AUTH_DETAILED_DIAGNOSTICS=on" >> .env
echo "AUTH_LOG_SQL_STATEMENTS=on" >> .env
```

### 2. Запуск Диагностики

```bash
# Автоматическая диагностика
python3 scripts/auth_truth_detector.py

# Или ручной тест
curl -X POST http://localhost:8000/api/v1/sessions/save \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test-tenant",
    "user_id": "test-user", 
    "session_string": "test-session-string",
    "telegram_user_id": 139883458
  }'
```

### 3. Анализ Логов

```bash
# Поиск ключевых строк в логах
docker-compose logs api telethon-ingest | grep -E "(Auth finalize|correlation_id|DB_|NON_DB)"

# Проверка метрик
curl http://localhost:8000/metrics | grep auth
```

## Диагностика по 4 Фактам

### 1. Источник Исключения

**DB ошибки**:
```
DB_INTEGRITY during session upsert
DB_OPERATIONAL during session upsert  
DB_GENERIC during session upsert
```

**Не-DB ошибки**:
```
NON_DB during session upsert
```

### 2. SQLSTATE (если БД)

**Коды ошибок PostgreSQL**:
- `23505` - unique_violation
- `23503` - foreign_key_violation
- `23502` - not_null_violation
- `23514` - check_violation
- `40001` - serialization_failure
- `40P01` - deadlock_detected

### 3. Корреляция с Шагом

**Шаги пайплайна**:
- `session_upsert` - upsert сессии
- `domain_updates` - доменные обновления
- `redis_updates` - обновления Redis

### 4. Детали Ошибки

**Логи содержат**:
- `correlation_id` - для трассировки
- `user_id` - пользователь
- `session_length` - длина сессии
- `sqlstate` - код ошибки БД
- `pgerror` - текст ошибки PostgreSQL
- `statement` - SQL запрос
- `params_length` - количество параметров

## Частые Причины

### 1. Пул Соединений / Timeouts

**Симптомы**: `DB_OPERATIONAL`, `connection timeout`, `server closed the connection`

**Решение**:
```python
# В database_url добавить параметры пула
DATABASE_URL = "postgresql+asyncpg://user:pass@host:port/db?pool_size=5&max_overflow=10&pool_pre_ping=True&pool_recycle=3600"
```

### 2. Скрытая Ошибка в Доменных Обновлениях

**Симптомы**: `DB_INTEGRITY`, `foreign_key_violation`, `not_null_violation`

**Решение**:
```python
# Добавить проверки
assert user_id is not None and user_id > 0
logger.info(f"Updating user {user_id} before domain updates")
```

### 3. Исключение Не из БД

**Симптомы**: `NON_DB`, `sqlstate=None`

**Возможные причины**:
- Redis (сеть/таймаут)
- Telethon (InvalidBufferError)
- JSON сериализация
- Base64 валидация

### 4. Блокировки/Конкурентность

**Симптомы**: `serialization_failure`, `deadlock_detected`

**Решение**:
```python
# Retry на SQLSTATE 40001, 40P01
if sqlstate in ['40001', '40P01']:
    await asyncio.sleep(0.1)
    # retry logic
```

## Bypass Тестирование

### 1. Включение Bypass

```bash
# В .env
AUTH_FINALIZE_DB_BYPASS=on
```

### 2. Интерпретация Результатов

**Если при bypass ошибка исчезает**:
- Проблема в доменных обновлениях (вторая половина транзакции)
- Нужно проверить `domain_updates` шаг

**Если при bypass ошибка остается**:
- Проблема в самом upsert или ниже по драйверу/пулу
- Нужно проверить `session_upsert` шаг

## Smoke Тестирование

### 1. Явный Upsert

```python
# Перед реальным вызовом финализации
result = await session_saver.upsert(...)
logger.info(f"Smoke test upsert result: {result}")
```

### 2. Интерпретация

**Если smoke проходит, а основной падает**:
- Проблема не в уникальном индексе/upsert
- Проблема в соседнем шаге или гонке условий

## Метрики Prometheus

### 1. Новые Метрики

```
auth_finalize_attempts_total{step="session_upsert", status="success|failed"}
auth_finalize_failures_total{reason="db_integrity|db_operational|non_db", error_type="IntegrityError|OperationalError|Exception", sqlstate="23505|None"}
auth_finalize_duration_seconds{step="session_upsert|domain_updates|redis_updates"}
auth_session_length_bytes
```

### 2. Алерты

```yaml
- alert: AuthFinalizeFailureRate
  expr: rate(auth_finalize_attempts_total{status="failed"}[5m]) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High auth finalize failure rate"
```

## Отключение Детектора

### 1. После Диагностики

```bash
# В .env
AUTH_FINALIZE_DB_BYPASS=off
AUTH_DETAILED_DIAGNOSTICS=off
AUTH_LOG_SQL_STATEMENTS=off
```

### 2. Перезапуск

```bash
docker-compose restart api telethon-ingest
```

## Результат

После одного прогона с правильно размеченными логами будет ясно:

1. **Источник ошибки**: БД или не-БД
2. **SQLSTATE**: если БД - какой код ошибки
3. **Шаг пайплайна**: где именно падает
4. **Детали**: полная информация для исправления

**Исправление обычно тривиально**:
- Либо увеличить/настроить пул
- Либо чинить конкретный доменный апдейт  
- Либо разбить один "широкий" except на специализированные

---

**Статус**: ✅ Детектор правды готов к использованию
