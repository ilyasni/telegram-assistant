# Исправление нормализации username каналов

**Дата:** 2025-11-03 18:00 MSK  
**Статус:** ✅ **ИСПРАВЛЕНО**

---

## Проблема

В базе данных username каналов хранятся в разных форматах:
- С `@` в начале: `@mosinkru`
- Без `@`: `mosinkru`

Это вызывало проблемы:
1. **Дублирование каналов** при подписке (поиск не находил существующий канал)
2. **Ошибки в `telegram_client.py`** (использование username с `@` без нормализации)
3. **Проблемы с ручным парсингом** (поиск не находил каналы с другим форматом)

---

## Решение

### 1. Нормализация данных в БД

Выполнен SQL UPDATE для удаления `@` из начала всех username:

```sql
UPDATE channels
SET username = LTRIM(username, '@')
WHERE username IS NOT NULL AND username LIKE '@%';
```

**Результат:** Все username в БД теперь хранятся без `@` в начале.

### 2. Исправление поиска каналов

**Файл:** `api/routers/channels.py`

**Изменения:**
```python
# БЫЛО:
existing_result = db.execute(
    text("SELECT id FROM channels WHERE username = :username"),
    {"username": normalized_username}
)

# СТАЛО:
# Context7: Поиск с нормализацией username в SQL
# Используем LTRIM для поиска, чтобы найти каналы как с @, так и без @
existing_result = db.execute(
    text("SELECT id FROM channels WHERE LTRIM(username, '@') = :username"),
    {"username": normalized_username}
)
```

**Эффект:** Поиск теперь находит каналы независимо от наличия `@` в БД (обратная совместимость).

### 3. Исправление telegram_client.py

**Файл:** `telethon-ingest/services/telegram_client.py`

**Изменения:**
```python
# БЫЛО:
if channel['username']:
    entity = await self.client.get_entity(channel['username'])

# СТАЛО:
# Context7: Нормализация username перед использованием
# Убираем @ из начала username, так как Telethon ожидает username без @
if channel['username']:
    clean_username = channel['username'].lstrip('@')
    entity = await self.client.get_entity(clean_username)
```

**Эффект:** Исключены ошибки при загрузке каналов с `@` в username.

### 4. Исправление manual_parse_channel.py

**Файл:** `telethon-ingest/scripts/manual_parse_channel.py`

**Изменения:**
```python
# БЫЛО:
WHERE c.username = :username AND c.is_active = true

# СТАЛО:
WHERE LTRIM(c.username, '@') = LTRIM(:username, '@') AND c.is_active = true
```

**Эффект:** Поиск работает независимо от формата username в БД и входного параметра.

### 5. Нормализация при сохранении

**Файл:** `telethon-ingest/services/atomic_db_saver.py`

**Изменения:**
```python
# БЫЛО:
'username': channel_data.get('username', ''),

# СТАЛО:
# Context7: Нормализация username перед сохранением в БД
username_raw = channel_data.get('username', '')
username_normalized = username_raw.lstrip('@') if username_raw else ''
'username': username_normalized,  # Сохраняем нормализованный username (без @)
```

**Эффект:** Все новые каналы сохраняются с нормализованным username (без `@`).

### 6. Исправление update_channel_ids.py

**Файл:** `scripts/update_channel_ids.py`

**Изменения:**
```python
# БЫЛО:
entity = await client.get_entity(channel['username'])

# СТАЛО:
# Context7: Нормализация username - убираем @ из начала
clean_username = channel['username'].lstrip('@') if channel['username'] else None
if not clean_username:
    print(f"  ❌ Пустой username для канала {channel['id']}")
    continue
entity = await client.get_entity(clean_username)
```

**Эффект:** Скрипт работает корректно с username в любом формате.

---

## Результаты

### До исправления:
- ❌ Поиск не находил каналы с другим форматом username
- ❌ Создавались дубликаты каналов
- ❌ Ошибки в `telegram_client.py` при загрузке каналов
- ❌ Проблемы с ручным парсингом

### После исправления:
- ✅ Все username в БД нормализованы (без `@`)
- ✅ Поиск работает с учетом нормализации (обратная совместимость)
- ✅ Все новые каналы сохраняются с нормализованным username
- ✅ Использование username в коде всегда нормализуется перед вызовом Telegram API

---

## Файлы

### Созданные файлы:
- `scripts/normalize_channel_usernames.sql` - SQL скрипт для нормализации
- `scripts/normalize_channel_usernames.py` - Python скрипт для нормализации (dry-run режим)

### Измененные файлы:
- ✅ `api/routers/channels.py` - нормализация при поиске
- ✅ `telethon-ingest/services/telegram_client.py` - нормализация перед использованием
- ✅ `telethon-ingest/scripts/manual_parse_channel.py` - нормализация при поиске
- ✅ `telethon-ingest/services/atomic_db_saver.py` - нормализация при сохранении
- ✅ `scripts/update_channel_ids.py` - нормализация перед использованием

---

## Проверка

### Команды для проверки:

1. **Проверка нормализации в БД:**
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT COUNT(*) FILTER (WHERE username LIKE '@%') as channels_with_at FROM channels WHERE username IS NOT NULL;"
```

2. **Проверка работы парсинга:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep -E "(get_entity|username|Failed to get entity)"
```

---

## Выводы

✅ **Все проблемы исправлены:**
- Username в БД нормализованы (без `@`)
- Поиск каналов учитывает нормализацию
- Использование username в коде всегда нормализуется
- Обратная совместимость сохранена (поиск работает с любым форматом)

