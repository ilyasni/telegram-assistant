# Руководство по тестированию миграции post_enrichment

**Дата**: 2025-01-30  
**Версия**: 1.0

## Контекст

Реализована унификация схемы `post_enrichment` с новой моделью:
- Поля: `kind`, `provider`, `params_hash`, `data` (JSONB), `status`, `error`
- Составной PK: `(post_id, kind)`
- Единый репозиторий `EnrichmentRepository`
- Все writers обновлены для использования новой модели

## Предварительные проверки

### 1. Валидация синтаксиса

```bash
# Проверка Python синтаксиса
python3 -m py_compile api/alembic/versions/20250130_unify_post_enrichment_schema.py
python3 -m py_compile shared/python/shared/repositories/enrichment_repository.py

# Запуск валидации
python3 scripts/test_enrichment_validation.py
python3 scripts/validate_migration_sql.py
```

### 2. Проверка импортов

Все зависимости должны быть установлены в Docker контейнерах:
- `structlog`
- `prometheus_client`
- `asyncpg` (для asyncpg.Pool)
- `sqlalchemy` (для AsyncSession)

## Тестирование в Docker окружении

### Шаг 1: Применение миграции

```bash
# В контейнере api
docker compose exec api alembic upgrade head

# Или через скрипт
docker compose exec api python -m alembic upgrade head
```

### Шаг 2: Проверка схемы БД

```bash
# Подключение к БД
docker compose exec postgres psql -U telegram_user -d telegram_assistant

# Выполнение SQL скрипта проверки
\i scripts/test_enrichment_migration.sql
```

### Шаг 3: Запуск unit тестов

```bash
# В контейнере worker или api
docker compose exec worker pytest tests/unit/test_enrichment_repository.py -v
docker compose exec worker pytest tests/unit/test_message_enricher.py -v
```

### Шаг 4: Запуск integration тестов

```bash
docker compose exec worker pytest tests/integration/test_forwards_reactions_replies.py -v
docker compose exec worker pytest tests/integration/test_enrichment_repository_integration.py -v
```

## Ручное тестирование функциональности

### Тест 1: Проверка сохранения через EnrichmentRepository

```python
# В Python консоли (Docker контейнер)
from shared.repositories.enrichment_repository import EnrichmentRepository
from sqlalchemy.ext.asyncio import AsyncSession

# Получить db_session из вашего приложения
repo = EnrichmentRepository(db_session)

# Тест сохранения vision
await repo.upsert_enrichment(
    post_id='test-post-id',
    kind='vision',
    provider='gigachat-vision',
    data={'model': 'gigachat-vision', 'labels': []}
)

# Проверка в БД
# SELECT * FROM post_enrichment WHERE post_id = 'test-post-id' AND kind = 'vision';
```

### Тест 2: Проверка модульности

```python
# Сохранение разных видов обогащений для одного поста
post_id = 'test-post-id'

await repo.upsert_enrichment(post_id=post_id, kind='vision', ...)
await repo.upsert_enrichment(post_id=post_id, kind='tags', ...)
await repo.upsert_enrichment(post_id=post_id, kind='crawl', ...)

# В БД должно быть 3 записи с одинаковым post_id, но разными kind
# SELECT post_id, kind, provider FROM post_enrichment WHERE post_id = 'test-post-id';
```

### Тест 3: Проверка идемпотентности

```python
# Два вызова с одинаковыми данными
params_hash = repo.compute_params_hash(model='test', version='1.0', inputs={})

await repo.upsert_enrichment(..., params_hash=params_hash)
await repo.upsert_enrichment(..., params_hash=params_hash)

# В БД должна быть только одна запись
```

## Проверка миграции данных

### До миграции

```sql
-- Проверка существующих данных
SELECT COUNT(*) FROM post_enrichment;
SELECT COUNT(*) FROM post_enrichment WHERE vision_provider IS NOT NULL;
SELECT COUNT(*) FROM post_enrichment WHERE crawl_md IS NOT NULL;
SELECT COUNT(*) FROM post_enrichment WHERE tags IS NOT NULL;
```

### После миграции

```sql
-- Проверка бекфилла
SELECT kind, COUNT(*) FROM post_enrichment GROUP BY kind;
SELECT kind, COUNT(*) FROM post_enrichment WHERE data IS NOT NULL GROUP BY kind;

-- Проверка структуры data JSONB
SELECT kind, jsonb_object_keys(data) as keys 
FROM post_enrichment 
WHERE data IS NOT NULL 
LIMIT 10;
```

## Проверка интеграции writers

### Vision Analysis Task

1. Отправить событие `stream:posts:vision`
2. Проверить, что данные сохранились:
```sql
SELECT post_id, kind, provider, data->>'model' as model
FROM post_enrichment 
WHERE kind = 'vision' 
ORDER BY updated_at DESC 
LIMIT 5;
```

### Tag Persistence Task

1. Отправить событие `posts.tagged`
2. Проверить сохранение:
```sql
SELECT post_id, kind, data->'tags' as tags
FROM post_enrichment 
WHERE kind = 'tags' 
ORDER BY updated_at DESC 
LIMIT 5;
```

### Crawl4AI Service

1. Отправить событие `posts.crawl`
2. Проверить сохранение:
```sql
SELECT post_id, kind, data->>'crawl_md' as crawl_md_preview
FROM post_enrichment 
WHERE kind = 'crawl' 
ORDER BY updated_at DESC 
LIMIT 5;
```

## Проверка forwards/reactions/replies

### Тест извлечения

```python
from telethon-ingest.services.message_enricher import (
    extract_forwards_details,
    extract_reactions_details,
    extract_replies_details
)

# С мок сообщением
forwards = extract_forwards_details(message)
reactions = extract_reactions_details(message)
replies = extract_replies_details(message, post_id)
```

### Проверка сохранения

```sql
-- Проверка forwards
SELECT COUNT(*) FROM post_forwards;
SELECT * FROM post_forwards LIMIT 5;

-- Проверка reactions
SELECT COUNT(*) FROM post_reactions;
SELECT * FROM post_reactions LIMIT 5;

-- Проверка replies
SELECT COUNT(*) FROM post_replies;
SELECT * FROM post_replies LIMIT 5;
```

## Критерии успешного тестирования

- [ ] Миграция применяется без ошибок
- [ ] Все существующие данные мигрированы (бекфилл выполнен)
- [ ] Новые записи создаются с правильной структурой
- [ ] Модульность работает (разные kind для одного post_id)
- [ ] Идемпотентность работает (повторные upsert не создают дубли)
- [ ] Все writers используют EnrichmentRepository
- [ ] Метрики Prometheus работают
- [ ] Forwards/reactions/replies сохраняются
- [ ] Тесты проходят (unit + integration)

## Откат миграции (если необходимо)

```bash
docker compose exec api alembic downgrade -1
```

**ВАЖНО**: Проверьте, что `downgrade()` правильно восстанавливает данные из JSONB в legacy поля.

## Известные ограничения

1. Тесты требуют Docker окружение (зависимости не установлены локально)
2. Полное тестирование требует реальных Telegram сообщений
3. Метрики Prometheus требуют запущенного приложения

## Следующие шаги после тестирования

1. Мониторинг метрик: `post_enrichment_upsert_total`, `post_enrichment_upsert_errors_total`
2. Проверка логов на ошибки: `grep -i "enrichment" logs/`
3. Проверка производительности запросов: `EXPLAIN ANALYZE SELECT ... FROM post_enrichment WHERE ...`

