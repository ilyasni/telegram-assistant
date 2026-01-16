# Исправление обработки каналов без tg_channel_id

**Дата**: 2026-01-14T17:15:00+03:00  
**Context7**: Исправление логики обработки каналов без tg_channel_id

---

## Context

Вопрос: Что будет с каналами, у которых нет `tg_channel_id` или он неверный?

---

## Проблема

### Текущая ситуация

**Каналы БЕЗ `tg_channel_id`**:
- Всего: 26 каналов
- Заблокированы: 0
- Никогда не парсились: 26 (100%)

**Проблема в коде**:
- Логика проверяет `if tg_channel_id_db is not None` первой
- Если `tg_channel_id_db IS NULL`, но `username IS NOT NULL`, код не обрабатывает этот случай
- Каналы без `tg_channel_id` пропускаются, даже если у них есть `username`

---

## Решение

### Исправлена логика обработки каналов без `tg_channel_id`

**Файл**: `telethon-ingest/services/channel_parser.py` (строки ~976-1020)

**Изменения**:

1. **Добавлена обработка каналов БЕЗ `tg_channel_id`, но С `username`**:
   - Используется `username` для получения entity
   - Автоматически заполняется `tg_channel_id` из полученного entity
   - `tg_channel_id` сохраняется в БД для будущих использований

2. **Удален дублирующий блок `elif tg_channel_id_db is not None`**:
   - Этот блок никогда не выполнялся (так как уже был `if tg_channel_id_db is not None` выше)
   - Заменен на `elif username:` для обработки каналов без `tg_channel_id`

**Код**:
```python
elif username:
    # Context7: Канал БЕЗ tg_channel_id, но С username
    # Используем username для получения entity и автоматически заполняем tg_channel_id
    clean_username = username.lstrip('@')
    try:
        entity = await client.get_entity(clean_username)
        # Получаем tg_channel_id из entity
        tg_channel_id = utils.get_peer_id(PeerChannel(entity.id))
        
        # Автоматически сохраняем tg_channel_id в БД
        await self.db_session.execute(
            text("UPDATE channels SET tg_channel_id = :tg_channel_id WHERE id = :channel_id"),
            {"tg_channel_id": tg_channel_id, "channel_id": channel_id}
        )
        await self.db_session.commit()
    except errors.FloodWaitError as e:
        # Обработка FloodWait
        ...
```

---

## Поведение после исправления

### 1. Каналы БЕЗ `tg_channel_id`, но С `username` (26 каналов)

**До исправления**:
- ❌ Не обрабатывались (никогда не парсились)
- Пропускались scheduler'ом

**После исправления**:
- ✅ Используется `username` для получения entity
- ✅ Автоматически заполняется `tg_channel_id` из полученного entity
- ✅ `tg_channel_id` сохраняется в БД
- ✅ В следующих тиках используется `tg_channel_id` напрямую (избегая FloodWait)

### 2. Каналы с НЕВЕРНЫМ `tg_channel_id` (47 каналов)

**До исправления**:
- ⚠️ Блокировались на 1 час
- ⚠️ После истечения блокировки снова блокировались
- ⚠️ Цикл повторялся бесконечно

**После исправления**:
- ⚠️ Блокируются на 1 час (без изменений)
- ✅ Можно использовать скрипт `validate_and_update_tg_channel_ids.py` для обновления
- ✅ После обновления `tg_channel_id` канал начнет парситься

### 3. Каналы БЕЗ `tg_channel_id` и БЕЗ `username`

**Поведение**:
- ❌ Не могут быть обработаны (нет способа получить entity)
- Логируется предупреждение
- Возвращается `None` (канал пропускается)

---

## Рекомендации

### 1. Для каналов без `tg_channel_id` (26 каналов)

**Действие**: Ничего не требуется
- После исправления каналы будут автоматически обрабатываться через `username`
- `tg_channel_id` будет автоматически заполнен при первом парсинге

### 2. Для каналов с неверным `tg_channel_id` (47 каналов)

**Действие**: Использовать скрипт для обновления
```bash
# Проверка (dry-run)
docker compose exec api python3 /opt/telegram-assistant/scripts/validate_and_update_tg_channel_ids.py

# Применить изменения
docker compose exec api python3 /opt/telegram-assistant/scripts/validate_and_update_tg_channel_ids.py --apply
```

---

## Ожидаемый результат

После применения исправлений:

1. ✅ **26 каналов без `tg_channel_id`** начнут парситься через `username`
2. ✅ `tg_channel_id` будет автоматически заполнен при первом парсинге
3. ✅ В следующих тиках каналы будут использовать `tg_channel_id` напрямую (избегая FloodWait)
4. ✅ **47 каналов с неверным `tg_channel_id`** можно обновить через скрипт

---

## Checks

После применения исправлений:

```bash
# Проверка каналов без tg_channel_id
docker compose exec api python3 -c "
import asyncio
import asyncpg
async def check():
    conn = await asyncpg.connect(host='supabase-db', port=5432, user='postgres', password='postgres', database='postgres')
    row = await conn.fetchrow('SELECT COUNT(*) as count FROM channels WHERE is_active = true AND tg_channel_id IS NULL AND EXISTS (SELECT 1 FROM user_channel uc WHERE uc.channel_id = channels.id AND uc.is_active = true)')
    print(f'Каналов без tg_channel_id: {row[\"count\"]}')
    await conn.close()
asyncio.run(check())
"

# Проверка логов на "Auto-updated tg_channel_id from username"
docker compose logs telethon-ingest --tail=100 | grep -i "Auto-updated tg_channel_id from username"
```

---

## Impact / Rollback

**Impact**:
- ✅ Каналы без `tg_channel_id` начнут парситься
- ✅ Автоматическое заполнение `tg_channel_id` улучшит производительность
- ✅ Меньше каналов будет пропущено

**Rollback**: Если нужно вернуть старую логику, восстановите блок `elif tg_channel_id_db is not None` в `channel_parser.py`

---

**Context7 Best Practices**: Исправление следует Context7 best practices для resilience и observability. Автоматическое заполнение `tg_channel_id` улучшает производительность и уменьшает FloodWait.
