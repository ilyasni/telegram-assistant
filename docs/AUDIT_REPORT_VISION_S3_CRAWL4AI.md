# Отчёт об аудите: Vision + S3 + Crawl4ai Integration

**Дата**: 2025-01-30  
**Версия**: 1.0  
**Статус**: Аудит в процессе

## Контекст

Проведён аудит нового функционала:
- Vision анализ (GigaChat Vision API + OCR fallback)
- S3 интеграция (Cloud.ru, content-addressed storage)
- Crawl4ai обогащение (с S3 кешированием)
- Новые таблицы БД: `media_objects`, `post_media_map`, расширение `post_enrichment`

## Критические проблемы

### 1. ❌ КРИТИЧЕСКОЕ: Конфликт схемы `post_enrichment` с полем `kind`

**Проблема**: 
- Код использует `ON CONFLICT (post_id, kind)`, но:
  - Модели ORM (`api/models/database.py`, `worker/shared/database.py`) **НЕ содержат** поле `kind`
  - В миграции `20250128_add_media_registry_vision.py` поле `kind` **НЕ добавляется**
  - Тест `test_tag_persistence.py:196` проверяет существование `kind`, что указывает на ожидание этого поля

**Места использования `(post_id, kind)`**:
- `crawl4ai/crawl4ai_service.py:364` — `ON CONFLICT (post_id, kind)`
- `worker/tasks/tag_persistence_task.py:366` — `ON CONFLICT (post_id, kind)`

**Риск**: 
- Ошибки при выполнении SQL: `unique constraint violation` или `column does not exist`
- Невозможность модульного сохранения обогащений (tags, crawl, vision)

**Рекомендация**: 
- Добавить миграцию для добавления поля `kind TEXT DEFAULT 'tags'` в `post_enrichment`
- Добавить уникальный индекс `UNIQUE (post_id, kind)`
- Обновить модели ORM для включения поля `kind`

### 2. ❌ КРИТИЧЕСКОЕ: Конфликт способов сохранения в `post_enrichment`

**Проблема**: Разные компоненты используют разные конфликт-таргеты:

| Компонент | Конфликт-таргет | Результат |
|-----------|----------------|-----------|
| `worker/tasks/vision_analysis_task.py:457` | `ON CONFLICT (post_id)` | Перезаписывает всю запись |
| `crawl4ai/crawl4ai_service.py:364` | `ON CONFLICT (post_id, kind)` | Модульное сохранение |
| `worker/tasks/tag_persistence_task.py:366` | `ON CONFLICT (post_id, kind)` | Модульное сохранение |
| `worker/tasks/enrichment_task.py:763` | `UPDATE ... WHERE post_id` | Предполагает одну запись |

**Риск**: 
- Vision анализ может перезаписать данные от crawl4ai и tags
- Потеря обогащений от разных источников
- Нарушение модульности данных

**Рекомендация**:
- Унифицировать на использование `(post_id, kind)`
- Изменить `vision_analysis_task.py` для использования `kind='vision'`
- Изменить `enrichment_task.py` для использования `kind` вместо прямого UPDATE

### 3. ❌ КРИТИЧЕСКОЕ: Таблицы `post_forwards`, `post_reactions`, `post_replies` НЕ заполняются

**Проблема**: 
Таблицы созданы в схемах (`api/models/database.py`, `worker/shared/database.py`), но:
- **Нет INSERT запросов** нигде в коде
- Данные извлекаются из Telegram сообщений (counters), но детали не сохраняются
- В `posts` сохраняются только счётчики: `forwards_count`, `reactions_count`, `replies_count`

**Места извлечения данных**:
- `telethon-ingest/services/telegram_client.py:604` — `forwards_count` извлекается
- `telethon-ingest/services/telegram_client.py:606` — `reactions` извлекаются, но только count
- `telethon-ingest/services/telegram_client.py:612` — `replies` извлекаются, но только count
- `telethon-ingest/services/channel_parser.py:965-967` — комментарии: "Будет заполнено отдельной таблицей"

**Риск**:
- Потеря детальной информации о forwards/reactions/replies
- Невозможность анализировать структуру взаимодействий
- Нарушение нормализации (данные должны быть в связанных таблицах)

**Рекомендация**:
- Добавить код для сохранения деталей forwards в `post_forwards`
- Добавить код для сохранения деталей reactions в `post_reactions`
- Добавить код для сохранения деталей replies в `post_replies`
- Использовать Context7 best practices для batch insert и идемпотентности

### 4. ⚠️ ВЫСОКИЙ: Медиа-данные не заполняются в `media_objects` и `post_media_map`

**Проблема**: 
- `telethon-ingest/services/media_processor.py` загружает медиа в S3, но:
  - **Не создаёт записи** в `media_objects`
  - **Не создаёт связи** в `post_media_map`
- Таблицы `post_media_map` используются только для SELECT (подсчёт медиа)
- Legacy таблица `post_media` тоже не заполняется

**Места, где должна быть логика**:
- `telethon-ingest/services/media_processor.py:_upload_to_s3` — после успешной загрузки в S3
- Должны быть созданы записи: `media_objects` (по SHA256) и `post_media_map` (связь post ↔ media)

