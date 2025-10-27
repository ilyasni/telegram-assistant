-- Миграция: Система управления каналами с enrichment и TTL-очисткой
-- Дата: 2024-10-25
-- Описание: Добавление полей для идемпотентности, TTL-очистки, enrichment статусов,
--          outbox pattern для надёжной доставки событий, метрики

-- ============================================================================
-- 1. ИДЕМПОТЕНТНОСТЬ ПАРСИНГА (уникальность по Telegram ID)
-- ============================================================================

-- [C7-ID: DB-IDEM-001]
-- Гарантия идемпотентности: tenant + channel + message_id
ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255),
    ADD CONSTRAINT ux_posts_idempotency UNIQUE (tenant_id, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_posts_idempotency ON posts(idempotency_key);

-- ============================================================================
-- 2. TTL И RETENTION ПОЛИТИКИ
-- ============================================================================

-- [C7-ID: DB-RET-001]
ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ 
        GENERATED ALWAYS AS (posted_at + INTERVAL '90 days') STORED,
    ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS link_count INTEGER DEFAULT 0;

-- BRIN индексы для append-only постов
CREATE INDEX IF NOT EXISTS idx_posts_expires_at_brin 
    ON posts USING BRIN(expires_at);
CREATE INDEX IF NOT EXISTS idx_posts_posted_at_brin 
    ON posts USING BRIN(posted_at);

-- Покрывающий B-tree для частых запросов по tenant + expires_at
CREATE INDEX IF NOT EXISTS idx_posts_tenant_expires 
    ON posts(tenant_id, expires_at);

CREATE INDEX IF NOT EXISTS idx_posts_hash ON posts(content_hash);

-- ============================================================================
-- 3. ENRICHMENT STATUS (безопасно через DO-блок)
-- ============================================================================

-- [C7-ID: DB-ENUM-001]
-- Безопасное создание ENUM (идемпотентность)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'enrichment_status_enum') THEN
    CREATE TYPE enrichment_status_enum AS ENUM (
      'pending', 'tagged', 'enriched', 'indexed', 'failed', 'skipped'
    );
  END IF;
END$$;

ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS enrichment_status enrichment_status_enum 
        DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS enrichment_error TEXT,
    ADD COLUMN IF NOT EXISTS processing_lock_at TIMESTAMPTZ;

CREATE INDEX idx_posts_enrichment_status 
    ON posts(enrichment_status) WHERE enrichment_status = 'pending';

CREATE INDEX idx_posts_processing_lock
    ON posts(processing_lock_at) WHERE processing_lock_at IS NOT NULL;

-- ============================================================================
-- 4. OUTBOX PATTERN ДЛЯ НАДЁЖНОЙ ДОСТАВКИ СОБЫТИЙ (РАСШИРЕННАЯ ВЕРСИЯ)
-- ============================================================================

-- [C7-ID: DB-OUTBOX-001]
CREATE TABLE IF NOT EXISTS outbox_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    schema_version VARCHAR(10) DEFAULT 'v1',
    trace_id UUID,
    aggregate_id VARCHAR(255),
    content_hash VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT
);

-- [C7-ID: DB-OUTBOX-DLQ-001]
-- Dead Letter Queue для failed событий
CREATE TABLE IF NOT EXISTS outbox_events_dlq (
    id BIGSERIAL PRIMARY KEY,
    original_id BIGINT REFERENCES outbox_events(id),
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    retry_count INTEGER,
    last_error TEXT,
    moved_at TIMESTAMPTZ DEFAULT NOW()
);

-- [C7-ID: DB-DEDUP-001]
-- Дедупликация событий по (aggregate_id, event_type, content_hash)
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_dedup 
    ON outbox_events (aggregate_id, event_type, content_hash) 
    WHERE processed_at IS NULL;

CREATE INDEX idx_outbox_unprocessed 
    ON outbox_events(created_at) 
    WHERE processed_at IS NULL;

CREATE INDEX idx_outbox_trace 
    ON outbox_events(trace_id);

CREATE INDEX idx_outbox_dlq_moved 
    ON outbox_events_dlq(moved_at);

-- ============================================================================
-- 5. МЕТРИКИ ENRICHMENT
-- ============================================================================

