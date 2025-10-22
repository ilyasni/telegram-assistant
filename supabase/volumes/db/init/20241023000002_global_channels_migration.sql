-- Миграция: Переход на глобальные каналы и посты
-- Дата: 2024-10-23
-- Описание: Удаление tenant_id из channels и posts, дедупликация данных,
--          обновление RLS политик для доступа через user_channel

-- ============================================================================
-- ЭТАП 0: ПОДГОТОВКА (без простоя)
-- ============================================================================

-- 0.1 Создать служебную схему для артефактов миграции
CREATE SCHEMA IF NOT EXISTS _shadow;

-- 0.2 Триггер маппинга (если НЕ останавливаем writer'ов)
-- ⚠️ ВНИМАНИЕ: Если останавливаете writer'ов на время миграции — пропустите этот блок

CREATE OR REPLACE FUNCTION _shadow.map_channel_before_insert() RETURNS trigger AS $$
BEGIN
  -- Если вставляют пост со старым channel_id, заменим на канонический
  NEW.channel_id := COALESCE(
    (SELECT new_channel_id FROM _shadow.channel_mapping WHERE old_channel_id = NEW.channel_id),
    NEW.channel_id
  );
  RETURN NEW;
END$$ LANGUAGE plpgsql;

-- Подключим временно триггер на posts
DROP TRIGGER IF EXISTS trg_posts_map_channel ON posts;
CREATE TRIGGER trg_posts_map_channel
BEFORE INSERT ON posts
FOR EACH ROW EXECUTE FUNCTION _shadow.map_channel_before_insert();

-- ============================================================================
-- ЭТАП 1: ДЕДУПЛИКАЦИЯ (persist, не TEMP)
-- ============================================================================

-- 1.1 Persist-таблица соответствий каналов
DROP TABLE IF EXISTS _shadow.channel_mapping;
CREATE TABLE _shadow.channel_mapping AS
SELECT 
    c.id AS old_channel_id,
    (SELECT id FROM channels c2
     WHERE c2.tg_channel_id = c.tg_channel_id
     ORDER BY created_at ASC
     LIMIT 1) AS new_channel_id,
    c.tg_channel_id
FROM channels c;

CREATE INDEX IF NOT EXISTS idx_channel_mapping_old ON _shadow.channel_mapping(old_channel_id);
CREATE INDEX IF NOT EXISTS idx_channel_mapping_new ON _shadow.channel_mapping(new_channel_id);

-- Статистика дедупликации
DO $$
DECLARE
    total_channels INTEGER;
    unique_channels INTEGER;
    duplicates INTEGER;
BEGIN
    SELECT COUNT(*), COUNT(DISTINCT new_channel_id), COUNT(*) - COUNT(DISTINCT new_channel_id)
    INTO total_channels, unique_channels, duplicates
    FROM _shadow.channel_mapping;
    
    RAISE NOTICE 'Дедупликация каналов: всего=%, уникальных=%, дубликатов=%', 
                 total_channels, unique_channels, duplicates;
END $$;

-- 1.2 Persist-таблица соответствий постов
DROP TABLE IF EXISTS _shadow.post_mapping;
CREATE TABLE _shadow.post_mapping AS
SELECT 
    p.id AS old_post_id,
    (SELECT p2.id
     FROM posts p2
     WHERE p2.channel_id = cm.new_channel_id
       AND p2.tg_message_id = p.tg_message_id
     ORDER BY p2.created_at ASC
     LIMIT 1) AS new_post_id
FROM posts p
JOIN _shadow.channel_mapping cm ON cm.old_channel_id = p.channel_id
WHERE cm.old_channel_id <> cm.new_channel_id;

CREATE INDEX IF NOT EXISTS idx_post_mapping_old ON _shadow.post_mapping(old_post_id);

-- Статистика постов
DO $$
DECLARE
    posts_to_remap INTEGER;
    unique_posts INTEGER;
BEGIN
    SELECT COUNT(*), COUNT(DISTINCT new_post_id)
    INTO posts_to_remap, unique_posts
    FROM _shadow.post_mapping
    WHERE new_post_id IS NOT NULL;
    
    RAISE NOTICE 'Ремап постов: к переносу=%, уникальных=%', posts_to_remap, unique_posts;
END $$;

-- ============================================================================
-- ЭТАП 2: РЕМАП ССЫЛОК (в явной последовательности)
-- ============================================================================

-- 2.1 Обновить user_channel
UPDATE user_channel uc
SET channel_id = cm.new_channel_id
FROM _shadow.channel_mapping cm
WHERE uc.channel_id = cm.old_channel_id
  AND cm.old_channel_id <> cm.new_channel_id;

-- Удаляем дубликаты подписок (оставляем самую раннюю)
DELETE FROM user_channel uc
USING user_channel uc2
WHERE uc.user_id = uc2.user_id
  AND uc.channel_id = uc2.channel_id
  AND uc.subscribed_at > uc2.subscribed_at;

-- Статистика подписок
DO $$
DECLARE
    active_subs INTEGER;
BEGIN
    SELECT COUNT(*) INTO active_subs FROM user_channel WHERE is_active = true;
    RAISE NOTICE 'Активных подписок: %', active_subs;
END $$;

-- 2.2 Обновить posts
UPDATE posts p
SET channel_id = cm.new_channel_id
FROM _shadow.channel_mapping cm
WHERE p.channel_id = cm.old_channel_id
  AND cm.old_channel_id <> cm.new_channel_id;

-- Статистика постов
DO $$
DECLARE
    total_posts INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_posts FROM posts;
    RAISE NOTICE 'Всего постов: %', total_posts;
END $$;

-- 2.3 Перенести post_enrichment
INSERT INTO post_enrichment (
    post_id, tags, vision_labels, ocr_text, crawl_md,
    enrichment_provider, enriched_at, enrichment_latency_ms, 
    metadata, updated_at
)
SELECT 
    pm.new_post_id, pe.tags, pe.vision_labels, pe.ocr_text, pe.crawl_md,
    pe.enrichment_provider, pe.enriched_at, pe.enrichment_latency_ms, 
    pe.metadata, pe.updated_at
FROM post_enrichment pe
JOIN _shadow.post_mapping pm ON pm.old_post_id = pe.post_id
WHERE pm.new_post_id IS NOT NULL
  AND pm.old_post_id <> pm.new_post_id
ON CONFLICT (post_id) DO NOTHING;

-- Статистика обогащения
DO $$
DECLARE
    enriched_posts INTEGER;
BEGIN
    SELECT COUNT(*) INTO enriched_posts FROM post_enrichment;
    RAISE NOTICE 'Обогащённых постов: %', enriched_posts;
END $$;

-- 2.4 Перенести post_media
INSERT INTO post_media (
    post_id, media_type, media_url, thumbnail_url, file_size_bytes, 
    width, height, duration_seconds,
    tg_file_id, tg_file_unique_id, sha256, created_at
)
SELECT 
    pm.new_post_id, m.media_type, m.media_url, m.thumbnail_url, m.file_size_bytes, 
    m.width, m.height, m.duration_seconds,
    m.tg_file_id, m.tg_file_unique_id, m.sha256, m.created_at
FROM post_media m
JOIN _shadow.post_mapping pm ON pm.old_post_id = m.post_id
WHERE pm.new_post_id IS NOT NULL
  AND pm.old_post_id <> pm.new_post_id
ON CONFLICT DO NOTHING;

-- Статистика медиа
DO $$
DECLARE
    media_files INTEGER;
BEGIN
    SELECT COUNT(*) INTO media_files FROM post_media;
    RAISE NOTICE 'Медиа-файлов: %', media_files;
END $$;

-- 2.5 Обновить indexing_status
UPDATE indexing_status ist
SET post_id = pm.new_post_id
FROM _shadow.post_mapping pm
WHERE ist.post_id = pm.old_post_id
  AND pm.new_post_id IS NOT NULL
  AND pm.old_post_id <> pm.new_post_id;

-- Статистика индексации
DO $$
DECLARE
    status_record RECORD;
BEGIN
    FOR status_record IN 
        SELECT embedding_status, COUNT(*) as count 
        FROM indexing_status 
        GROUP BY embedding_status
    LOOP
        RAISE NOTICE 'Статус %: % записей', status_record.embedding_status, status_record.count;
    END LOOP;
END $$;

-- ============================================================================
-- ЭТАП 3: ЧИСТКА ДУБЛЕЙ
-- ============================================================================

-- 3.1 Удалить посты-дубликаты
DELETE FROM posts p
USING _shadow.post_mapping pm
WHERE pm.old_post_id = p.id
  AND pm.new_post_id IS NOT NULL
  AND pm.old_post_id <> pm.new_post_id;

-- Проверка оставшихся постов
DO $$
DECLARE
    remaining_posts INTEGER;
BEGIN
    SELECT COUNT(*) INTO remaining_posts FROM posts;
    RAISE NOTICE 'Оставшихся постов: %', remaining_posts;
END $$;

-- 3.2 Удалить каналы-дубликаты
DELETE FROM channels c
USING _shadow.channel_mapping cm
WHERE cm.old_channel_id = c.id
  AND cm.old_channel_id <> cm.new_channel_id;

-- Проверка оставшихся каналов
DO $$
DECLARE
    remaining_channels INTEGER;
BEGIN
    SELECT COUNT(*) INTO remaining_channels FROM channels;
    RAISE NOTICE 'Оставшихся каналов: %', remaining_channels;
END $$;

-- ============================================================================
-- ЭТАП 4: СХЕМА — УБРАТЬ TENANT_ID (онлайн)
-- ============================================================================

-- 4.1 Удалить FK и колонки (в транзакции)
BEGIN;

-- Channels
ALTER TABLE channels DROP CONSTRAINT IF EXISTS channels_tenant_id_fkey;
ALTER TABLE channels DROP COLUMN IF EXISTS tenant_id;

-- Posts
ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_tenant_id_fkey;
ALTER TABLE posts DROP COLUMN IF EXISTS tenant_id;

COMMIT;

-- 4.2 Уникальные индексы (CONCURRENTLY, вне транзакции!)
-- ⚠️ ВНИМАНИЕ: Выполнять ВНЕ транзакции!
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_channels_tg_global 
    ON channels(tg_channel_id);

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_posts_chan_msg 
    ON posts(channel_id, tg_message_id);

-- 4.3 Удалить старые индексы
DROP INDEX IF EXISTS idx_channels_tenant_id;
DROP INDEX IF EXISTS idx_posts_tenant_id;
DROP INDEX IF EXISTS posts_tenant_id_channel_id_telegram_message_id_key;
DROP INDEX IF EXISTS ux_channels_tg;  -- старый неглобальный

-- ============================================================================
-- ЭТАП 5: RLS ПОЛИТИКИ (без tenant_id)
-- ============================================================================

-- 5.1 Обновить политику для posts
DROP POLICY IF EXISTS posts_by_subscription ON posts;

CREATE POLICY posts_by_subscription ON posts
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM user_channel uc
        JOIN users u ON u.id = uc.user_id
        WHERE uc.channel_id = posts.channel_id
          AND uc.is_active = true
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
    )
);

