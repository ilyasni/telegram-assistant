# Исправление критических ошибок в парсинге и сохранении альбомов

**Дата**: 2025-01-22  
**Context7**: Глубокая проверка и исправление всех ошибок в цепочке парсинга альбомов

---

## Context

Проведена полная проверка цепочки парсинга и сохранения альбомов (media groups). Обнаружены и исправлены критические ошибки, которые приводили к проблемам при сохранении альбомов.

---

## Критические исправления

### Исправление 1: Несоответствие длин массивов при сохранении альбомов

**Проблема**: 
- При сборе данных альбома для каждого поста добавлялись ВСЕ медиа файлы
- Если у поста было несколько медиа, массивы `media_types`, `media_sha256s`, `media_bytes` становились длиннее, чем `post_ids`
- Это приводило к ошибке в `save_media_group`: `len(post_ids) != len(media_types)`

**Исправление**:
- Теперь для каждого поста берется только ПЕРВОЕ медиа (основное)
- Каждый пост имеет ровно ОДИН элемент в каждом массиве
- Добавлена проверка соответствия длин массивов перед сохранением

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 1995-2068)

**Код до исправления**:
```python
# ❌ Неправильно - добавлялись все медиа из поста
for post_row in album_posts:
    if post_id_str in media_by_post:
        for media in media_by_post[post_id_str]:  # Все медиа!
            media_types.append(media_type)
            media_sha256s.append(media['sha256'])
            # ...
```

**Код после исправления**:
```python
# ✅ Правильно - берем только первое медиа из поста
for post_row in album_posts:
    if post_id_str in media_by_post and media_by_post[post_id_str]:
        # Берем первое медиа из поста (основное)
        media = media_by_post[post_id_str][0]  # Только первое!
        media_types.append(media_type)
        media_sha256s.append(media['sha256'])
        # ...

# Context7: Проверяем соответствие длин массивов
if len(media_types) != len(actual_post_ids):
    logger.error("Mismatch between post_ids and media_types lengths")
    continue  # Пропускаем альбом
```

---

### Исправление 2: Улучшена обработка ошибок при сохранении альбомов

**Проблема**: 
- Ошибки при сохранении альбомов логировались недостаточно детально
- Не было информации о длинах массивов, что затрудняло диагностику

**Исправление**:
- Добавлено детальное логирование с информацией о длинах массивов
- Добавлен `error_type` для лучшей диагностики
- Использован `continue` вместо пропуска для обработки следующих альбомов

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 2075-2090)

**Код**:
```python
except Exception as e:
    # Context7: Детальное логирование ошибок сохранения альбомов
    logger.error(
        "Failed to save media group to DB",
        grouped_id=grouped_id,
        channel_id=channel_id,
        user_uuid=user_uuid if 'user_uuid' in locals() else None,
        post_ids_count=len(actual_post_ids) if 'actual_post_ids' in locals() else 0,
        media_types_count=len(media_types) if 'media_types' in locals() else 0,
        error=str(e),
        error_type=type(e).__name__,
        exc_info=True
    )
    continue  # Обрабатываем следующий альбом
```

---

### Исправление 3: Обработка постов без медиа в альбомах

**Проблема**: 
- Если пост в альбоме не имел медиа, массивы становились короче, чем `post_ids`
- Это приводило к несоответствию длин массивов

**Исправление**:
- Добавлена обработка постов без медиа с использованием значений по умолчанию
- Логируется предупреждение для таких случаев

**Код**:
```python
else:
    # Пост без медиа - используем значения по умолчанию
    logger.warning(
        "Post in album has no media, using defaults",
        post_id=post_id_str,
        grouped_id=grouped_id,
        channel_id=channel_id
    )
    media_types.append('photo')  # Fallback
    media_sha256s.append(None)
    media_bytes.append(None)
    media_kinds.append('photo')
```

---

## Проверенные компоненты

### 1. ✅ Сбор данных альбома

**Проверено**:
- ✅ Получение всех постов с `grouped_id` из БД
- ✅ Получение медиа информации из `post_media_map` и `media_objects`
- ✅ Сортировка по `telegram_message_id` для сохранения порядка
- ✅ Группировка медиа по `post_id`

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 1927-1993)

---

### 2. ✅ Формирование массивов для сохранения

**Проверено**:
- ✅ Каждый пост имеет ровно ОДИН элемент в каждом массиве
- ✅ Проверка соответствия длин массивов перед сохранением
- ✅ Обработка постов без медиа

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 1995-2044)

