-- Context7 best practice: добавляем недостающие поля пользователя
-- first_name, last_name для полной информации о пользователе

-- Добавляем поля для имени пользователя
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name VARCHAR(255);

-- Индексы для поиска по имени
CREATE INDEX IF NOT EXISTS ix_users_first_name ON users(first_name);
CREATE INDEX IF NOT EXISTS ix_users_last_name ON users(last_name);

-- Комментарии
COMMENT ON COLUMN users.first_name IS 'Имя пользователя из Telegram';
COMMENT ON COLUMN users.last_name IS 'Фамилия пользователя из Telegram';
COMMENT ON COLUMN users.username IS 'Username пользователя в Telegram (@username)';
