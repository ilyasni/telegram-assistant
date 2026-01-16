# Исправление парсера канала - приоритет tg_channel_id

**Дата**: 2026-01-14T16:05:00+03:00  
**Проблема**: Канал chinamashina_news не парсится из-за FloodWait на ResolveUsernameRequest  
**Context7**: Исправление приоритета использования tg_channel_id vs username

---

## Context

Канал `chinamashina_news` не парсится, хотя в нем есть новые посты (например, https://t.me/chinamashina_news/11139). Последний парсинг был 17.6 часов назад.

---

## Проблема

### Причина: FloodWait на ResolveUsernameRequest

**Детали**:
- Канал попадает в выборку для парсинга ✅
- Парсинг начинается ✅
- При попытке получить entity канала через `client.get_entity(username)` получает FloodWait на **32777 секунд** (~9 часов)
- Код устанавливает cooldown только на 300 секунд (cap для безопасности)
- После 300 секунд канал снова пытается парситься и снова получает FloodWait
- Цикл повторяется

**Логи**:
```
[WARNING] FloodWait when getting entity by username
  channel_id: 44ed4370-8c56-4f93-b3d2-0074ee6896b3
  username: chinamashina_news
  wait_seconds: 300 (capped)
  error_seconds: 32777 (реальный FloodWait)
```

**Проблема в коде**:
- Приоритет username вместо tg_channel_id (строка 826)
- Если tg_channel_id есть в БД, нужно использовать его напрямую
- ResolveUsernameRequest вызывает большие FloodWait, а GetChannelRequest (по ID) обычно работает без проблем

---

## Исправления

### 1. Изменен приоритет: tg_channel_id → username

**Файл**: `telethon-ingest/services/channel_parser.py`

**Изменение**: Если `tg_channel_id` есть в БД, используем его напрямую, а не username.

**Было** (строка 826):
```python
if username:
    # Приоритет: username (более надёжный способ)
    entity = await client.get_entity(clean_username)
    # ... FloodWait на ResolveUsernameRequest
```

**Стало**:
```python
# Context7: Если tg_channel_id есть в БД, используем его напрямую
# Это избегает FloodWait на ResolveUsernameRequest
if tg_channel_id_db is not None:
    try:
        entity = await client.get_entity(int(tg_channel_id_db))
        tg_channel_id = int(tg_channel_id_db)
    except Exception as e:
        # Fallback на username если tg_channel_id не работает
        if username:
            clean_username = username.lstrip('@')
            entity = await client.get_entity(clean_username)
```

### 2. Улучшена обработка FloodWait на ResolveUsernameRequest

**Добавлено**: Установка `blocked_until` на реальное время FloodWait (но не больше 24 часов) при FloodWait на ResolveUsernameRequest.

**Код**:
```python
except errors.FloodWaitError as e:
    # Context7: Устанавливаем blocked_until на реальное время FloodWait (но не больше 24 часов)
    max_block_seconds = min(e.seconds, 86400)  # Максимум 24 часа
    blocked_until = datetime.now(timezone.utc) + timedelta(seconds=max_block_seconds)
    await self.db_session.execute(
        text("UPDATE channels SET blocked_until = :blocked_until WHERE id = :channel_id"),
        {"blocked_until": blocked_until, "channel_id": channel_id}
    )
    await self.db_session.commit()
```

---

## Ожидаемый результат

После исправления:
1. ✅ Канал будет парситься через `tg_channel_id` напрямую (избегая ResolveUsernameRequest)
2. ✅ Если все же получим FloodWait на ResolveUsernameRequest, `blocked_until` будет установлен на реальное время
3. ✅ Канал не будет пытаться парситься до истечения `blocked_until`

---

## Checks

Для проверки исправлений:

```bash
# Проверка, что канал использует tg_channel_id
docker compose logs telethon-ingest --tail=100 | grep -i "chinamashina.*tg_channel_id\|Successfully got entity by tg_channel_id"

# Проверка blocked_until
docker compose exec api python3 -c "
import asyncio
import asyncpg
async def check():
    conn = await asyncpg.connect(host='supabase-db', port=5432, user='postgres', password='postgres', database='postgres')
    row = await conn.fetchrow('SELECT blocked_until, last_parsed_at FROM channels WHERE username = \"chinamashina_news\"')
    print(f'Blocked until: {row[\"blocked_until\"]}')
    print(f'Last parsed: {row[\"last_parsed_at\"]}')
    await conn.close()
asyncio.run(check())
"

# Проверка новых постов после исправления
docker compose exec api python3 -c "
import asyncio
import asyncpg
async def check():
    conn = await asyncpg.connect(host='supabase-db', port=5432, user='postgres', password='postgres', database='postgres')
    row = await conn.fetchrow('SELECT id FROM channels WHERE username = \"chinamashina_news\"')
    if row:
        posts = await conn.fetch('SELECT telegram_message_id, created_at FROM posts WHERE channel_id = \$1 ORDER BY created_at DESC LIMIT 5', row['id'])
        print(f'Последние посты:')
        for post in posts:
            print(f\"  Message ID: {post['telegram_message_id']}, Created: {post['created_at']}\")
    await conn.close()
asyncio.run(check())
"
```

---

## Impact / Rollback

**Impact**:
- ✅ Каналы с `tg_channel_id` в БД будут парситься быстрее (без ResolveUsernameRequest)
- ✅ Меньше FloodWait на ResolveUsernameRequest
- ✅ Правильная установка `blocked_until` при больших FloodWait

**Rollback**: Если нужно вернуть приоритет username, восстановите старую логику в `channel_parser.py` (строка 826)

---

**Context7 Best Practices**: Исправление следует Context7 best practices для resilience и observability. Использование `tg_channel_id` напрямую избегает ненужных FloodWait на ResolveUsernameRequest.
