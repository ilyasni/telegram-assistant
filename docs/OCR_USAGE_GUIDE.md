# Руководство по использованию улучшений OCR

**Дата**: 2025-12-05  
**Context7**: Инструкции по использованию всех реализованных компонентов

---

## Context

Все компоненты улучшений OCR реализованы и готовы к использованию. Это руководство описывает, как использовать новые функции.

---

## 1. Автоматические словари OCR

### Как это работает

Автоматические словари обновляются при обработке каждого OCR текста. Термины извлекаются, категоризируются и сохраняются в таблице `ocr_dictionaries`.

### Просмотр словарей

```sql
-- Все термины
SELECT term, category, frequency, confidence
FROM ocr_dictionaries
ORDER BY frequency DESC
LIMIT 100;

-- Термины по категории
SELECT term, frequency
FROM ocr_dictionaries
WHERE category = 'politics'
ORDER BY frequency DESC;

-- Самые частые термины
SELECT term, category, frequency
FROM ocr_dictionaries
WHERE frequency >= 5
ORDER BY frequency DESC;
```

### Мониторинг роста словарей

```sql
-- Статистика по категориям
SELECT 
    category,
    COUNT(*) as terms_count,
    SUM(frequency) as total_frequency,
    AVG(confidence) as avg_confidence
FROM ocr_dictionaries
GROUP BY category
ORDER BY terms_count DESC;
```

---

## 2. OCR Preview в Neo4j

### Поиск постов с OCR

```cypher
// Все посты с OCR
MATCH (p:Post {has_ocr: true})
RETURN p.post_id, p.ocr_preview
LIMIT 10;

// Поиск по OCR preview (пример)
MATCH (p:Post)
WHERE p.ocr_preview CONTAINS 'Бельгия'
RETURN p.post_id, p.ocr_preview;

// Статистика
MATCH (p:Post)
RETURN 
    count(p) as total_posts,
    count(p.has_ocr) as posts_with_ocr,
    count(p.ocr_preview) as posts_with_preview;
```

### Фильтрация по OCR

```cypher
// Посты с OCR и entities
MATCH (p:Post {has_ocr: true})
MATCH (p)-[:MENTIONS]->(e:Entity {source: 'ocr'})
RETURN p.post_id, p.ocr_preview, collect(e.name) as entities;
```

---

## 3. Метрики Prometheus

### Доступные метрики

1. **`ocr_quality_score`** (Histogram)
   - Оценка качества OCR (0-1)
   - Query: `histogram_quantile(0.95, rate(ocr_quality_score_bucket[5m]))`

2. **`ocr_entities_coverage`** (Counter)
   - Покрытие entities
   - Query: `sum(rate(ocr_entities_coverage_total{status="with_entities"}[5m]))`

3. **`ocr_enhancement_impact`** (Histogram)
   - Влияние enhancement
   - Query: `histogram_quantile(0.95, rate(ocr_enhancement_impact_bucket[5m]))`

4. **`ocr_dictionary_updates_total`** (Counter)
   - Обновления словарей
   - Query: `sum(rate(ocr_dictionary_updates_total[5m])) by (category)`

### Проверка метрик

```bash
# Все OCR метрики
curl http://localhost:9090/api/v1/label/__name__/values | grep ocr

# Конкретная метрика
curl "http://localhost:9090/api/v1/query?query=ocr_quality_score"
```

### Grafana Dashboard

Создан дашборд: `grafana/dashboards/ocr_quality.json`

**Панели**:
- OCR Quality Score (p95, p50)
- Entities Coverage Rate
- Enhancement Impact
- Dictionary Updates by Category
- Enhancement Rate
- Enhancement Duration

**Импорт**:
1. Открыть Grafana
2. Dashboards → Import
3. Выбрать файл `grafana/dashboards/ocr_quality.json`

---

## 4. Скрипт переобработки

### Использование

```bash
# Dry-run (без изменений)
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/reprocess_ocr_entities.py --days 7 --limit 10 --dry-run

# Реальная переобработка
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/reprocess_ocr_entities.py --days 7 --limit 10

# Конкретный пост
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/reprocess_ocr_entities.py --post-id <post_id>

# Больше постов
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/reprocess_ocr_entities.py --days 30 --limit 100
```

### Параметры

- `--days N` - Поиск постов за последние N дней
- `--limit N` - Максимальное количество постов
- `--post-id UUID` - Обработать конкретный пост
- `--dry-run` - Только проверка без изменений

### Результаты

Скрипт выводит статистику:
- Всего постов найдено
- Обработано
- Обновлено в БД
- Переиндексировано в Neo4j
- Ошибок

---

## 5. Мониторинг качества OCR

### Prometheus Queries

