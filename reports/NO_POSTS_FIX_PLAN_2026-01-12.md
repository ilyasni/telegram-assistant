# План исправления проблемы отсутствия новых постов

**Дата**: 2026-01-12 20:10 UTC  
**Проблема**: За последние 3 часа не было добавлено ни одного поста  
**Context7**: Исправление с использованием best practices

---

## Context

Проблема подтверждена: парсер работает, но не сохраняет посты из-за:
1. FloodWait от Telegram API (11472 секунд)
2. Entity not found после FloodWait
3. messages_processed = 0 (нет сообщений для обработки)

---

## 1. Проблема

### Текущая ситуация

**Логи показывают**:
```
[WARNING] "A wait of 11472 seconds is required (caused by ResolveUsernameRequest)"
[ERROR] "Could not find the input entity for PeerChannel(channel_id=...)"
[INFO] "messages_processed": 0, "status": "ok"
```

**Проблемные каналы**:
- `naebnet`, `designsniper`, `ilyabirman_channel`, `How2AI`

### Причина

1. **FloodWait при ResolveUsernameRequest**:
   - Парсер пытается получить entity канала по `username`
   - Telegram API возвращает FloodWait: 11472 секунд
   - Парсер не обрабатывает этот FloodWait правильно

2. **Entity not found после FloodWait**:
   - После FloodWait парсер пытается использовать `tg_channel_id`
   - Но entity не найден (канал недоступен или `tg_channel_id` неверен)

3. **Результат**: `messages_processed = 0`, посты не сохраняются

---

## 2. Исправления

### 2.1. Улучшить обработку FloodWait для ResolveUsernameRequest

**Файл**: `telethon-ingest/services/channel_parser.py`

**Проблема**: FloodWait при `get_entity(username)` не обрабатывается правильно.

**Текущий код** (строка 826):
```python
try:
    entity = await client.get_entity(clean_username)
except Exception as e:
    logger.warning("Failed to get entity by username, trying tg_channel_id", ...)
    # Пытается использовать tg_channel_id, но не обрабатывает FloodWait
```

**Исправление**: Добавить специальную обработку FloodWait:

```python
try:
    entity = await client.get_entity(clean_username)
except FloodWaitError as e:
    # Context7: Обработка FloodWait для ResolveUsernameRequest
    wait_seconds = min(e.seconds, 300)  # Cap at 5 minutes для безопасности
    
    logger.warning(
        "FloodWait when getting entity by username",
        channel_id=channel_id,
        username=username,
        wait_seconds=wait_seconds,
        error_seconds=e.seconds
    )
    
    # Устанавливаем cooldown для канала
    if self.redis_client:
        await set_channel_cooldown(self.redis_client, channel_id, wait_seconds)
    
    # Пытаемся использовать tg_channel_id как fallback
    if tg_channel_id_db:
        try:
            entity = await client.get_entity(int(tg_channel_id_db))
            logger.info("Successfully got entity by tg_channel_id after FloodWait",
                       channel_id=channel_id)
        except Exception as e2:
            logger.error("Failed to get entity by tg_channel_id after FloodWait",
                        channel_id=channel_id,
                        error=str(e2))
            return None
    else:
        logger.warning("No tg_channel_id available, skipping channel",
                      channel_id=channel_id)
        return None
except Exception as e:
    # Остальная обработка ошибок
    ...
```

### 2.2. Улучшить обработку больших FloodWait

**Файл**: `telethon-ingest/services/telethon_retry.py`

**Проблема**: `MAX_FLOOD_WAIT = 60` секунд, но FloodWait может быть больше (11472 секунд).

**Исправление**: Улучшить обработку больших FloodWait:

```python
MAX_FLOOD_WAIT = 300  # Увеличено до 5 минут для обработки больших FloodWait

# В обработке FloodWait:
if e.seconds > MAX_FLOOD_WAIT:
    # Для больших FloodWait устанавливаем cooldown на MAX_FLOOD_WAIT
    # Но логируем реальное время ожидания
    logger.warning(
        "Large FloodWait detected, using cooldown",
        channel_id=channel_id,
        actual_seconds=e.seconds,
        cooldown_seconds=MAX_FLOOD_WAIT
    )
    if redis_client:
        # Устанавливаем cooldown на MAX_FLOOD_WAIT, но логируем реальное время
        await set_channel_cooldown(redis_client, channel_id, MAX_FLOOD_WAIT)
    return []
```

