# Отчет об исправлении сохранения альбомов

**Дата**: 2025-11-05  
**Context7**: Исправление получения `user_id` для сохранения альбомов в `media_groups`

---

## Проблема

Альбомы не сохранялись в `media_groups`, хотя посты с `grouped_id` существовали.

**Наблюдения**:
- Всего альбомов в `media_groups`: 0
- Постов с `grouped_id` (альбомы): 10 групп
- Событий `albums.parsed`: только 1 (старое от 2025-11-03)

---

## Причина

В `telethon-ingest/services/channel_parser.py` (строки 1425-1449) использовался запрос:
```python
SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1
```

Этот запрос не находил `user_id` для некоторых каналов, из-за чего альбомы пропускались (строка 1441: `continue`).

**Проблема**: Не учитывалась связь `user_channel`, которая является основным источником связи пользователей с каналами.

---

## Исправление

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 1425-1449)

**Изменения**:
1. **Приоритет 1**: Получение `user_id` через `user_channel` (основной источник)
   ```python
   SELECT u.id::text
   FROM users u
   JOIN user_channel uc ON uc.user_id = u.id
   WHERE uc.channel_id = :channel_id
   LIMIT 1
   ```

2. **Приоритет 2**: Fallback на `telegram_id` (для обратной совместимости)
   ```python
   SELECT id::text FROM users WHERE telegram_id = :telegram_id LIMIT 1
   ```

**Преимущества**:
- Использует правильную связь `user_channel` для получения `user_id`
- Fallback на `telegram_id` для обратной совместимости
- Улучшенное логирование с `channel_id` и `telegram_id`

---

## Ожидаемый результат

После перезапуска `telethon-ingest`:
1. Альбомы будут сохраняться в `media_groups` для постов с `grouped_id`
2. События `albums.parsed` будут эмитироваться (если `tenant_id` присутствует)
3. `AlbumAssemblerTask` сможет обрабатывать новые альбомы
4. `album_size` и `vision_labels_agg` будут заполняться в `post_enrichment`

---

## Документация

- `telethon-ingest/services/channel_parser.py` - Channel Parser
- `telethon-ingest/services/media_group_saver.py` - Media Group Saver
- `worker/tasks/album_assembler_task.py` - Album Assembler Task
- `docs/ALBUM_FIX_REPORT.md` - Отчет об исправлении заполнения `album_size`