**Риск**:
- Нарушение content-addressed storage архитектуры
- Невозможность отслеживать использование медиа (refs_count)
- Дублирование данных (S3 keys в `post_enrichment.s3_media_keys` vs `media_objects`)

**Рекомендация**:
- Добавить логику заполнения `media_objects` при загрузке медиа в S3
- Добавить логику заполнения `post_media_map` при привязке медиа к посту
- Использовать транзакции для атомарности операций
- Обновить `s3_media_keys` в `post_enrichment` после создания записей

### 5. ⚠️ ВЫСОКИЙ: Дублирование OCR/Vision полей

**Проблема**: Существуют дублирующиеся поля:

| Legacy поле | Новое поле | Использование |
|-------------|------------|---------------|
| `ocr_text` | `vision_ocr_text` | Оба существуют, но назначение неясно |
| `vision_labels` | `vision_classification` | `vision_labels` помечено как legacy, но всё ещё используется |

**Места использования**:
- `crawl4ai/crawl4ai_service.py:347` — сохраняет в `ocr_text` и `vision_labels`
- `worker/tasks/vision_analysis_task.py:430` — сохраняет в `vision_ocr_text` и `vision_classification`

**Риск**:
- Неопределённость источника данных
- Путаница в логике чтения
- Возможная потеря данных при миграции

**Рекомендация**:
- Определить clear migration path: legacy → новые поля
- Добавить документацию о назначении полей
- Рассмотреть депрекацию legacy полей после миграции данных

## Проблемы средней критичности

### 6. ⚠️ СРЕДНИЙ: Отсутствие индексов для `kind` в `post_enrichment`

**Проблема**: Если `kind` будет добавлен, нужен индекс для производительности запросов `WHERE post_id = ? AND kind = ?`

**Рекомендация**: Добавить составной индекс `(post_id, kind)` (часть уникального constraint)

### 7. ⚠️ СРЕДНИЙ: Отсутствие foreign key constraints для `post_forwards`, `post_reactions`, `post_replies`

**Проблема**: В моделях есть FK, но нужно убедиться, что они созданы в БД с правильными `ON DELETE` действиями

**Рекомендация**: Проверить миграции и добавить `ON DELETE CASCADE` для связанных записей

## SQL скрипты для диагностики

### Проверка существования поля `kind`

```sql
-- Проверка наличия поля kind в post_enrichment
SELECT column_name, data_type, column_default, is_nullable
FROM information_schema.columns
WHERE table_name = 'post_enrichment'
AND column_name = 'kind';

-- Проверка уникального индекса на (post_id, kind)
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'post_enrichment'
AND indexname LIKE '%kind%';
```

### Проверка заполненности таблиц

```sql
-- Проверка использования post_forwards
SELECT 
    COUNT(*) as total_forwards,
    COUNT(DISTINCT post_id) as posts_with_forwards
FROM post_forwards;

-- Проверка использования post_reactions
SELECT 
    COUNT(*) as total_reactions,
    COUNT(DISTINCT post_id) as posts_with_reactions
FROM post_reactions;

-- Проверка использования post_replies
SELECT 
    COUNT(*) as total_replies,
    COUNT(DISTINCT post_id) as posts_with_replies
FROM post_replies;

-- Проверка использования media_objects
SELECT 
    COUNT(*) as total_media_objects,
    SUM(refs_count) as total_refs
FROM media_objects;

-- Проверка использования post_media_map
SELECT 
    COUNT(*) as total_media_links,
    COUNT(DISTINCT post_id) as posts_with_media,
    COUNT(DISTINCT file_sha256) as unique_media_files
FROM post_media_map;

-- Проверка legacy post_media
SELECT 
    COUNT(*) as total_legacy_media
FROM post_media;
```

### Поиск дублирования данных

```sql
-- Проверка конфликтов в post_enrichment (если kind существует)
SELECT post_id, COUNT(*) as enrichment_count
FROM post_enrichment
GROUP BY post_id
HAVING COUNT(*) > 1;

-- Проверка заполнения legacy vs новых полей
SELECT 
    COUNT(*) as total_records,
    COUNT(ocr_text) as has_ocr_text,
    COUNT(vision_ocr_text) as has_vision_ocr_text,
    COUNT(vision_labels) as has_vision_labels,
    COUNT(vision_classification) as has_vision_classification
FROM post_enrichment;
```

### Проверка целостности данных

```sql
-- Проверка orphaned записей в post_media_map (медиа без записи в media_objects)
SELECT pmm.post_id, pmm.file_sha256
FROM post_media_map pmm
LEFT JOIN media_objects mo ON pmm.file_sha256 = mo.file_sha256
WHERE mo.file_sha256 IS NULL;

-- Проверка постов с медиа, но без записей в post_media_map
SELECT p.id, p.has_media
FROM posts p
WHERE p.has_media = true
AND NOT EXISTS (
    SELECT 1 FROM post_media_map pmm WHERE pmm.post_id = p.id
);
```

## Рекомендации по исправлению (приоритизированные)

### Критический приоритет (блокируют работу)

