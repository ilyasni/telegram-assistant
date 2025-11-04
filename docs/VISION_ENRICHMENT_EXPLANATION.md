# Объяснение обогащения Vision данных

**Дата**: 2025-11-03

## Вопрос

Vision summary сохраняется в S3 — а не должны ли данные распознавания сохраняться в БД? Как происходит обогащение?

## Ответ: Обогащение происходит на двух уровнях

### 1. Post-level обогащение (посты) ✅

**Когда**: После vision анализа каждого поста  
**Кто**: `VisionAnalysisTask`  
**Где**: `post_enrichment` таблица

**Что сохраняется**:
- `kind='vision'`
- `provider='gigachat'` (или `'ocr_fallback'`)
- `data` (JSONB) - **основной формат** с полной структурой:
  ```json
  {
    "model": "GigaChat-Pro",
    "provider": "gigachat",
    "analyzed_at": "2025-11-03T...",
    "classification": "photo",
    "description": "Описание изображения",
    "labels": ["tag1", "tag2"],
    "is_meme": false,
    "ocr": {
      "text": "OCR текст",
      "engine": "gigachat"
    },
    "s3_keys": {...},
    "tokens_used": 150
  }
  ```

**Legacy поля** (для обратной совместимости):
- `vision_description` ← синхронизируется из `data->>'description'`
- `vision_classification` ← синхронизируется из `data->'labels'`
- `vision_analyzed_at` ← синхронизируется из `data->>'analyzed_at'`
- `vision_is_meme` ← синхронизируется из `data->>'is_meme'`
- `vision_ocr_text` ← синхронизируется из `data->'ocr'->>'text'`

**Важно**: Legacy поля заполняются автоматически при сохранении через `EnrichmentRepository` (исправлено).

### 2. Album-level обогащение (альбомы) ✅

**Когда**: После анализа всех постов альбома  
**Кто**: `AlbumAssemblerTask`  
**Где**: 
- БД: `media_groups.meta->enrichment` (JSONB)
- S3: `album/{tenant_id}/{album_id}_vision_summary_v1.json`

**Что сохраняется**:
- Агрегированные данные из всех постов альбома
- Объединённое описание, labels, OCR текст
- Флаги (has_meme, has_text)
- S3 ключ для полного vision summary

## Поток обогащения

```
1. Vision Analysis Task
   ↓
   Анализ медиа через GigaChat Vision API
   ↓
2. Сохранение в БД (post_enrichment)
   ├─ data (JSONB) - основной формат ✅
   └─ Legacy поля - синхронизируются автоматически ✅
   ↓
3. Эмиссия posts.vision.analyzed события
   ↓
4. Album Assembler Task
   ├─ Читает vision результаты из БД (post_enrichment)
   ├─ Агрегирует данные от всех постов альбома
   └─ Сохраняет:
       ├─ В БД: media_groups.meta->enrichment ✅
       └─ В S3: album vision summary ✅
```

## Где хранятся данные

| Уровень | Где | Что | Формат |
|---------|-----|-----|--------|
| **Post-level** | `post_enrichment` | Vision результаты каждого поста | `data` (JSONB) + legacy поля |
| **Album-level** | `media_groups.meta` | Агрегированные данные альбома | JSONB в поле `enrichment` |
| **Album-level** | S3 | Полный vision summary альбома | Сжатый JSON файл |

## Зачем S3 для альбомов?

S3 используется для:
1. **Архивирования** полных vision summaries
2. **Оптимизации запросов** (не нужно читать из БД большие JSON)
3. **Аудита** и восстановления данных
4. **Масштабируемости** (БД не перегружается большими JSON)

**Важно**: Основные данные всё равно в БД (`post_enrichment` для постов, `media_groups.meta` для альбомов).

## Проверка данных

### Post-level (посты)
```sql
SELECT 
    pe.post_id,
    pe.data->>'description' as description,
    pe.data->'labels' as labels,
    pe.vision_description as legacy_desc  -- Должен быть синхронизирован
FROM post_enrichment pe
WHERE pe.kind = 'vision';
```

### Album-level (альбомы)
```sql
SELECT 
    mg.id,
    mg.meta->'enrichment'->>'vision_summary' as summary,
    mg.meta->'enrichment'->>'s3_key' as s3_key
FROM media_groups mg
WHERE mg.id = 4;
```

## Исправления

1. ✅ `_get_vision_results()` обновлён для использования нового формата (`data` JSONB)
2. ✅ `EnrichmentRepository` синхронизирует legacy поля при сохранении
3. ✅ Поддержка fallback на legacy поля для обратной совместимости

## Вывод

✅ **Vision данные сохраняются в БД** (post_enrichment)  
✅ **Album enrichment сохраняется в БД** (media_groups.meta)  
✅ **S3 используется для архивирования** полных vision summaries альбомов

Обогащение работает корректно на обоих уровнях!