-- 5.2 Обновить политику для post_enrichment
DROP POLICY IF EXISTS enrichment_by_subscription ON post_enrichment;

CREATE POLICY enrichment_by_subscription ON post_enrichment
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_enrichment.post_id
          AND uc.is_active = true
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
    )
);

-- 5.3 Обновить политику для post_media
DROP POLICY IF EXISTS post_media_by_subscription ON post_media;

CREATE POLICY post_media_by_subscription ON post_media
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_media.post_id
          AND uc.is_active = true
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
    )
);

-- ============================================================================
-- ЭТАП 6: ЗАВЕРШЕНИЕ
-- ============================================================================

-- 6.1 Отключить аварийный триггер
DROP TRIGGER IF EXISTS trg_posts_map_channel ON posts;
DROP FUNCTION IF EXISTS _shadow.map_channel_before_insert();

-- 6.2 Инструментальные индексы
CREATE INDEX IF NOT EXISTS ix_indexing_status_embed 
    ON indexing_status(embedding_status);
CREATE INDEX IF NOT EXISTS ix_indexing_status_graph 
    ON indexing_status(graph_status);

-- 6.3 Комментарии к таблицам
COMMENT ON TABLE channels IS 'Глобальные каналы (без tenant_id), доступ через user_channel';
COMMENT ON TABLE posts IS 'Глобальные посты (без tenant_id), доступ через user_channel + RLS';
COMMENT ON SCHEMA _shadow IS 'Артефакты миграции на глобальные каналы/посты';