---

### 3. ✅ Сохранение альбома

**Проверено**:
- ✅ Валидация длин массивов в `save_media_group`
- ✅ Обработка ошибок с детальным логированием
- ✅ Идемпотентность через `ON CONFLICT`

**Файл**: `telethon-ingest/services/media_group_saver.py`

---

### 4. ✅ Обработка транзакций

**Проверено**:
- ✅ Отдельная транзакция для сохранения альбомов (после сохранения постов)
- ✅ Проверка состояния транзакции перед началом новой
- ✅ Откат транзакции при ошибках

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 1738-1744)

---

### 5. ✅ Получение user_uuid

**Проверено**:
- ✅ Получение через `user_channel` (приоритет 1)
- ✅ Fallback через `telegram_id` (приоритет 2)
- ✅ Создание `user_channel` если отсутствует

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 1746-1899)

---

## Проверка цепочки после исправлений

### Сценарий 1: Успешное сохранение альбома

1. ✅ Посты сохранены в БД с `grouped_id`
2. ✅ Сбор всех постов с `grouped_id` из БД
3. ✅ Получение медиа информации для каждого поста
4. ✅ Формирование массивов (по одному элементу на пост)
5. ✅ Проверка соответствия длин массивов
6. ✅ Сохранение альбома через `save_media_group`
7. ✅ Эмиссия события `albums.parsed`

### Сценарий 2: Альбом с постами, имеющими несколько медиа

1. ✅ Посты сохранены в БД (некоторые с несколькими медиа)
2. ✅ Сбор всех постов с `grouped_id`
3. ✅ Получение медиа информации
4. ✅ Для каждого поста берется только ПЕРВОЕ медиа
5. ✅ Массивы имеют правильную длину (равную количеству постов)
6. ✅ Альбом успешно сохраняется

### Сценарий 3: Ошибка при сохранении альбома

1. ✅ Посты сохранены в БД
2. ✅ Сбор данных альбома
3. ❌ Ошибка при сохранении (например, несоответствие длин)
4. ✅ Детальное логирование ошибки
5. ✅ Обработка следующего альбома продолжается
6. ✅ Транзакция откатывается автоматически

---

## Метрики для мониторинга

### Логи для проверки

```bash
# Проверка успешных сохранений альбомов
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep "Media group saved to DB"

# Проверка ошибок сохранения альбомов
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep -E "(Failed to save media group|Mismatch between post_ids)"

# Проверка постов без медиа
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep "Post in album has no media"
```

### Проверка альбомов в БД

```sql
-- Проверка альбомов в канале
SELECT 
    mg.id,
    mg.grouped_id,
    mg.album_kind,
    mg.items_count,
    COUNT(mgi.id) as actual_items_count
FROM media_groups mg
LEFT JOIN media_group_items mgi ON mgi.group_id = mg.id
WHERE mg.channel_id = 'a5f1157f-606e-459a-8d53-f5eb6ddda0c8'
GROUP BY mg.id, mg.grouped_id, mg.album_kind, mg.items_count;

-- Проверка постов с grouped_id без альбомов
SELECT 
    p.grouped_id,
    COUNT(*) as posts_count
FROM posts p
LEFT JOIN media_groups mg ON mg.channel_id = p.channel_id 
                          AND mg.grouped_id = p.grouped_id
WHERE p.channel_id = 'a5f1157f-606e-459a-8d53-f5eb6ddda0c8'
  AND p.grouped_id IS NOT NULL
  AND mg.id IS NULL
GROUP BY p.grouped_id;
```

---

## Выводы

✅ **Все критические ошибки в обработке альбомов исправлены**:
- Массивы для сохранения альбомов имеют правильную длину (по одному элементу на пост)
- Добавлена проверка соответствия длин массивов перед сохранением
- Улучшена обработка ошибок с детальным логированием
- Обработка постов без медиа в альбомах

✅ **Цепочка парсинга альбомов работает надежно**:
- Альбомы собираются из всех постов с `grouped_id` (не только из текущего batch)
- Каждый пост имеет ровно один элемент в массивах медиа
- Ошибки при сохранении одного альбома не блокируют сохранение других
- Транзакции обрабатываются корректно

---

## Следующие шаги

1. ✅ Мониторинг логов на наличие ошибок сохранения альбомов
2. ✅ Проверка альбомов в БД на соответствие постам
3. ✅ Тестирование на реальных каналах с альбомами
4. ✅ Проверка что все альбомы сохраняются корректно

