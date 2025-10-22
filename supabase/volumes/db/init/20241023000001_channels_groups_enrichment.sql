-- Миграция: Добавление many-to-many связей, групп, обогащения постов
-- Дата: 2024-10-23
-- Описание: Расширение схемы для поддержки подписок пользователей на каналы,
--          групповых чатов, обогащения контента и оптимизации индексов

-- ============================================================================
-- 1. ПЕРЕИМЕНОВАНИЕ ПОЛЕЙ ДЛЯ ЕДИНООБРАЗИЯ TELEGRAM-ИДЕНТИФИКАТОРОВ
-- ============================================================================

-- Единообразие: все Telegram-поля начинаются с tg_*
ALTER TABLE channels RENAME COLUMN telegram_id TO tg_channel_id;
ALTER TABLE posts RENAME COLUMN telegram_message_id TO tg_message_id;

-- ============================================================================
-- 2. ДОБАВЛЕНИЕ ПОЛЕЙ В СУЩЕСТВУЮЩИЕ ТАБЛИЦЫ
-- ============================================================================

-- Добавить поля в posts
ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS url TEXT,
    ADD COLUMN IF NOT EXISTS has_media BOOLEAN NOT NULL DEFAULT false;

-- Добавить вычисляемое поле после добавления posted_at
ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS yyyymm INTEGER 
        GENERATED ALWAYS AS (
            (EXTRACT(YEAR FROM posted_at)::INT * 100) + EXTRACT(MONTH FROM posted_at)::INT
        ) STORED;

-- ============================================================================
-- 3. НОВЫЕ ТАБЛИЦЫ
-- ============================================================================

-- 3.1 Таблица user_channel (many-to-many связь пользователей и каналов)
CREATE TABLE user_channel (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT true,
    settings JSONB DEFAULT '{}',
    PRIMARY KEY (user_id, channel_id)
);

-- 3.2 Таблица post_enrichment (обогащение постов)
CREATE TABLE post_enrichment (
    post_id UUID PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    tags JSONB DEFAULT '[]',
    vision_labels JSONB DEFAULT '[]',
    ocr_text TEXT,
    crawl_md TEXT,
    enrichment_provider VARCHAR(50),  -- gigachat | openrouter
    enriched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enrichment_latency_ms INTEGER,
    metadata JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3.3 Таблица post_media (медиа-файлы)
CREATE TABLE post_media (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    media_type VARCHAR(50) NOT NULL,
    media_url TEXT NOT NULL,
    thumbnail_url TEXT,
    file_size_bytes BIGINT,
    width INTEGER,
    height INTEGER,
    duration_seconds INTEGER,
    -- Telegram-специфичные поля для дедупликации
    tg_file_id TEXT,
    tg_file_unique_id TEXT,
    sha256 BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_media_type CHECK (media_type IN ('photo', 'video', 'document'))
);

-- 3.4 Таблица groups (групповые чаты)
CREATE TABLE groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tg_chat_id BIGINT NOT NULL,
    title VARCHAR(500) NOT NULL,
    username VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settings JSONB DEFAULT '{}',
    UNIQUE(tenant_id, tg_chat_id)
);

-- 3.5 Таблица user_group (подписки на группы)
CREATE TABLE user_group (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    monitor_mentions BOOLEAN NOT NULL DEFAULT true,
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT true,
    settings JSONB DEFAULT '{}',
    PRIMARY KEY (user_id, group_id)
);

-- 3.6 Таблица group_messages (сообщения из групп)
CREATE TABLE group_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    tg_message_id BIGINT NOT NULL,
    sender_tg_id BIGINT,
    sender_username VARCHAR(255),
    content TEXT,
    posted_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(group_id, tg_message_id)
);

-- 3.7 Таблица group_mentions (упоминания в группах)
CREATE TABLE group_mentions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_message_id UUID NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
    mentioned_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mentioned_user_tg_id BIGINT NOT NULL,
    context_snippet TEXT,
    is_processed BOOLEAN NOT NULL DEFAULT false,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 4. ИНДЕКСЫ ДЛЯ ПРОИЗВОДИТЕЛЬНОСТИ
-- ============================================================================

-- 4.1 Индексы для user_channel
CREATE INDEX ix_user_channel_user ON user_channel(user_id) WHERE is_active = true;
CREATE INDEX ix_user_channel_channel ON user_channel(channel_id) WHERE is_active = true;