1. **Добавить миграцию для поля `kind` в `post_enrichment`**
   - Файл: `api/alembic/versions/YYYYMMDD_add_kind_to_post_enrichment.py`
   - Добавить колонку `kind TEXT DEFAULT 'tags'`
   - Добавить уникальный constraint `UNIQUE (post_id, kind)`
   - Обновить модели ORM

2. **Унифицировать конфликт-таргеты в `post_enrichment`**
   - Изменить `vision_analysis_task.py` для использования `kind='vision'`
   - Изменить `enrichment_task.py` для использования `kind='crawl'` (или соответствующий)
   - Проверить все места сохранения

### Высокий приоритет (могут привести к потере данных)

3. **Реализовать заполнение `media_objects` и `post_media_map`**
   - Добавить код в `media_processor.py` после успешной загрузки в S3
   - Использовать транзакции для атомарности
   - Обновить `refs_count` в `media_objects`

4. **Реализовать заполнение `post_forwards`, `post_reactions`, `post_replies`**
   - Добавить извлечение деталей из Telegram сообщений
   - Добавить batch insert с идемпотентностью
   - Использовать Context7 best practices

5. **Мигрировать legacy поля OCR/Vision**
   - Определить clear migration path
   - Добавить скрипт миграции данных
   - Обновить документацию

### Средний приоритет (влияют на производительность)

6. Добавить недостающие индексы
7. Проверить foreign key constraints
8. Оптимизировать запросы

## Дополнительные находки

### 8. ⚠️ СРЕДНИЙ: Обработка ошибок S3

**Статус**: ✅ Реализовано с Context7 best practices

**Находки**:
- `api/services/s3_storage.py` корректно обрабатывает Cloud.ru специфичные ошибки (500 вместо 404 на HEAD)
- Логирование включает request IDs для поддержки
- Quota проверки реализованы с emergency cleanup

**Рекомендация**: Проверить интеграцию с LRUEvictionService (может быть недоступен)

### 9. ✅ ХОРОШО: Идемпотентность Vision анализа

**Статус**: ✅ Реализовано

**Находки**:
- Redis-based идемпотентность через SHA256 + dedupe keys
- Правильное использование idempotency cache

### 10. ⚠️ СРЕДНИЙ: Отсутствие транзакций при заполнении media_objects

**Проблема**: Если `media_processor.py` будет обновлён для заполнения `media_objects`, нужно обеспечить атомарность с S3 загрузкой

**Рекомендация**: Использовать транзакции БД или компенсирующие транзакции (Saga pattern)

## Сводная таблица проблем

| # | Проблема | Критичность | Статус | Файлы |
|---|----------|-------------|--------|-------|
| 1 | Отсутствие поля `kind` в `post_enrichment` | ❌ КРИТИЧЕСКОЕ | Не исправлено | Модели, миграции |
| 2 | Конфликт конфликт-таргетов в `post_enrichment` | ❌ КРИТИЧЕСКОЕ | Не исправлено | vision_analysis_task, enrichment_task |
| 3 | Таблицы forwards/reactions/replies не заполняются | ❌ КРИТИЧЕСКОЕ | Не исправлено | telegram_client, channel_parser |
| 4 | `media_objects` и `post_media_map` не заполняются | ⚠️ ВЫСОКИЙ | Не исправлено | media_processor |
| 5 | Дублирование OCR/Vision полей | ⚠️ ВЫСОКИЙ | Требует миграции | Все компоненты |
| 6 | Отсутствие индексов для `kind` | ⚠️ СРЕДНИЙ | Зависит от #1 | Миграции |
| 7 | Отсутствие FK constraints проверки | ⚠️ СРЕДНИЙ | Требует проверки | Миграции |
| 8 | Обработка ошибок S3 | ✅ ОК | Реализовано | s3_storage |
| 9 | Идемпотентность Vision | ✅ ОК | Реализовано | vision_analysis_task |
| 10 | Транзакции для media_objects | ⚠️ СРЕДНИЙ | Требует реализации | media_processor |

## Next Steps

1. 🔴 КРИТИЧЕСКОЕ: Создать миграцию для добавления поля `kind` в `post_enrichment`
2. 🔴 КРИТИЧЕСКОЕ: Унифицировать конфликт-таргеты (использовать `(post_id, kind)` везде)
3. 🔴 КРИТИЧЕСКОЕ: Реализовать заполнение `post_forwards`, `post_reactions`, `post_replies`
4. 🟡 ВЫСОКИЙ: Реализовать заполнение `media_objects` / `post_media_map`
5. 🟡 ВЫСОКИЙ: Определить migration path для legacy полей OCR/Vision
6. 🟢 СРЕДНИЙ: Добавить недостающие индексы и constraints
7. 🟢 СРЕДНИЙ: Проверить транзакции для атомарности операций

## Источники

- `api/models/database.py`
- `worker/shared/database.py`
- `api/alembic/versions/*.py`
- `telethon-ingest/services/media_processor.py`
- `telethon-ingest/services/telegram_client.py`
- `worker/tasks/vision_analysis_task.py`
- `crawl4ai/crawl4ai_service.py`
- `worker/tasks/tag_persistence_task.py`

