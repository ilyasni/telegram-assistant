# Диагностика проблемы отсутствия новых постов

**Дата**: 2026-01-12 20:10 UTC  
**Проблема**: За последние 3 часа не было добавлено ни одного поста, хотя посты были в каналах  
**Context7**: Диагностика с использованием best practices

---

## Context

Пользователь сообщает, что за последние 3 часа не было добавлено ни одного поста в БД, хотя посты были в каналах. Scheduler работает, но посты не сохраняются.

---

## 1. Проблема подтверждена

### Анализ логов

**Ключевые ошибки в логах**:

1. **FloodWait от Telegram API**:
   ```
   "A wait of 11472 seconds is required (caused by ResolveUsernameRequest)"
   ```
   - FloodWait: 11472 секунд (~3 часа)
   - Причина: `ResolveUsernameRequest` (попытка получить entity канала по username)

2. **Entity not found**:
   ```
   "Could not find the input entity for PeerChannel(channel_id=...)"
   ```
   - После FloodWait парсер пытается использовать `tg_channel_id`
   - Entity не найден (канал недоступен или удален)

3. **messages_processed = 0**:
   ```
   "messages_processed": 0, "status": "ok"
   ```
   - Парсер запускается (`CHANNEL_PARSE_START`, `CHANNEL_PARSE_END`)
   - Но не обрабатывает сообщения (`messages_processed = 0`)
   - Статус `ok`, но посты не сохраняются

### Проблемные каналы

Из логов видно проблемы с каналами:
- `naebnet` (1a81433d-351a-43bb-9974-f64e1c3bc1a6)
- `designsniper` (35d8421a-ebe8-4a64-8912-f299a565b287)
- `ilyabirman_channel` (843237db-1f4f-47df-8608-f979c66b57fb)
- `How2AI` (b309b2ae-5401-4301-a809-008009a02bfd)

---

## 2. Причины проблемы

### 2.1. FloodWait от Telegram API

**Проблема**: Telegram API ограничил запросы из-за слишком частых запросов `ResolveUsernameRequest`.

**Механизм**:
1. Парсер пытается получить entity канала по `username` через `ResolveUsernameRequest`
2. Telegram API возвращает FloodWait: 11472 секунд (~3 часа)
3. Парсер пытается использовать `tg_channel_id` как fallback
4. Но entity не найден (канал недоступен или удален)

**Влияние**:
- Парсер не может получить доступ к каналу
- `messages_processed = 0` (нет сообщений для обработки)
- Посты не сохраняются в БД

### 2.2. Entity Resolution Failure

**Проблема**: После FloodWait парсер не может получить entity канала даже через `tg_channel_id`.

**Причины**:
1. Канал удален или недоступен
2. `tg_channel_id` устарел или неверен
3. Telegram клиент не имеет доступа к каналу
4. Проблемы с entity cache в Telethon

**Влияние**:
- Парсер не может получить доступ к каналу
- Не может получить сообщения для парсинга
- `messages_processed = 0`

### 2.3. Обработка FloodWait

**Текущая реализация**: `telethon-ingest/services/telethon_retry.py:143-175`

```python
except errors.FloodWaitError as e:
    if e.seconds > MAX_FLOOD_WAIT:  # MAX_FLOOD_WAIT = 60
        # Перевести канал в cooldown
        if redis_client:
            await set_channel_cooldown(redis_client, channel_id, e.seconds)
        return []
    
    # Ждем FloodWait + 1 секунда
    await asyncio.sleep(e.seconds + 1)
```

**Проблема**: 
- FloodWait = 11472 секунд > MAX_FLOOD_WAIT (60 секунд)
- Канал переводится в cooldown
- Парсер возвращает пустой список сообщений
- Но cooldown может не работать правильно для таких больших значений

---

## 3. Context7 Best Practices - Анализ

### ✅ Что реализовано правильно

1. **FloodWait handling**: Есть обработка FloodWait с cooldown
2. **Retry logic**: Есть retry с экспоненциальным backoff
3. **Entity fallback**: Парсер пытается использовать `tg_channel_id` после ошибки username
4. **Метрики**: Есть метрики `parser_floodwait_seconds_total`, `parser_retries_total`

### ⚠️ Что можно улучшить

1. **FloodWait для ResolveUsernameRequest**:
   - Текущая реализация обрабатывает FloodWait для `iter_messages`
   - Но FloodWait при `ResolveUsernameRequest` обрабатывается по-другому
   - Нужно улучшить обработку FloodWait при получении entity

2. **Entity cache**:
   - Telethon имеет entity cache, но он может быть неактуален
   - Нужно проверить актуальность entity cache
   - Возможно, нужно обновить entity cache перед парсингом

3. **Cooldown для больших FloodWait**:
   - Текущий cooldown работает для FloodWait < 60 секунд
   - Для больших FloodWait (11472 секунд) cooldown может не работать правильно
   - Нужно улучшить обработку больших FloodWait

---

## 4. Решения

### 4.1. Немедленные действия

#### 1. Проверить доступность каналов

```bash
# Проверить каналы вручную через Telegram
# Убедиться, что каналы доступны и не удалены
```

#### 2. Обновить tg_channel_id

Если каналы доступны, но `tg_channel_id` неверен:
```bash
# Использовать скрипт для обновления tg_channel_id
python3 telethon-ingest/scripts/backfill_tg_channel_id.py
```

#### 3. Дождаться окончания FloodWait

FloodWait = 11472 секунд (~3 часа), нужно дождаться окончания.