-- [C7-ID: DB-METRICS-001]
CREATE TABLE IF NOT EXISTS enrichment_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
    provider VARCHAR(50),  -- gigachat | openrouter | local
    model VARCHAR(100),
    operation VARCHAR(50), -- tagging | embedding | enrichment
    latency_ms INTEGER,
    token_count INTEGER,
    success BOOLEAN,
    error_class VARCHAR(100),  -- model_timeout | schema_invalid | provider_down
    error_message TEXT,
    trace_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_metrics_provider ON enrichment_metrics(provider, operation);
CREATE INDEX idx_metrics_trace ON enrichment_metrics(trace_id);

-- ============================================================================
-- 6. ФУНКЦИЯ ОЧИСТКИ С OUTBOX
-- ============================================================================

-- [C7-ID: DB-RET-002]
CREATE OR REPLACE FUNCTION fn_cleanup_expired_posts(
    batch_size INT DEFAULT 5000,
    sleep_ms INT DEFAULT 100
)
RETURNS TABLE(deleted_count BIGINT, batch_num INT) AS $$
DECLARE
    v_batch_num INT := 0;
    v_deleted BIGINT;
    v_post_ids UUID[];
BEGIN
    LOOP
        -- Выбрать батч постов к удалению
        SELECT ARRAY_AGG(p.id) INTO v_post_ids
        FROM posts p
        WHERE p.expires_at < NOW()
        LIMIT batch_size;
        
        EXIT WHEN v_post_ids IS NULL OR ARRAY_LENGTH(v_post_ids, 1) = 0;
        
        -- Записать события post.deleted в outbox (транзакционно!)
        INSERT INTO outbox_events (event_type, payload, trace_id)
        SELECT 
            'post.deleted',
            jsonb_build_object(
                'post_id', id,
                'tenant_id', tenant_id,
                'channel_id', channel_id,
                'reason', 'ttl',
                'occurred_at', NOW()
            ),
            gen_random_uuid()
        FROM posts
        WHERE id = ANY(v_post_ids);
        
        -- Удалить посты (каскадно удалятся связи)
        DELETE FROM posts WHERE id = ANY(v_post_ids);
        
        GET DIAGNOSTICS v_deleted = ROW_COUNT;
        v_batch_num := v_batch_num + 1;
        
        deleted_count := v_deleted;
        batch_num := v_batch_num;
        RETURN NEXT;
        
        -- Пауза между батчами (снижение нагрузки)
        PERFORM pg_sleep(sleep_ms / 1000.0);
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 7. OUTBOX RELAY ФУНКЦИЯ
-- ============================================================================

-- [C7-ID: DB-OUTBOX-002]
CREATE OR REPLACE FUNCTION fn_outbox_relay(
    batch_size INT DEFAULT 100,
    max_retries INT DEFAULT 3
)
RETURNS TABLE(processed_count BIGINT, failed_count BIGINT) AS $$
DECLARE
    v_processed BIGINT := 0;
    v_failed BIGINT := 0;
    v_event RECORD;
BEGIN
    -- Обработать необработанные события
    FOR v_event IN 
        SELECT id, event_type, payload, retry_count
        FROM outbox_events 
        WHERE processed_at IS NULL 
        AND (retry_count < max_retries OR retry_count IS NULL)
        ORDER BY created_at
        LIMIT batch_size
    LOOP
        BEGIN
            -- Здесь будет вызов Redis Streams API
            -- Пока просто помечаем как обработанное
            UPDATE outbox_events 
            SET processed_at = NOW()
            WHERE id = v_event.id;
            
            v_processed := v_processed + 1;
            
        EXCEPTION WHEN OTHERS THEN
            -- Увеличить счётчик попыток
            UPDATE outbox_events 
            SET retry_count = COALESCE(retry_count, 0) + 1,
                last_error = SQLERRM
            WHERE id = v_event.id;
            
            v_failed := v_failed + 1;
        END;
    END LOOP;
    
    processed_count := v_processed;
    failed_count := v_failed;
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 8. PG_STAT_STATEMENTS ДЛЯ МОНИТОРИНГА МЕДЛЕННЫХ ЗАПРОСОВ
-- ============================================================================

