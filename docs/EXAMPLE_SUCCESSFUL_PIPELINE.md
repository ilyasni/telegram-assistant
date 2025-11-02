# Пример успешного прохождения пайплайна с нововведениями

**Дата**: 2025-01-28  
**Post ID**: `93a50d27-f074-4c36-b111-54e74d1586e7`

## Контекст

Демонстрация успешного прохождения полного пайплайна обработки поста с медиа с использованием всех нововведений:
1. ✅ Обработка медиа через MediaProcessor
2. ✅ Сохранение в media_objects и post_media_map
3. ✅ Эмиссия VisionUploadedEventV1
4. ✅ Vision анализ через GigaChat
5. ✅ Tagging с использованием Vision результатов
6. ✅ Media_sha256_list в событиях posts.parsed

## Этап 1: Парсинг и обработка медиа

### Логи MediaProcessor

```
[INFO] Message media processed
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  media_count=1
  media_type=photo
  is_album=false
  duration_seconds=0.234
  trace_id=tenant_123:channel_456:message_789
```

### Результат обработки

```json
{
  "media_files": [
    {
      "sha256": "a1b2c3d4e5f6...",
      "s3_key": "media/tenant/a1/a1b2c3d4e5f6....jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 256000,
      "telegram_file_id": "AgACAgIAAxkBAAI...",
      "telegram_file_unique_id": "AQADzwABY4..."
    }
  ]
}
```

### Сохранение в БД

**media_objects**:
```sql
INSERT INTO media_objects (
  file_sha256, mime, size_bytes, s3_key, s3_bucket, refs_count
) VALUES (
  'a1b2c3d4e5f6...', 'image/jpeg', 256000, 
  'media/tenant/a1/a1b2c3d4e5f6....jpg', 'test-467940', 1
)
ON CONFLICT (file_sha256) DO UPDATE SET
  last_seen_at = NOW(),
  refs_count = media_objects.refs_count + 1;
```

**post_media_map**:
```sql
INSERT INTO post_media_map (
  post_id, file_sha256, position, role
) VALUES (
  '93a50d27-f074-4c36-b111-54e74d1586e7',
  'a1b2c3d4e5f6...',
  0,
  'primary'
)
ON CONFLICT (post_id, file_sha256) DO NOTHING;
```

## Этап 2: Эмиссия события posts.parsed

### Событие PostParsedEventV1

```json
{
  "schema_version": "v1",
  "idempotency_key": "tenant_123:channel_456:message_789",
  "post_id": "93a50d27-f074-4c36-b111-54e74d1586e7",
  "channel_id": "channel_uuid",
  "tenant_id": "tenant_123",
  "text": "Интересная новость о технологиях...",
  "has_media": true,
  "media_sha256_list": ["a1b2c3d4e5f6..."],
  "telegram_post_url": "https://t.me/c/1234567890/12345",
  "posted_at": "2025-01-28T12:00:00Z"
}
```

**✅ Нововведение**: Поле `media_sha256_list` содержит SHA256 всех обработанных медиа файлов.

## Этап 3: Эмиссия VisionUploadedEventV1

### Логи эмиссии

```
[INFO] Vision uploaded event emitted
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  media_count=1
  trace_id=tenant_123:channel_456:message_789
```

### Событие VisionUploadedEventV1

```json
{
  "schema_version": "v1",
  "idempotency_key": "tenant_123:93a50d27-f074-4c36-b111-54e74d1586e7:vision_upload",
  "post_id": "93a50d27-f074-4c36-b111-54e74d1586e7",
  "tenant_id": "tenant_123",
  "media_files": [
    {
      "sha256": "a1b2c3d4e5f6...",
      "s3_key": "media/tenant/a1/a1b2c3d4e5f6....jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 256000
    }
  ],
  "requires_vision": true,
  "trace_id": "tenant_123:channel_456:message_789"
}
```

## Этап 4: Vision анализ

### Логи VisionAnalysisTask

```
[INFO] Vision event processed
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  media_count=1
  analyzed_count=1
  duration_ms=1234
  trace_id=tenant_123:channel_456:message_789
```

### Результаты Vision анализа

**post_enrichment**:
```sql
INSERT INTO post_enrichment (
  post_id, kind, provider, data, status
) VALUES (
  '93a50d27-f074-4c36-b111-54e74d1586e7',
  'vision',
  'gigachat',
  '{
    "description": "На изображении показана инфографика с данными о росте продаж. График демонстрирует увеличение выручки на 25% за последний квартал.",
    "classification": "infographic",
    "is_meme": false,
    "labels": ["график", "данные", "инфографика", "бизнес"],
    "ocr": null
  }',
  'ok'
);
```

## Этап 4.5: Retagging после Vision (новый этап)

### VisionAnalyzedEventV1 с версионированием

```json
{
  "schema_version": "v1",
  "event_type": "posts.vision.analyzed",
  "post_id": "93a50d27-f074-4c36-b111-54e74d1586e7",
  "tenant_id": "tenant_123",
  "vision": {
    "provider": "gigachat",
    "model": "GigaChat-Pro",
    "description": "На изображении показана инфографика...",
    "classification": {"type": "infographic"}
  },
  "vision_version": "vision@2025-01-29#p3",
  "features_hash": "sha256:abc123...",
  "trace_id": "tenant_123:channel_456:message_789"
}
```

### RetaggingTask обработка

**Проверка версий:**
- Если `tags_version` отсутствует или `vision_version > tags_version` → ретеггинг нужен
- Если `features_hash` изменился → ретеггинг нужен

