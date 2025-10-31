# Индекс скриптов

[C7-ID: CODE-CLEANUP-020] Централизованный индекс всех скриптов проекта

## Maintenance (Обслуживание)

### Очистка данных
- `scripts/cleanup_posts.py` - очистка старых постов по retention политике
- `scripts/retag_posts.py` - перетегирование постов
- `scripts/retag_empty_posts.py` - перетегирование постов без тегов
- `scripts/cleanup_qdrant.py` - очистка Qdrant коллекций

### Миграции
- `telethon-ingest/scripts/migrate_sessions.py` - миграция Telegram сессий
- `telethon-ingest/scripts/apply_migrations.py` - применение миграций БД
- `telethon-ingest/scripts/backfill_tg_channel_id.py` - заполнение tg_channel_id

## Diagnostics (Диагностика)

### Проверка пайплайна
- `scripts/check_pipeline_e2e.py` - end-to-end проверка пайплайна
- `scripts/test_event_pipeline.py` - проверка event pipeline
- `scripts/smoke_test_pipeline.py` - быстрый smoke test
- `scripts/run_stabilization_checks.py` - проверка стабильности системы

### Проверка логов
- `scripts/check_logs_health.py` - проверка здоровья логов
- `scripts/system_health_check.py` - комплексная проверка системы

### Специфичные для сервисов
- `worker/scripts/diag_s3.py` - диагностика S3 storage
- `worker/scripts/diag_vision.py` - диагностика Vision pipeline

## Code Quality

- `scripts/inventory_dead_code.py` - инвентаризация мёртвого кода
- `scripts/cleanup_legacy.py` - очистка legacy кода по меткам

## Тестирование

### E2E тесты
- `scripts/test_e2e_vision.py` - E2E тесты Vision
- `scripts/test_vision_e2e.py` - альтернативный E2E Vision тест
- `scripts/test_vision_pipeline.py` - тест Vision pipeline
- `scripts/test_crawl_trigger.py` - тест crawl trigger

### Интеграционные тесты
- `scripts/test_session_integration.py` - интеграционные тесты сессий

## Утилиты

### Triggers
- `scripts/trigger_parsing.py` - триггер парсинга каналов
- `scripts/generate_parsed_events.py` - генерация тестовых событий

### Fixes
- `scripts/fix_pipeline_issues.py` - исправление проблем пайплайна
- `scripts/update_channel_ids.py` - обновление channel IDs
- `telethon-ingest/scripts/fix_channel_tg_id.py` - исправление tg_channel_id

### Инвайты
- `scripts/invites_cli.py` - CLI для управления инвайтами

### Consumer Groups
- `scripts/create_consumer_groups.py` - создание Redis consumer groups

## S3 Management

- `worker/scripts/setup_s3_lifecycle.py` - настройка S3 lifecycle правил
- `worker/scripts/cleanup_s3_ttl.py` - очистка S3 по TTL
- `worker/scripts/emergency_s3_cleanup.py` - экстренная очистка S3

## Тестирование (Worker)

- `worker/scripts/test_tagging.py` - тест тегирования
- `worker/scripts/process_pending_posts.py` - обработка pending постов

## Backup & Restore

- `telethon-ingest/scripts/backup_scheduler.py` - **DEPRECATED** (см. legacy/)
- `telethon-ingest/scripts/restore_telegram_session.py` - восстановление сессий
- `telethon-ingest/scripts/populate_channel_ids.py` - заполнение channel IDs

