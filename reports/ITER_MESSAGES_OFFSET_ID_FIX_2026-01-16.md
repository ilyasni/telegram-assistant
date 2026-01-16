# КРИТИЧЕСКАЯ ПРОБЛЕМА: Использование offset_id для получения новых сообщений

**Дата**: 2026-01-16 20:35 UTC  
**Проблема**: Telegram API возвращает старые сообщения при запросе БЕЗ offset_id

---

## Context

Парсер запрашивает сообщения БЕЗ `offset_id`, что приводит к тому, что Telegram API возвращает сообщения с датой старше `since_date`. Новые посты (85784, 14386, 20638) не находятся.

---

## 1. ПРОБЛЕМА

### Текущая логика

**Парсер НЕ использует `offset_id`:**
```python
offset_date_param = None  # Не используем offset_date
messages = await fetch_messages_with_retry(
    client,
    channel_for_fetch,
    limit=limit,  # batch_size * 500 для incremental
    offset_date=None,  # ❌ НЕ используем
    reverse=False
)
```

**Результат:**
- Telegram API возвращает последние N сообщений, начиная с самых новых
- Но если новых сообщений нет в первых N, API возвращает старые
- `first_message_date = 2026-01-15` (старое!), `since_date = 2026-01-16 17:22:24` (новое)
- Новые посты (85784) не находятся, так как они не в первых 200 проверенных сообщениях

### Почему не работает без offset_id

**Telegram API поведение:**
- `iter_messages(channel, limit=500)` - возвращает последние 500 сообщений
- Первое сообщение - самое новое в канале
- Но если новых сообщений нет, API возвращает старые

**Проблема:** Если посты опубликованы недавно, они могут быть не в первых N сообщениях из-за:
- Задержки синхронизации API
- Нехронологического порядка
- Ограничений API

---

## 2. РЕШЕНИЕ: Использовать `offset_id`

### Как работает `offset_id` в Telegram API

**Telethon `iter_messages` параметры:**
- `offset_id` - получить сообщения ПОСЛЕ указанного message_id (новее)
- `min_id` - получить сообщения с message_id >= min_id
- `reverse=False` - от новых к старым

**Правильная логика для incremental:**
```python
# Получаем последний message_id из БД
last_message_id = await get_last_message_id(channel_id)

# Запрашиваем сообщения ПОСЛЕ last_message_id
messages = await fetch_messages_with_retry(
    client,
    channel,
    limit=limit,
    offset_id=last_message_id,  # ✅ Получаем сообщения ПОСЛЕ этого ID
    reverse=False
)
```

### Изменения в коде

1. **Добавить получение `last_message_id` из БД:**
   ```python
   async def _get_last_message_id(self, channel_id: str) -> Optional[int]:
       """Получить последний telegram_message_id из БД для канала."""
       result = await self.db_session.execute(
           text("SELECT MAX(telegram_message_id) FROM posts WHERE channel_id = :channel_id"),
           {"channel_id": channel_id}
       )
       return result.scalar()
   ```

2. **Использовать `offset_id` в incremental режиме:**
   ```python
   if mode == "incremental":
       last_message_id = await self._get_last_message_id(channel_id)
       if last_message_id:
           iter_params["offset_id"] = last_message_id
   ```

---

## 3. АЛЬТЕРНАТИВНОЕ РЕШЕНИЕ: Использовать `min_id`

**`min_id` - более надежный вариант:**
- Получает все сообщения с `message_id >= min_id`
- Гарантирует получение всех новых сообщений
- Работает даже если есть gap в message_id

```python
if mode == "incremental":
    last_message_id = await self._get_last_message_id(channel_id)
    if last_message_id:
        iter_params["min_id"] = last_message_id + 1  # Получить все новее
```

---

## 4. ПРИОРИТЕТ ИСПРАВЛЕНИЯ

**Критичность:** P0 (критично)  
**Влияние:** Новые посты не парсятся  
**Решение:** Использовать `offset_id` или `min_id` для incremental режима

---

## 5. ПЛАН ДЕЙСТВИЙ

1. **Добавить функцию получения `last_message_id`**
2. **Изменить `_get_message_batches` для использования `offset_id/min_id` в incremental режиме**
3. **Протестировать на проблемных каналах (banksta, autoreview2022, frank_media)**
4. **Проверить, что новые посты (85784, 14386, 20638) появляются**

---

## ВЫВОДЫ

**Проблема:** Парсер не использует `offset_id` для запроса новых сообщений, что приводит к пропуску новых постов.

**Решение:** Использовать `offset_id` или `min_id` в incremental режиме для получения сообщений ПОСЛЕ последнего `message_id` из БД.
