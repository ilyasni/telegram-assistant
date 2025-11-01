# Форматы сохранения файлов в S3

**Дата**: 2025-01-30  
**Статус**: Актуально

## 📁 Форматы S3 ключей

### 1. Медиа файлы (изображения, документы)

**Метод**: `S3StorageService.build_media_key()`

**Формат**:
```
media/{tenant_id}/{sha256[:2]}/{sha256}.{extension}
```

**Примеры**:
- `media/default/a1/a1b2c3d4e5f6...jpg`
- `media/tenant_123/7f/7f8e9d0c1b2a3...png`
- `media/default/ff/ffeeddccbbaa...pdf`

**Особенности**:
- Content-addressed storage (по SHA256 хешу)
- Префикс из первых 2 символов SHA256 для распределения файлов
- Расширение определяется автоматически из MIME типа
- Идемпотентность: одинаковые файлы имеют одинаковый ключ

**Где используется**:
- `MediaProcessor._upload_to_s3()` - загрузка медиа из Telegram
- `S3StorageService.put_media()` - основной метод загрузки

---

### 2. Vision результаты (JSON)

**Метод**: `S3StorageService.build_vision_key()`

**Формат**:
```
vision/{tenant_id}/{sha256}_{provider}_{model}_v{schema_version}.json
```

**Примеры**:
- `vision/default/a1b2c3d4e5f6_gigachat_GigaChat-Pro_v1.0.json`
- `vision/tenant_123/7f8e9d0c1b2a_gigachat_GigaChat-Pro_v1.0.json`

**Особенности**:
- Сохраняется как JSON с gzip сжатием
- Content-addressed по SHA256 медиа файла
- Включает provider и model для версионирования
- Используется для кэширования результатов Vision анализа

**Где используется**:
- `GigaChatVisionAdapter.analyze_media()` - кэширование результатов
  - Генерация `cache_key` через `build_vision_key()` (строка 285)
  - Проверка кэша перед анализом через `head_object()` (строки 293-297)
  - Сохранение результата в S3 после анализа (строки 377-382)

---

### 3. Crawl результаты (HTML/JSON)

**Метод**: `S3StorageService.build_crawl_key()`

**Формат**:
```
crawl/{tenant_id}/{urlhash[:2]}/{urlhash}{suffix}
```

**Примеры**:
- `crawl/default/3a/3a4b5c6d7e8f...html`
- `crawl/tenant_123/7f/7f8e9d0c1b2a3...json`

**Особенности**:
- Content-addressed storage (по url_hash)
- Префикс из первых 2 символов url_hash
- По умолчанию suffix = `.html`
- Может быть сжат gzip

**Где используется**:
- `Crawl4AIService` - сохранение HTML контента
- `S3StorageService.put_json()` - сохранение JSON метаданных

---

### 4. JSON данные (общие)

**Метод**: `S3StorageService.put_json()`

**Формат ключа**: Передаётся явно в параметре `s3_key`

**Особенности**:
- Автоматическое сжатие gzip (если `compress=True`)
- `Content-Encoding: gzip` заголовок
- `Content-Type: application/json`

**Примеры использования**:
- Vision кэш (в `gigachat_vision.py`, но использует свой `cache_key`)
- Crawl метаданные

---

## 🗄️ Куда попадают обогащения после Vision

### Пайплайн Vision анализа

```
1. Telegram Message (медиа файл)
   ↓
2. MediaProcessor._upload_to_s3()
   ├─ Вычисление SHA256
   ├─ Загрузка в S3: media/{tenant}/{sha256[:2]}/{sha256}.{ext}
   └─ Эмиссия VisionUploadedEventV1
   ↓
3. VisionAnalysisTask._handle_vision_uploaded()
   ├─ Проверка политики (VisionPolicyEngine)
   ├─ Проверка бюджета (BudgetGate)
   ├─ Vision анализ через GigaChat API
   │  ├─ Результат анализа (classification, description, ocr_text)
   │  └─ Сохранение в S3 кэш? (НЕ используется build_vision_key)
   └─ _save_to_db()
   ↓
4. EnrichmentRepository.upsert_enrichment()
   ├─ kind = 'vision'
   ├─ provider = 'gigachat-vision' (или 'ocr_fallback')
   ├─ data = {
   │     "model": "GigaChat-Pro",
   │     "provider": "gigachat-vision",
   │     "analyzed_at": "2025-01-30T12:00:00Z",
   │     "labels": [...],           // classification
   │     "caption": "...",          // description
   │     "ocr": {
   │       "text": "...",
   │       "engine": null
   │     },
   │     "is_meme": false,
   │     "context": {...},
   │     "file_id": "...",
   │     "tokens_used": 1234,
   │     "cost_microunits": 5678,
   │     "analysis_reason": "new",
   │     "s3_keys": [               // ⚠️ Ключи МЕДИА, не vision результатов
   │       {
   │         "sha256": "...",
   │         "s3_key": "media/{tenant}/{sha256[:2]}/{sha256}.jpg",
   │         "analyzed_at": "..."
   │       }
   │     ]
   │   }
   └─ Сохранение в post_enrichment (БД)
   ↓
5. Neo4j (опционально)
   └─ create_image_content_node() - синхронизация в граф
```

