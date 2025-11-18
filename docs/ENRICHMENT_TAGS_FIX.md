# Исправление ошибки Enrichment Processing Rate - tags casting

**Дата**: 2025-11-06  
**Context7**: Исправление ошибки `cannot cast type jsonb to text[]` при сохранении enrichment с `kind='tags'`

---

## Проблема

В логах worker обнаружена ошибка:
```
error=cannot cast type jsonb to text[]
kind=tags
```

Ошибка возникала при попытке сохранить enrichment с `kind='tags'` в таблицу `post_enrichment`.

---

## Причина

В БД колонка `tags` имеет тип `ARRAY (_text)`, но в SQLAlchemy версии запроса использовалось неправильное преобразование JSONB в text[].

В SQL запросе использовался синтаксис:
```sql
ARRAY(SELECT jsonb_array_elements_text(EXCLUDED.data->'tags'))
```

Этот синтаксис работает в asyncpg, но в SQLAlchemy с `text()` может вызывать проблемы с типами.

---

## Решение

### Исправлен SQLAlchemy вариант (строки 376-391)

**Было**:
```sql
THEN ARRAY(SELECT jsonb_array_elements_text(EXCLUDED.data->'tags'))
```

**Стало**:
```sql
THEN (SELECT array_agg(value) FROM jsonb_array_elements_text(EXCLUDED.data->'tags') AS value)
```

Использование `array_agg()` вместо `ARRAY(SELECT ...)` обеспечивает правильное преобразование JSONB массива в PostgreSQL `text[]`.

---

## Проверка

### Проверить ошибки в логах:
```bash
docker compose logs worker --since 10m | grep -i "error.*tags\|cannot cast"
```

### Проверить успешные сохранения:
```bash
docker compose logs worker --since 10m | grep -i "enrichment.*tags.*saved\|tags.*upserted"
```

### Проверить метрики:
```bash
docker compose exec worker python -c "
from prometheus_client import CollectorRegistry, generate_latest
registry = CollectorRegistry()
# Проверка метрик enrichment
"
```

---

## Влияние

1. **Исправлена ошибка**: Теперь enrichment с `kind='tags'` сохраняется корректно
2. **Обратная совместимость**: Изменение не влияет на другие kinds (vision, crawl, general)
3. **Производительность**: Использование `array_agg()` не влияет на производительность

---

## Статус

✅ **Исправление применено**
- Файл обновлен: `shared/python/shared/repositories/enrichment_repository.py`
- Добавлен volume mount для `shared/` в `docker-compose.yml` для hot reload
- Пакет `shared` переустановлен в контейнере
- Worker перезапущен

**Примечание**: Для применения изменений в `shared/` пакете необходимо:
1. Добавить volume mount в `docker-compose.yml` (выполнено)
2. Переустановить пакет: `docker compose exec worker pip install -e /app/shared/python --force-reinstall --no-deps`
3. Перезапустить worker: `docker compose restart worker`

---

## Связанные файлы

- `shared/python/shared/repositories/enrichment_repository.py` - основной файл с исправлением
- `worker/tasks/tag_persistence_task.py` - создает enrichment с `kind='tags'`
- `api/models/database.py` - модель PostEnrichment (tags как JSONB в модели, но ARRAY в БД)