### 2.3. Добавить проверку доступности каналов перед парсингом

**Файл**: `telethon-ingest/services/channel_parser.py`

**Проблема**: Парсер пытается парсить недоступные каналы.

**Исправление**: Добавить проверку доступности:

```python
async def _check_channel_accessible(self, client, channel_id, username, tg_channel_id):
    """Проверка доступности канала перед парсингом."""
    try:
        if username:
            clean_username = username.lstrip('@')
            entity = await client.get_entity(clean_username)
            return True, entity
        elif tg_channel_id:
            entity = await client.get_entity(int(tg_channel_id))
            return True, entity
        else:
            return False, None
    except FloodWaitError as e:
        logger.warning("FloodWait when checking channel accessibility",
                      channel_id=channel_id,
                      wait_seconds=e.seconds)
        return False, None
    except Exception as e:
        logger.warning("Channel not accessible",
                      channel_id=channel_id,
                      error=str(e))
        return False, None
```

---

## 3. Немедленные действия

### 3.1. Проверить посты в БД

```sql
-- Проверка постов за последние 3 часа
SELECT COUNT(*) 
FROM posts 
WHERE created_at > NOW() - INTERVAL '3 hours';

-- Последний пост
SELECT 
    id,
    channel_id,
    created_at,
    EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600 as hours_ago
FROM posts 
ORDER BY created_at DESC 
LIMIT 1;
```

### 3.2. Проверить cooldown в Redis

```bash
# Проверка каналов в cooldown
docker compose exec redis redis-cli KEYS "channel:cooldown:*"

# Проверка времени cooldown
docker compose exec redis redis-cli TTL "channel:cooldown:{channel_id}"
```

### 3.3. Проверить доступность каналов

```bash
# Использовать скрипт для проверки доступности
python3 telethon-ingest/scripts/check_channels_accessibility.py
```

### 3.4. Обновить tg_channel_id

Если каналы доступны, но `tg_channel_id` неверен:

```bash
# Обновить tg_channel_id для проблемных каналов
python3 telethon-ingest/scripts/backfill_tg_channel_id.py
```

---

## 4. Применение исправлений

### 4.1. Исправление обработки FloodWait

**Файл**: `telethon-ingest/services/channel_parser.py`

**Строки**: ~826-867 (метод `_get_channel_entity`)

**Изменения**:
1. Добавить специальную обработку `FloodWaitError` при `get_entity(username)`
2. Установить cooldown для канала при FloodWait
3. Использовать `tg_channel_id` как fallback после FloodWait
4. Логировать детальную информацию о FloodWait

### 4.2. Исправление MAX_FLOOD_WAIT

**Файл**: `telethon-ingest/services/telethon_retry.py`

**Строка**: ~59

**Изменение**:
```python
MAX_FLOOD_WAIT = 300  # Увеличено с 60 до 300 секунд (5 минут)
```

### 4.3. Добавить проверку доступности

**Файл**: `telethon-ingest/services/channel_parser.py`

**Новый метод**: `_check_channel_accessible`

**Использование**: Вызывать перед парсингом в `parse_channel_messages`

---

## 5. Context7 Best Practices

### ✅ Применено

1. **Обработка ошибок**: Retry logic, exponential backoff
2. **Метрики**: `parser_floodwait_seconds_total`, `parser_retries_total`
3. **Логирование**: Детальное логирование всех ошибок
4. **Cooldown**: Установка cooldown для каналов при FloodWait

### ⚠️ Требует улучшения

1. **FloodWait для ResolveUsernameRequest**: Не обрабатывается правильно
2. **Большие FloodWait**: MAX_FLOOD_WAIT слишком мал (60 секунд)
3. **Entity cache**: Не обновляется перед парсингом
4. **Проверка доступности**: Нет проверки перед парсингом

---

## 6. Checks

1. ✅ Проблема подтверждена: нет постов за последние 3 часа
2. ✅ Причина найдена: FloodWait + Entity not found
3. ⏳ Исправления: требуют применения
4. ⏳ Тестирование: требуется после исправлений

**Статус**: ⏳ **ТРЕБУЕТСЯ ПРИМЕНЕНИЕ ИСПРАВЛЕНИЙ**