-- 4.2 Индексы для post_enrichment
-- Универсальные GIN индексы (без jsonb_path_ops) для поддержки всех операторов
CREATE INDEX ix_post_enrichment_tags_gin 
    ON post_enrichment USING GIN(tags);
CREATE INDEX ix_post_enrichment_vision_gin 
    ON post_enrichment USING GIN(vision_labels);
CREATE INDEX ix_post_enrichment_enriched_at 
    ON post_enrichment(enriched_at DESC);

-- 4.3 Индексы для post_media
CREATE INDEX ix_post_media_post_id ON post_media(post_id);
CREATE INDEX ix_post_media_type ON post_media(media_type);
CREATE UNIQUE INDEX ux_post_media_dedup 
    ON post_media(post_id, COALESCE(tg_file_unique_id, tg_file_id));
CREATE INDEX ix_post_media_sha256 ON post_media(sha256) WHERE sha256 IS NOT NULL;

-- 4.4 Индексы для groups
CREATE INDEX ix_groups_tenant ON groups(tenant_id);
CREATE INDEX ix_groups_active ON groups(is_active) WHERE is_active = true;

-- 4.5 Индексы для user_group
CREATE INDEX ix_user_group_user ON user_group(user_id) WHERE is_active = true;
CREATE INDEX ix_user_group_group ON user_group(group_id) WHERE is_active = true;

-- 4.6 Индексы для group_messages
CREATE UNIQUE INDEX ux_group_messages ON group_messages(group_id, tg_message_id);
CREATE INDEX ix_group_messages_posted ON group_messages(group_id, posted_at DESC);
CREATE INDEX ix_group_messages_sender ON group_messages(sender_tg_id);

-- 4.7 Индексы для group_mentions
CREATE INDEX ix_group_mentions_user ON group_mentions(mentioned_user_tg_id);
CREATE INDEX ix_group_mentions_message ON group_mentions(group_message_id);
CREATE INDEX ix_group_mentions_processed ON group_mentions(is_processed) 
    WHERE is_processed = false;

-- 4.8 Улучшение индексов для существующих таблиц
-- Индексы для posts (создаются после добавления полей)
CREATE INDEX IF NOT EXISTS ix_posts_posted_at ON posts(posted_at DESC);
CREATE INDEX IF NOT EXISTS ix_posts_channel_posted ON posts(channel_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS ix_posts_yyyymm ON posts(yyyymm);
CREATE INDEX IF NOT EXISTS ix_posts_channel_id ON posts(channel_id);

-- Уникальные индексы
CREATE UNIQUE INDEX IF NOT EXISTS ux_channels_tg ON channels(tg_channel_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_chan_msg ON posts(channel_id, tg_message_id);

-- Удалить старые дубликаты
DROP INDEX IF EXISTS idx_channels_telegram_id;

-- ============================================================================
-- 5. ТРИГГЕРЫ ДЛЯ АВТОМАТИЗАЦИИ
-- ============================================================================

-- 5.1 Триггер для автоматического обновления has_media
CREATE OR REPLACE FUNCTION sync_post_has_media() RETURNS TRIGGER AS $$
BEGIN
  UPDATE posts p
     SET has_media = EXISTS(SELECT 1 FROM post_media pm WHERE pm.post_id = p.id)
   WHERE p.id = COALESCE(NEW.post_id, OLD.post_id);
  RETURN NULL;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_post_media_sync_ai ON post_media;
CREATE TRIGGER trg_post_media_sync_ai AFTER INSERT ON post_media
FOR EACH ROW EXECUTE FUNCTION sync_post_has_media();

DROP TRIGGER IF EXISTS trg_post_media_sync_ad ON post_media;
CREATE TRIGGER trg_post_media_sync_ad AFTER DELETE ON post_media
FOR EACH ROW EXECUTE FUNCTION sync_post_has_media();

DROP TRIGGER IF EXISTS trg_post_media_sync_au ON post_media;
CREATE TRIGGER trg_post_media_sync_au AFTER UPDATE ON post_media
FOR EACH ROW WHEN (OLD.post_id IS DISTINCT FROM NEW.post_id)
EXECUTE FUNCTION sync_post_has_media();

-- 5.2 Триггер для updated_at
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN 
  NEW.updated_at = NOW(); 
  RETURN NEW; 
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pe_updated ON post_enrichment;
CREATE TRIGGER trg_pe_updated BEFORE UPDATE ON post_enrichment
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 6. RLS ПОЛИТИКИ
-- ============================================================================

-- 6.1 Включить RLS на новых таблицах
ALTER TABLE user_channel ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_enrichment ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_media ENABLE ROW LEVEL SECURITY;
ALTER TABLE groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_group ENABLE ROW LEVEL SECURITY;
ALTER TABLE group_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE group_mentions ENABLE ROW LEVEL SECURITY;

-- 6.2 Политика для posts (фильтр по подпискам)
ALTER TABLE posts ENABLE ROW LEVEL SECURITY;

CREATE POLICY posts_by_subscription ON posts
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM users u
        JOIN user_channel uc ON uc.user_id = u.id
        WHERE u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
          AND uc.channel_id = posts.channel_id
          AND uc.is_active = true
    )
);

