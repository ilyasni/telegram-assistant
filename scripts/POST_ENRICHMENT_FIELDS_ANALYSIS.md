# Анализ пустых полей в post_enrichment

## Дата
2025-11-06 07:00 MSK

## Контекст
В таблице `post_enrichment` есть столбцы с пустыми значениями (crawl_md, vision_labels, ocr_text, summary).

## Архитектура

### Унифицированная схема (Context7 Best Practice)
Таблица использует унифицированную схему с полем `data` (JSONB) для всех типов обогащений:
- `data` JSONB - основной источник данных
- Legacy поля (crawl_md, vision_labels, ocr_text, summary) - DEPRECATED, для обратной совместимости

### Текущее состояние

**Для kind='vision':**
- ✅ Legacy поля синхронизируются из `data` JSONB (строки 196-212 в EnrichmentRepository)
- ✅ Данные доступны и в `data`, и в legacy полях

**Для kind='general', 'crawl', 'tags':**
- ✅ Данные сохраняются в `data` JSONB
- ❌ Legacy поля НЕ синхронизируются (остаются пустыми)
- ⚠️ Это нормально для новой архитектуры, но может сломать старый код

## Проблема

1. **Legacy поля пустые** - это нормально для новой архитектуры, но:
   - Старый код, который читает `crawl_md`, `vision_labels`, `ocr_text` не получит данные
   - Grafana дашборды могут показывать пустые значения

2. **Данные есть в `data` JSONB**:
   ```json
   {
     "enrichment_data": {...},
     "urls": [...],
     "crawl_md": "...",  // для crawl kind
     "word_count": 100
   }
   ```

## Решение

### Вариант 1: Синхронизация legacy полей (рекомендуется для обратной совместимости)

Обновить `EnrichmentRepository.upsert_enrichment` для синхронизации legacy полей для всех kinds:

```python
# Для kind='crawl':
crawl_md = data.get('crawl_md') or data.get('enrichment_data', {}).get('crawl_data', {}).get('crawl_md')

# Для kind='tags':
tags = data.get('tags') or data.get('enrichment_data', {}).get('tags', [])

# Для kind='general':
summary = data.get('summary') or data.get('enrichment_data', {}).get('summary')
```

### Вариант 2: VIEW для обратной совместимости

Создать VIEW, который читает данные из `data` JSONB:

```sql
CREATE OR REPLACE VIEW post_enrichment_legacy AS
SELECT 
    post_id,
    kind,
    provider,
    status,
    data,
    -- Legacy поля из data JSONB
    COALESCE(
        crawl_md,
        data->>'crawl_md',
        data->'enrichment_data'->'crawl_data'->>'crawl_md'
    ) as crawl_md,
    COALESCE(
        vision_labels,
        data->'labels',
        data->'enrichment_data'->'labels'
    )::jsonb as vision_labels,
    COALESCE(
        ocr_text,
        data->'ocr'->>'text',
        data->'enrichment_data'->'ocr'->>'text'
    ) as ocr_text,
    COALESCE(
        summary,
        data->>'summary',
        data->'enrichment_data'->>'summary'
    ) as summary
FROM post_enrichment;
```

### Вариант 3: Обновить код чтения (рекомендуется для нового кода)

Обновить все места, где читаются legacy поля, чтобы читать из `data` JSONB:

```python
# Вместо:
crawl_md = enrichment.crawl_md

# Использовать:
crawl_md = enrichment.data.get('crawl_md') or enrichment.data.get('enrichment_data', {}).get('crawl_data', {}).get('crawl_md')
```

## Context7 Best Practices

### ✅ JSONB как единый источник данных
- Все данные в `data` JSONB
- Гибкая структура для разных видов обогащений
- Легко расширять без изменения схемы

### ✅ Обратная совместимость
- Legacy поля синхронизируются для vision
- Нужно добавить синхронизацию для других kinds

### ✅ Миграция данных
- Старые данные мигрированы в `data` JSONB
- Новые данные сохраняются только в `data` JSONB

## Рекомендации

1. **Краткосрочно**: Добавить синхронизацию legacy полей в EnrichmentRepository для всех kinds
2. **Долгосрочно**: Обновить весь код для чтения из `data` JSONB
3. **Мониторинг**: Создать VIEW для Grafana дашбордов

## Проверка

```sql
-- Проверить наличие данных в data JSONB
SELECT 
    kind,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE data IS NOT NULL AND data != '{}'::jsonb) as has_data,
    COUNT(*) FILTER (WHERE data->>'crawl_md' IS NOT NULL) as has_crawl_md_in_data,
    COUNT(*) FILTER (WHERE crawl_md IS NOT NULL AND crawl_md != '') as has_crawl_md_legacy
FROM post_enrichment
GROUP BY kind;
```
