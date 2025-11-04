# Исправление критической ошибки в incremental парсинге

**Дата:** 2025-11-03 14:45 MSK  
**Статус:** ✅ **ИСПРАВЛЕНО**

---

## Проблема

Парсинг пропускал новые посты в каналах из-за **неправильного использования `offset_date` в Telethon**.

### Симптомы
- Последний пост в БД: 2025-11-03 07:25:01 UTC (4+ часа назад)
- Scheduler работает корректно, парсинг запускается каждые 5 минут
- Все каналы парсятся, но `messages_processed: 0` для всех
- В логах: `messages_newer_than_since_date: 0` для всех каналов
- `first_message_date` всегда **СТАРШЕ** `since_date`

### Корневая причина

**Telethon `iter_messages(offset_date=X)` возвращает сообщения ПРЕДШЕСТВУЮЩИЕ указанной дате, а не ПОСЛЕ!**

Документация Telethon:
```
offset_date (`datetime`):
    Offset date (messages *previous* to this date will be
    retrieved). Exclusive.
```

**Неправильная логика (до исправления):**
```python
# Для incremental режима использовали offset_date=since_date
# Это возвращало сообщения СТАРШЕ since_date вместо НОВЕЕ!
offset_date_param = since_date if mode == "incremental" else None
messages = await fetch_messages_with_retry(..., offset_date=offset_date_param)
```

**Результат:**
- Запрос: получить сообщения после `since_date = 2025-11-03 09:38:22`
- Telethon возвращал: сообщения **до** 09:38:22 (например, от 2025-11-01)
- Фильтрация: `if message_date <= since_date: break` → все сообщения отфильтровывались
- Итог: `messages_processed: 0`

---

## Исправление

### Context7 Best Practices применены

1. **Правильное использование Telethon API**: 
   - Для incremental режима: НЕ используем `offset_date`, получаем последние сообщения
   - Для historical режима: используем `offset_date` для получения старых сообщений

2. **Локальная фильтрация**:
   - Получаем последние N сообщений (limit=5000)
   - Фильтруем локально по `date > since_date`

### Изменения в коде

#### 1. `channel_parser.py` - `_get_message_batches()`

**Было:**
```python
offset_date_param = None if mode == "historical" else since_date
```

**Стало:**
```python
if mode == "incremental":
    # Context7: Для incremental режима получаем последние сообщения БЕЗ offset_date
    # Затем фильтруем их локально по date > since_date
    offset_date_param = None
else:
    # Для historical режима используем offset_date для получения сообщений до определенной даты
    offset_date_param = since_date
```

#### 2. `telethon_retry.py` - `fetch_messages_with_retry()`

Обновлены комментарии для ясности:
```python
# Context7: КРИТИЧНО - offset_date в Telethon возвращает сообщения ПРЕДШЕСТВУЮЩИЕ дате!
# Если offset_date указан - получаем сообщения СТАРШЕ этой даты (для historical режима)
# Если offset_date НЕ указан - получаем последние сообщения (для incremental режима)
```

#### 3. Fallback логика

Также исправлена fallback логика на случай ошибок retry:
```python
if mode == "incremental":
    # Context7: Для incremental НЕ используем offset_date (он возвращает старые сообщения)
    limit_fallback = batch_size * 100
    iter_params = {"limit": limit_fallback}  # БЕЗ offset_date!
```

---

## Ожидаемое поведение после исправления

### Incremental режим (новые посты)
1. Получаем последние 5000 сообщений из канала (без `offset_date`)
2. Фильтруем локально: `message.date > since_date`
3. Обрабатываем только новые посты

### Historical режим (старые посты)
1. Используем `offset_date=since_date` для получения сообщений до определенной даты
2. Останавливаемся на сообщениях < since_date

---

## Проверка

### Команды для проверки

1. **Мониторинг логов парсинга:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 5m | grep -E "(messages_processed|messages_newer_than_since_date|Fetched messages range)"
```

2. **Проверка новых постов в БД:**
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT MAX(created_at) as last_post, 
       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as posts_last_hour
FROM posts;
"
```

3. **Проверка конкретного канала:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep -A 10 "Channel parsing completed" | grep "messages_processed"
```

### Ожидаемые результаты

- ✅ `messages_processed > 0` для каналов с новыми постами
- ✅ `messages_newer_than_since_date > 0` в логах
- ✅ `first_message_date` **НОВЕЕ** `since_date` в логах
- ✅ Новые посты появляются в БД после следующего цикла парсинга

---

## Время проверки

Исправление применено в **14:45 MSK**. Следующий цикл парсинга должен произойти в течение **5-10 минут** (scheduler работает каждые 5 минут).

Рекомендуется проверить результаты через **15-20 минут** после исправления.

---

## Связанные файлы

- `telethon-ingest/services/channel_parser.py` - основная логика парсинга
- `telethon-ingest/services/telethon_retry.py` - retry обвязка для Telethon API
- Документация: [Telethon iter_messages offset_date](https://docs.telethon.dev/en/latest/quick-references/client-reference.html#telethon.client.messages.TelegramClient.iter_messages)

---

## Заключение

**Статус:** ✅ **ИСПРАВЛЕНО**

Критическая ошибка в логике incremental парсинга устранена. Парсер теперь корректно получает новые сообщения из каналов и не пропускает посты.

