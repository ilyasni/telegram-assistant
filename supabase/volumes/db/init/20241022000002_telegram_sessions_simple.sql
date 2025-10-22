-- Context7 best practice: упрощенная миграция для Telegram сессий
-- Создание таблиц без внешних ключей для начала

-- Таблица ключей шифрования
CREATE TABLE IF NOT EXISTS encryption_keys (
    key_id VARCHAR(64) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    retired_at TIMESTAMP WITH TIME ZONE
);

-- Таблица Telegram сессий (без внешних ключей)
CREATE TABLE IF NOT EXISTS telegram_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255),
    session_string_enc TEXT NOT NULL,
    key_id VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS ix_telegram_sessions_tenant ON telegram_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_telegram_sessions_status ON telegram_sessions(status);
CREATE INDEX IF NOT EXISTS ix_telegram_sessions_user ON telegram_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_telegram_sessions_created ON telegram_sessions(created_at);

-- Таблица аудита авторизации
CREATE TABLE IF NOT EXISTS telegram_auth_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    event VARCHAR(64) NOT NULL,
    reason VARCHAR(255),
    error_code VARCHAR(64),
    ip VARCHAR(64),
    user_agent VARCHAR(512),
    latency_ms INTEGER,
    at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    meta JSONB DEFAULT '{}'::jsonb
);

-- Индексы для аудита
CREATE INDEX IF NOT EXISTS ix_telegram_auth_logs_session ON telegram_auth_logs(session_id);
CREATE INDEX IF NOT EXISTS ix_telegram_auth_logs_event ON telegram_auth_logs(event);
CREATE INDEX IF NOT EXISTS ix_telegram_auth_logs_at ON telegram_auth_logs(at);

-- Context7 best practice: создание дефолтного ключа шифрования
INSERT INTO encryption_keys (key_id, created_at) 
VALUES ('default_key_' || extract(epoch from now())::text, NOW())
ON CONFLICT (key_id) DO NOTHING;

-- Context7 best practice: функция для обновления updated_at
CREATE OR REPLACE FUNCTION update_telegram_sessions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Триггер для автоматического обновления updated_at
CREATE TRIGGER trigger_telegram_sessions_updated_at
    BEFORE UPDATE ON telegram_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_telegram_sessions_updated_at();

-- Context7 best practice: комментарии для документации
COMMENT ON TABLE encryption_keys IS 'Ключи шифрования для Telegram StringSession (поддержка ротации)';
COMMENT ON TABLE telegram_sessions IS 'Зашифрованные Telethon StringSession на арендатора/пользователя';
COMMENT ON TABLE telegram_auth_logs IS 'Аудит событий авторизации Telegram (QR/miniapp)';

COMMENT ON COLUMN telegram_sessions.session_string_enc IS 'Зашифрованная StringSession от Telethon';
COMMENT ON COLUMN telegram_sessions.status IS 'Статус сессии: pending|authorized|revoked|expired|failed';
COMMENT ON COLUMN telegram_sessions.key_id IS 'ID ключа шифрования для расшифровки session_string_enc';
