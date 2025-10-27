# Политики хранения данных (Retention Policy)

## Аудит-таблицы

### telegram_auth_events
- **Полное хранение**: 90 дней
- **Псевдонимизация PII**: после 30 дней
- **Очистка**: ежедневно в 03:30 UTC
- **Индекс**: BRIN (оптимизация для time-series)

### outbox_events
- **Хранение**: 7 дней
- **Очистка**: ежедневно в 02:00 UTC

### system_logs
- **Хранение**: 30 дней (рекомендуется)

## Контентные данные

### posts
- **Хранение**: 90 дней (настраивается per-tenant)
- **Очистка**: ежедневно через cleanup service

## Проверка статистики

```sql
-- Статистика telegram_auth_events
SELECT * FROM get_telegram_auth_events_stats();

-- Размеры таблиц
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables 
WHERE tablename IN ('telegram_auth_events', 'outbox_events', 'system_logs')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

## Управление pg_cron

```sql
-- Список задач
SELECT * FROM cron.job;

-- Отключить задачу
SELECT cron.unschedule('telegram-auth-events-cleanup');

-- Включить обратно
SELECT cron.schedule(
    'telegram-auth-events-cleanup',
    '30 3 * * *',
    $$SELECT cleanup_telegram_auth_events(90);$$
);
```

## Схема таблицы telegram_auth_events

Ожидаемые колонки:
- `id UUID` — первичный ключ
- `user_id UUID` — ссылка на пользователя
- `event VARCHAR(64)` — тип события
- `reason VARCHAR(255)` — причина события
- `at TIMESTAMPTZ` — время события
- `ip VARCHAR(64)` — IP адрес
- `user_agent VARCHAR(512)` — User-Agent
- `meta JSONB DEFAULT '{}'::jsonb` — дополнительные метаданные

## Мониторинг

### Grafana метрики
- График размера `telegram_auth_events`
- Счетчик событий за 7/30/90 дней
- Метрика успешности cleanup/pseudonymize (из `system_logs`)

### Алерты
- Рост размера таблицы > 1GB
- Ошибки в cleanup функциях
- Отсутствие активности pg_cron задач

## Безопасность

### Псевдонимизация PII
- **IPv4**: обрезка до /24 (192.168.1.0)
- **IPv6**: обрезка до /56 (2001:db8::/56)
- **User-Agent**: только семейство браузера

### Аудит операций
Все операции cleanup и pseudonymize логируются в `system_logs` с метаданными:
- Количество обработанных записей
- Время выполнения
- Параметры retention

## Troubleshooting

### Проверка работы pg_cron
```sql
-- Статус задач
SELECT jobid, jobname, schedule, active, last_run, next_run 
FROM cron.job 
WHERE jobname LIKE '%telegram-auth%';

-- История выполнения
SELECT * FROM cron.job_run_details 
WHERE jobname LIKE '%telegram-auth%' 
ORDER BY start_time DESC 
LIMIT 10;
```

### Ручной запуск
```sql
-- Тестовая очистка (dry-run)
SELECT cleanup_telegram_auth_events(90, 1000);

-- Тестовая псевдонимизация
SELECT pseudonymize_telegram_auth_events();

-- Получение статистики
SELECT * FROM get_telegram_auth_events_stats();
```

### Откат изменений
```sql
-- Отключить pg_cron задачи
SELECT cron.unschedule('telegram-auth-events-cleanup');
SELECT cron.unschedule('telegram-auth-events-pseudonymize');

-- Удалить функции
DROP FUNCTION IF EXISTS cleanup_telegram_auth_events;
DROP FUNCTION IF EXISTS pseudonymize_telegram_auth_events;
DROP FUNCTION IF EXISTS get_telegram_auth_events_stats;

-- Восстановить btree индекс
DROP INDEX IF EXISTS ix_telegram_auth_events_at_brin;
CREATE INDEX ix_telegram_auth_events_at ON telegram_auth_events(at);
```
