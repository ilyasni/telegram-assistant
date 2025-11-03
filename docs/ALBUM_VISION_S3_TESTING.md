# Тестирование пайплайна альбомов: Vision → S3

**Дата**: 2025-11-03  
**Статус**: В процессе тестирования

## Обзор

Проверка полного пайплайна обработки альбомов с vision анализом и сохранением в S3.

## Текущий статус

### ✅ Реализовано
1. **Album Assembler Task** — отслеживает сборку альбомов
2. **Vision Summary Aggregation** — агрегирует результаты vision анализа
3. **S3 Integration** — сохранение vision summary в S3
4. **DB Enrichment** — сохранение enrichment в `media_groups.meta`

### ⏳ В процессе тестирования
1. **E2E тест полного пайплайна**:
   - Создание альбома
   - Эмиссия `albums.parsed`
   - Vision анализ элементов
   - Обработка `vision.analyzed`
   - Сборка альбома
   - Сохранение в S3

## Компоненты

### 1. Album Assembler Task

**Файл**: `worker/tasks/album_assembler_task.py`

**Функционал**:
- Слушает события `albums.parsed` и `posts.vision.analyzed`
- Отслеживает прогресс vision анализа элементов альбома
- Агрегирует vision summary на уровне альбома
- Сохраняет в S3: `album/{tenant_id}/{album_id}_vision_summary_v1.json`
- Сохраняет enrichment в БД: `media_groups.meta->enrichment`

**Метрики**:
- `albums_parsed_total` — количество полученных событий albums.parsed
- `albums_assembled_total` — количество собранных альбомов
- `album_assembly_lag_seconds` — задержка сборки альбома
- `album_vision_summary_size_bytes` — размер vision summary в S3
- `album_aggregation_duration_ms` — время агрегации summary

### 2. Vision Results в БД

**Таблица**: `post_enrichment`

**Поля**:
- `vision_description` — описание изображения
- `vision_classification` — классификация (JSONB)
- `vision_is_meme` — является ли мемом
- `vision_ocr_text` — распознанный текст

**Запрос vision результатов**:
```sql
SELECT 
    pe.vision_description,
    pe.vision_classification,
    pe.vision_is_meme,
    pe.vision_ocr_text,
    pe.vision_analyzed_at
FROM post_enrichment pe
WHERE pe.post_id = :post_id
AND pe.vision_analyzed_at IS NOT NULL
ORDER BY pe.vision_analyzed_at DESC
LIMIT 1
```

### 3. S3 Структура

**Ключ**: `album/{tenant_id}/{album_id}_vision_summary_v1.json.gz`

**Формат данных**:
```json
{
  "album_id": 123,
  "grouped_id": 987654321,
  "tenant_id": "uuid",
  "channel_id": "uuid",
  "items_count": 5,
  "items_analyzed": 5,
  "vision_summary": "Aggregated description...",
  "vision_labels": ["label1", "label2"],
  "ocr_text": "Combined OCR text...",
  "has_meme": true,
  "has_text": true,
  "first_analyzed_at": "2025-11-03T10:00:00Z",
  "last_analyzed_at": "2025-11-03T10:05:00Z",
  "assembly_completed_at": "2025-11-03T10:05:00Z",
  "assembly_lag_seconds": 300,
  "schema_version": "1.0"
}
```

**Сжатие**: Автоматическое gzip сжатие через `S3StorageService.put_json(..., compress=True)`

### 4. DB Enrichment

**Поле**: `media_groups.meta->enrichment`

**Структура**:
```json
{
  "enrichment": {
    "vision_summary": "...",
    "vision_labels": [...],
    "ocr_text": "...",
    "has_meme": true,
    "has_text": true,
    "assembly_completed_at": "2025-11-03T10:05:00Z",
    "s3_key": "album/{tenant_id}/{album_id}_vision_summary_v1.json.gz"
  }
}
```

## Тестирование

### Скрипты

1. **`scripts/test_album_vision_s3_e2e.py`** — проверка текущего состояния:
   - Альбомы с enrichment в БД
   - Альбомы в S3
   - Vision события для альбомов
   - Статус album_assembler_task

2. **`scripts/test_album_full_vision_flow.py`** — полный E2E тест:
   - Создание альбома
   - Эмиссия `albums.parsed`
   - Сохранение vision результатов в БД
   - Эмиссия `vision.analyzed` событий
   - Ожидание сборки альбома
   - Проверка сохранения в S3 и БД

### Команды

```bash
# Проверка текущего состояния
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/test_album_vision_s3_e2e.py

# Полный E2E тест
docker exec telegram-assistant-worker-1 python3 /opt/telegram-assistant/scripts/test_album_full_vision_flow.py

# Проверка альбомов в БД
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    mg.id,
    mg.meta->'enrichment'->>'s3_key' as s3_key,
    CASE WHEN mg.meta->'enrichment'->>'vision_summary' IS NOT NULL THEN 'yes' ELSE 'no' END as has_summary
FROM media_groups mg
WHERE mg.meta->'enrichment' IS NOT NULL;
"

# Проверка событий в Redis
docker exec telegram-assistant-redis-1 redis-cli XLEN stream:albums:parsed
docker exec telegram-assistant-redis-1 redis-cli XLEN stream:album:assembled

# Проверка S3
docker exec telegram-assistant-worker-1 python3 -c "
from api.services.s3_storage import S3StorageService
import os
s3 = S3StorageService(**{...})
# list objects
"
```

## Текущие проблемы

1. **Комментарии в SQL** — asyncpg не поддерживает комментарии `#` в SQL запросах
2. **Отсутствие tenant_id в channels** — используется `user_id` как `tenant_id`
3. **Vision результаты не всегда в БД** — нужно сохранять перед обработкой `vision.analyzed` событий

## Следующие шаги

1. ✅ Исправить SQL запросы (убрать комментарии)
2. ⏳ Запустить полный E2E тест
3. ⏳ Проверить сохранение в S3
4. ⏳ Проверить enrichment в БД
5. ⏳ Протестировать на реальных данных из Telegram

