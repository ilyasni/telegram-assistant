# Тестирование нововведений аудита медиа

**Версия**: 1.0 | **Дата**: 2025-01-28

## Контекст

Документация по тестированию нововведений аудита парсинга медиа:
- Поддержка медиа-альбомов
- Интеграция Vision в Tagging
- Метрики обработки медиа
- Media_sha256_list в событиях

## Предварительные требования

1. **Запущенные сервисы**:
   ```bash
   docker compose up -d
   ```

2. **Переменные окружения**:
   ```bash
   DATABASE_URL=postgresql://postgres:postgres@supabase-db:5432/postgres
   REDIS_URL=redis://redis:6379
   FEATURE_VISION_ENABLED=true
   ```

## Быстрый тест реальных данных

### 1. Проверка состояния БД

```bash
# Проверка постов с медиа
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    p.id,
    p.has_media,
    LENGTH(p.content) as text_length,
    (SELECT COUNT(*) FROM post_media_map pmm WHERE pmm.post_id = p.id) as media_count
FROM posts p
WHERE p.has_media = true
ORDER BY p.created_at DESC
LIMIT 10;
"

# Проверка media_objects
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as total, COUNT(DISTINCT mime) as unique_mimes
FROM media_objects;
"

# Проверка post_media_map
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total_links,
    COUNT(DISTINCT post_id) as posts_with_media,
    COUNT(DISTINCT file_sha256) as unique_media
FROM post_media_map;
"
```

### 2. Поиск поста с коротким текстом + медиа

```bash
# Посты для тестирования новой логики tagging
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    p.id::text,
    LENGTH(p.content) as text_length,
    (SELECT COUNT(*) FROM post_media_map pmm WHERE pmm.post_id = p.id) as media_count
FROM posts p
WHERE p.has_media = true
AND LENGTH(COALESCE(p.content, '')) < 80
ORDER BY p.created_at DESC
LIMIT 5;
"
```

### 3. Проверка Vision результатов

```bash
# Посты с Vision анализом
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    p.id::text,
    pe.vision_provider,
    pe.vision_analyzed_at,
    pe.vision_description IS NOT NULL as has_description,
    pe.vision_ocr_text IS NOT NULL as has_ocr
FROM posts p
JOIN post_enrichment pe ON pe.post_id = p.id
WHERE pe.vision_analyzed_at IS NOT NULL
ORDER BY pe.vision_analyzed_at DESC
LIMIT 5;
"
```

## Полный E2E тест пайплайна

### Шаг 1: Выбор тестового поста

```bash
# Найдём пост с медиа и Vision результатами
POST_ID=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -A -c "
SELECT p.id::text
FROM posts p
JOIN post_enrichment pe ON pe.post_id = p.id
WHERE p.has_media = true
AND pe.vision_analyzed_at IS NOT NULL
ORDER BY p.created_at DESC
LIMIT 1;
")

echo "Тестируем пост: $POST_ID"
```

### Шаг 2: Проверка медиа в БД

```bash
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    mo.file_sha256,
    mo.mime,
    mo.size_bytes / 1024 as size_kb,
    pmm.position,
    pmm.role
FROM post_media_map pmm
JOIN media_objects mo ON pmm.file_sha256 = mo.file_sha256
WHERE pmm.post_id = '$POST_ID'
ORDER BY pmm.position;
"
```

### Шаг 3: Проверка Vision результатов

```bash
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    vision_provider,
    vision_model,
    vision_analyzed_at,
    SUBSTRING(vision_description, 1, 100) as description_preview,
    SUBSTRING(vision_ocr_text, 1, 100) as ocr_preview,
    vision_is_meme
FROM post_enrichment
WHERE post_id = '$POST_ID' AND kind = 'vision';
"
```

### Шаг 4: Проверка Tagging результатов

```bash
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    provider,
    data->>'tags' as tags_json,
    created_at
FROM post_enrichment
WHERE post_id = '$POST_ID' AND kind = 'tags'
ORDER BY created_at DESC
LIMIT 1;
"
```

### Шаг 5: Проверка событий posts.parsed

```bash
# Проверяем Redis Stream
docker compose exec -T redis redis-cli XLEN stream:posts:parsed

# Ищем событие для поста (последние 100 событий)
docker compose exec -T redis redis-cli XREVRANGE stream:posts:parsed + - COUNT 100 | \
grep -A 5 "$POST_ID" | head -20
```

## Пример успешного прохождения пайплайна

### Пример 1: Пост с альбомом (несколько медиа)

