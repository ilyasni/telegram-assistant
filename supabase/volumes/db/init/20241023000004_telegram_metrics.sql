-- Миграция: Добавление Telegram-специфичных метрик для постов
-- Дата: 2024-10-23
-- Описание: Добавление полей для просмотров, реакций, репостов и других Telegram-метрик

-- ============================================================================
-- ДОБАВЛЕНИЕ ПОЛЕЙ ДЛЯ TELEGRAM МЕТРИК
-- ============================================================================

-- Добавить поля для Telegram-специфичных метрик
ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS views_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS forwards_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reactions_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS replies_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_edited BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS post_author TEXT,
    ADD COLUMN IF NOT EXISTS reply_to_message_id BIGINT,
    ADD COLUMN IF NOT EXISTS reply_to_chat_id BIGINT,
    ADD COLUMN IF NOT EXISTS via_bot_id BIGINT,
    ADD COLUMN IF NOT EXISTS via_business_bot_id BIGINT,
    ADD COLUMN IF NOT EXISTS is_silent BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_legacy BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS noforwards BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS invert_media BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS last_metrics_update TIMESTAMPTZ DEFAULT NOW();

-- ============================================================================
-- ИНДЕКСЫ ДЛЯ МЕТРИК
-- ============================================================================

-- Индексы для популярности и аналитики
CREATE INDEX IF NOT EXISTS ix_posts_views_count ON posts(views_count DESC) 
    WHERE views_count > 0;

CREATE INDEX IF NOT EXISTS ix_posts_forwards_count ON posts(forwards_count DESC) 
    WHERE forwards_count > 0;

CREATE INDEX IF NOT EXISTS ix_posts_reactions_count ON posts(reactions_count DESC) 
    WHERE reactions_count > 0;

CREATE INDEX IF NOT EXISTS ix_posts_replies_count ON posts(replies_count DESC) 
    WHERE replies_count > 0;

-- Индекс для закреплённых постов
CREATE INDEX IF NOT EXISTS ix_posts_pinned ON posts(is_pinned) 
    WHERE is_pinned = TRUE;

-- Индекс для отредактированных постов
CREATE INDEX IF NOT EXISTS ix_posts_edited ON posts(is_edited, edited_at DESC) 
    WHERE is_edited = TRUE;

-- Индекс для постов с автором
CREATE INDEX IF NOT EXISTS ix_posts_author ON posts(post_author) 
    WHERE post_author IS NOT NULL;

-- Индекс для последнего обновления метрик
CREATE INDEX IF NOT EXISTS ix_posts_metrics_update ON posts(last_metrics_update DESC);

-- ============================================================================
-- ТАБЛИЦА ДЛЯ РЕАКЦИЙ
-- ============================================================================

-- Создать таблицу для детальных реакций
CREATE TABLE IF NOT EXISTS post_reactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    reaction_type VARCHAR(50) NOT NULL, -- 'emoji', 'custom_emoji', 'paid'
    reaction_value TEXT NOT NULL, -- emoji или document_id
    user_tg_id BIGINT, -- ID пользователя (если доступен)
    is_big BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индексы для реакций
CREATE INDEX IF NOT EXISTS ix_post_reactions_post_id ON post_reactions(post_id);
CREATE INDEX IF NOT EXISTS ix_post_reactions_type ON post_reactions(reaction_type);
CREATE INDEX IF NOT EXISTS ix_post_reactions_value ON post_reactions(reaction_value);
CREATE INDEX IF NOT EXISTS ix_post_reactions_user ON post_reactions(user_tg_id) 
    WHERE user_tg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_post_reactions_created ON post_reactions(created_at DESC);

-- Уникальный индекс для предотвращения дубликатов
CREATE UNIQUE INDEX IF NOT EXISTS ux_post_reactions_unique 
    ON post_reactions(post_id, reaction_type, reaction_value, user_tg_id);

-- ============================================================================
-- ТАБЛИЦА ДЛЯ РЕПОСТОВ
-- ============================================================================

