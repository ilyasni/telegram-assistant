# ✅ Миграция legacy данных завершена

**Дата**: 2025-01-30  
**Статус**: Миграция выполнена

## Что было сделано

### Скрипт миграции
- Создан `scripts/migrate_legacy_enrichment_data.sql`
- Идемпотентный скрипт (можно запускать несколько раз)
- Миграция всех legacy полей в унифицированную структуру `data` JSONB

### Мигрированные поля

**Для kind='tags':**
- `tags` (text[]) → `data->>'tags'`
- `vision_labels` (jsonb) → `data->>'labels'`
- `crawl_md` (text) → `data->'crawl'->>'markdown'`

**Для kind='vision':**
- `vision_labels` → `data->>'labels'`
- `vision_classification` → `data->>'labels'` (fallback)
- `ocr_text` → `data->'ocr'->>'text'`
- `vision_ocr_text` → `data->'ocr'->>'text'` (fallback)
- `vision_description` → `data->>'caption'`
- `vision_is_meme` → `data->>'is_meme'`
- `vision_context` → `data->>'context'`
- `vision_model` → `data->>'model'`
- `vision_tokens_used` → `data->>'tokens_used'`
- `vision_cost_microunits` → `data->>'cost_microunits'`
- `vision_file_id` → `data->>'file_id'`
- `vision_analysis_reason` → `data->>'analysis_reason'`
- `s3_vision_keys` → `data->>'s3_keys'`

**Для kind='crawl':**
- `crawl_md` → `data->'crawl'->>'markdown'`
- `ocr_text` → `data->'ocr'->>'text'`
- `vision_labels` → `data->>'labels'`
- `s3_crawl_keys` → `data->>'s3_keys'`

**Общие поля:**
- `enrichment_provider` → `provider` (если provider пусто)
- `summary` → `data->>'summary'`

---

## Результаты миграции

После выполнения скрипта все legacy данные должны быть перенесены в `data` JSONB.

Для проверки выполните:
```sql
SELECT 
    kind,
    COUNT(*) as total,
    COUNT(CASE WHEN data IS NULL OR data = '{}'::jsonb THEN 1 END) as empty_data,
    COUNT(CASE WHEN data->>'tags' IS NOT NULL THEN 1 END) as migrated_tags,
    COUNT(CASE WHEN data->>'labels' IS NOT NULL THEN 1 END) as migrated_labels,
    COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL THEN 1 END) as migrated_ocr,
    COUNT(CASE WHEN data->'crawl'->>'markdown' IS NOT NULL THEN 1 END) as migrated_crawl
FROM post_enrichment
GROUP BY kind
ORDER BY kind;
```

---

## Следующие шаги (опционально)

После успешной миграции можно:
1. Убедиться, что все данные мигрированы
2. Протестировать чтение данных из `data` JSONB
3. Отключить обновление legacy полей в коде
4. Удалить legacy поля в будущей миграции (когда будет уверенность, что они не нужны)

---

## Важные замечания

- Скрипт идемпотентный - можно запускать несколько раз безопасно
- Скрипт не удаляет legacy поля - они остаются для обратной совместимости
- Код всё ещё обновляет legacy поля параллельно с `data` для совместимости
- Удаление legacy полей требует отдельной миграции после полного перехода

