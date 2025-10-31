# Event-Driven Pipeline Setup Guide

## Обзор

Реализован event-driven пайплайн для стабилизации работы с БД:
- `telethon-ingest` публикует события в Redis Streams
- `PostPersistenceWorker` асинхронно обрабатывает события и сохраняет в PostgreSQL
- Полная изоляция sync/async кода

## Архитектура

```
telethon-ingest → stream:posts:parsed → PostPersistenceWorker → PostgreSQL
```

## Компоненты

### 1. Event Bus (Redis Streams)
- **Стрим**: `stream:posts:parsed`
- **Consumer Group**: `post_persist_workers`
- **Синхронный адаптер**: `telethon-ingest/event_bus_sync.py`

### 2. PostPersistenceWorker
- **Файл**: `worker/tasks/post_persistence_task.py`
- **Функция**: Асинхронная обработка событий из Redis Streams
- **Идемпотентность**: Через `dedup_key` в Redis

### 3. Database Factories
- **Sync**: `telethon-ingest/database.py`
- **Async**: `worker/database.py`
- **Фича-флаг**: `USE_ASYNC_DB=true|false`

## Настройка

### 1. Environment Variables

Добавь в `.env`:
```bash
# Database Driver Configuration
USE_ASYNC_DB=true
DB_URL_SYNC=postgresql://telegram_user:password@localhost:5432/postgres
DB_URL_ASYNC=postgresql+asyncpg://telegram_user:password@localhost:5432/postgres

# Redis
REDIS_URL=redis://localhost:6379
```

### 2. Создание Consumer Groups

```bash
python scripts/create_consumer_groups.py --create
```

### 3. Запуск Workers

```bash
# Запуск всех воркеров (включая PostPersistenceWorker)
python worker/run_all_tasks.py
```

## Тестирование

### 1. Быстрый тест пайплайна

```bash
python scripts/test_event_pipeline.py
```

### 2. Смоук-тест

```bash
python scripts/smoke_test_pipeline.py
```

### 3. Комплексная проверка

```bash
python scripts/run_stabilization_checks.py
```

## Мониторинг

### 1. Grafana Dashboard

Импортируй дашборд:
- **Файл**: `monitoring/grafana/dashboards/event_pipeline.json`
- **UID**: `event-pipeline-health`

### 2. Prometheus Metrics

- `post_persist_total{status="ok|error|conflict"}`
- `post_persist_latency_seconds{operation="upsert|ack|batch"}`
- `post_persist_batch_size{status="processed|failed"}`
- `stream_pending_size{stream="posts.parsed", group="post_persist_workers"}`

### 3. Alerts

Добавлены алёрты в `grafana/alerts.yml`:
- `EventPipelineBacklog`: Backlog > 1000 сообщений
- `PostPersistenceFailureRate`: Failure rate > 10%
- `PostPersistenceLatencyHigh`: P95 latency > 10s
- `EventPipelineDLQ`: События в DLQ

## Troubleshooting

### 1. События не обрабатываются

```bash
# Проверь consumer group
redis-cli XINFO GROUPS stream:posts:parsed

# Проверь pending messages
redis-cli XPENDING stream:posts:parsed post_persist_workers
```

### 2. PostPersistenceWorker не запускается

```bash
# Проверь логи
docker logs worker-container

# Проверь соединение с БД
python -c "import asyncpg; print('DB OK')"
```

### 3. Высокая латентность

- Увеличь `batch_size` в PostPersistenceWorker
- Проверь нагрузку на PostgreSQL
- Мониторь метрики в Grafana

## Rollback

Если нужно откатиться к прямой записи в БД:

1. Установи `USE_ASYNC_DB=false`
2. Восстанови прямые вызовы БД в `TelegramIngestionService._save_message()`
3. Останови PostPersistenceWorker

## Best Practices

1. **Идемпотентность**: Все события имеют уникальный `post_id`
2. **Batching**: PostPersistenceWorker обрабатывает события батчами
3. **Error Handling**: Неудачные события попадают в DLQ
4. **Monitoring**: Все операции метрируются в Prometheus
5. **Graceful Shutdown**: Воркеры корректно завершают работу

## Следующие шаги

1. **Фаза 2**: Унификация на `psycopg` v3
2. **Фаза 3**: Миграция S3 на `aioboto3`
3. **Оптимизация**: Настройка batch size и retry policies
4. **Масштабирование**: Горизонтальное масштабирование воркеров
