-- Дополнительная миграция: Исправление представлений и ограничений
-- Дата: 2024-10-23
-- Описание: Обновление представлений telegram_bot и удаление старых ограничений

-- ============================================================================
-- ОБНОВЛЕНИЕ ПРЕДСТАВЛЕНИЙ TELEGRAM_BOT
-- ============================================================================

-- Обновить представление channels (убрать tenant_id)
DROP VIEW IF EXISTS telegram_bot.channels;
CREATE VIEW telegram_bot.channels AS
SELECT 
    channels.id,
    channels.tg_channel_id AS telegram_id,
    channels.username,
    channels.title,
    channels.is_active,
    channels.last_message_at,
    channels.created_at,
    channels.settings
FROM channels;

-- Обновить представление posts (убрать tenant_id)
DROP VIEW IF EXISTS telegram_bot.posts;
CREATE VIEW telegram_bot.posts AS
SELECT 
    posts.id,
    posts.channel_id,
    posts.tg_message_id AS telegram_message_id,
    posts.content,
    posts.media_urls,
    posts.posted_at,
    posts.url,
    posts.has_media,
    posts.created_at,
    posts.is_processed
FROM posts;

-- Обновить представление users (оставить tenant_id для пользователей)
-- Это представление можно оставить как есть, так как users остаются per-tenant

-- ============================================================================
-- УДАЛЕНИЕ СТАРЫХ ОГРАНИЧЕНИЙ
-- ============================================================================

-- Удалить старое ограничение на posts
ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_tenant_id_channel_id_telegram_message_id_key;

-- ============================================================================
-- ПОВТОРНОЕ УДАЛЕНИЕ TENANT_ID
-- ============================================================================

-- Теперь можно безопасно удалить tenant_id
ALTER TABLE channels DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE posts DROP COLUMN IF EXISTS tenant_id;

-- ============================================================================
-- ПРОВЕРКИ
-- ============================================================================

-- Проверить, что tenant_id удалены
DO $$
DECLARE
    channels_has_tenant BOOLEAN;
    posts_has_tenant BOOLEAN;
BEGIN
    -- Проверить channels
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'channels' 
          AND column_name = 'tenant_id'
          AND table_schema = 'public'
    ) INTO channels_has_tenant;
    
    -- Проверить posts
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'posts' 
          AND column_name = 'tenant_id'
          AND table_schema = 'public'
    ) INTO posts_has_tenant;
    
    IF channels_has_tenant THEN
        RAISE WARNING 'Колонка tenant_id всё ещё существует в channels!';
    ELSE
        RAISE NOTICE 'tenant_id успешно удалён из channels ✓';
    END IF;
    
    IF posts_has_tenant THEN
        RAISE WARNING 'Колонка tenant_id всё ещё существует в posts!';
    ELSE
        RAISE NOTICE 'tenant_id успешно удалён из posts ✓';
    END IF;
END $$;

-- Проверить уникальные индексы
DO $$
DECLARE
    channels_unique_exists BOOLEAN;
    posts_unique_exists BOOLEAN;
BEGIN
    -- Проверить уникальный индекс на channels
    SELECT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'channels' 
          AND indexname = 'ux_channels_tg_global'
    ) INTO channels_unique_exists;
    
    -- Проверить уникальный индекс на posts
    SELECT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'posts' 
          AND indexname = 'ux_posts_chan_msg'
    ) INTO posts_unique_exists;
    
    IF channels_unique_exists THEN
        RAISE NOTICE 'Уникальный индекс на channels создан ✓';
    ELSE
        RAISE WARNING 'Уникальный индекс на channels НЕ создан!';
    END IF;
    
    IF posts_unique_exists THEN
        RAISE NOTICE 'Уникальный индекс на posts создан ✓';
    ELSE
        RAISE WARNING 'Уникальный индекс на posts НЕ создан!';
    END IF;
END $$;

-- Финальная проверка структуры
DO $$
DECLARE
    table_record RECORD;
BEGIN
    RAISE NOTICE '=== СТРУКТУРА ТАБЛИЦ ===';
    
    FOR table_record IN 
        SELECT 
            table_name,
            column_name,
            data_type,
            is_nullable
        FROM information_schema.columns 
        WHERE table_name IN ('channels', 'posts')
          AND table_schema = 'public'
        ORDER BY table_name, ordinal_position
    LOOP
        RAISE NOTICE '%: % % (%)', 
            table_record.table_name, 
            table_record.column_name, 
            table_record.data_type,
            CASE WHEN table_record.is_nullable = 'YES' THEN 'NULL' ELSE 'NOT NULL' END;
    END LOOP;
END $$;
