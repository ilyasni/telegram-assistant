# Исправление проблемы блокировок каналов

**Дата**: 2026-01-14T17:00:00+03:00  
**Context7**: Улучшение обработки ошибок при неработающем tg_channel_id

---

## Context

47 каналов заблокированы из-за FloodWait на ResolveUsernameRequest. Причина: каналы имеют `tg_channel_id`, но он не работает (ошибка "Could not find the input entity"), код делает fallback на `username` → получает FloodWait (~17315 секунд).

---

## Проблема

### Симптомы:
- 47 каналов заблокированы до ~20:23 UTC (4.1 часа)
- Все заблокированные каналы имеют `tg_channel_id`
- При ошибке "Could not find the input entity" код делает fallback на `username`
- Fallback на `username` вызывает FloodWait на ResolveUsernameRequest
- Устанавливается `blocked_until` на реальное время FloodWait (~4.8 часов)

### Причина:
1. `tg_channel_id` неверный или канал недоступен
2. Код делает fallback на `username` без проверки типа ошибки
3. ResolveUsernameRequest вызывает большой FloodWait

---

## Решение

### 1. Улучшена обработка ошибки "Could not find the input entity"

**Файл**: `telethon-ingest/services/channel_parser.py` (строки ~836-850)

**Изменения**:
- Добавлена проверка типа ошибки перед fallback на `username`
- При ошибке "Could not find the input entity" НЕ используется `username` как fallback
- Вместо этого устанавливается короткая блокировка (1 час) для повторной попытки
- Для других ошибок (Timeout, Connection) fallback на `username` сохраняется

**Код**:
```python
except Exception as e:
    error_str = str(e).lower()
    is_entity_not_found = "could not find the input entity" in error_str
    
    if is_entity_not_found:
        # Context7: tg_channel_id неверный или канал недоступен
        # НЕ используем username как fallback, чтобы избежать FloodWait
        logger.warning("tg_channel_id not found, skipping username fallback to avoid FloodWait",
                     channel_id=channel_id,
                     tg_channel_id=tg_channel_id_db,
                     error=str(e))
        # Устанавливаем короткую блокировку для повторной попытки (1 час)
        blocked_until = datetime.now(timezone.utc) + timedelta(hours=1)
        await self.db_session.execute(
            text("UPDATE channels SET blocked_until = :blocked_until WHERE id = :channel_id"),
            {"blocked_until": blocked_until, "channel_id": channel_id}
        )
        await self.db_session.commit()
        return None
    else:
        # Для других ошибок можно попробовать username
        logger.warning("Failed to get entity by tg_channel_id, trying username",
                     channel_id=channel_id,
                     tg_channel_id=tg_channel_id_db,
                     error=str(e))
        # ... fallback на username
```

### 2. Добавлена автоматическая очистка истекших блокировок

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py` (строка ~1475)

**Изменения**:
- Перед обработкой каналов очищаются истекшие блокировки (`blocked_until < NOW()`)
- Это улучшает читаемость данных и предотвращает накопление "мертвых" блокировок

**Код**:
```python
# Context7: Очистка истекших блокировок перед обработкой каналов
try:
    cursor.execute("""
        UPDATE channels 
        SET blocked_until = NULL 
        WHERE blocked_until IS NOT NULL 
            AND blocked_until < NOW()
    """)
    cleared_count = cursor.rowcount
    if cleared_count > 0:
        logger.debug("Cleared expired blocked_until",
                   cleared_count=cleared_count)
except Exception as cleanup_error:
    logger.warning("Failed to clear expired blocked_until",
                 error=str(cleanup_error))
```

### 3. Создан скрипт для проверки и обновления неверных tg_channel_id

**Файл**: `scripts/validate_and_update_tg_channel_ids.py` (новый)

**Функционал**:
- Проверяет все каналы с `tg_channel_id`
- Пытается получить entity по `tg_channel_id`
- Если не работает → пытается получить по `username`
- Если работает по `username` → обновляет `tg_channel_id` в БД
- Поддерживает dry-run режим (по умолчанию)

**Использование**:
```bash
# Dry-run (только проверка)
docker compose exec api python3 /opt/telegram-assistant/scripts/validate_and_update_tg_channel_ids.py

# Применить изменения
docker compose exec api python3 /opt/telegram-assistant/scripts/validate_and_update_tg_channel_ids.py --apply
```

---

## Ожидаемый результат

После применения исправлений:
1. ✅ Каналы с неверным `tg_channel_id` не будут получать FloodWait на username
2. ✅ Блокировки будут устанавливаться на короткое время (1 час) вместо ~4.8 часов
3. ✅ Истекшие блокировки будут автоматически очищаться
4. ✅ Каналы с неверным `tg_channel_id` можно будет обновить через скрипт
5. ✅ Меньше каналов будет заблокировано в будущем

---

## Checks

После применения исправлений:

```bash
# Проверка блокировок
docker compose exec api python3 -c "
import asyncio
import asyncpg
async def check():
    conn = await asyncpg.connect(host='supabase-db', port=5432, user='postgres', password='postgres', database='postgres')
    row = await conn.fetchrow('SELECT COUNT(*) as blocked FROM channels WHERE blocked_until IS NOT NULL AND blocked_until > NOW()')
    print(f'Заблокировано: {row[\"blocked\"]}')
    await conn.close()
asyncio.run(check())
"

# Проверка логов на "tg_channel_id not found, skipping username fallback"
docker compose logs telethon-ingest --tail=100 | grep -i "skipping username fallback"

# Проверка очистки истекших блокировок
docker compose logs telethon-ingest --tail=100 | grep -i "Cleared expired blocked_until"
```

---

## Impact / Rollback

**Impact**:
- ✅ Меньше FloodWait на ResolveUsernameRequest
- ✅ Более короткие блокировки (1 час вместо ~4.8 часов)
- ✅ Лучшая обработка ошибок
- ✅ Автоматическая очистка истекших блокировок

**Rollback**: Если нужно вернуть старую логику, восстановите fallback на username в `channel_parser.py` (строка ~836)

---

**Context7 Best Practices**: Все исправления следуют Context7 best practices для resilience и observability. Улучшенная обработка ошибок предотвращает ненужные FloodWait и уменьшает время блокировок.