---

## 📊 Структура данных в post_enrichment после Vision

**Таблица**: `post_enrichment`

**Поля**:
- `post_id` (UUID, PK)
- `kind` = `'vision'` (PK)
- `provider` = `'gigachat-vision'` или `'ocr_fallback'`
- `params_hash` = SHA256 hash параметров модели
- `status` = `'ok'`, `'partial'` или `'error'`
- `data` (JSONB) = структурированные данные обогащения

**Структура `data` JSONB**:
```json
{
  "model": "GigaChat-Pro",
  "model_version": null,
  "provider": "gigachat-vision",
  "analyzed_at": "2025-01-30T12:00:00.000Z",
  "labels": [
    {"category": "person", "confidence": 0.95},
    {"category": "outdoor", "confidence": 0.87}
  ],
  "caption": "Photo of a person in outdoor setting",
  "ocr": {
    "text": "Extracted text from image",
    "engine": null,  // или "tesseract" для OCR fallback
    "confidence": null
  },
  "is_meme": false,
  "context": {
    "contains_text": true,
    "language": "ru"
  },
  "file_id": "telegram_file_id",
  "tokens_used": 1234,
  "cost_microunits": 5678,
  "analysis_reason": "new",
  "s3_keys": [
    {
      "sha256": "a1b2c3d4e5f6...",
      "s3_key": "media/default/a1/a1b2c3d4e5f6...jpg",
      "analyzed_at": "2025-01-30T12:00:00.000Z"
    }
  ]
}
```

---

## 🔍 Важные замечания

### 1. Vision результаты сохраняются в S3 для кэширования

- ✅ Метод `build_vision_key()` используется в `GigaChatVisionAdapter`
- ✅ Результаты сохраняются **в S3** (`vision/{tenant}/...`) для кэширования
- ✅ Результаты также сохраняются **в БД** (`post_enrichment.data`) для быстрого доступа
- ✅ Медиа файлы сохраняются в S3 (`media/{tenant}/...`)

**Двойное сохранение**:
- S3: для кэширования и долгосрочного хранения (сжатое JSON)
- БД: для быстрого доступа и поиска (структурированный JSONB)

### 2. S3 кэш в gigachat_vision.py

В `gigachat_vision.py` код сохранения в S3 (строки 285-382):
- ✅ Использует `build_vision_key()` для генерации `cache_key` (строка 285)
- ✅ Проверяет наличие в кэше перед анализом (строка 293)
- ✅ Сохраняет результат в S3 после анализа (строки 377-382)
- ✅ Vision результаты сохраняются и в S3, и в БД

**Процесс кэширования**:
1. Генерация `cache_key` через `build_vision_key()`
2. Проверка существования через `head_object()` (если есть - возврат из кэша)
3. Анализ через GigaChat API
4. Сохранение результата в S3 через `put_json()` с gzip сжатием

### 3. s3_keys в data JSONB

В `data->'s3_keys'` сохраняются:
- ✅ Ключи **медиа файлов** (не vision результатов)
- Формат: `media/{tenant}/{sha256[:2]}/{sha256}.{ext}`

---

## 📝 Текущая реализация Vision кэширования

Vision результаты **уже сохраняются в S3** через `GigaChatVisionAdapter`:

1. Генерация ключа:
```python
cache_key = s3_service.build_vision_key(
    tenant_id=tenant_id,
    sha256=sha256,
    provider="gigachat",
    model="GigaChat-Pro",
    schema_version="1.0"
)
```

2. Проверка кэша:
```python
cached_result = await s3_service.head_object(cache_key)
if cached_result:
    # Загрузить из S3 и вернуть (TODO: реализовано частично)
    return cached_result
```

3. Сохранение результата:
```python
await s3_service.put_json(
    data=analysis_result,
    s3_key=cache_key,
    compress=True  # gzip сжатие
)
```

**Примечание**: В текущей реализации проверка кэша находит объект, но загрузка из кэша не реализована полностью (TODO на строке 295).

---

## ✅ Итог

**Медиа файлы**: 
- S3: `media/{tenant}/{sha256[:2]}/{sha256}.{ext}`
- БД: `media_objects` (CAS) + `post_media_map` (связи)

**Vision результаты**: 
- S3: `vision/{tenant}/{sha256}_{provider}_{model}_v{schema}.json` (gzip JSON кэш)
- БД: `post_enrichment` с `kind='vision'`, данные в `data` JSONB

**Crawl результаты**: 
- S3: `crawl/{tenant}/{urlhash[:2]}/{urlhash}.html` (gzip HTML)
- БД: `post_enrichment` с `kind='crawl'`, данные в `data` JSONB

**Кэширование**:
- ✅ Vision результаты кэшируются в S3 для повторного использования
- ✅ Crawl результаты сохраняются в S3 для долгосрочного хранения

