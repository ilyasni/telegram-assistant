-- Миграция для outbox_events таблицы
-- Реализует outbox-паттерн для event-driven архитектуры

CREATE TABLE IF NOT EXISTS outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    tenant_id VARCHAR(100),
    stream_key VARCHAR(200) NOT NULL,
    event_data JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
    
    -- Индексы для производительности
    CONSTRAINT outbox_events_status_check CHECK (status IN ('pending', 'sent', 'failed')),
    CONSTRAINT outbox_events_retry_count_check CHECK (retry_count >= 0),
    CONSTRAINT outbox_events_max_retries_check CHECK (max_retries > 0)
);

-- Индексы для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_outbox_events_status ON outbox_events(status);
CREATE INDEX IF NOT EXISTS idx_outbox_events_created_at ON outbox_events(created_at);
CREATE INDEX IF NOT EXISTS idx_outbox_events_next_retry_at ON outbox_events(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_outbox_events_tenant_id ON outbox_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_outbox_events_event_type ON outbox_events(event_type);

-- Составной индекс для pending событий
CREATE INDEX IF NOT EXISTS idx_outbox_events_pending 
ON outbox_events(status, created_at) 
WHERE status = 'pending';

-- Составной индекс для failed событий с retry
CREATE INDEX IF NOT EXISTS idx_outbox_events_failed_retry 
ON outbox_events(status, next_retry_at, retry_count) 
WHERE status = 'failed' AND retry_count < max_retries;

-- Функция для очистки старых событий
CREATE OR REPLACE FUNCTION cleanup_outbox_events()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Удаляем события старше 7 дней
    DELETE FROM outbox_events 
    WHERE created_at < NOW() - INTERVAL '7 days'
    AND status IN ('sent', 'failed');
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    
    -- Логируем очистку
    INSERT INTO system_logs (level, message, metadata)
    VALUES (
        'INFO',
        'Outbox events cleanup completed',
        jsonb_build_object(
            'deleted_count', deleted_count,
            'cleanup_time', NOW()
        )
    );
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Создание таблицы для логов системы (если не существует)
CREATE TABLE IF NOT EXISTS system_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индекс для system_logs
CREATE INDEX IF NOT EXISTS idx_system_logs_created_at ON system_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);

-- Функция для получения статистики outbox
CREATE OR REPLACE FUNCTION get_outbox_stats()
RETURNS TABLE (
    total_events BIGINT,
    pending_events BIGINT,
    sent_events BIGINT,
    failed_events BIGINT,
    avg_processing_time_ms NUMERIC,
    oldest_pending_event TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        COUNT(*) as total_events,
        COUNT(*) FILTER (WHERE status = 'pending') as pending_events,
        COUNT(*) FILTER (WHERE status = 'sent') as sent_events,
        COUNT(*) FILTER (WHERE status = 'failed') as failed_events,
        AVG(EXTRACT(EPOCH FROM (sent_at - created_at)) * 1000) as avg_processing_time_ms,
        MIN(created_at) FILTER (WHERE status = 'pending') as oldest_pending_event
    FROM outbox_events;
END;
$$ LANGUAGE plpgsql;

-- Функция для retry failed событий
CREATE OR REPLACE FUNCTION retry_failed_outbox_events()
RETURNS INTEGER AS $$
DECLARE
    retry_count INTEGER;
BEGIN
    -- Сбрасываем статус для событий, которые можно повторить
    UPDATE outbox_events 
    SET status = 'pending',
        next_retry_at = NULL,
        last_error = NULL
    WHERE status = 'failed' 
    AND retry_count < max_retries
    AND next_retry_at <= NOW();
    
    GET DIAGNOSTICS retry_count = ROW_COUNT;
    
    -- Логируем retry
    INSERT INTO system_logs (level, message, metadata)
    VALUES (
        'INFO',
        'Failed outbox events retry initiated',
        jsonb_build_object(
            'retry_count', retry_count,
            'retry_time', NOW()
        )
    );
    
    RETURN retry_count;
END;
$$ LANGUAGE plpgsql;

-- Создание cron job для очистки (если pg_cron доступен)
-- SELECT cron.schedule('outbox-cleanup', '0 2 * * *', 'SELECT cleanup_outbox_events();');

-- Создание cron job для retry (если pg_cron доступен)
-- SELECT cron.schedule('outbox-retry', '*/5 * * * *', 'SELECT retry_failed_outbox_events();');

-- Комментарии к таблице
COMMENT ON TABLE outbox_events IS 'Outbox events для event-driven архитектуры';
COMMENT ON COLUMN outbox_events.id IS 'Уникальный ID записи в outbox';
COMMENT ON COLUMN outbox_events.event_id IS 'ID события (из event envelope)';
COMMENT ON COLUMN outbox_events.event_type IS 'Тип события (например, auth.login.started)';
COMMENT ON COLUMN outbox_events.tenant_id IS 'ID арендатора';
COMMENT ON COLUMN outbox_events.stream_key IS 'Ключ Redis Stream для публикации';
COMMENT ON COLUMN outbox_events.event_data IS 'Полные данные события в JSON формате';
COMMENT ON COLUMN outbox_events.status IS 'Статус: pending, sent, failed';
COMMENT ON COLUMN outbox_events.retry_count IS 'Количество попыток обработки';
COMMENT ON COLUMN outbox_events.max_retries IS 'Максимальное количество попыток';
COMMENT ON COLUMN outbox_events.last_error IS 'Последняя ошибка при обработке';
COMMENT ON COLUMN outbox_events.created_at IS 'Время создания записи';
COMMENT ON COLUMN outbox_events.sent_at IS 'Время успешной отправки';
COMMENT ON COLUMN outbox_events.next_retry_at IS 'Время следующей попытки retry';
