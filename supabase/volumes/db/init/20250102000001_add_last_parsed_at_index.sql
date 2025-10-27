-- Migration: Add index on last_parsed_at for scheduler optimization
-- Date: 2025-01-02
-- Description: Context7 best practice - индекс для эффективной сортировки каналов

-- ============================================================================
-- ИНДЕКС ДЛЯ SCHEDULER
-- ============================================================================

-- Partial индекс: только активные каналы с NULL last_parsed_at (приоритетные)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_channels_active_unparsed
    ON channels (last_parsed_at NULLS FIRST)
    WHERE is_active = true AND last_parsed_at IS NULL;

-- Обычный индекс: сортировка по last_parsed_at для инкрементального режима
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_channels_last_parsed_at
    ON channels (last_parsed_at ASC NULLS FIRST)
    WHERE is_active = true;

COMMENT ON INDEX idx_channels_active_unparsed IS 
    'Context7: Приоритизация каналов без last_parsed_at для первичного парсинга';

COMMENT ON INDEX idx_channels_last_parsed_at IS 
    'Context7: Оптимизация сортировки для scheduler (oldest first)';
