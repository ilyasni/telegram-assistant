# Pipeline обработки постов и альбомов в Telegram

**Дата**: 2025-11-02 | **Context7**: Best practices | **Telethon 1.34.0**

## Что такое Telegram-альбомы?

**Альбом** — группа до 10 медиафайлов (фото/видео), объединённых в один визуальный пост:

```
Телеграм-пост:
├─ Сообщение 62654 (grouped_id: 14096853933353738, position: 1)
│   └─ Photo #1
├─ Сообщение 62655 (grouped_id: 14096853933353738, position: 2)
│   └─ Photo #2
├─ Сообщение 62656 (grouped_id: 14096853933353738, position: 3)
│   └─ Video #1
└─ Сообщение 62657 (grouped_id: 14096853933353738, position: 4)
    └─ Photo #3
```

**Ключевые особенности:**
- Все сообщения имеют **одинаковый `grouped_id`**
- Порядок определяется `message.id` (последовательность в альбоме)
- Типы: `photo`, `video`, `document`, `mixed` (комбинация)
- Текст обычно только у первого сообщения

---

## Полный пайплайн обработки

### 1. Telegram → ChannelParser

```
Telegram Message/Album
    ↓
_get_message_batches() → батч сообщений
    ↓
_process_message_batch()
    ├─ Для КАЖДОГО сообщения:
    │   ├─ Извлечение grouped_id
    │   ├─ Проверка идемпотентности (channel_id + telegram_message_id)
    │   └─ Извлечение post_data
    ↓
```

**Context7 best practice**: 
- ✅ НЕТ ранней дедупликации по `grouped_id` (удалена из-за race conditions)
- ✅ Идемпотентность через `UNIQUE (channel_id, telegram_message_id)` в БД
- ✅ Каждое сообщение обрабатывается независимо

### 2. MediaProcessor (для каждого сообщения с медиа)

```python
process_message_media()
    ├─ MessageMediaPhoto → _process_photo() → S3
    ├─ MessageMediaDocument → _process_document() → S3
    └─ grouped_id != None → _process_media_group()
        ├─ client.get_messages(peer, min_id±20, max_id±20, limit=50)
        ├─ Фильтрация по grouped_id
        ├─ Сортировка по message.id
        ├─ asyncio.gather() — параллельная загрузка ВСЕХ элементов
        └─ Каждое медиа:
            ├─ QuotaCheck (15 GB limit)
            ├─ S3 upload (content-addressed: media/{tenant}/{sha256[:2]}/{sha256})
            └─ SHA256 → MediaFile
```

**Контекст7:**
- Параллельная загрузка через `asyncio.gather()`
- Окно ±20 сообщений; при больших альбомах возможен пропуск
- Порядок сохраняется по `message.id`

### 3. AtomicDBSaver → БД

```sql
-- 3.1. Bulk INSERT постов
INSERT INTO posts (
    id, channel_id, telegram_message_id, content, 
    grouped_id, has_media, ...
)
ON CONFLICT (channel_id, telegram_message_id) DO NOTHING

-- 3.2. Каждое медиа → CAS таблицы
INSERT INTO media_objects (file_sha256, s3_key, mime, size_bytes, ...)
ON CONFLICT (file_sha256) DO NOTHING

INSERT INTO post_media_map (post_id, file_sha256)
ON CONFLICT (post_id, file_sha256) DO NOTHING
```

**Контекст7:**
- Идемпотентность на уровне БД
- Каждый пост пишется отдельно, даже из альбома
- `grouped_id` остаётся в `posts.grouped_id`

### 4. MediaGroupSaver → Альбомы

```python
# Собираем альбомы из уже сохранённых постов
albums_data = {}
for post_data in posts_data:
    grouped_id = post_data.get('grouped_id')
    if grouped_id:
        albums_data[grouped_id]['post_ids'].append(post_id)
        albums_data[grouped_id]['media_sha256s'].append(sha256)
        
# Сохраняем только альбомы с >1 элемента
if len(post_ids) > 1:
    save_media_group(
        user_id, channel_id, grouped_id,
        post_ids, media_types, media_sha256s, ...
    )
```

```sql
-- 4.1. Media Groups
INSERT INTO media_groups (
    user_id, channel_id, grouped_id, album_kind, items_count
)
ON CONFLICT (user_id, channel_id, grouped_id) 
DO UPDATE SET ...;

-- 4.2. Media Group Items (с порядком)
INSERT INTO media_group_items (
    group_id, post_id, position, media_type, media_sha256, ...
)
ON CONFLICT (group_id, position) DO UPDATE SET ...;
```