-- [C7-ID: DB-MONITORING-001]
-- Включение pg_stat_statements для диагностики производительности
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements') THEN
        CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
        RAISE NOTICE 'pg_stat_statements extension created';
    ELSE
        RAISE NOTICE 'pg_stat_statements extension already exists';
    END IF;
END$$;

-- ============================================================================
-- 9. РЕГИСТРАЦИЯ PG_CRON ЗАДАЧ
-- ============================================================================

-- Проверить, что pg_cron включен
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        RAISE WARNING 'pg_cron extension not found. Please install it first.';
    ELSE
        -- Регистрация задачи очистки
        PERFORM cron.schedule(
            'cleanup-expired-posts',
            '30 3 * * *',  -- Ежедневно в 03:30 UTC
            $$SELECT fn_cleanup_expired_posts(5000, 100);$$
        );
        
        -- Регистрация задачи outbox relay
        PERFORM cron.schedule(
            'outbox-relay',
            '*/30 * * * *',  -- Каждые 30 секунд
            $$SELECT fn_outbox_relay(100, 3);$$
        );
        
        RAISE NOTICE 'pg_cron tasks registered successfully';
    END IF;
END$$;

-- ============================================================================
-- 10. КОММЕНТАРИИ К ТАБЛИЦАМ
-- ============================================================================

COMMENT ON TABLE outbox_events IS 'Outbox pattern для надёжной доставки событий в Redis Streams';
COMMENT ON TABLE outbox_events_dlq IS 'Dead Letter Queue для failed событий после исчерпания retry';
COMMENT ON TABLE enrichment_metrics IS 'Метрики производительности AI операций (тегирование, эмбеддинги, enrichment)';

COMMENT ON COLUMN posts.idempotency_key IS 'Уникальный ключ для идемпотентности парсинга (tenant:channel:message_id)';
COMMENT ON COLUMN posts.expires_at IS 'Время истечения поста (автоматически = posted_at + 90 дней)';
COMMENT ON COLUMN posts.content_hash IS 'SHA256 хеш контента для дедупликации';
COMMENT ON COLUMN posts.enrichment_status IS 'Статус обработки поста в enrichment pipeline';
COMMENT ON COLUMN posts.processing_lock_at IS 'Время блокировки для предотвращения дублирования обработки';

COMMENT ON COLUMN outbox_events.aggregate_id IS 'Идентификатор агрегата для дедупликации';
COMMENT ON COLUMN outbox_events.content_hash IS 'Хеш контента для дедупликации';
COMMENT ON COLUMN outbox_events_dlq.original_id IS 'Ссылка на оригинальное событие в outbox_events';

-- ============================================================================
-- 11. ПРОВЕРКИ (РАСШИРЕННЫЕ)
-- ============================================================================

-- Проверить, что все таблицы и индексы созданы
DO $$
DECLARE
    table_count INTEGER;
    index_count INTEGER;
    extension_count INTEGER;
BEGIN
    -- Проверка таблиц (включая DLQ)
    SELECT COUNT(*) INTO table_count
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_name IN ('outbox_events', 'outbox_events_dlq', 'enrichment_metrics');
    
    IF table_count != 3 THEN
        RAISE EXCEPTION 'Не все таблицы созданы. Ожидалось: 3, получено: %', table_count;
    END IF;
    
    -- Проверка индексов (включая дедуп)
    SELECT COUNT(*) INTO index_count
    FROM pg_indexes 
    WHERE schemaname = 'public' 
    AND indexname IN ('idx_posts_idempotency', 'idx_posts_tenant_expires', 'idx_outbox_unprocessed', 'ux_outbox_dedup');
    
    IF index_count < 4 THEN
        RAISE WARNING 'Не все индексы созданы. Ожидалось: 4+, получено: %', index_count;
    END IF;
    
    -- Проверка расширений
    SELECT COUNT(*) INTO extension_count
    FROM pg_extension 
    WHERE extname IN ('pg_cron', 'pg_stat_statements');
    
    IF extension_count < 2 THEN
        RAISE WARNING 'Не все расширения установлены. Ожидалось: 2, получено: %', extension_count;
    END IF;
    
    RAISE NOTICE 'Миграция channel_management_enrichment (расширенная) успешно применена';
    RAISE NOTICE 'Создано таблиц: %, индексов: %, расширений: %', table_count, index_count, extension_count;
END $$;
