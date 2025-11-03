# Поток обогащения Vision данных

**Дата**: 2025-11-03

## Как работает обогащение Vision

### 1. Vision Analysis Task сохраняет в БД

**Task**: `VisionAnalysisTask`
**Метод**: `_save_to_db()`
**Репозиторий**: `EnrichmentRepository.upsert_enrichment()`

**Что сохраняется**:
- `post_id` + `kind='vision'` (составной ключ)
- `provider='gigachat'` (или `'ocr_fallback'`)
- `data` (JSONB) - основной формат с полной структурой:
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
      "engine": "gigachat",
      "confidence": 0.95
    },
    "s3_keys": {...},
    "tokens_used": 150,
    ...
  }
  ```

**Где**: `post_enrichment` таблица, запись с `kind='vision'`

### 2. Legacy поля (для обратной совместимости)

Legacy поля (`vision_description`, `vision_classification`, `vision_analyzed_at`) заполняются:
- При миграции данных (миграция `20250130_unify_post_enrichment_schema.py`)
- При необходимости через триггеры (если настроены)

**Важно**: Основной источник данных - поле `data` (JSONB), legacy поля - для обратной совместимости.

### 3. Album Assembler Task читает из БД

**Task**: `AlbumAssemblerTask`
**Метод**: `_get_vision_results()`

**Что читается**:
- Приоритет: поле `data` (JSONB) - новый формат
- Fallback: legacy поля (`vision_description`, `vision_classification`) - для обратной совместимости

**Извлечение данных**:
```python
# Новый формат (приоритет)
description = data_jsonb.get('description') or data_jsonb.get('caption')
labels = data_jsonb.get('labels', [])
is_meme = data_jsonb.get('is_meme', False)
ocr_text = ocr_data.get('text') if isinstance(ocr_data, dict) else None
```

### 4. Агрегация на уровне альбома

**Метод**: `_aggregate_vision_summary()`

Агрегирует результаты от всех постов альбома:
- Объединённое описание (взвешенное по позиции)
- Объединённые labels (дедупликация)
- Объединённый OCR текст
- Флаги (has_meme, has_text)

### 5. Сохранение агрегированных данных

**Альбом-level enrichment**:
- В БД: `media_groups.meta->enrichment` (JSONB)
- В S3: `album/{tenant_id}/{album_id}_vision_summary_v1.json` (сжатый JSON)

**Что сохраняется**:
```json
{
  "vision_summary": "Объединённое описание альбома",
  "vision_labels": ["label1", "label2", ...],
  "ocr_text": "Объединённый OCR текст",
  "has_meme": false,
  "has_text": true,
  "s3_key": "album/.../4_vision_summary_v1.json",
  "assembly_completed_at": "2025-11-03T..."
}
```

## Важно: Два уровня обогащения

### 1. Post-level (посты)
- **Где**: `post_enrichment` (`kind='vision'`)
- **Когда**: После vision анализа каждого поста
- **Кто**: `VisionAnalysisTask` через `EnrichmentRepository`
- **Формат**: `data` (JSONB) + legacy поля

### 2. Album-level (альбомы)
- **Где**: `media_groups.meta->enrichment` + S3
- **Когда**: После анализа всех постов альбома
- **Кто**: `AlbumAssemblerTask`
- **Формат**: Агрегированные данные из всех постов альбома

## Проверка

### Post-level данные в БД
```sql
SELECT 
    pe.post_id,
    pe.data->>'description' as description,
    pe.data->'labels' as labels,
    pe.data->'ocr'->>'text' as ocr_text
FROM post_enrichment pe
WHERE pe.kind = 'vision'
AND pe.post_id = '...';
```

### Album-level данные в БД
```sql
SELECT 
    mg.id,
    mg.meta->'enrichment'->>'vision_summary' as summary,
    mg.meta->'enrichment'->>'s3_key' as s3_key
FROM media_groups mg
WHERE mg.id = 4;
```

## Вывод

✅ **Vision данные сохраняются в БД** через `VisionAnalysisTask`
✅ **Album Assembler читает из БД** (исправлено на новый формат)
✅ **Агрегированные данные** сохраняются на уровне альбома

Обогащение работает на двух уровнях:
1. **Post-level**: в `post_enrichment` (через VisionAnalysisTask)
2. **Album-level**: в `media_groups.meta` + S3 (через AlbumAssemblerTask)

