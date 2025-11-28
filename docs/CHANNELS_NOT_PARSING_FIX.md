# Исправление проблемы: каналы не парсятся

## Context

Обнаружена проблема: каналы с подписчиками и `tg_channel_id` не парсятся (last_parsed_at = NULL).

## Причины

### 1. Ошибка типа данных в проверке подписки

**Проблема:**
В `channel_parser.py` при проверке подписки пользователя `user_id` передается как строка, но в SQL запросе `telegram_id` должен быть integer.

**Ошибка в логах:**
```
invalid input for query argument $1: '105957884' ('str' object cannot be interpreted as an integer)
```

**Места:**
- `telethon-ingest/services/channel_parser.py:1758` - проверка подписки при отсутствии новых постов
- `telethon-ingest/services/channel_parser.py:2146` - проверка подписки при обработке постов

**Исправление:**
Преобразование `user_id` в `int` перед передачей в SQL запрос:
```python
telegram_id_int = int(user_id) if isinstance(user_id, str) else user_id
```

### 2. Scheduler выбирает каналы правильно

Scheduler работает и выбирает каналы через `_get_active_channels()`, который:
- Выбирает активные каналы (`is_active = true`)
- Сортирует по `last_parsed_at NULLS FIRST` (новые каналы первыми)
- Ограничивает лимитом `PARSER_MAX_CHANNELS_PER_TICK` (по умолчанию 100)

## Исправления

### Файл: `telethon-ingest/services/channel_parser.py`

1. **Строка 1750-1759**: Добавлено преобразование `user_id` в `int`:
```python
telegram_id_int = int(user_id) if isinstance(user_id, str) else user_id
check_subscription = await self.db_session.execute(
    text("""
        SELECT user_id FROM user_channel 
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1)
          AND channel_id = :channel_id
          AND is_active = true
        LIMIT 1
    """),
    {"telegram_id": telegram_id_int, "channel_id": channel_id}
)
```

2. **Строка 2146**: Аналогичное исправление для второго места.

## Проверка

### 1. Проверить каналы без парсинга

```sql
SELECT 
    c.id,
    c.username,
    c.is_active,
    c.tg_channel_id IS NOT NULL as has_tg_id,
    c.last_parsed_at,
    (SELECT COUNT(*) FROM user_channel uc WHERE uc.channel_id = c.id AND uc.is_active = true) as subscribers
FROM channels c
WHERE c.is_active = true
  AND (c.last_parsed_at IS NULL OR c.last_parsed_at < c.created_at)
ORDER BY c.created_at DESC;
```

### 2. Проверить логи после исправления

```bash
docker logs telegram-assistant-telethon-ingest-1 --tail 100 | grep -iE "beer|prostopropivo|parsing|subscription"
```

### 3. Проверить работу scheduler

```bash
docker logs telegram-assistant-telethon-ingest-1 --tail 500 | grep -iE "scheduler|Active channels|Starting scheduler"
```

## Ожидаемый результат

После исправления:
1. Каналы с подписчиками должны парситься
2. `last_parsed_at` должен обновляться после парсинга
3. Ошибки типа данных в проверке подписки должны исчезнуть

## Impact / Rollback

### Изменения
- `telethon-ingest/services/channel_parser.py`: исправлена проверка подписки (2 места)

### Откат
- Можно откатить через `git revert`
- Изменения минимальны и безопасны

### Риски
- Низкий риск - только исправление типа данных
- Улучшает стабильность парсинга