-- 6.3 Политика для post_enrichment (через posts)
CREATE POLICY enrichment_by_subscription ON post_enrichment
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_enrichment.post_id
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
          AND uc.is_active = true
    )
);

-- 6.4 Политика для post_media (через posts)
CREATE POLICY post_media_by_subscription ON post_media
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_media.post_id
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
          AND uc.is_active = true
    )
);

-- 6.5 Политика для groups
CREATE POLICY groups_by_user ON groups
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM users u
        JOIN user_group ug ON ug.user_id = u.id
        WHERE u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
          AND ug.group_id = groups.id
          AND ug.is_active = true
    )
);

-- 6.6 Политика для user_channel (только свои подписки)
CREATE POLICY uc_write_own ON user_channel
FOR ALL TO authenticated
USING (
    EXISTS (
        SELECT 1 
        FROM users u 
        WHERE u.id = user_channel.user_id 
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 
        FROM users u 
        WHERE u.id = user_channel.user_id 
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
    )
);

-- 6.7 Bypass RLS для воркеров
CREATE ROLE worker_role;
GRANT worker_role TO postgres;

-- Отключить RLS для worker_role на всех рабочих таблицах
ALTER TABLE posts FORCE ROW LEVEL SECURITY;
CREATE POLICY posts_worker_bypass ON posts
FOR ALL
TO worker_role
USING (true)
WITH CHECK (true);

CREATE POLICY enrichment_worker_bypass ON post_enrichment
FOR ALL
TO worker_role
USING (true)
WITH CHECK (true);

CREATE POLICY post_media_worker_bypass ON post_media
FOR ALL
TO worker_role
USING (true)
WITH CHECK (true);

CREATE POLICY groups_worker_bypass ON groups
FOR ALL
TO worker_role
USING (true)
WITH CHECK (true);

CREATE POLICY group_messages_worker_bypass ON group_messages
FOR ALL
TO worker_role
USING (true)
WITH CHECK (true);

CREATE POLICY group_mentions_worker_bypass ON group_mentions
FOR ALL
TO worker_role
USING (true)
WITH CHECK (true);

-- ============================================================================
-- 7. КОММЕНТАРИИ К ТАБЛИЦАМ
-- ============================================================================

COMMENT ON TABLE user_channel IS 'Many-to-many связь пользователей и каналов для подписок';
COMMENT ON TABLE post_enrichment IS 'Обогащённые данные постов: теги, OCR, vision, crawl результаты';
COMMENT ON TABLE post_media IS 'Медиа-файлы постов с Telegram-специфичными идентификаторами';
COMMENT ON TABLE groups IS 'Групповые чаты для мониторинга упоминаний';
COMMENT ON TABLE user_group IS 'Подписки пользователей на группы';
COMMENT ON TABLE group_messages IS 'Сообщения из групповых чатов';
COMMENT ON TABLE group_mentions IS 'Упоминания пользователей в группах';

-- ============================================================================
-- 8. ПРОВЕРКИ
-- ============================================================================

-- Проверить, что все таблицы созданы
DO $$
DECLARE
    table_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO table_count
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_name IN ('user_channel', 'post_enrichment', 'post_media', 'groups', 'user_group', 'group_messages', 'group_mentions');
    
    IF table_count != 7 THEN
        RAISE EXCEPTION 'Не все таблицы созданы. Ожидалось: 7, получено: %', table_count;
    END IF;
    
    RAISE NOTICE 'Миграция успешно применена. Создано % новых таблиц.', table_count;
END $$;