#### 4. Проверить cooldown в Redis

```bash
# Проверить, какие каналы в cooldown
redis-cli KEYS "channel:cooldown:*"
```

### 4.2. Долгосрочные улучшения

#### 1. Улучшить обработку FloodWait для ResolveUsernameRequest

**Проблема**: FloodWait при `ResolveUsernameRequest` обрабатывается не так, как для `iter_messages`.

**Решение**: Добавить специальную обработку FloodWait для entity resolution:

```python
# В channel_parser.py при получении entity
try:
    entity = await client.get_entity(username)
except FloodWaitError as e:
    if e.seconds > MAX_FLOOD_WAIT:
        # Установить cooldown для канала
        await set_channel_cooldown(redis_client, channel_id, e.seconds)
        # Пропустить канал в этом тике
        return {"status": "skipped", "reason": "floodwait", "wait_seconds": e.seconds}
    else:
        # Ждать и повторить
        await asyncio.sleep(e.seconds + 1)
        # Повторить попытку
        entity = await client.get_entity(username)
```

#### 2. Улучшить entity cache

**Проблема**: Entity cache может быть неактуален.

**Решение**: 
- Обновлять entity cache перед парсингом
- Использовать `client.get_entity()` с force=True для обновления cache
- Проверять актуальность entity перед использованием

#### 3. Улучшить обработку больших FloodWait

**Проблема**: Cooldown не работает правильно для больших FloodWait (> 60 секунд).

**Решение**:
- Увеличить `MAX_FLOOD_WAIT` или убрать ограничение
- Улучшить cooldown механизм для больших значений
- Добавить метрики для отслеживания больших FloodWait

#### 4. Добавить проверку доступности каналов

**Проблема**: Парсер пытается парсить недоступные каналы.

**Решение**:
- Добавить проверку доступности канала перед парсингом
- Обновлять `is_active` для недоступных каналов
- Логировать недоступные каналы для ручной проверки

---

## 5. Проверка текущего состояния

### 5.1. Проверка постов в БД

```sql
-- Посты за последние 3 часа
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

### 5.2. Проверка активности каналов

```sql
-- Каналы с последним постом
SELECT 
    c.id,
    c.username,
    c.title,
    c.last_parsed_at,
    MAX(p.posted_at) as last_post_date,
    EXTRACT(EPOCH FROM (NOW() - MAX(p.posted_at))) / 3600 as hours_since_last_post
FROM channels c
LEFT JOIN posts p ON p.channel_id = c.id
WHERE c.is_active = true
GROUP BY c.id, c.username, c.title, c.last_parsed_at
ORDER BY MAX(p.posted_at) DESC NULLS LAST
LIMIT 10;
```

### 5.3. Проверка FloodWait и cooldown

```bash
# Проверка cooldown в Redis
redis-cli KEYS "channel:cooldown:*"

# Проверка метрик FloodWait
curl "http://localhost:9090/api/v1/query?query=parser_floodwait_seconds_total"
```

---

## 6. Рекомендации (Context7 Best Practices)

### Немедленные действия (высокий приоритет)

1. **Дождаться окончания FloodWait**:
   - FloodWait = 11472 секунд (~3 часа)
   - Проверить, когда был последний FloodWait
   - Дождаться окончания перед следующей попыткой

2. **Проверить доступность каналов**:
   - Проверить каналы вручную через Telegram
   - Убедиться, что каналы не удалены и доступны

3. **Обновить tg_channel_id**:
   - Если каналы доступны, но entity не найден
   - Использовать скрипт для обновления `tg_channel_id`

### Средний приоритет

4. **Улучшить обработку FloodWait**:
   - Добавить специальную обработку для `ResolveUsernameRequest`
   - Улучшить cooldown для больших FloodWait
   - Добавить метрики для отслеживания

5. **Улучшить entity cache**:
   - Обновлять entity cache перед парсингом
   - Проверять актуальность entity

### Низкий приоритет

6. **Добавить проверку доступности каналов**:
   - Автоматическая проверка перед парсингом
   - Обновление `is_active` для недоступных каналов

---

## 7. Checks

1. ✅ Проблема подтверждена: нет постов за последние 3 часа
2. ✅ Причина найдена: FloodWait + Entity not found
3. ⚠️ Решение: дождаться окончания FloodWait, проверить каналы
4. ⚠️ Улучшения: улучшить обработку FloodWait для ResolveUsernameRequest

**Статус**: ❌ **ПРОБЛЕМА ПОДТВЕРЖДЕНА - требуется исправление**

---

## 8. Выводы

### ❌ Проблема

1. **FloodWait от Telegram API**: 11472 секунд (~3 часа)
   - Причина: слишком частые запросы `ResolveUsernameRequest`
   - Влияние: парсер не может получить entity канала

2. **Entity not found**: Каналы недоступны после FloodWait
   - Причина: `tg_channel_id` неверен или канал удален
   - Влияние: парсер не может получить доступ к каналу

3. **messages_processed = 0**: Парсер запускается, но не обрабатывает сообщения
   - Причина: нет доступа к каналу из-за FloodWait и entity errors
   - Влияние: посты не сохраняются в БД

### ✅ Решения

1. **Немедленные**: Дождаться окончания FloodWait, проверить каналы
2. **Средний приоритет**: Улучшить обработку FloodWait для ResolveUsernameRequest
3. **Низкий приоритет**: Добавить проверку доступности каналов

**Статус**: ❌ **ТРЕБУЕТСЯ ИСПРАВЛЕНИЕ**
