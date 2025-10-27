-- Context7 best practice: retention policy для telegram_auth_events
-- Политика очистки: 90 дней, псевдонимизация PII через 30 дней
-- Автоматизация через pg_cron в Supabase

-- ============================================================================
-- 1. ОПТИМИЗАЦИЯ ИНДЕКСОВ
-- ============================================================================

-- BRIN для append-only audit таблицы (оптимизация для time-series)
CREATE INDEX IF NOT EXISTS ix_telegram_auth_events_at_brin 
  ON telegram_auth_events USING BRIN (at);

-- Узкий btree под частые запросы по пользователю и дате
CREATE INDEX IF NOT EXISTS ix_telegram_auth_events_user_date
  ON telegram_auth_events (user_id, at DESC);

-- ============================================================================
-- 2. ФУНКЦИЯ ПСЕВДОНИМИЗАЦИИ PII (30 дней)
-- ============================================================================

CREATE OR REPLACE FUNCTION pseudonymize_telegram_auth_events()
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
  affected_count INTEGER;
BEGIN
  -- Context7 best practice: псевдонимизация IP (IPv4: /24, IPv6: /56)
  UPDATE telegram_auth_events
  SET 
    ip = CASE
      WHEN ip IS NULL THEN NULL
      WHEN position(':' in ip) = 0 THEN 
        regexp_replace(ip, E'(\\d+\\.\\d+\\.\\d+)\\.\\d+', E'\\1.0')
      ELSE 
        regexp_replace(ip, E'((?:[0-9a-f]{1,4}:){3})[^:]+.*', E'\\1::/56')
    END,
    user_agent = CASE
      WHEN user_agent IS NULL THEN NULL
      ELSE split_part(user_agent, ' ', 1)
    END,
    meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object('pseudonymized_at', NOW())
  WHERE at < NOW() - INTERVAL '30 days'
    AND at >= NOW() - INTERVAL '90 days'
    AND (meta IS NULL OR NOT (meta ? 'pseudonymized_at'));

  GET DIAGNOSTICS affected_count = ROW_COUNT;

  -- Context7 best practice: логирование в system_logs
  IF affected_count > 0 THEN
    INSERT INTO system_logs (level, message, metadata)
    VALUES (
      'INFO',
      'Telegram auth events pseudonymization completed',
      jsonb_build_object('affected_count', affected_count, 'ts', NOW())
    );
  END IF;

  RETURN affected_count;
END $$;

-- ============================================================================
-- 3. ФУНКЦИЯ ОЧИСТКИ СТАРЫХ СОБЫТИЙ (90 дней)
-- ============================================================================

CREATE OR REPLACE FUNCTION cleanup_telegram_auth_events(
  p_retention_days INTEGER DEFAULT 90,
  p_batch_size INTEGER DEFAULT 50000
)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
  v_deleted INTEGER := 0;
  v_round   INTEGER := 0;
BEGIN
  -- Context7 best practice: батчевое удаление для снижения блокировок
  LOOP
    WITH del AS (
      DELETE FROM telegram_auth_events
      WHERE ctid IN (
        SELECT ctid
        FROM telegram_auth_events
        WHERE at < NOW() - make_interval(days => p_retention_days)
        ORDER BY at
        LIMIT p_batch_size
      )
      RETURNING 1
    )
    SELECT COALESCE(COUNT(*),0) INTO v_round FROM del;

    v_deleted := v_deleted + v_round;

    EXIT WHEN v_round < p_batch_size;
    PERFORM pg_sleep(0.2); -- снять нагрузку с VACUUM/реплики
  END LOOP;

  -- Context7 best practice: логирование в system_logs
  INSERT INTO system_logs (level, message, metadata)
  VALUES (
    'INFO',
    'Telegram auth events cleanup completed',
    jsonb_build_object('deleted_count', v_deleted, 'retention_days', p_retention_days, 'ts', NOW())
  );

  RETURN v_deleted;
END $$;

-- ============================================================================
-- 4. ФУНКЦИЯ СТАТИСТИКИ (для мониторинга)
-- ============================================================================

CREATE OR REPLACE FUNCTION get_telegram_auth_events_stats()
RETURNS TABLE (
  total_events BIGINT,
  events_last_7d BIGINT,
  events_last_30d BIGINT,
  events_last_90d BIGINT,
  pseudonymized_events BIGINT,
  oldest_event TIMESTAMPTZ,
  table_size_pretty TEXT
) LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  SELECT 
    COUNT(*)::bigint,
    COUNT(*) FILTER (WHERE at >= NOW() - INTERVAL '7 days')::bigint,
    COUNT(*) FILTER (WHERE at >= NOW() - INTERVAL '30 days')::bigint,
    COUNT(*) FILTER (WHERE at >= NOW() - INTERVAL '90 days')::bigint,
    COUNT(*) FILTER (WHERE COALESCE(meta,'{}'::jsonb) ? 'pseudonymized_at')::bigint,
    MIN(at),
    pg_size_pretty(pg_total_relation_size('telegram_auth_events'::regclass))
  FROM telegram_auth_events;
END $$;

-- ============================================================================
-- 5. PGC_CRON РАСПИСАНИЕ
-- ============================================================================

-- Context7 best practice: включение pg_cron если еще не включен
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- На всякий случай уберём старые версии
SELECT cron.unschedule('telegram-auth-events-cleanup');
SELECT cron.unschedule('telegram-auth-events-pseudonymize');

-- Ежедневная очистка в 03:30 UTC
SELECT cron.schedule(
  'telegram-auth-events-cleanup',
  '30 3 * * *',
  $$SELECT cleanup_telegram_auth_events(90, 50000);$$
);

-- Еженедельная псевдонимизация по понедельникам в 04:00 UTC
SELECT cron.schedule(
  'telegram-auth-events-pseudonymize',
  '0 4 * * 1',
  $$SELECT pseudonymize_telegram_auth_events();$$
);

-- ============================================================================
-- 6. КОММЕНТАРИИ
-- ============================================================================

COMMENT ON FUNCTION cleanup_telegram_auth_events IS 'Удаляет события авторизации старше retention_days (по умолчанию 90)';
COMMENT ON FUNCTION pseudonymize_telegram_auth_events IS 'Псевдонимизирует IP/UA для событий старше 30 дней';
COMMENT ON FUNCTION get_telegram_auth_events_stats IS 'Статистика событий авторизации для мониторинга';