-- Создать таблицу для репостов
CREATE TABLE IF NOT EXISTS post_forwards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    from_chat_id BIGINT, -- ID чата, откуда репост
    from_message_id BIGINT, -- ID сообщения, откуда репост
    from_chat_title TEXT, -- Название чата
    from_chat_username TEXT, -- Username чата
    forwarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индексы для репостов
CREATE INDEX IF NOT EXISTS ix_post_forwards_post_id ON post_forwards(post_id);
CREATE INDEX IF NOT EXISTS ix_post_forwards_from_chat ON post_forwards(post_id);
CREATE INDEX IF NOT EXISTS ix_post_forwards_created ON post_forwards(created_at DESC);

-- ============================================================================
-- ТАБЛИЦА ДЛЯ КОММЕНТАРИЕВ
-- ============================================================================

-- Создать таблицу для комментариев
CREATE TABLE IF NOT EXISTS post_replies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    reply_to_post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
    reply_message_id BIGINT NOT NULL,
    reply_chat_id BIGINT NOT NULL,
    reply_author_tg_id BIGINT,
    reply_author_username TEXT,
    reply_content TEXT,
    reply_posted_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индексы для комментариев
CREATE INDEX IF NOT EXISTS ix_post_replies_post_id ON post_replies(post_id);
CREATE INDEX IF NOT EXISTS ix_post_replies_reply_to ON post_replies(reply_to_post_id);
CREATE INDEX IF NOT EXISTS ix_post_replies_author ON post_replies(reply_author_tg_id) 
    WHERE reply_author_tg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_post_replies_posted ON post_replies(reply_posted_at DESC);

-- ============================================================================
-- ТРИГГЕРЫ ДЛЯ АВТОМАТИЧЕСКОГО ОБНОВЛЕНИЯ МЕТРИК
-- ============================================================================

-- Функция для обновления метрик поста
CREATE OR REPLACE FUNCTION update_post_metrics() RETURNS TRIGGER AS $$
BEGIN
    -- Обновляем счётчики на основе связанных таблиц
    UPDATE posts SET
        reactions_count = (
            SELECT COUNT(DISTINCT CONCAT(reaction_type, ':', reaction_value))
            FROM post_reactions 
            WHERE post_id = NEW.post_id
        ),
        forwards_count = (
            SELECT COUNT(*) 
            FROM post_forwards 
            WHERE post_id = NEW.post_id
        ),
        replies_count = (
            SELECT COUNT(*) 
            FROM post_replies 
            WHERE post_id = NEW.post_id
        ),
        last_metrics_update = NOW()
    WHERE id = NEW.post_id;
    
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

-- Триггеры для автоматического обновления метрик
DROP TRIGGER IF EXISTS trg_post_reactions_metrics ON post_reactions;
CREATE TRIGGER trg_post_reactions_metrics 
    AFTER INSERT OR UPDATE OR DELETE ON post_reactions
    FOR EACH ROW EXECUTE FUNCTION update_post_metrics();

DROP TRIGGER IF EXISTS trg_post_forwards_metrics ON post_forwards;
CREATE TRIGGER trg_post_forwards_metrics 
    AFTER INSERT OR UPDATE OR DELETE ON post_forwards
    FOR EACH ROW EXECUTE FUNCTION update_post_metrics();

DROP TRIGGER IF EXISTS trg_post_replies_metrics ON post_replies;
CREATE TRIGGER trg_post_replies_metrics 
    AFTER INSERT OR UPDATE OR DELETE ON post_replies
    FOR EACH ROW EXECUTE FUNCTION update_post_metrics();

-- ============================================================================
-- RLS ПОЛИТИКИ ДЛЯ НОВЫХ ТАБЛИЦ
-- ============================================================================

-- Включить RLS для новых таблиц
ALTER TABLE post_reactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_forwards ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_replies ENABLE ROW LEVEL SECURITY;

-- Политики для post_reactions
CREATE POLICY post_reactions_by_subscription ON post_reactions
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_reactions.post_id
          AND uc.is_active = true
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
    )
);

