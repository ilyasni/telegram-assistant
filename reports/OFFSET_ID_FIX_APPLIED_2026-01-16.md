# Исправление: Добавлено использование offset_id для получения новых сообщений

**Дата**: 2026-01-16 20:40 UTC  
**Проблема**: Парсер не находил новые посты, так как не использовал `offset_id` для запроса сообщений после последнего `message_id`

---

## Context

Новые посты (85784, 14386, 20638) не парсились, так как Telegram API возвращал старые сообщения при запросе БЕЗ `offset_id`.

---

## 1. ПРОБЛЕМА

### До исправления

**Парсер запрашивал сообщения БЕЗ `offset_id`:**
```python
messages = await fetch_messages_with_retry(
    client,
    channel,
    limit=limit,
    offset_date=None,  # Не использовали
    reverse=False
)
```

**Результат:**
- Telegram API возвращал последние N сообщений (например, 2558)
- `first_message_date = 2026-01-15` (старое), `since_date = 2026-01-16 17:22:24` (новое)
- Новые посты (85784) не находились, так как они не в первых 200 проверенных сообщениях

---

## 2. РЕШЕНИЕ

### Добавлено использование `offset_id`

**Изменения:**

1. **В `telethon_retry.py`:**
   - Добавлен параметр `offset_id: Optional[int]` в `fetch_messages_with_retry()`
   - Добавлено `iter_params["offset_id"] = offset_id` при наличии параметра

2. **В `channel_parser.py`:**
   - Добавлено получение `last_message_id` из БД для incremental режима
   - Добавлено использование `offset_id=last_message_id` при запросе сообщений
   - Логирование использования `offset_id` для диагностики

### Логика работы

**Для incremental режима:**
```python
# Получаем последний message_id из БД
last_message_id = MAX(telegram_message_id) FROM posts WHERE channel_id = ...

# Запрашиваем сообщения ПОСЛЕ last_message_id
messages = await fetch_messages_with_retry(
    client,
    channel,
    limit=limit,
    offset_id=last_message_id,  # ✅ Получаем сообщения ПОСЛЕ этого ID
    reverse=False
)
```

**Результат:**
- Telegram API возвращает только сообщения с `message_id > last_message_id`
- Гарантируется получение всех новых сообщений
- Не нужно проверять тысячи старых сообщений

---

## 3. ИЗМЕНЕНИЯ В КОДЕ

### `telethon_retry.py`

**Добавлен параметр `offset_id`:**
```python
async def fetch_messages_with_retry(
    ...
    offset_id: Optional[int] = None,  # ✅ Новый параметр
    ...
):
```

**Добавлено использование `offset_id`:**
```python
if offset_id:
    iter_params["offset_id"] = offset_id
```

### `channel_parser.py`

**Добавлено получение `last_message_id` для incremental режима:**
```python
if mode == "incremental":
    result = await self.db_session.execute(
        text("SELECT MAX(telegram_message_id) FROM posts WHERE channel_id = :channel_id"),
        {"channel_id": channel_id}
    )
    last_message_id = result.scalar()
    if last_message_id:
        offset_id_param = last_message_id
```

**Добавлено использование `offset_id`:**
```python
messages = await fetch_messages_with_retry(
    ...
    offset_id=offset_id_param,  # ✅ Используем для incremental
    ...
)
```

---

## 4. ПРОВЕРКА

### Статус

- ✅ Изменения применены
- ✅ Сервис перезапущен
- ✅ Нет ошибок линтера

### Ожидаемый результат

После следующего тика scheduler:
1. Парсер будет использовать `offset_id = 85783` для banksta
2. Telegram API вернет только сообщения с `message_id > 85783` (включая 85784)
3. Новые посты (85784, 14386, 20638) будут найдены и сохранены

---

## 5. ДИАГНОСТИКА

### Логи для проверки

После следующего тика ищите в логах:
```
"Using offset_id for incremental mode"
"offset_id": 85783 (или другое значение)
```

### Проверка результата

```sql
-- Проверить, появились ли новые посты
SELECT telegram_message_id, created_at 
FROM posts 
WHERE channel_id IN (
    SELECT id FROM channels WHERE username IN ('banksta', 'autoreview2022', 'frank_media')
)
AND telegram_message_id IN (85784, 14386, 20638);
```

---

## ВЫВОДЫ

**Проблема:** Парсер не использовал `offset_id`, что приводило к получению старых сообщений и пропуску новых постов.

**Решение:** ✅ Добавлено использование `offset_id` в incremental режиме для получения сообщений ПОСЛЕ последнего `message_id` из БД.

**Статус:** ✅ Исправлено, требует проверки после следующего тика scheduler.