**Контекст7:**
- Идемпотентность: `UNIQUE (user_id, channel_id, grouped_id)`
- Порядок через `position`
- Одиночные медиа не попадают в `media_groups`

### 5. VisionUploadedEventV1 (для каждого поста с медиа)

```python
emit_vision_uploaded_event(
    post_id, tenant_id, media_files, trace_id
) → stream:posts:vision
```

**Контекст7:**
- Событие на каждый пост с медиа
- `media_sha256_list` для связи
- Vision-анализ не блокирует парсинг

---

## Сравнение: что изменилось

### До (некорректно)

```
1. SETNX "seen:group:{user_id}:{channel_id}:{grouped_id}"
2. Если already_seen → continue (пропускаем всё)
3. process_message_media()
```

**Проблема**: при ошибке обработки альбом помечается как обработанный, повтор пропускается.

### После (Context7)

```
1. Извлечение grouped_id
2. Проверка идемпотентности (channel_id + telegram_message_id)
3. Если duplicate → continue
4. process_message_media()
5. Сохранение в БД → ON CONFLICT DO NOTHING
6. Сбор альбомов из сохранённых постов
7. Сохранение media_groups → ON CONFLICT DO UPDATE
```

**Решение**: идемпотентность на уровне БД.

---

## Архитектурные рекомендации (Context7)

### Идемпотентность
- ✅ Посты: `UNIQUE (channel_id, telegram_message_id)`
- ✅ Альбомы: `UNIQUE (user_id, channel_id, grouped_id)`
- ✅ Медиа: `UNIQUE (file_sha256)`
- ✅ Таблицы связи: `UNIQUE (post_id, file_sha256)`

### Порядок
- ✅ Сортировка по `message.id` до обработки
- ✅ `position` в `media_group_items`
- ✅ События после сохранения

### Производительность
- ✅ Параллельная загрузка через `asyncio.gather()`
- ✅ Bulk INSERT постов
- ✅ Bulk INSERT элементов альбома
- ⚠️ `get_messages(±20)` может пропустить >20 элементов

---

## Что нужно улучшить

### 1. Диапазон `get_messages(±20)` узок
```python
# Текущий код (media_processor.py:362-363)
min_id = max(0, current_msg_id - 20)
max_id = current_msg_id + 20
```
**Проблема**: альбомы >20 элементов пропускаются.

**Решение**: увеличить диапазон и добавить пагинацию.

### 2. Отсутствует кеш для повторных запросов
```python
# TODO: Кешировать grouped_id для последующих сообщений альбома
# чтобы не делать повторный get_messages()
```

### 3. Импорты между сервисами
```python
# telethon-ingest импортирует из worker (нарушение архитектурных границ)
from worker.services.storage_quota import StorageQuotaService
from worker.events.schemas.posts_vision_v1 import MediaFile, VisionUploadedEventV1
```

**Решение**: свести общее в `shared`.

---

## Проверка корректности пайплайна

```bash
# Проверка альбомов в БД
SELECT 
    c.username, 
    COUNT(DISTINCT p.id) as album_items,
    mg.album_kind
FROM media_groups mg
JOIN channels c ON c.id = mg.channel_id
JOIN media_group_items mgi ON mgi.group_id = mg.id
JOIN posts p ON p.id = mgi.post_id
GROUP BY mg.grouped_id, c.username, mg.album_kind
ORDER BY mg.created_at DESC
LIMIT 10;

# Проверка медиа в S3
SELECT p.id, mo.s3_key, mo.mime, mo.size_bytes
FROM posts p
JOIN post_media_map pmm ON pmm.post_id = p.id
JOIN media_objects mo ON mo.file_sha256 = pmm.file_sha256
WHERE p.telegram_message_id BETWEEN 62654 AND 62659;

# Проверка Vision enrichment
SELECT pe.kind, pe.data->'vision'->'labels' as labels
FROM post_enrichment pe
WHERE pe.post_id = '...' AND pe.data->'vision' IS NOT NULL;
```

---

## Связанные документы

- `ARCHITECTURE_PRINCIPLES.md` — архитектурные принципы
- `VISION_S3_INTEGRATION.md` — интеграция Vision и S3
- `IMPLEMENTATION_COMPLETE.md` — Event Flow
- `MIGRATION_003_SUPABASE_INSTRUCTIONS.md` — схема БД альбомов
