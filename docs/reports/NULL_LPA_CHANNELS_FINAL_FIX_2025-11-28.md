# Финальное исправление парсинга каналов с last_parsed_at = NULL

**Дата**: 2025-11-28  
**Context7**: Исправление обработки результатов парсинга с ошибками и обновление last_parsed_at

---

## Проблема

7 каналов с `last_parsed_at = NULL` не парсятся, хотя scheduler их выбирает. Причина:

1. `parse_channel_messages` возвращает результат с `status: 'error'` когда `_get_channel_entity` возвращает `None`
2. В `_run_tick` результат с `status: 'error'` не обрабатывается специально, попадает в блок "Неожиданный формат"
3. `last_parsed_at` не обновляется для таких результатов, каналы остаются с `NULL` и блокируют scheduler

---

## Решение

### Context7 Best Practice:
Обработка ошибок парсинга с обновлением `last_parsed_at` для отслеживания попыток и предотвращения бесконечных попыток парсинга каналов, которые не могут быть спарсены.

### Изменения:

#### 1. Обработка результата с `status: 'error'` в `_run_tick`

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py` (строки 577-588)

**Добавлено:**
```python
elif result and result.get("status") == "error":
    # Context7: Обработка ошибок парсинга (channel_not_found и др.)
    error_type = result.get("error", "unknown")
    logger.warning(
        "Channel parsing failed with error",
        channel_id=channel['id'],
        channel_title=channel.get('title'),
        channel_username=channel.get('username'),
        mode=mode,
        error_type=error_type
    )
    parser_runs_total.labels(mode=mode, status="error").inc()
    # Context7: Обновляем last_parsed_at даже при ошибке для отслеживания попыток
    # Это предотвращает бесконечные попытки парсинга каналов, которые не могут быть спарсены
    await self._update_last_parsed_at(channel['id'])
```

#### 2. Метод `_update_last_parsed_at` в `ParseAllChannelsTask`

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py` (после строки 279)

**Добавлено:**
```python
async def _update_last_parsed_at(self, channel_id: str):
    """
    Context7: Обновление last_parsed_at для отслеживания попыток парсинга.
    Используется для каналов с ошибками, чтобы они не оставались с NULL и не блокировали scheduler.
    """
    try:
        import psycopg2
        conn = psycopg2.connect(self.db_url)
        cursor = conn.cursor()
        
        # Context7: Обновляем last_parsed_at на текущее время для отслеживания попытки парсинга
        cursor.execute("""
            UPDATE channels 
            SET last_parsed_at = NOW() 
            WHERE id = %s
        """, (channel_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.debug("Updated last_parsed_at for channel with error",
                    channel_id=channel_id)
    except Exception as e:
        logger.error("Failed to update last_parsed_at for channel with error",
                    channel_id=channel_id,
                    error=str(e),
                    error_type=type(e).__name__)
```

#### 3. Улучшение логирования в `channel_parser.py`

**Файл**: `telethon-ingest/services/channel_parser.py` (строки 397-416)

**Улучшено:**
- Добавлено получение информации о канале из БД для лучшей диагностики
- Добавлены поля `channel_title`, `channel_username`, `channel_tg_channel_id` в логирование
- Добавлены флаги `has_username` и `has_tg_channel_id` для диагностики

---

## Результат

### Ожидаемое поведение:
- ✅ Результаты с `status: 'error'` обрабатываются специально
- ✅ `last_parsed_at` обновляется даже при ошибках для отслеживания попыток
- ✅ Каналы не блокируют scheduler бесконечными попытками
- ✅ Улучшена диагностика через детальное логирование
- ✅ Метрики `parser_runs_total` с `status="error"` для мониторинга

### Проверка:
```bash
# Проверить логи после перезапуска
docker logs telegram-assistant-telethon-ingest-1 --tail 50 | grep -E "Channel parsing failed with error|Updated last_parsed_at for channel with error"

# Проверить каналы с NULL last_parsed_at
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT COUNT(*) FROM channels WHERE is_active = true AND last_parsed_at IS NULL;"

# Проверить метрики
docker exec telegram-assistant-telethon-ingest-1 curl -s http://localhost:8000/metrics | grep parser_runs_total | grep status=error
```

---

## Impact / Rollback

### Impact:
- ✅ Каналы с ошибками не остаются с `NULL` last_parsed_at
- ✅ Scheduler не блокируется бесконечными попытками парсинга
- ✅ Улучшена диагностика проблемных каналов
- ✅ Метрики для мониторинга ошибок парсинга

### Rollback:
Если нужно откатить:
1. Убрать обработку `status: 'error'` в `_run_tick`
2. Убрать метод `_update_last_parsed_at`
3. Вернуть логирование к предыдущей версии

---

## Связанные исправления

- `NULL_LPA_CHANNELS_FIX_2025-11-28.md` - первоначальное исправление обработки ошибок
- `PROBLEMS_FIX_SUMMARY_2025-11-28.md` - итоговый отчет по проблемам
- `FINAL_STATUS_2025-11-28.md` - финальный статус

**Важно**: Это исправление завершает решение проблемы с каналами, которые не парсятся из-за ошибок получения entity.