```json
{
  "post_id": "123e4567-e89b-12d3-a456-426614174000",
  "stages": {
    "post_found": {
      "channel_id": "channel-uuid",
      "has_media": true,
      "text_length": 45
    },
    "media_files": [
      {
        "sha256": "abc123...",
        "mime": "image/jpeg",
        "size_kb": 125.5,
        "position": 0,
        "role": "primary"
      },
      {
        "sha256": "def456...",
        "mime": "image/jpeg",
        "size_kb": 98.2,
        "position": 1,
        "role": "attachment"
      }
    ],
    "vision_analysis": {
      "provider": "gigachat",
      "has_description": true,
      "has_ocr": false,
      "description_preview": "На изображении показана инфографика с данными..."
    },
    "tagging": {
      "provider": "gigachat",
      "tags_count": 5,
      "tags": ["финансы", "инфографика", "данные", "аналитика", "график"]
    },
    "parsed_event": {
      "has_media_sha256_list": true,
      "media_sha256_count": 2,
      "media_sha256_list": ["abc123...", "def456..."]
    },
    "validation": {
      "media_processed": true,
      "vision_completed": true,
      "tagging_completed": true,
      "event_has_media_sha256": true,
      "short_text_with_media": true
    }
  },
  "success": true
}
```

### Пример 2: Пост с коротким текстом + медиа

**Важно**: Нововведение - такие посты теперь НЕ пропускаются при тегировании!

**До изменений**:
- Пост с текстом < 80 символов → пропуск при tagging
- Даже если есть медиа с Vision описанием

**После изменений**:
- Пост с текстом < 80 символов + медиа → обработка продолжается
- Vision описание используется для обогащения текста при tagging

```json
{
  "post_id": "789e0123-e89b-12d3-a456-426614174001",
  "stages": {
    "post_found": {
      "text_length": 35,
      "has_media": true
    },
    "media_files": [
      {
        "sha256": "ghi789...",
        "mime": "image/jpeg",
        "size_kb": 210.3
      }
    ],
    "vision_analysis": {
      "has_description": true,
      "description_preview": "На фотографии изображён график роста продаж..."
    },
    "tagging": {
      "tags": ["продажи", "график", "бизнес", "аналитика"],
      "note": "Теги получены с использованием Vision описания"
    },
    "validation": {
      "short_text_with_media": true,
      "tagging_completed": true
    }
  }
}
```

## Проверка метрик Prometheus

```bash
# Метрики обработки медиа
curl -s http://localhost:8001/metrics | grep media_processing

# Ожидаемые метрики:
# media_processing_duration_seconds{media_type="photo",status="success"}
# media_processing_total{media_type="photo",status="success"}
# media_albums_processed_total{status="success"}
# media_processing_failed_total{reason="timeout"}
```

## Проверка логов

```bash
# Логи обработки медиа
docker compose logs telethon-ingest | grep -i "media processed"

# Логи Vision интеграции в tagging
docker compose logs worker | grep -i "vision enrichment"

# Логи обработки альбомов
docker compose logs telethon-ingest | grep -i "album\|grouped"
```

## Автоматический скрипт тестирования

```bash
# Запуск комплексного теста
python scripts/test_media_audit_features.py --check-real-data

# Тест конкретного поста
python scripts/test_media_audit_features.py --test-post-id <uuid>

# Полный E2E тест
python scripts/test_media_audit_features.py --test-post-id <uuid> --full
```

## Чек-лист успешного теста

- [ ] Посты с альбомами обрабатываются (несколько медиа файлов)
- [ ] Посты с коротким текстом + медиа НЕ пропускаются при tagging
- [ ] Vision результаты используются для обогащения текста при tagging
- [ ] События posts.parsed содержат media_sha256_list
- [ ] Метрики Prometheus обновляются (media_processing_*)
- [ ] Медиа сохраняются в media_objects и post_media_map
- [ ] Vision анализ выполняется для всех медиа файлов

## Troubleshooting

### Пост не обрабатывается

1. Проверьте наличие медиа:
   ```sql
   SELECT * FROM post_media_map WHERE post_id = '<post_id>';
   ```

2. Проверьте события:
   ```bash
   docker compose logs telethon-ingest | grep "<post_id>"
   ```

### Vision не обогащает tagging

1. Проверьте Vision результаты:
   ```sql
   SELECT * FROM post_enrichment WHERE post_id = '<post_id>' AND kind = 'vision';
   ```

2. Проверьте логи tagging:
   ```bash
   docker compose logs worker | grep "vision enrichment"
   ```

### Альбомы не обрабатываются

1. Проверьте тип медиа в логах:
   ```bash
   docker compose logs telethon-ingest | grep "MessageMediaGroup\|album"
   ```

2. Проверьте метрики альбомов:
   ```bash
   curl -s http://localhost:8001/metrics | grep media_albums_processed_total
   ```

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28