-- ============================================================================
-- ПРОВЕРКИ ПОСЛЕ МИГРАЦИИ
-- ============================================================================

-- Проверка дубликатов
DO $$
DECLARE
    channel_duplicates INTEGER;
    post_duplicates INTEGER;
BEGIN
    -- Дубликаты каналов
    SELECT COUNT(*) INTO channel_duplicates
    FROM (
        SELECT tg_channel_id, COUNT(*) 
        FROM channels 
        GROUP BY tg_channel_id 
        HAVING COUNT(*) > 1
    ) dupes;
    
    -- Дубликаты постов
    SELECT COUNT(*) INTO post_duplicates
    FROM (
        SELECT channel_id, tg_message_id, COUNT(*) 
        FROM posts 
        GROUP BY channel_id, tg_message_id 
        HAVING COUNT(*) > 1
    ) dupes;
    
    IF channel_duplicates > 0 THEN
        RAISE WARNING 'Найдено % дубликатов каналов!', channel_duplicates;
    ELSE
        RAISE NOTICE 'Дубликатов каналов: 0 ✓';
    END IF;
    
    IF post_duplicates > 0 THEN
        RAISE WARNING 'Найдено % дубликатов постов!', post_duplicates;
    ELSE
        RAISE NOTICE 'Дубликатов постов: 0 ✓';
    END IF;
