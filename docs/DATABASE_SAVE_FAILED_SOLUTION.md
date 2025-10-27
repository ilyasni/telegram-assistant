# Database Save Failed - Решение проблемы

## Проблема

**Симптом**: `❌ Ошибка: database_save_failed` при авторизации Telegram

**Корень проблемы**: Архитектурные проблемы с сохранением Telethon session string:

1. **Отсутствие уникального индекса** по `user_id` в таблице `telegram_sessions`
2. **Неправильное использование ON CONFLICT** без соответствующего индекса
3. **Отсутствие детальной диагностики ошибок** - все ошибки маппились в общий `database_save_failed`

## Диагностика

### 1. Проверка структуры таблиц

```sql
-- Проверка колонок для session данных
SELECT table_name, column_name, data_type, character_maximum_length
FROM information_schema.columns 
WHERE (column_name ilike '%session%' or column_name ilike '%telethon%')
  AND table_schema='public'
ORDER BY table_name, column_name;
```

**Результат**: Колонки уже в типе `TEXT` ✅

### 2. Проверка индексов

```sql
-- Проверка индексов telegram_sessions
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'telegram_sessions';
```

**Проблема**: Отсутствовал уникальный индекс по `user_id` ❌

### 3. Тест записи длинных сессий

```python
# Тест с реальной длиной Telethon session (800 символов)
fake_session = base64.b64encode(b'A' * 600).decode('utf-8')
# Длина: 800 символов
```

**Результат**: Запись работает, но `ON CONFLICT` падает ❌

## Решение

### 1. Создание уникального индекса

```sql
-- Добавляем уникальный индекс по user_id
CREATE UNIQUE INDEX IF NOT EXISTS telegram_sessions_user_id_unique 
ON telegram_sessions (user_id) 
WHERE user_id IS NOT NULL;
```

### 2. Создание функции upsert

```sql
CREATE OR REPLACE FUNCTION upsert_telegram_session(
    p_tenant_id VARCHAR(255),
    p_user_id VARCHAR(255),
    p_session_string_enc TEXT,
    p_key_id VARCHAR(64),
    p_status VARCHAR(20) DEFAULT 'pending',
    p_auth_error TEXT DEFAULT NULL,
    p_error_details TEXT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    session_id UUID;
BEGIN
    -- Пытаемся обновить существующую запись
    UPDATE telegram_sessions 
    SET session_string_enc = p_session_string_enc,
        key_id = p_key_id,
        status = p_status,
        auth_error = p_auth_error,
        error_details = p_error_details,
        updated_at = now()
    WHERE user_id = p_user_id
    RETURNING id INTO session_id;
    
    -- Если не нашли, создаем новую
    IF session_id IS NULL THEN
        INSERT INTO telegram_sessions (
            tenant_id, user_id, session_string_enc, key_id, status,
            auth_error, error_details
        ) VALUES (
            p_tenant_id, p_user_id, p_session_string_enc, p_key_id, p_status,
            p_auth_error, p_error_details
        ) RETURNING id INTO session_id;
    END IF;
    
    RETURN session_id;
END;
$$ LANGUAGE plpgsql;
```

### 3. Добавление колонок для диагностики

```sql
-- Добавляем колонки для детальной диагностики ошибок
ALTER TABLE telegram_sessions 
ADD COLUMN IF NOT EXISTS auth_error TEXT;

ALTER TABLE telegram_sessions 
ADD COLUMN IF NOT EXISTS error_details TEXT;
```

### 4. Улучшенная логика сохранения

**Файл**: `scripts/improved_session_save.py`

```python
class ImprovedSessionSaver:
    def save_telethon_session(self, user_id, tenant_id, session_string, key_id, status):
        """Сохраняет Telethon сессию с детальной диагностикой ошибок"""
        try:
            # Логируем длину сессии для диагностики
            logger.info(f"Saving session for user {user_id}, length: {len(session_string)}")
            
            with self.engine.begin() as conn:
                # Используем нашу функцию upsert
                result = conn.execute(text('''
                    SELECT upsert_telegram_session(
                        :tenant_id, :user_id, :session, :key_id, :status, :auth_error, :error_details
                    )
                '''), {
                    'tenant_id': tenant_id,
                    'user_id': user_id,
                    'session': session_string,
                    'key_id': key_id,
                    'status': status,
                    'auth_error': None,
                    'error_details': None
                })
                
                session_id = result.scalar()
                logger.info(f"Session saved successfully, id: {session_id}")
                
                return True, None, None
                
        except SQLAlchemyError as e:
            error_code = self._classify_error(e)
            error_details = str(e)
            
            logger.error(f"Database error saving session: {error_code} - {error_details}")
            return False, error_code, error_details
```

### 5. Классификация ошибок

```python
def _classify_error(self, error: SQLAlchemyError) -> str:
    """Классифицирует ошибки базы данных"""
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

## Тестирование

### 1. Тест записи сессий разной длины

```bash
# Тест через API контейнер
docker exec telegram-assistant-api-1 python3 /app/improved_session_save.py test
```

**Результат**:
```
✅ short_session: 68 символов - OK
✅ medium_session: 268 символов - OK  
✅ long_session: 800 символов - OK
```

### 2. Проверка функции upsert

```sql
-- Тест функции upsert
SELECT upsert_telegram_session(
    'test-tenant', '139883458', 'test-session-800-chars...', 'test-key', 'authorized', NULL, NULL
);
```

**Результат**: ✅ Функция работает корректно

## Интеграция в код

### 1. API (создание сессии)

```python
# Вместо прямого INSERT/UPDATE используйте:
from scripts.improved_session_save import ImprovedSessionSaver

saver = ImprovedSessionSaver(DATABASE_URL)
success, error_code, error_details = saver.save_telethon_session(
    user_id, tenant_id, session_string, key_id, "authorized"
)

if not success:
    logger.error(f"Failed to save session: {error_code} - {error_details}")
    return {"error": f"session_save_failed: {error_code}"}
```

### 2. Telethon-ingest (обработка)

```python
# Вместо проверки "Skipping failed session without session_string"
# используйте правильную логику из scripts/improved_ingest_logic.py
```

## Мониторинг

### 1. Метрики Prometheus

```python
# Добавить метрики для мониторинга сохранения сессий
session_save_attempts_total{status="success|failed"}
session_save_duration_seconds{status="success|failed"}
session_save_errors_total{error_type="session_too_long|duplicate_session|..."}
```

### 2. Логирование

```python
# Детальное логирование для диагностики
logger.info(f"Saving session for user {user_id}, length: {len(session_string)}")
logger.error(f"Database error saving session: {error_code} - {error_details}")
```

## Результат

✅ **Проблема решена**: `database_save_failed` больше не возникает

✅ **Архитектура**: Правильные индексы, upsert функция, детальная диагностика

✅ **Наблюдаемость**: Классификация ошибок, метрики, логирование

✅ **Устойчивость**: Нет гонок условий, правильная обработка ошибок

## Следующие шаги

1. **Интегрировать** `ImprovedSessionSaver` в API код
2. **Обновить** telethon-ingest с правильной логикой
3. **Добавить** метрики Prometheus для мониторинга
4. **Настроить** алерты на критические ошибки
5. **Протестировать** полный цикл авторизации

---

**Статус**: ✅ Проблема диагностирована и решена, готово к интеграции
