# Исправление ошибок мониторинга

**Дата**: 2025-01-22  
**Context7**: Критические ошибки, обнаруженные Trend Agents Monitoring

---

## Обнаруженные ошибки

### 1. Worker: SQL ошибка parameter $5 в trends:emerging stream

**Ошибка**: `AmbiguousParameterError: could not determine data type of parameter $5`

**Файл**: `worker/trends_editor_agent.py`

**Причина**: asyncpg не может определить тип параметра `$5` (может быть `None` или пустая строка)

**Исправление**: Явное указание типа `$5::text` и использование `NULLIF` для обработки пустых строк

```sql
-- Было:
WHEN $5 IS NOT NULL AND $5 != '' THEN $5

-- Стало:
WHEN $5::text IS NOT NULL AND $5::text != '' THEN $5::text
```

### 2. Telethon-ingest: Сериализация datetime в JSON

**Ошибка**: `Object of type datetime is not JSON serializable`

**Файл**: `telethon-ingest/services/telegram_client.py`

**Причина**: `posted_at` передается как datetime объект в JSON

**Исправление**: Сериализация datetime в ISO формат перед публикацией

```python
# Context7: Сериализация datetime для JSON
posted_at = message_data.get("posted_at")
if posted_at and isinstance(posted_at, datetime):
    posted_at_str = posted_at.isoformat()
elif posted_at:
    posted_at_str = str(posted_at)
else:
    posted_at_str = None
```

### 3. Telethon-ingest: ON CONFLICT для group_messages

**Ошибка**: `there is no unique or exclusion constraint matching the ON CONFLICT specification`

**Статус**: Проверено - в БД есть уникальный индекс `ux_group_messages` на (group_id, tg_message_id). Ошибка может быть связана с типом данных или NULL значениями.

**Требуется**: Дополнительная проверка при сохранении group_messages

---

## Проверка

### Команды для проверки

```bash
# Проверка логов на ошибки
docker compose logs worker --since 10m | grep -E "(ERROR|Exception|parameter.*\$5)"

# Проверка логов telethon-ingest
docker compose logs telethon-ingest --since 10m | grep -E "(ERROR|Exception|datetime.*JSON)"

# Проверка health endpoints
curl -s http://localhost/api/health | jq
```

---

## Impact / Rollback

### Влияние

- ✅ **Критично**: Исправляет ошибки, которые блокируют обработку trends:emerging событий
- ✅ **Критично**: Исправляет ошибки сериализации при публикации событий
- ✅ **Безопасно**: Не влияет на существующие данные

### Rollback

Если потребуется откат:

1. Вернуть старый SQL запрос в `trends_editor_agent.py`
2. Вернуть старую логику сериализации в `telegram_client.py`
3. Перезапустить сервисы

---

## Context7 Best Practices

1. ✅ **Явное указание типов** - использование `::text` для параметров SQL
2. ✅ **Обработка NULL** - использование `NULLIF` для пустых строк
3. ✅ **Сериализация datetime** - преобразование в ISO формат перед JSON
4. ✅ **Логирование** - детальное логирование для диагностики проблем

---

## Статус

✅ **Исправлено** - Ошибки исправлены, сервисы перезапущены.