END $$;

-- Проверка сирот (orphan записи)
DO $$
DECLARE
    orphan_subs INTEGER;
    orphan_posts INTEGER;
BEGIN
    -- Потерянные подписки
    SELECT COUNT(*) INTO orphan_subs
    FROM user_channel uc
    LEFT JOIN channels c ON c.id = uc.channel_id
    WHERE c.id IS NULL;
    
    -- Потерянные посты
    SELECT COUNT(*) INTO orphan_posts
    FROM posts p
    LEFT JOIN channels c ON c.id = p.channel_id
    WHERE c.id IS NULL;
    
    IF orphan_subs > 0 THEN
        RAISE WARNING 'Найдено % потерянных подписок!', orphan_subs;
    ELSE
        RAISE NOTICE 'Потерянных подписок: 0 ✓';
    END IF;
    
    IF orphan_posts > 0 THEN
        RAISE WARNING 'Найдено % потерянных постов!', orphan_posts;
    ELSE
        RAISE NOTICE 'Потерянных постов: 0 ✓';
    END IF;
END $$;

-- Финальная статистика
DO $$
DECLARE
    stats_record RECORD;
BEGIN
    RAISE NOTICE '=== ФИНАЛЬНАЯ СТАТИСТИКА ===';
    
    FOR stats_record IN 
        SELECT 
            'channels' as table_name,
            COUNT(*) as count
        FROM channels
        UNION ALL
        SELECT 'posts', COUNT(*) FROM posts
        UNION ALL
        SELECT 'user_channel', COUNT(*) FROM user_channel
        UNION ALL
        SELECT 'post_enrichment', COUNT(*) FROM post_enrichment
        UNION ALL
        SELECT 'post_media', COUNT(*) FROM post_media
        ORDER BY table_name
    LOOP
        RAISE NOTICE '%: % записей', stats_record.table_name, stats_record.count;
    END LOOP;
    
    RAISE NOTICE '=== МИГРАЦИЯ ЗАВЕРШЕНА ===';
END $$;
