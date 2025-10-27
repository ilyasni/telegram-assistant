-- Fix telegram_sessions table for proper session management
-- Добавляем уникальный индекс по user_id для ON CONFLICT

-- 1. Добавляем уникальный индекс по user_id (если его нет)
CREATE UNIQUE INDEX IF NOT EXISTS telegram_sessions_user_id_unique 
ON telegram_sessions (user_id) 
WHERE user_id IS NOT NULL;

-- 2. Добавляем уникальный индекс по tenant_id + user_id для мульти-тенантности
CREATE UNIQUE INDEX IF NOT EXISTS telegram_sessions_tenant_user_unique 
ON telegram_sessions (tenant_id, user_id) 
WHERE user_id IS NOT NULL;

-- 3. Проверяем, что все колонки для session данных в типе TEXT
-- (уже проверено - session_string_enc уже в типе TEXT)

-- 4. Добавляем колонку для хранения метаданных сессии (если нужно)
ALTER TABLE telegram_sessions 
ADD COLUMN IF NOT EXISTS session_metadata JSONB DEFAULT '{}';

-- 5. Добавляем колонку для хранения ошибок авторизации
ALTER TABLE telegram_sessions 
ADD COLUMN IF NOT EXISTS auth_error TEXT;

-- 6. Добавляем колонку для хранения деталей ошибки
ALTER TABLE telegram_sessions 
ADD COLUMN IF NOT EXISTS error_details TEXT;

-- 7. Обновляем триггер для updated_at
CREATE OR REPLACE FUNCTION update_telegram_sessions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 8. Создаем функцию для безопасного upsert сессии
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

-- 9. Создаем функцию для очистки старых сессий
CREATE OR REPLACE FUNCTION cleanup_old_telegram_sessions(
    p_older_than_hours INTEGER DEFAULT 24
) RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM telegram_sessions 
    WHERE created_at < now() - INTERVAL '1 hour' * p_older_than_hours
    AND status IN ('failed', 'expired', 'superseded');
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- 10. Создаем индекс для быстрого поиска по статусу и времени
CREATE INDEX IF NOT EXISTS telegram_sessions_status_created 
ON telegram_sessions (status, created_at);

-- 11. Создаем индекс для очистки старых сессий
CREATE INDEX IF NOT EXISTS telegram_sessions_cleanup 
ON telegram_sessions (created_at, status) 
WHERE status IN ('failed', 'expired', 'superseded');
