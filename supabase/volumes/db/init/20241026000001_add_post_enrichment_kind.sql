-- Добавление поля kind для разделения типов обогащения
-- [C7-ID: DB-MIGRATION-001]

-- Добавление поля kind
ALTER TABLE post_enrichment
    ADD COLUMN IF NOT EXISTS kind text DEFAULT 'tags';

-- Уникальный индекс на (post_id, kind)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'ux_post_enrichment_post_kind'
    ) THEN
        CREATE UNIQUE INDEX CONCURRENTLY ux_post_enrichment_post_kind
            ON post_enrichment (post_id, kind);
    END IF;
END $$;

-- Привести tags к text[] (если сейчас JSONB)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'post_enrichment'
          AND column_name = 'tags'
          AND data_type = 'jsonb'
    ) THEN
        ALTER TABLE post_enrichment
            ALTER COLUMN tags TYPE text[] USING
                CASE
                    WHEN jsonb_typeof(tags::jsonb) = 'array'
                        THEN ARRAY(SELECT jsonb_array_elements_text(tags::jsonb))
                    ELSE ARRAY[]::text[]
                END;
    END IF;
END $$;

-- Комментарии
COMMENT ON COLUMN post_enrichment.kind IS 'Тип обогащения: tags, vision, ocr, crawl';
COMMENT ON INDEX ux_post_enrichment_post_kind IS 'Уникальность по (post_id, kind) для разных типов обогащения';

-- Обновить существующие записи (если есть)
UPDATE post_enrichment 
SET kind = 'tags' 
WHERE kind IS NULL;