CREATE POLICY post_reactions_worker_bypass ON post_reactions
FOR ALL TO worker_role
USING (true) WITH CHECK (true);

-- Политики для post_forwards
CREATE POLICY post_forwards_by_subscription ON post_forwards
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_forwards.post_id
          AND uc.is_active = true
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
    )
);

CREATE POLICY post_forwards_worker_bypass ON post_forwards
FOR ALL TO worker_role
USING (true) WITH CHECK (true);

-- Политики для post_replies
CREATE POLICY post_replies_by_subscription ON post_replies
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_replies.post_id
          AND uc.is_active = true
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
    )
);

CREATE POLICY post_replies_worker_bypass ON post_replies
FOR ALL TO worker_role
USING (true) WITH CHECK (true);

-- ============================================================================
-- КОММЕНТАРИИ К ТАБЛИЦАМ
-- ============================================================================

COMMENT ON TABLE post_reactions IS 'Реакции на посты (эмодзи, кастомные эмодзи, платные)';
COMMENT ON TABLE post_forwards IS 'Репосты постов в другие чаты/каналы';
COMMENT ON TABLE post_replies IS 'Комментарии/ответы на посты';

COMMENT ON COLUMN posts.views_count IS 'Количество просмотров поста';
COMMENT ON COLUMN posts.forwards_count IS 'Количество репостов поста';
COMMENT ON COLUMN posts.reactions_count IS 'Количество уникальных реакций';
COMMENT ON COLUMN posts.replies_count IS 'Количество комментариев';
COMMENT ON COLUMN posts.is_pinned IS 'Закреплён ли пост в канале';
COMMENT ON COLUMN posts.is_edited IS 'Был ли пост отредактирован';
COMMENT ON COLUMN posts.post_author IS 'Автор поста (если доступен)';
COMMENT ON COLUMN posts.last_metrics_update IS 'Время последнего обновления метрик';

-- ============================================================================
-- ПРОВЕРКИ ПОСЛЕ МИГРАЦИИ
-- ============================================================================

-- Проверить, что все поля добавлены
DO $$
DECLARE
    missing_fields TEXT[];
    field_name TEXT;
    expected_fields TEXT[] := ARRAY[
        'views_count', 'forwards_count', 'reactions_count', 'replies_count',
        'is_pinned', 'is_edited', 'edited_at', 'post_author',
        'reply_to_message_id', 'reply_to_chat_id', 'via_bot_id',
        'via_business_bot_id', 'is_silent', 'is_legacy', 'noforwards',
        'invert_media', 'last_metrics_update'
    ];
BEGIN
    FOREACH field_name IN ARRAY expected_fields
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'posts' 
              AND column_name = field_name
              AND table_schema = 'public'
        ) THEN
            missing_fields := array_append(missing_fields, field_name);
        END IF;
    END LOOP;
    
    IF array_length(missing_fields, 1) > 0 THEN
        RAISE WARNING 'Отсутствуют поля: %', array_to_string(missing_fields, ', ');
    ELSE
        RAISE NOTICE 'Все поля для Telegram метрик добавлены ✓';
    END IF;
END $$;

-- Проверить новые таблицы
DO $$
DECLARE
    table_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO table_count
    FROM information_schema.tables 
    WHERE table_name IN ('post_reactions', 'post_forwards', 'post_replies')
      AND table_schema = 'public';
    
    IF table_count = 3 THEN
        RAISE NOTICE 'Все новые таблицы созданы ✓';
    ELSE
        RAISE WARNING 'Создано только % из 3 таблиц', table_count;
    END IF;
END $$;

-- Статистика по индексам
DO $$
DECLARE
    index_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO index_count
    FROM pg_indexes 
    WHERE tablename IN ('posts', 'post_reactions', 'post_forwards', 'post_replies')
      AND schemaname = 'public'
      AND indexname LIKE 'ix_%';
    
    RAISE NOTICE 'Создано % индексов для метрик', index_count;
END $$;

-- Миграция завершена
