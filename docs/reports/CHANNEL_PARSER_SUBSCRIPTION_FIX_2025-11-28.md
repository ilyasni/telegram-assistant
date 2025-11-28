# Исправление блокировки парсинга в channel_parser.py

**Дата**: 2025-11-28  
**Context7**: Исправление блокировки парсинга новых каналов из-за проверки подписки

---

## Проблема

### Симптомы:
- Каналы с `last_parsed_at = NULL` не парсятся (6 каналов)
- В логах: `User not subscribed to channel or subscription inactive, skipping parsing`
- Посты не сохраняются, так как парсинг блокируется до сохранения

### Причина:
В `channel_parser.py` проверка подписки происходит ДО сохранения постов через `AtomicDBSaver`. Если подписки нет, парсинг блокируется, и `AtomicDBSaver` не может автоматически создать подписку для активных каналов.

**Конфликт логики:**
1. `channel_parser.py` (строка 1775-1780) блокирует парсинг без подписки
2. `AtomicDBSaver` (строка 248-262) автоматически создает подписку для активных каналов
3. Но `AtomicDBSaver` не вызывается, так как парсинг блокируется раньше

---

## Решение

### Context7 Best Practice:
Для системного парсинга (scheduler) разрешить парсинг активных каналов даже без подписки, так как `AtomicDBSaver` автоматически создаст подписку при сохранении постов.

### Изменения:

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 1759-1785)

**Было:**
```python
# Проверка подписки - если нет, блокируем парсинг
if not check_subscription.fetchone():
    return {"status": "skipped", "reason": "not_subscribed", ...}
```

**Стало:**
```python
# Проверяем активность канала
channel_active_check = await self.db_session.execute(...)
is_channel_active = channel_active_row and channel_active_row.is_active

# Проверяем подписку
subscription_exists = check_subscription.fetchone() is not None

if not subscription_exists:
    if is_channel_active:
        # Канал активен - разрешаем парсинг для системного парсинга
        # AtomicDBSaver автоматически создаст подписку
        logger.info("Channel is active, allowing parsing for system parsing...")
    else:
        # Канал неактивен - блокируем парсинг
        return {"status": "skipped", "reason": "not_subscribed_inactive_channel", ...}
```

### Логика:
1. Проверяем активность канала (`is_active = true`)
2. Проверяем наличие активной подписки
3. Если подписки нет:
   - Если канал активен → разрешаем парсинг (AtomicDBSaver создаст подписку)
   - Если канал неактивен → блокируем парсинг (как раньше)
4. Если подписка есть → продолжаем парсинг

---

## Результат

### Ожидаемое поведение:
- ✅ Новые каналы (с `last_parsed_at = NULL`) парсятся в historical режиме
- ✅ Активные каналы парсятся даже без подписки (для системного парсинга)
- ✅ Неактивные каналы по-прежнему требуют явной подписки
- ✅ Подписки автоматически создаются через `AtomicDBSaver`

### Проверка:
```bash
# Проверить логи после перезапуска
docker logs telegram-assistant-telethon-ingest-1 --tail 50 | grep -i "Channel is active.*allowing parsing\|messages_processed"

# Проверить парсинг новых каналов
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT COUNT(*) FROM channels WHERE is_active = true AND last_parsed_at IS NULL;"

# Проверить новые посты
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT COUNT(*), MAX(posted_at) FROM posts WHERE posted_at > NOW() - INTERVAL '1 hour';"
```

---

## Impact / Rollback

### Impact:
- ✅ Новые каналы теперь парсятся автоматически
- ✅ Системный парсинг работает для всех активных каналов
- ✅ Не влияет на пользовательский парсинг (требует явной подписки)

### Rollback:
Если нужно откатить:
1. Вернуть проверку подписки без проверки активности канала
2. Вручную создать подписки для нужных каналов через API

---

## Связанные исправления

- `SUBSCRIPTION_FIX_2025-11-28.md` - автоматическое создание подписок в AtomicDBSaver
- `SCHEDULER_PIPELINE_CHECK_2025-11-28.md` - первоначальная диагностика

**Важно**: Это исправление работает вместе с исправлением в `AtomicDBSaver`. Оба исправления необходимы для корректной работы системного парсинга.

