# Vision Analysis API Endpoints

**Версия**: 1.0 | **Дата**: 2025-01-28

## Контекст

API endpoints для получения результатов Vision анализа медиа из постов. Интегрирован с `post_enrichment`, `media_objects` и `post_media_map` таблицами.

## Endpoints

### 1. GET /api/v1/vision/posts/{post_id}

Получить результаты Vision анализа для конкретного поста.

**Path Parameters:**
- `post_id` (UUID): ID поста

**Response:** `VisionAnalysisResponse`

```json
{
  "post_id": "uuid",
  "analyzed_at": "2025-01-28T12:00:00Z",
  "provider": "gigachat",
  "model": "GigaChat-Pro",
  "tokens_used": 1320,
  "classification": {
    "type": "meme",
    "confidence": 0.92,
    "tags": ["funny", "viral"],
    "is_meme": true,
    "description": "Мем про..."
  },
  "ocr_text": "Извлечённый текст...",
  "is_meme": true,
  "media_count": 2,
  "s3_vision_keys": ["vision/tenant/sha256_model_v1.json"],
  "s3_media_keys": ["media/tenant/sha256.jpg"],
  "trace_id": "req_abc123"
}
```

**Status Codes:**
- `200 OK`: Результаты найдены
- `404 Not Found`: Vision анализ для поста не найден
- `500 Internal Server Error`: Ошибка сервера

---

### 2. GET /api/v1/vision/posts

Список результатов Vision анализа с фильтрацией и пагинацией.

**Query Parameters:**
- `channel_id` (UUID, optional): Фильтр по каналу
- `has_meme` (bool, optional): Фильтр по наличию мемов (`true`/`false`)
- `provider` (string, optional): Фильтр по провайдеру (`gigachat`, `ocr`)
- `analyzed_after` (datetime, optional): Фильтр по дате анализа (ISO 8601)
- `page` (int, default=1): Номер страницы (≥ 1)
- `page_size` (int, default=50, max=100): Размер страницы

**Response:** `VisionAnalysisListResponse`

```json
{
  "results": [
    {
      "post_id": "uuid",
      "analyzed_at": "2025-01-28T12:00:00Z",
      "provider": "gigachat",
      "is_meme": true,
      ...
    }
  ],
  "total": 150,
  "page": 1,
  "page_size": 50
}
```

**Example:**
```bash
# Получить все мемы за последние 7 дней
GET /api/v1/vision/posts?has_meme=true&analyzed_after=2025-01-21T00:00:00Z

# Получить результаты по каналу
GET /api/v1/vision/posts?channel_id=uuid&page=2&page_size=20
```

---

### 3. GET /api/v1/vision/media/{sha256}

Информация о медиа файле и его Vision анализе.

**Path Parameters:**
- `sha256` (string): SHA256 hash медиа файла

**Response:** `VisionMediaFile`

```json
{
  "sha256": "abc123...",
  "s3_key": "media/tenant/sha256.jpg",
  "mime_type": "image/jpeg",
  "size_bytes": 1024000,
  "vision_classification": {
    "type": "photo",
    "confidence": 0.95
  },
  "analyzed_at": "2025-01-28T12:00:00Z"
}
```

**Status Codes:**
- `200 OK`: Медиа файл найден
- `404 Not Found`: Медиа файл не найден

---

### 4. GET /api/v1/vision/stats

Статистика Vision анализа по всей системе.

**Response:**

```json
{
  "total_analyzed": 1500,
  "memes_count": 450,
  "by_provider": {
    "gigachat": 1200,
    "ocr": 250,
    "cached": 50
  },
  "tokens": {
    "total": 1980000,
    "average": 1320
  },
  "by_type": {
    "meme": 450,
    "photo": 800,
    "doc": 150,
    "infographic": 100
  },
  "trace_id": "req_abc123"
}
```

---

## Интеграция с БД

### Таблицы

- **`post_enrichment`**: Vision поля (`vision_classification`, `vision_description`, `vision_ocr_text`, `vision_is_meme`, `vision_provider`, `vision_model`, `vision_analyzed_at`, `vision_tokens_used`, `s3_vision_keys`, `s3_media_keys`)
- **`media_objects`**: Реестр медиа файлов (SHA256, S3 ключи, MIME, размер)
- **`post_media_map`**: Связи постов и медиа файлов

### Запросы

```sql
-- Получить Vision анализ для поста
SELECT 
    post_id, vision_classification, vision_description,
    vision_ocr_text, vision_is_meme, vision_provider,
    vision_model, vision_analyzed_at, vision_tokens_used,
    s3_vision_keys, s3_media_keys,
    (SELECT COUNT(*) FROM post_media_map WHERE post_media_map.post_id = post_enrichment.post_id) as media_count
FROM post_enrichment
WHERE post_id = $1
```

---

## Trace ID Propagation

Все endpoints используют `trace_id` из `request.state` (генерируется `TracingMiddleware`):

- HTTP header: `X-Trace-ID` добавлен в response
- Логирование: все логи содержат `trace_id`
- Корреляция: можно отследить запрос через всю систему

---

## Error Handling

Все endpoints используют единый подход:

```python
try:
    # Логика
except HTTPException:
    raise  # Передаём дальше
except Exception as e:
    logger.error("Failed to ...", error=str(e), trace_id=trace_id)
    raise HTTPException(status_code=500, detail=f"Failed to ...: {str(e)}")
```

---

## Примеры использования

```bash
# Получить анализ поста
curl http://localhost:8000/api/v1/vision/posts/123e4567-e89b-12d3-a456-426614174000

# Поиск мемов
curl "http://localhost:8000/api/v1/vision/posts?has_meme=true&page=1&page_size=10"

# Статистика
curl http://localhost:8000/api/v1/vision/stats

# Информация о медиа
curl http://localhost:8000/api/v1/vision/media/abc123def456...
```

---

## Best Practices

1. **Async Database**: Использует `asyncpg` connection pool для эффективности
2. **Trace Propagation**: Все запросы имеют `trace_id` для корреляции
3. **Error Handling**: Единый подход с логированием и HTTPException
4. **Pydantic Models**: Типизированные request/response модели
5. **Filtering**: Гибкая фильтрация по каналам, провайдерам, датам
6. **Pagination**: Поддержка пагинации для больших списков

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28

