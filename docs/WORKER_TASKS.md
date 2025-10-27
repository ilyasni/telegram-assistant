# Worker Tasks

Документация по задачам (tasks) в worker-сервисе.

## Tagging Task

**Файл:** `worker/tasks/tagging_task.py`

**Назначение:** Генерация тегов для постов через GigaChat с использованием строгого промпта.

**Особенности:**
- TTL-LRU кеш идемпотентности (10K записей, TTL 24ч)
- Redis-дедупликация для предотвращения повторной обработки
- Строгий промпт: теги должны быть подстроками текста
- Метрики: `tagging_processed_total`, `tagging_cache_size`, `tagging_redis_dedup_hits_total`

**Запуск:**
```bash
python -m worker.tasks.tagging_task
```

## Tag Persistence Task

**Файл:** `worker/tasks/tag_persistence_task.py`

**Назначение:** Сохранение тегов из событий `posts.tagged` в таблицу `post_enrichment`.

**Схема пайплайна:**

```
telethon-ingest → posts.parsed
         ↓
tagging_task (GigaChat) — строгий промпт, TTL-LRU + Redis дедуп
         ↓
posts.tagged (v1: {post_id, tags[], tags_hash})
         ↓
tag_persistence_task → UPSERT post_enrichment(post_id, kind='tags')
```

**Особенности:**

- Идемпотентность по хешу тегов
- UPSERT обновляет запись только при изменении тегов
- Поддержка поля `kind` для разных типов обогащения (tags, vision, ocr)
- Метрики: `tags_persisted_total`, `tags_persist_latency_seconds`, `tags_persist_conflicts_total`

**Запуск:**
```bash
python -m worker.tasks.tag_persistence_task
```

## Enrichment Task

**Файл:** `worker/tasks/enrichment_task.py`

**Назначение:** Обогащение постов через crawl4ai для извлечения дополнительного контента.

**Особенности:**
- Обрабатывает события `posts.tagged`
- Использует crawl4ai для извлечения контента из URL
- Сохраняет результаты в `post_enrichment` с `kind='crawl'`

## Indexing Task

**Файл:** `worker/tasks/indexing_task.py`

**Назначение:** Индексация обогащённых постов в Qdrant и Neo4j.

**Особенности:**
- Обрабатывает события `posts.enriched`
- Создаёт эмбеддинги через GigaChat
- Индексирует в Qdrant с TTL
- Создаёт граф в Neo4j

## Cleanup Task

**Файл:** `worker/tasks/cleanup_task.py`

**Назначение:** Очистка устаревших данных по TTL.

**Особенности:**
- Удаляет expired посты из Qdrant
- Очищает orphan узлы в Neo4j
- Поддерживает checkpoint для возобновления

## Архитектура пайплайна

```
telethon-ingest
    ↓ posts.parsed
tagging_task (GigaChat)
    ↓ posts.tagged
tag_persistence_task (DB)
    ↓
enrichment_task (crawl4ai)
    ↓ posts.enriched
indexing_task (Qdrant + Neo4j)
    ↓ posts.indexed
```

## Метрики и мониторинг

### Tagging Task
- `tagging_processed_total{status}` — количество обработанных постов
- `tagging_cache_size` — размер кеша идемпотентности
- `tagging_redis_dedup_hits_total` — попадания в Redis-дедуп

### Tag Persistence Task
- `tags_persisted_total{status}` — количество сохранённых тегов
- `tags_persist_latency_seconds` — латентность сохранения
- `tags_persist_conflicts_total` — конфликты при UPSERT

### Алерты
```yaml
- alert: TaggingPipelineBroken
  expr: rate(tagging_processed_total{status="success"}[5m]) > 0 
        AND rate(tags_persisted_total{status="success"}[5m]) == 0
  for: 5m
  annotations:
    summary: "Теги генерируются, но не сохраняются в БД"

- alert: TaggingCacheOverflow
  expr: tagging_cache_size > 9000
  for: 10m
  annotations:
    summary: "Кеш идемпотентности близок к переполнению"
```

## Конфигурация

### Environment Variables
```bash
# Redis
REDIS_URL=redis://localhost:6379

# Database
DATABASE_URL=postgresql://postgres:password@localhost:5432/telegram_assistant

# AI Providers
GIGACHAT_API_KEY=your_key
OPENROUTER_API_KEY=your_key

# Feature Flags
LEGACY_REDIS_CONSUMER_ENABLED=false
```

### Docker Compose
```yaml
services:
  tagging-worker:
    image: telegram-assistant-worker
    command: python -m worker.tasks.tagging_task
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql://postgres:password@postgres:5432/telegram_assistant
    depends_on:
      - redis
      - postgres

  tag-persistence-worker:
    image: telegram-assistant-worker
    command: python -m worker.tasks.tag_persistence_task
    environment:
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql://postgres:password@postgres:5432/telegram_assistant
    depends_on:
      - redis
      - postgres
```

## Troubleshooting

### Tagging Task не обрабатывает посты
1. Проверить подключение к Redis: `redis-cli ping`
2. Проверить события в стриме: `redis-cli XLEN posts.parsed`
3. Проверить логи: `docker logs telegram-assistant-worker-1`

### Tag Persistence Task не сохраняет теги
1. Проверить подключение к БД: `psql -U postgres -d telegram_assistant -c "SELECT 1"`
2. Проверить события: `redis-cli XLEN posts.tagged`
3. Проверить метрики: `curl localhost:8000/metrics | grep tags_persisted`

### Зацикливание на одних постах
1. Проверить размер кеша: `curl localhost:8000/metrics | grep tagging_cache_size`
2. Очистить Redis: `redis-cli FLUSHDB`
3. Перезапустить worker

### Некорректные теги
1. Проверить промпт в `worker/prompts/tagging.py`
2. Проверить логи GigaChat API
3. Запустить unit-тесты: `pytest tests/unit/test_tagging_prompt_strict.py`
