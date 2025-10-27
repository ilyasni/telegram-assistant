-- Migration: Auto-create indexing_status on post insert
-- Date: 2025-01-01
-- Description: Context7 + Supabase best practices - автоматическое создание indexing_status
--              через триггер при вставке поста

-- ============================================================================
-- 1. ФУНКЦИЯ ДЛЯ СОЗДАНИЯ indexing_status
-- ============================================================================

CREATE OR REPLACE FUNCTION create_indexing_status()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = 'public'
LANGUAGE plpgsql
AS $$
BEGIN
    -- Автоматическое создание indexing_status с дефолтными статусами
    INSERT INTO public.indexing_status (
        post_id,
        embedding_status,
        graph_status,
        processing_started_at,
        retry_count
    )
    VALUES (
        NEW.id,              -- ID нового поста
        'pending',           -- Статус векторизации
        'pending',           -- Статус графовой индексации
        NOW(),               -- Время начала обработки
        0                    -- Счётчик повторов
    )
    ON CONFLICT (post_id) DO NOTHING;  -- Идемпотентность при повторной вставке
    
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION create_indexing_status() IS 
'Context7 best practice: автоматическое создание indexing_status при создании поста';

-- ============================================================================
-- 2. ТРИГГЕР НА ТАБЛИЦУ posts
-- ============================================================================

-- Удаляем старый триггер если существует
DROP TRIGGER IF EXISTS trg_create_indexing_status ON posts;

-- Создаём новый триггер
CREATE TRIGGER trg_create_indexing_status
    AFTER INSERT ON posts
    FOR EACH ROW
    EXECUTE FUNCTION create_indexing_status();

COMMENT ON TRIGGER trg_create_indexing_status ON posts IS 
'Контекст7: автоматическое создание indexing_status при вставке поста';

-- ============================================================================
-- 3. ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ РУЧНОГО СОЗДАНИЯ (для существующих постов)
-- ============================================================================

CREATE OR REPLACE FUNCTION backfill_indexing_status()
RETURNS TABLE(
    posts_processed INTEGER,
    status_created INTEGER
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_processed INTEGER := 0;
    v_created INTEGER := 0;
    v_post RECORD;
BEGIN
    -- Создаём indexing_status для всех постов, у которых её нет
    FOR v_post IN
        SELECT id
        FROM posts
        WHERE NOT EXISTS (
            SELECT 1 FROM indexing_status WHERE post_id = posts.id
        )
    LOOP
        BEGIN
            INSERT INTO indexing_status (
                post_id,
                embedding_status,
                graph_status,
                processing_started_at,
                retry_count
            )
            VALUES (
                v_post.id,
                'pending',
                'pending',
                NOW(),
                0
            );
            v_created := v_created + 1;
        EXCEPTION
            WHEN OTHERS THEN
                -- Пропускаем дубликаты
                NULL;
        END;
        
        v_processed := v_processed + 1;
    END LOOP;
    
    RETURN QUERY SELECT v_processed, v_created;
END;
$$;

COMMENT ON FUNCTION backfill_indexing_status() IS 
'Backfill: создание indexing_status для всех существующих постов';

-- ============================================================================
-- 4. ВЫПОЛНЕНИЕ BACKFILL ДЛЯ СУЩЕСТВУЮЩИХ ДАННЫХ
-- ============================================================================

-- Заполняем indexing_status для существующих постов (если есть)
DO $$
DECLARE
    result RECORD;
BEGIN
    SELECT * INTO result FROM backfill_indexing_status();
    RAISE NOTICE 'Backfill indexing_status: processed=%, created=%', 
        result.posts_processed, result.status_created;
END $$;
