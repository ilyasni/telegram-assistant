# Исправление парсинга каналов с last_parsed_at = NULL

**Дата**: 2025-11-28  
**Context7**: Исправление блокировки парсинга каналов без tg_channel_id

---

## Проблема

### Симптомы:
- 8 каналов с `last_parsed_at = NULL` не парсятся
- В логах нет записей "Channel parsing status" для этих каналов
- Каналы загружаются (Loaded channel), но парсинг не запускается

### Причина:
В `channel_parser.py` функция `_get_channel_entity` возвращает `None` для каналов без `tg_channel_id` или `username`, что приводит к исключению `ValueError("Channel {channel_id} not found")` и блокировке парсинга.

**Проблемные каналы:**
- `dvapiva` (59b172a0-26f3-4a40-a9ff-d219dfa1c026) - нет `tg_channel_id`
- `vpoiskahpiva` (fb5f8d45-33d4-49e2-bce8-c1ef5e0c1593) - нет `tg_channel_id`
- Остальные 6 каналов имеют `tg_channel_id`, но не парсятся по другим причинам

---

## Решение

### Context7 Best Practice:
Улучшенная обработка ошибок для graceful degradation - вместо исключения возвращаем результат с ошибкой, чтобы не блокировать парсинг других каналов.

### Изменения:

**Файл**: `telethon-ingest/services/channel_parser.py`

#### 1. Улучшена обработка ошибок в `parse_channel_messages` (строки 397-410)

**Было:**
```python
channel_result = await self._get_channel_entity(telegram_client, channel_id)
if not channel_result:
    raise ValueError(f"Channel {channel_id} not found")
```

**Стало:**
```python
channel_result = await self._get_channel_entity(telegram_client, channel_id)
if not channel_result:
    # Context7: Логируем детальную информацию для диагностики
    logger.error(
        "Failed to get channel entity - channel may be missing tg_channel_id or inaccessible",
        channel_id=channel_id,
        user_id=user_id,
        mode=mode
    )
    # Context7: Возвращаем результат с ошибкой вместо исключения для graceful degradation
    self.stats['errors'] += 1
    return {
        'status': 'error',
        'error': 'channel_not_found',
        'processed': 0,
        'skipped': 0,
        'max_date': None,
        'messages_processed': 0
    }
```

#### 2. Улучшено логирование в `_get_channel_entity` (строки 750-752, 745-748)

**Было:**
```python
logger.error("No username and no tg_channel_id", channel_id=channel_id, title=title)
return None
```

**Стало:**
```python
logger.warning(
    "Channel has no username and no tg_channel_id, cannot resolve entity",
    channel_id=channel_id,
    title=title,
    username=username
)
# Context7: Метрика для отслеживания каналов без tg_channel_id
channel_not_found_total.labels(exists_in_db='true').inc()
return None
```

---

## Результат

### Ожидаемое поведение:
- ✅ Каналы без `tg_channel_id` логируются с предупреждением, но не блокируют парсинг других каналов
- ✅ Метрики `channel_not_found_total` отслеживают проблемные каналы
- ✅ Парсинг других каналов продолжается без прерывания

### Проверка:
```bash
# Проверить логи после перезапуска
docker logs telegram-assistant-telethon-ingest-1 --tail 50 | grep -i "channel.*not found\|cannot resolve entity"

# Проверить метрики
docker exec telegram-assistant-telethon-ingest-1 curl -s http://localhost:8000/metrics | grep channel_not_found_total

# Проверить каналы без tg_channel_id
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT id, title, username, tg_channel_id FROM channels WHERE is_active = true AND tg_channel_id IS NULL;"
```

---

## Impact / Rollback

### Impact:
- ✅ Парсинг не блокируется для каналов без `tg_channel_id`
- ✅ Улучшена диагностика проблемных каналов
- ✅ Метрики для мониторинга

### Rollback:
Если нужно откатить:
1. Вернуть `raise ValueError` вместо возврата результата с ошибкой
2. Убрать метрики `channel_not_found_total`

---

## Следующие шаги

1. **Заполнение tg_channel_id**: Для каналов без `tg_channel_id` нужно заполнить их через Telegram API
2. **Мониторинг**: Следить за метриками `channel_not_found_total` для выявления проблемных каналов
3. **Автоматическое заполнение**: Рассмотреть автоматическое заполнение `tg_channel_id` при создании канала

---

## Связанные исправления

- `CHANNEL_PARSER_SUBSCRIPTION_FIX_2025-11-28.md` - исправление проверки подписки
- `FINAL_PIPELINE_FIX_SUMMARY_2025-11-28.md` - итоговый отчет

