-- Улучшение схемы таблицы posts
-- Context7 + Supabase Best Practices

-- Set correct database context
SET search_path TO public;

-- 1. Добавление прямой ссылки на Telegram пост
ALTER TABLE posts ADD COLUMN telegram_post_url TEXT;

-- 2. Переименование полей для лучшей читаемости (только tg_message_id)
ALTER TABLE posts RENAME COLUMN tg_message_id TO telegram_message_id;

-- Commented out optional renames (out of scope for this migration):
-- ALTER TABLE posts RENAME COLUMN post_author TO author_id;
-- ALTER TABLE posts RENAME COLUMN reply_to_message_id TO reply_to_telegram_message_id;
-- ALTER TABLE posts RENAME COLUMN reply_to_chat_id TO reply_to_telegram_chat_id;
-- ALTER TABLE posts RENAME COLUMN via_bot_id TO via_telegram_bot_id;
-- ALTER TABLE posts RENAME COLUMN via_business_bot_id TO via_telegram_business_bot_id;

-- 3. Группировка метрик (опционально - можно оставить как есть)
-- ALTER TABLE posts RENAME COLUMN views_count TO metrics_views;
-- ALTER TABLE posts RENAME COLUMN forwards_count TO metrics_forwards;
-- ALTER TABLE posts RENAME COLUMN reactions_count TO metrics_reactions;
-- ALTER TABLE posts RENAME COLUMN replies_count TO metrics_replies;

-- 4. Группировка флагов (опционально - можно оставить как есть)
-- ALTER TABLE posts RENAME COLUMN is_pinned TO flags_pinned;
-- ALTER TABLE posts RENAME COLUMN is_edited TO flags_edited;
-- ALTER TABLE posts RENAME COLUMN is_silent TO flags_silent;
-- ALTER TABLE posts RENAME COLUMN is_legacy TO flags_legacy;
-- ALTER TABLE posts RENAME COLUMN noforwards TO flags_no_forwards;
-- ALTER TABLE posts RENAME COLUMN invert_media TO flags_invert_media;

-- 5. Создание функции для генерации telegram_post_url
CREATE OR REPLACE FUNCTION generate_telegram_post_url(
    p_channel_username TEXT,
    p_message_id BIGINT
) RETURNS TEXT AS $$
BEGIN
    -- Handle NULL inputs
    IF p_channel_username IS NULL OR p_message_id IS NULL THEN
        RETURN NULL;
    END IF;
    
    -- Handle empty username (private channels)
    IF TRIM(p_channel_username) = '' THEN
        RETURN NULL;
    END IF;
    
    -- Generate URL for public channels only
    -- Private channels require internal_id mapping (not in scope)
    RETURN CONCAT('https://t.me/', p_channel_username, '/', p_message_id);
END;
$$ LANGUAGE plpgsql;

-- 6. Обновление существующих записей с telegram_post_url
UPDATE posts 
SET telegram_post_url = generate_telegram_post_url(
    (SELECT username FROM channels WHERE id = posts.channel_id),
    telegram_message_id
)
WHERE telegram_post_url IS NULL;

-- 7. Создание индекса для быстрого поиска по telegram_post_url
CREATE INDEX idx_posts_telegram_url ON posts(telegram_post_url);

-- 8. Создание триггера для автоматического обновления telegram_post_url
CREATE OR REPLACE FUNCTION update_telegram_post_url()
RETURNS TRIGGER AS $$
BEGIN
    NEW.telegram_post_url = generate_telegram_post_url(
        (SELECT username FROM channels WHERE id = NEW.channel_id),
        NEW.telegram_message_id
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_posts_telegram_url
    BEFORE INSERT OR UPDATE ON posts
    FOR EACH ROW
    EXECUTE FUNCTION update_telegram_post_url();

-- 9. Обновление комментариев для лучшей документации
COMMENT ON COLUMN posts.telegram_message_id IS 'Telegram message ID (bigint)';
COMMENT ON COLUMN posts.telegram_post_url IS 'Direct link to Telegram post (https://t.me/username/message_id)';

-- 12. Валидация миграции
-- Проверка существования колонки
DO $$ 
BEGIN
    PERFORM 1 FROM information_schema.columns 
    WHERE table_name='posts' AND column_name='telegram_post_url';
    IF NOT FOUND THEN 
        RAISE EXCEPTION 'telegram_post_url column missing after migration';
    END IF;
    
    RAISE NOTICE 'Migration validation: telegram_post_url column exists';
END $$;

-- Проверка статистики заполнения URL
SELECT 
    COUNT(*) AS total_posts,
    COUNT(telegram_post_url) AS posts_with_url,
    ROUND(100.0 * COUNT(telegram_post_url) / NULLIF(COUNT(*), 0), 2) AS url_percentage
FROM posts;

-- 10. Создание view для удобного доступа к полным данным поста
CREATE OR REPLACE VIEW posts_with_telegram_links AS
SELECT 
    p.*,
    c.username as channel_username,
    c.title as channel_title,
    generate_telegram_post_url(c.username, p.telegram_message_id) as computed_telegram_url
FROM posts p
JOIN channels c ON p.channel_id = c.id;

-- 11. Создание backward-compatible view с явными колонками
CREATE OR REPLACE VIEW posts_legacy AS
SELECT 
    id,
    channel_id,
    telegram_message_id AS tg_message_id,
    content,
    media_urls,
    created_at,
    is_processed,
    posted_at,
    url,
    has_media,
    yyyymm,
    views_count,
    forwards_count,
    reactions_count,
    replies_count,
    is_pinned,
    is_edited,
    edited_at,
    post_author,
    reply_to_message_id,
    reply_to_chat_id,
    via_bot_id,
    via_business_bot_id,
    is_silent,
    is_legacy,
    noforwards,
    invert_media,
    last_metrics_update,
    telegram_post_url
FROM posts;
