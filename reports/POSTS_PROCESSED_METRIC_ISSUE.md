# Проблема с метрикой "Posts Processed (24h)"

**Дата**: 2025-12-03  
**Проблема**: Панель "Posts Processed (24h)" показывает 0, хотя в БД есть 501 пост за последние 24 часа

---

## Диагностика

### Текущая ситуация

1. **В БД есть посты**:
   - Всего постов: 7948
   - За последние 24 часа: 501 пост
   - Последний пост: 2025-12-03 10:04:50
   - За последний час: 14 постов

2. **Метрика в Grafana**:
   - Запрос: `sum(increase(posts_processed_total[24h]))`
   - Показывает: 0 (или очень мало)

3. **Метрики в Prometheus**:
   - `posts_processed_total{stage="parsing"}`: 0
   - `posts_processed_total{stage="tagging"}`: 380
   - `posts_processed_total{stage="enrichment"}`: ~0
   - `posts_processed_total{stage="indexing"}`: ~0

---

## Причина проблемы

### Метрика `posts_processed_total`

Метрика `posts_processed_total` обновляется **только в worker** при обработке постов через пайплайн:
- ✅ **tagging**: Обновляется в `api/worker/tasks/tagging_task.py`
- ✅ **enrichment**: Обновляется в `api/worker/tasks/enrichment_task.py`
- ✅ **indexing**: Обновляется в `api/worker/tasks/indexing_task.py`
- ❌ **parsing**: НЕ обновляется (парсинг происходит в telethon-ingest)

### Парсинг использует другую метрику

Парсинг в `telethon-ingest` использует метрику `posts_parsed_total`, которая:
- Обновляется в `telethon-ingest/tasks/parse_all_channels_task.py`
- Экспортируется из telethon-ingest на порту 8011
- НЕ включается в запрос панели Grafana

---

## Решение

### Вариант 1: Исправить запрос в Grafana (рекомендуется)

Объединить обе метрики в один запрос:

```promql
sum(increase(posts_processed_total[24h])) + sum(increase(posts_parsed_total[24h]))
```

Или более точный вариант с группировкой по стадиям:

```promql
sum(increase(posts_processed_total{stage=~"tagging|enrichment|indexing"}[24h])) + sum(increase(posts_parsed_total[24h]))
```

### Вариант 2: Использовать только метрику парсинга

Если нужно показывать только количество распарсенных постов:

```promql
sum(increase(posts_parsed_total[24h]))
```

### Вариант 3: Использовать количество постов в БД

Если нужно точное количество постов в БД:

```promql
# Нужно создать метрику из БД через exporter
# Или использовать другой источник данных
```

---

## Рекомендация

**Использовать вариант 1** - объединить обе метрики, чтобы показать:
- Сколько постов было распарсено (parsing)
- Сколько постов обработано через пайплайн (tagging, enrichment, indexing)

Это даст полную картину обработки постов за последние 24 часа.

---

## Примечание

Метрика `posts_processed_total{stage="parsing"}` инициализируется в worker, но никогда не обновляется, потому что парсинг происходит в telethon-ingest. Это нормально - парсинг использует свою метрику `posts_parsed_total`.

---

**Статус**: Требуется исправление запроса в Grafana панели

