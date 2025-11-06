# Исправление логики определения альбомов

**Дата**: 2025-11-05 20:10:00  
**Context7**: Исправление логики определения альбомов для работы с альбомами, разбитыми на несколько batches

---

## Проблема

Текущая логика собирала альбомы **ТОЛЬКО** из текущего batch (`posts_data`). Это приводило к следующим проблемам:

1. **Альбомы, разбитые на несколько batches**: Если альбом был разбит на несколько batches (из-за задержки Telegram API или других причин), альбом не собирался полностью
2. **Пропуск альбомов**: Если в batch был только один пост из альбома, альбом пропускался (фильтр `len(album_data['post_ids']) <= 1`)

### Пример проблемы

**Альбом с grouped_id=14098828991549074**:
- 6 постов
- Время между первым и последним: 465.9 секунд
- ⚠️ Возможно разные batches
- ❌ Альбом НЕ сохранен

---

## Решение

**Context7 best practice**: Собирать альбомы из БД, а не только из текущего batch.

### Изменения

1. **Сборка уникальных grouped_id из текущего batch**: Определяем, какие альбомы затронуты текущим batch
2. **Запрос ВСЕХ постов из БД**: Для каждого `grouped_id` запрашиваем ВСЕ посты с этим `grouped_id` из БД (не только из текущего batch)
3. **Идемпотентность**: Проверяем, существует ли уже альбом в `media_groups` перед сохранением
4. **Сборка медиа информации**: Получаем медиа информацию из `post_media_map` и `media_objects` для всех постов альбома

### Преимущества

- ✅ Работает для альбомов, разбитых на несколько batches
- ✅ Идемпотентность через `UNIQUE (user_id, channel_id, grouped_id)`
- ✅ Не пропускает альбомы из-за разбиения на batches
- ✅ Сохраняет порядок постов через `telegram_message_id`

---

## Код изменений

**Файл**: `telethon-ingest/services/channel_parser.py`

**Основные изменения**:

1. **Сборка уникальных grouped_id**:
```python
grouped_ids_in_batch = set()
for post_data in posts_data:
    grouped_id = post_data.get('grouped_id')
    if grouped_id:
        grouped_ids_in_batch.add(grouped_id)
```

2. **Запрос ВСЕХ постов из БД**:
```python
posts_result = await self.db_session.execute(
    text("""
        SELECT 
            p.id,
            p.content,
            p.posted_at,
            p.telegram_message_id,
            COUNT(DISTINCT pm.file_sha256) as media_count
        FROM posts p
        LEFT JOIN post_media_map pm ON pm.post_id = p.id
        WHERE p.channel_id = :channel_id
          AND p.grouped_id = :grouped_id
        GROUP BY p.id, p.content, p.posted_at, p.telegram_message_id
        ORDER BY p.telegram_message_id ASC
    """),
    {"channel_id": channel_id, "grouped_id": grouped_id}
)
```

3. **Проверка идемпотентности**:
```python
existing_album = await self.db_session.execute(
    text("""
        SELECT id FROM media_groups 
        WHERE user_id = :user_id 
          AND channel_id = :channel_id 
          AND grouped_id = :grouped_id
        LIMIT 1
    """),
    {"user_id": user_uuid, "channel_id": channel_id, "grouped_id": grouped_id}
)
```

---

## Ожидаемые результаты

После применения исправлений:

1. ✅ Альбомы, разбитые на несколько batches, будут собираться корректно
2. ✅ Альбомы из одного batch будут сохраняться (после исправления SQL ошибок)
3. ✅ Идемпотентность обеспечивается через проверку существования альбома

---

## Следующие шаги

1. ✅ Исправлена логика сборки альбомов из БД
2. ✅ Исправлены SQL ошибки
3. ⏳ Перезапустить `telethon-ingest` для применения изменений
4. ⏳ Протестировать на реальных данных
5. ⏳ Мониторить появление новых альбомов в `media_groups`