```promql
# Средний quality score
histogram_quantile(0.50, rate(ocr_quality_score_bucket[5m]))

# Процент текстов с entities
(sum(rate(ocr_entities_coverage_total{status="with_entities"}[5m])) / 
 sum(rate(ocr_entities_coverage_total[5m]))) * 100

# Обновления словарей за час
sum(increase(ocr_dictionary_updates_total[1h])) by (category)
```

### Проверка в реальном времени

```bash
# Текущее качество OCR
curl "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,rate(ocr_quality_score_bucket[5m]))"

# Покрытие entities
curl "http://localhost:9090/api/v1/query?query=sum(rate(ocr_entities_coverage_total{status=\"with_entities\"}[5m]))"
```

---

## 6. Работа с автоматическими словарями

### Просмотр терминов в БД

```sql
-- Топ-20 терминов
SELECT term, category, frequency, confidence, last_seen_at
FROM ocr_dictionaries
ORDER BY frequency DESC, last_seen_at DESC
LIMIT 20;

-- Новые термины (за последний час)
SELECT term, category, frequency
FROM ocr_dictionaries
WHERE last_seen_at > NOW() - INTERVAL '1 hour'
ORDER BY last_seen_at DESC;
```

### Анализ категорий

```sql
-- Статистика по категориям
SELECT 
    COALESCE(category, 'general') as category,
    COUNT(*) as terms,
    SUM(frequency) as total_occurrences,
    AVG(confidence) as avg_confidence
FROM ocr_dictionaries
GROUP BY category
ORDER BY terms DESC;
```

---

## 7. Проверка OCR Preview в Neo4j

### Статистика

```cypher
// Всего постов с OCR
MATCH (p:Post {has_ocr: true})
RETURN count(p) as posts_with_ocr;

// Посты с preview
MATCH (p:Post)
WHERE p.ocr_preview IS NOT NULL
RETURN count(p) as posts_with_preview;
```

### Примеры использования

```cypher
// Найти посты с определенным текстом в OCR
MATCH (p:Post)
WHERE p.ocr_preview CONTAINS 'Европейская комиссия'
RETURN p.post_id, p.ocr_preview, p.posted_at;

// Посты с OCR и их entities
MATCH (p:Post {has_ocr: true})
OPTIONAL MATCH (p)-[:MENTIONS]->(e:Entity {source: 'ocr'})
RETURN p.post_id, p.ocr_preview, collect(DISTINCT e.name) as entities
LIMIT 10;
```

---

## 8. Примеры использования

### Переобработка старых постов

```bash
# 1. Проверить, сколько постов без entities
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT COUNT(*) 
FROM post_enrichment 
WHERE kind = 'vision' 
  AND data->'ocr'->>'text' IS NOT NULL
  AND (data->'ocr'->'entities' IS NULL OR jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) = 0);
"

# 2. Dry-run на небольшой выборке
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/reprocess_ocr_entities.py --days 7 --limit 5 --dry-run

# 3. Реальная переобработка
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/reprocess_ocr_entities.py --days 7 --limit 10
```

### Мониторинг качества

```bash
# Проверка метрик
curl http://localhost:9090/metrics | grep ocr_quality_score

# Просмотр в Grafana
# Открыть дашборд "OCR Quality Dashboard"
```

---

## 9. Troubleshooting

### Словари не обновляются

**Проверка**:
```sql
SELECT COUNT(*) FROM ocr_dictionaries;
```

**Решение**:
- Убедиться, что `auto_dictionaries_enabled=True` в конфигурации
- Проверить логи worker на ошибки

### Entities не извлекаются

**Проверка**:
```bash
# Проверить логи entity extraction
docker logs telegram-assistant-worker-1 | grep "Entity extraction"
```

**Решение**:
- Проверить доступность LLM (gpt2giga-proxy)
- Проверить промпт entity extraction
- Переобработать пост заново

### Метрики не появляются

**Проверка**:
```bash
curl http://localhost:9090/metrics | grep ocr_quality_score
```

**Решение**:
- Метрики появятся после обработки новых постов с OCR
- Проверить, что worker собирает метрики

---

## 10. Best Practices

### Регулярная переобработка

Рекомендуется периодически переобрабатывать старые посты:

```bash
# Еженедельная переобработка (cron)
0 2 * * 0 docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/reprocess_ocr_entities.py --days 7 --limit 50
```

### Мониторинг словарей

Отслеживать рост автоматических словарей:

```sql
-- Еженедельный отчет
SELECT 
    category,
    COUNT(*) as new_terms,
    SUM(frequency) as new_occurrences
FROM ocr_dictionaries
WHERE last_seen_at > NOW() - INTERVAL '7 days'
GROUP BY category;
```

### Оптимизация

- Периодически очищать редкие термины (frequency < 2)
- Анализировать категории для улучшения категоризации
- Настраивать алерты на низкое качество OCR

---

**Готово! Все функции доступны для использования.** 🚀

