-- Context7 best practice: упрощенное хранение Telegram сессий
-- Добавляем поля прямо в таблицу users

-- Добавляем поля для Telegram авторизации в таблицу users
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_session_enc TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_session_key_id VARCHAR(64);
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_auth_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_auth_created_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_auth_updated_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_auth_error TEXT;

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS ix_users_telegram_auth_status ON users(telegram_auth_status);
CREATE INDEX IF NOT EXISTS ix_users_telegram_auth_created ON users(telegram_auth_created_at);

-- Функция для обновления telegram_auth_updated_at
CREATE OR REPLACE FUNCTION update_users_telegram_auth_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.telegram_auth_updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Триггер для автоматического обновления
CREATE TRIGGER trigger_users_telegram_auth_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    WHEN (OLD.telegram_session_enc IS DISTINCT FROM NEW.telegram_session_enc 
          OR OLD.telegram_auth_status IS DISTINCT FROM NEW.telegram_auth_status)
    EXECUTE FUNCTION update_users_telegram_auth_updated_at();

-- Простая таблица для аудита (опционально)
CREATE TABLE IF NOT EXISTS telegram_auth_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    event VARCHAR(64) NOT NULL,
    reason VARCHAR(255),
    ip VARCHAR(64),
    user_agent VARCHAR(512),
    at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    meta JSONB DEFAULT '{}'::jsonb
);

-- Индексы для аудита
CREATE INDEX IF NOT EXISTS ix_telegram_auth_events_user ON telegram_auth_events(user_id);
CREATE INDEX IF NOT EXISTS ix_telegram_auth_events_event ON telegram_auth_events(event);
CREATE INDEX IF NOT EXISTS ix_telegram_auth_events_at ON telegram_auth_events(at);

-- Комментарии
COMMENT ON COLUMN users.telegram_session_enc IS 'Зашифрованная StringSession от Telethon';
COMMENT ON COLUMN users.telegram_auth_status IS 'Статус авторизации: pending|authorized|revoked|expired|failed';
COMMENT ON COLUMN users.telegram_session_key_id IS 'ID ключа шифрования для расшифровки session_string_enc';
COMMENT ON TABLE telegram_auth_events IS 'События авторизации Telegram (упрощенная версия)';
