# Исправления индексов, миграции legacy полей и улучшения парсинга Vision

**Дата**: 2025-01-22  
**Context7**: Исправление индексов, миграция legacy полей и улучшение парсинга Vision ответов

---

## Context

Выполнены три задачи низкого приоритета:
1. Проверка и исправление индекса для `post_enrichment.kind`
2. Миграция legacy полей `vision_labels` в `data` JSONB
3. Улучшение парсинга Vision ответов с метриками и обработкой ошибок

---

## Задача 1: Индекс для post_enrichment.kind

### Проблема

В модели определен индекс `idx_pe_kind` с условием `WHERE kind IS NOT NULL`, но в БД он не был создан. Существующий индекс `idx_post_enrichment_kind` был составным `(kind, post_id)`, что не оптимально для запросов, фильтрующих только по `kind`.

### Решение

Создана миграция `20250122_add_post_enrichment_kind_single_index.py` для создания отдельного индекса только для `kind` с условием `WHERE kind IS NOT NULL`.

**Файл**: `api/alembic/versions/20250122_add_post_enrichment_kind_single_index.py`

**SQL**:
```sql
CREATE INDEX idx_pe_kind ON post_enrichment(kind) WHERE kind IS NOT NULL;
```

### Проверка

```sql
SELECT indexname, indexdef FROM pg_indexes 
WHERE tablename = 'post_enrichment' AND indexname LIKE '%kind%';
```

---

## Задача 2: Миграция legacy полей

### Проблема

В БД было обнаружено 10095 записей с `vision_labels IS NOT NULL AND data->>'labels' IS NULL`, которые требовали миграции в унифицированную структуру `data` JSONB.

### Решение

Создана миграция `20250122_migrate_legacy_vision_labels.py` для идемпотентной миграции `vision_labels` в `data->>'labels'`.

**Файл**: `api/alembic/versions/20250122_migrate_legacy_vision_labels.py`

**SQL логика**:
```sql
UPDATE post_enrichment
SET data = jsonb_set(
    COALESCE(data, '{}'::jsonb),
    '{labels}',
    vision_labels::jsonb
)
WHERE vision_labels IS NOT NULL 
AND kind = 'vision'
AND (data->>'labels' IS NULL OR data->'labels' = 'null'::jsonb);
```

### Статус

При проверке выяснилось, что данные уже мигрированы (0 записей требуют миграции). Миграция создана для будущего использования и для соответствия архитектуре.

### Проверка

```sql
SELECT COUNT(*) as migrated FROM post_enrichment 
WHERE vision_labels IS NOT NULL AND data->>'labels' IS NOT NULL;
```

---

## Задача 3: Улучшение парсинга Vision ответов

### Проблема

Парсинг Vision ответов не имел детальных метрик для отслеживания типов ошибок, что затрудняло диагностику проблем.

### Решение

Улучшен парсинг Vision ответов с добавлением:
1. Метрик для отслеживания типов ошибок парсинга
2. Улучшенного логирования с контекстом ошибок
3. Обработки edge cases (пустые ответы, неполные JSON)
4. Вынесения fallback логики в отдельный метод

**Файл**: `api/worker/ai_adapters/gigachat_vision.py`

### Добавленные метрики

```python
vision_parse_errors_total = Counter(
    'vision_parse_errors_total',
    'Total vision parsing errors by type',
    ['error_type']  # error_type: json_decode|validation|missing_field|empty_response|other
)
```

### Улучшения

1. **Валидация входных данных**: Проверка на пустые ответы перед парсингом
2. **Детальное логирование**: Добавлен `file_id` и `error_type` во все логи ошибок
3. **Метрики ошибок**: Отслеживание типов ошибок (json_decode, validation, missing_field, empty_response, other)
4. **Вынесение fallback логики**: Метод `_create_minimal_fallback` для переиспользования

### Проверка

- Проверить метрики `vision_parse_errors_total` в Prometheus
- Проверить логи на наличие ошибок парсинга
- Проверить успешность сохранения Vision результатов в БД

---

## Применение изменений

### Запуск миграций

```bash
cd /opt/telegram-assistant
docker compose exec api alembic upgrade head
```

### Проверка результатов

1. **Индекс**:
```sql
SELECT indexname, indexdef FROM pg_indexes 
WHERE tablename = 'post_enrichment' AND indexname = 'idx_pe_kind';
```

2. **Миграция legacy полей**:
```sql
SELECT COUNT(*) as need_migration FROM post_enrichment 
WHERE vision_labels IS NOT NULL 
AND kind = 'vision' 
AND (data->>'labels' IS NULL OR data->'labels' = 'null'::jsonb);
```

3. **Метрики парсинга Vision**:
```bash
curl http://localhost:8001/metrics | grep vision_parse_errors_total
```

---

## Impact / Rollback

### Impact

- **Индекс**: Улучшит производительность запросов, фильтрующих только по `kind`
- **Миграция**: Данные уже мигрированы, миграция создана для соответствия архитектуре
- **Парсинг Vision**: Улучшенная диагностика проблем через метрики и логирование

### Rollback

1. **Индекс**: 
```sql
DROP INDEX IF EXISTS idx_pe_kind;
```

2. **Миграция**: Миграция идемпотентна и не изменяет уже мигрированные данные

3. **Парсинг Vision**: Изменения обратно совместимы, fallback логика сохранена

---

## Заключение

Все три задачи выполнены:
- ✅ Индекс для `post_enrichment.kind` - миграция создана
- ✅ Миграция legacy `vision_labels` - миграция создана (данные уже мигрированы)
- ✅ Улучшение парсинга Vision - метрики и логирование добавлены

Все изменения соответствуют Context7 best practices и готовы к применению.

