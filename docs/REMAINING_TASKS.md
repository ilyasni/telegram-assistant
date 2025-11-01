# Оставшиеся этапы и задачи

**Дата**: 2025-01-30  
**Статус**: Опциональные улучшения

## ✅ Выполненные этапы (завершены)

1. ✅ Этап 1: Миграция post_enrichment
2. ✅ Этап 3: Завершение миграции медиа на CAS
3. ✅ Этап 4: S3 идемпотентность
4. ✅ Этап 5: Детерминированное кэширование Crawl4ai
5. ✅ Этап 6: Метрики Prometheus
6. ✅ Этап 7: Forwards/Reactions/Replies

---

## 🟢 Оставшиеся задачи (СРЕДНИЙ приоритет)

### 1. Индексы для post_enrichment.kind

**Статус**: Требует проверки

Миграция создала уникальный constraint `ux_post_enrichment_post_kind`, но может отсутствовать обычный индекс для фильтрации по `kind`.

**SQL для проверки:**
```sql
SELECT indexname 
FROM pg_indexes 
WHERE tablename = 'post_enrichment' 
AND indexname LIKE '%kind%';
```

**Если индекса нет, добавить:**
```sql
CREATE INDEX IF NOT EXISTS idx_post_enrichment_kind 
ON post_enrichment(kind) 
WHERE kind IS NOT NULL;
```

**Приоритет**: 🟢 СРЕДНИЙ (влияет на производительность запросов с фильтром по kind)

---

### 2. Индексы для post_forwards, post_reactions, post_replies

**Статус**: ✅ **УЖЕ ЕСТЬ**

Проверка показала, что индексы уже существуют:
- `ix_post_forwards_post_id` ✅
- `ix_post_reactions_post_id` ✅
- `ix_post_replies_post_id` ✅
- `ix_post_replies_reply_to` ✅

**Действие**: Ничего не требуется

---

### 3. Foreign Key Constraints

**Статус**: ✅ **ПРОВЕРЕНЫ И КОРРЕКТНЫ**

Все FK constraints имеют правильные правила CASCADE:
- `post_forwards_post_id_fkey` → CASCADE ✅
- `post_reactions_post_id_fkey` → CASCADE ✅
- `post_replies_post_id_fkey` → CASCADE ✅
- `post_replies_reply_to_post_id_fkey` → CASCADE ✅
- `post_media_map_post_id_fkey` → CASCADE ✅
- `post_media_map_file_sha256_fkey` → NO ACTION ✅

**Действие**: Ничего не требуется

---

### 4. Миграция legacy полей (опционально)

**Статус**: 🟡 ОПЦИОНАЛЬНО

Legacy поля в `post_enrichment` помечены как DEPRECATED, но ещё используются для обратной совместимости:
- `ocr_text` → переместить в `data->>'ocr'->>'text'`
- `vision_labels` → переместить в `data->>'labels'`
- `crawl_md` → переместить в `data->>'crawl'->>'markdown'`

**Текущее состояние:**
- Новые записи сохраняются в `data` JSONB
- Старые записи остаются в legacy полях
- Код обновляет оба места для совместимости

**План миграции (если нужен):**
```sql
-- Миграция ocr_text в data JSONB
UPDATE post_enrichment
SET data = jsonb_set(
    COALESCE(data, '{}'::jsonb),
    '{ocr,text}',
    to_jsonb(ocr_text)
)
WHERE ocr_text IS NOT NULL 
AND kind = 'vision'
AND data->'ocr'->>'text' IS NULL;

-- Миграция vision_labels в data JSONB
UPDATE post_enrichment
SET data = jsonb_set(
    COALESCE(data, '{}'::jsonb),
    '{labels}',
    vision_labels::jsonb
)
WHERE vision_labels IS NOT NULL 
AND kind = 'vision'
AND data->>'labels' IS NULL;

-- Миграция crawl_md в data JSONB
UPDATE post_enrichment
SET data = jsonb_set(
    COALESCE(data, '{}'::jsonb),
    '{crawl,markdown}',
    to_jsonb(crawl_md)
)
WHERE crawl_md IS NOT NULL 
AND kind = 'crawl'
AND data->'crawl'->>'markdown' IS NULL;
```

**Приоритет**: 🟡 НИЗКИЙ (можно отложить, текущее состояние работает)

**Когда выполнять:**
- После полного перехода всех компонентов на новую схему
- Перед удалением legacy полей (если планируется)

---

## 📊 Итоговый статус

### ✅ Готово к использованию
- Все критические этапы завершены
- Индексы для новых таблиц уже есть
- FK constraints корректны

### 🟢 Опциональные улучшения
1. **Индекс для `post_enrichment.kind`** - проверить и добавить при необходимости
2. **Миграция legacy полей** - отложить до полного перехода на новую схему

### 🔍 Рекомендации

1. **Проверить производительность** запросов с фильтром по `kind`:
   ```sql
   EXPLAIN ANALYZE 
   SELECT * FROM post_enrichment WHERE kind = 'vision';
   ```
   Если планы запросов показывают Seq Scan - добавить индекс.

2. **Мониторинг метрик**:
   - Следить за метриками CAS операций
   - Проверять DLQ backlog
   - Мониторить производительность запросов к `post_enrichment`

3. **Тестирование в production**:
   - Проверить работу сохранения медиа в CAS
   - Убедиться, что детерминированное кэширование Crawl4ai работает корректно
   - Проверить идемпотентность S3 операций

---

## Вывод

**Основные этапы завершены. Система готова к использованию.**

Остались только опциональные улучшения:
- Проверка индекса для `kind` (5 минут)
- Миграция legacy полей (можно отложить)