**Логи RetaggingTask:**
```
[INFO] Retagging triggered
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  vision_version=vision@2025-01-29#p3
  old_tags_version=vision@2025-01-01#p1
  trace_id=tenant_123:channel_456:message_789

[INFO] Post retagged successfully
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  old_tags_hash=a1b2c3d4
  new_tags_hash=e5f6g7h8
  changed=true
```

### PostTaggedEventV1 с trigger=vision_retag

```json
{
  "schema_version": "v1",
  "event_type": "posts.tagged",
  "post_id": "93a50d27-f074-4c36-b111-54e74d1586e7",
  "tags": ["финансы", "инфографика", "продажи", "бизнес-аналитика"],
  "tags_hash": "e5f6g7h8...",
  "trigger": "vision_retag",
  "vision_version": "vision@2025-01-29#p3",
  "trace_id": "tenant_123:channel_456:message_789"
}
```

**✅ Нововведение**: `trigger=vision_retag` предотвращает цикл (TaggingTask игнорирует такие события)

## Этап 5: Tagging с Vision обогащением

### Логи TaggingTask

```
[DEBUG] Text short but has media - will use Vision enrichment if available
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  text_length=35
  media_count=1

[DEBUG] Tagging with vision enrichment
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  original_length=35
  enriched_length=285
  has_description=true
  has_ocr=false

[INFO] Post tagged with vision enrichment
  post_id=93a50d27-f074-4c36-b111-54e74d1586e7
  used_vision=true
```

### Обогащенный текст для tagging

**Оригинальный текст**: `"Интересная новость о технологиях..."` (35 символов)

**Обогащенный текст**:
```
Интересная новость о технологиях...

[Изображение: На изображении показана инфографика с данными о росте продаж. График демонстрирует увеличение выручки на 25% за последний квартал.]
```

**✅ Нововведение**: Пост с коротким текстом (35 < 80) но с медиа НЕ пропускается, а обрабатывается с использованием Vision описания.

### Результаты Tagging

```json
{
  "tags": [
    "финансы",
    "инфографика",
    "продажи",
    "бизнес-аналитика",
    "график"
  ],
  "provider": "gigachat",
  "metadata": {
    "model": "GigaChat:latest",
    "used_vision": true
  }
}
```

**post_enrichment**:
```sql
INSERT INTO post_enrichment (
  post_id, kind, provider, data, status
) VALUES (
  '93a50d27-f074-4c36-b111-54e74d1586e7',
  'tags',
  'gigachat',
  '{
    "tags": ["финансы", "инфографика", "продажи", "бизнес-аналитика", "график"],
    "latency_ms": 1234,
    "used_vision_enrichment": true
  }',
  'ok'
);
```

## Метрики Prometheus

### media_processing_total

```
media_processing_total{media_type="photo",status="success"} 1
```

### media_processing_duration_seconds

```
media_processing_duration_seconds_bucket{media_type="photo",status="success",le="0.5"} 1
```

### media_albums_processed_total

```
media_albums_processed_total{status="success"} 0  # Для одиночного фото
```

## Валидация полного пайплайна

### SQL проверка всех этапов

```sql
-- 1. Пост найден
SELECT id, has_media FROM posts WHERE id = '93a50d27-f074-4c36-b111-54e74d1586e7';

-- 2. Медиа сохранено
SELECT COUNT(*) FROM post_media_map WHERE post_id = '93a50d27-f074-4c36-b111-54e74d1586e7';

-- 3. Vision анализ выполнен
SELECT vision_provider, vision_description IS NOT NULL as has_desc
FROM post_enrichment 
WHERE post_id = '93a50d27-f074-4c36-b111-54e74d1586e7' AND kind = 'vision';

-- 4. Tagging выполнен
SELECT provider, data->>'tags' as tags
FROM post_enrichment 
WHERE post_id = '93a50d27-f074-4c36-b111-54e74d1586e7' AND kind = 'tags';
```

### Результаты валидации

```json
{
  "validation": {
    "media_processed": true,
    "vision_completed": true,
    "tagging_completed": true,
    "event_has_media_sha256": true,
    "short_text_with_media": true
  },
  "success": true
}
```

## Пример с альбомом (несколько медиа)

### Логи обработки альбома

```
[INFO] Message media processed
  post_id=album-post-uuid
  media_count=3
  media_type=album
  is_album=true
  duration_seconds=1.456

[INFO] Media group processed
  grouped_id=12345
  total_items=3
  processed_count=3
```

### Результаты обработки альбома

```json
{
  "media_files": [
    {
      "sha256": "photo1_sha256...",
      "mime_type": "image/jpeg",
      "position": 0,
      "role": "primary"
    },
    {
      "sha256": "photo2_sha256...",
      "mime_type": "image/jpeg",
      "position": 1,
      "role": "attachment"
    },
    {
      "sha256": "photo3_sha256...",
      "mime_type": "image/jpeg",
      "position": 2,
      "role": "attachment"
    }
  ]
}
```

**post_media_map**:
```sql
-- Все 3 фото сохранены с правильными позициями
SELECT position, role, file_sha256 
FROM post_media_map 
WHERE post_id = 'album-post-uuid'
ORDER BY position;
```

**Событие posts.parsed**:
```json
{
  "media_sha256_list": [
    "photo1_sha256...",
    "photo2_sha256...",
    "photo3_sha256..."
  ]
}
```

## Ключевые улучшения

1. ✅ **Альбомы обрабатываются** - все медиа из альбома сохраняются параллельно
2. ✅ **Посты с коротким текстом + медиа не пропускаются** - используется Vision для обогащения
3. ✅ **Vision результаты интегрированы в tagging** - описания и OCR используются для улучшения тегов
4. ✅ **Метрики отслеживаются** - Prometheus метрики для всех этапов обработки
5. ✅ **События содержат media_sha256_list** - связь между постами и медиа

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28

