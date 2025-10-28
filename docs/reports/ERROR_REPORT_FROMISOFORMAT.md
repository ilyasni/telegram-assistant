# Технический отчёт: Ошибка `fromisoformat: argument must be str` в Scheduler

## Контекст проблемы

**Дата обнаружения**: 2025-10-28  
**Сервис**: `telethon-ingest`  
**Компонент**: `ParseAllChannelsTask` (scheduler для incremental parsing)  
**Критичность**: 🔴 Высокая - блокирует парсинг каналов

## Симптомы

### 1. Ошибка
```
Failed to monitor channel <channel_id>: fromisoformat: argument must be str
TypeError: fromisoformat: argument must be str
```

### 2. Влияние
- ❌ Парсинг заблокирован для всех каналов (10 активных каналов)
- ❌ Последний успешный парсинг: **2025-10-27 18:58:13** (15+ часов назад)
- ❌ Новые посты не появляются в БД
- ✅ Scheduler запущен и тикает каждые 5 минут
- ✅ Контейнер работает (`healthy`)

### 3. Частота ошибки
- Ошибка возникает на **каждом тике** (каждые 5 минут)
- Затрагивает **все 10 активных каналов** одновременно
- Блокирует весь цикл обработки канала

## Технические детали

### Место возникновения
По traceback в логах ошибка происходит в:
```
File "/app/tasks/parse_all_channels_task.py", line 377, in _run_tick
```

### Дополнительные RuntimeWarning
```
/app/tasks/parse_all_channels_task.py:377: RuntimeWarning: coroutine 'Redis.execute_command' was never awaited
/app/tasks/parse_all_channels_task.py:361: RuntimeWarning: coroutine 'Redis.execute_command' was never awaited
/app/tasks/parse_all_channels_task.py:171: RuntimeWarning: coroutine 'Redis.execute_command' was never awaited
```

**Интерпретация**: Redis клиент создаётся как async, но используется синхронно.

## Архитектура и зависимости

### Компоненты
1. **ParseAllChannelsTask** (`telethon-ingest/tasks/parse_all_channels_task.py`)
   - Использует **синхронный** Redis клиент
   - Импорт: `import redis` (синхронный)

2. **ChannelParser** (`telethon-ingest/services/channel_parser.py`)
   - Использует **async** Redis клиент
   - Импорт: `import redis.asyncio as redis`

3. **Передача Redis клиента**
   - `ParseAllChannelsTask` создаёт синхронный Redis
   - Передаёт его в `ChannelParser` (строка 320): `redis_client=self.redis`

### Поток данных
```
ParseAllChannelsTask._run_tick()
  ├─> self._get_active_channels()  # psycopg2 → возвращает datetime объекты
  ├─> self.redis.get(hwm_key)      # Синхронный Redis
  ├─> self._decide_mode(channel)   # Работает с last_parsed_at
  ├─> channel.get('last_parsed_at') # Может быть datetime или None
  └─> datetime.fromisoformat(...)  # ❌ ОШИБКА ЗДЕСЬ
```

## История изменений

### Изменение 1: Исправление импорта Redis
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:17`

**Было**:
```python
import redis.asyncio as redis  # ❌ Async клиент
```

**Стало**:
```python
import redis  # ✅ Синхронный клиент
```

**Причина**: Scheduler использует синхронный API (`self.redis.get()`, `self.redis.set()`), но был импортирован async клиент.

**Результат**: ⚠️ Частично - ошибка сохраняется

---

### Изменение 2: Явное создание синхронного Redis клиента
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:130-152`

**Изменение**:
```python
# Явно создаём синхронный Redis клиент из redis.client, не из redis.asyncio
from redis.client import Redis as RedisSync
from urllib.parse import urlparse
parsed = urlparse(settings.redis_url)
self.redis = RedisSync(
    host=parsed.hostname or 'redis',
    port=parsed.port or 6379,
    db=int(parsed.path.lstrip('/')) if parsed.path else 0,
    decode_responses=False
)
```

**Результат**: ⚠️ Частично - RuntimeWarning сохраняется на строках 361, 377, 171

---

### Изменение 3: Защита типов для HWM из Redis
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:441-462`

**Добавлено**:
- Проверка типа `hwm_str` (datetime, bytes, str)
- Обработка каждого типа
- Логирование перед `fromisoformat`
- Обработка ошибок с детальным логированием

**Результат**: ⚠️ Ошибка сохраняется, но теперь логируется тип

---

### Изменение 4: Защита типов для `last_parsed_at` в `_decide_mode()`
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:609-621`

**Добавлено**:
- Проверка типа `last_parsed_at` перед использованием
- Конвертация строки в datetime
- Fallback на historical mode при ошибке парсинга

**Результат**: ⚠️ Ошибка сохраняется

---

### Изменение 5: Защита типов для gauge метрик
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:507-521`

**Добавлено**:
- Проверка типа перед вычислением age_seconds
- Обработка ошибок при вычислении метрик

**Результат**: ⚠️ Ошибка сохраняется

---

### Изменение 6: Улучшение ChannelParser для поддержки sync Redis
**Файл**: `telethon-ingest/services/channel_parser.py:90-121`

**Добавлено**:
- Класс `SyncRedisWrapper` для оборачивания синхронного Redis клиента
- Автоматическое определение типа Redis клиента (async/sync)
- Использование `run_in_executor` для sync → async обёртки

**Результат**: ✅ Код обновлён, но парсинг не запускается из-за ошибки в scheduler

---

### Изменение 7: Детальное логирование ошибок
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:521-542`

**Добавлено**:
- Вывод traceback в stderr
- Логирование типов переменных
- Логирование значений перед `fromisoformat`

**Результат**: ⚠️ Traceback в логах не отображается полностью (возможно, structlog фильтрует)

---

### Изменение 8: Защита от async Redis в runtime
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:419-439`

**Добавлено**:
- Проверка что `redis.get()` не возвращает coroutine
- Автоматическое пересоздание синхронного клиента при обнаружении async

**Результат**: ⚠️ Ошибка сохраняется

---

### Изменение 9: Защита для `max_message_date`
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:332-349`

**Добавлено**:
- Обработка разных типов `max_message_date` (datetime, str, bytes)
- Логирование перед `fromisoformat`

**Результат**: ⚠️ Ошибка сохраняется, но эта часть кода не выполняется (парсинг не запускается)

---

### Изменение 10: Исправление ChannelParser._get_since_date()
**Файл**: `telethon-ingest/services/channel_parser.py:368-371`

**Изменение**:
```python
# Было:
hwm_str = self.redis_client.get(hwm_key)

# Стало:
hwm_str = await self.redis_client.get(hwm_key) if asyncio.iscoroutinefunction(self.redis_client.get) else self.redis_client.get(hwm_key)
if asyncio.iscoroutine(hwm_str):
    hwm_str = await hwm_str
```

**Результат**: ✅ Код обновлён, но ошибка происходит в scheduler до вызова парсера

## Диагностическая информация

### Проверка Redis клиента
```python
# Тест: создание синхронного клиента
from redis.client import Redis as RedisSync
client = RedisSync.from_url("redis://redis:6379", decode_responses=False)
print(f"Client type: {type(client)}")  # <class 'redis.client.Redis'>
print(f"get() is coroutine: {inspect.iscoroutinefunction(client.get)}")  # False
result = client.get("test")
print(f"get() result type: {type(result)}")  # <class 'NoneType'>
```

**Вывод**: Синхронный клиент создаётся правильно и возвращает значения, не coroutines.

### Проверка данных из БД
```python
# Тест: psycopg2 возвращает
channel['last_parsed_at'] type: <class 'datetime.datetime'>
channel['last_parsed_at'] value: 2025-10-27 18:58:13.348045+00:00
```

**Вывод**: `psycopg2` возвращает `datetime` объекты напрямую, не строки.

### Проверка RuntimeWarning
```
RuntimeWarning: coroutine 'Redis.execute_command' was never awaited
File "/app/tasks/parse_all_channels_task.py", line 377, in _run_tick
    hwm_str = self.redis.get(hwm_key)
```

**Вывод**: Несмотря на использование `redis.client.Redis`, `self.redis.get()` всё ещё возвращает coroutine. Это означает, что где-то `self.redis` перезаписывается на async клиент.

## Возможные причины

### Версия 1: Redis клиент перезаписывается
- `self.redis` создаётся синхронным в `run_forever()`
- Где-то позже перезаписывается на async клиент
- Проверено: в коде нет явной перезаписи после инициализации

### Версия 2: Импорт конфликт
- В `channel_parser.py` импортирован `redis.asyncio as redis`
- Возможно, влияет на импорт в `parse_all_channels_task.py`
- Проверено: разные модули, не должно влиять

### Версия 3: Ошибка не в `fromisoformat` с HWM
- Ошибка может возникать в другом месте
- Traceback показывает строку 377, но реальная ошибка может быть выше по стеку
- Непроверено: полный traceback не отображается

### Версия 4: Ошибка в обработке `last_parsed_at`
- `psycopg2` возвращает `datetime`
- Где-то вызывается `fromisoformat` на `datetime` объекте
- Вероятно: ошибка в gauge метриках (строка 512) или в `_decide_mode()` (строка 617)

## Код до и после (ключевые места)

### Место 1: Инициализация Redis
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:130-152`

**Текущая версия**:
```python
from redis.client import Redis as RedisSync
from urllib.parse import urlparse
parsed = urlparse(settings.redis_url)
self.redis = RedisSync(
    host=parsed.hostname or 'redis',
    port=parsed.port or 6379,
    db=int(parsed.path.lstrip('/')) if parsed.path else 0,
    decode_responses=False
)
# Проверка что это синхронный клиент
import inspect
if hasattr(self.redis, 'execute_command') and inspect.iscoroutinefunction(getattr(self.redis, 'execute_command', None)):
    raise ValueError("Redis client is async, but scheduler requires sync client!")
```

### Место 2: Получение HWM из Redis
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:415-439`

**Текущая версия**:
```python
hwm_key = f"parse_hwm:{channel['id']}"
try:
    hwm_str = self.redis.get(hwm_key)
    # Проверка: если это coroutine, значит redis клиент async
    import asyncio
    if asyncio.iscoroutine(hwm_str):
        logger.error(f"Redis client is async! get() returned coroutine", 
                   channel_id=channel['id'],
                   redis_type=type(self.redis).__module__)
        raise ValueError("Redis client is async, but sync expected")
except ValueError:
    # Пересоздаём синхронный клиент
    # ... (код пересоздания)
    hwm_str = self.redis.get(hwm_key)

if hwm_str:
    try:
        if isinstance(hwm_str, datetime):
            hwm_ts = hwm_str
        elif isinstance(hwm_str, bytes):
            hwm_str = hwm_str.decode('utf-8')
            hwm_ts = datetime.fromisoformat(hwm_str.replace('Z', '+00:00'))
        elif isinstance(hwm_str, str):
            hwm_ts = datetime.fromisoformat(hwm_str.replace('Z', '+00:00'))
        # ...
    except Exception as hwm_error:
        logger.warning(f"Failed to parse HWM from Redis", ...)
```

### Место 3: _decide_mode()
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:609-621`

**Текущая версия**:
```python
last_parsed_at = channel.get('last_parsed_at')
if isinstance(last_parsed_at, str):
    try:
        logger.debug(f"_decide_mode: last_parsed_at is str, calling fromisoformat", ...)
        last_parsed_at = datetime.fromisoformat(last_parsed_at.replace('Z', '+00:00'))
    except Exception as e:
        logger.warning(f"Failed to parse last_parsed_at as datetime, using historical mode", ...)
        return "historical"
elif not isinstance(last_parsed_at, datetime):
    logger.warning(f"Unexpected last_parsed_at type: {type(last_parsed_at)}, using historical mode", ...)
    return "historical"
```

### Место 4: Gauge метрики
**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py:507-521`

**Текущая версия**:
```python
last_parsed_at = channel.get('last_parsed_at')
if last_parsed_at:
    try:
        if isinstance(last_parsed_at, str):
            logger.debug(f"Gauge: last_parsed_at is str, calling fromisoformat", ...)
            last_parsed_at = datetime.fromisoformat(last_parsed_at.replace('Z', '+00:00'))
        elif not isinstance(last_parsed_at, datetime):
            logger.warning(f"Unexpected last_parsed_at type for gauge: {type(last_parsed_at)}", ...)
            last_parsed_at = None
        if last_parsed_at:
            age_seconds = (datetime.now(timezone.utc) - last_parsed_at).total_seconds()
            # ...
    except Exception as gauge_error:
        logger.warning(f"Failed to calculate watermark age", ...)
```

## Текущее состояние

### Статус изменений
- ✅ Импорт Redis исправлен (async → sync)
- ✅ Защита типов добавлена во всех местах использования `fromisoformat`
- ✅ Детальное логирование добавлено
- ✅ ChannelParser поддерживает sync Redis через wrapper
- ⚠️ Ошибка всё ещё возникает
- ⚠️ RuntimeWarning о coroutine сохраняется

### Логи
- Ошибки логируются, но полный traceback не отображается
- `CRITICAL ERROR` блоки не появляются в логах (возможно, structlog фильтрует stderr)
- RuntimeWarning указывает на строки 377, 361, 171

### Версии зависимостей
```
redis: 5.0.1
psycopg2-binary: 2.9.9
Python: 3.11
```

## Рекомендации для специалиста

### 1. Диагностика
**Приоритет 1**: Получить полный Python traceback ошибки
- Добавить `traceback.print_exc()` в stdout (не stderr)
- Проверить, не фильтрует ли structlog вывод
- Возможно, использовать стандартный `logging` вместо structlog для traceback

**Приоритет 2**: Проверить что `self.redis` действительно синхронный во время выполнения
- Добавить логирование `type(self.redis)` и `type(self.redis.get)` перед каждым вызовом
- Проверить, не перезаписывается ли `self.redis` где-то в коде

**Приоритет 3**: Найти точное место ошибки
- Возможно, ошибка не на строке 377, а в другом месте
- Проверить все места где вызывается `fromisoformat` (6 мест в коде)

### 2. Решение
**Вариант A**: Если ошибка действительно на строке 377 с `hwm_str`
- Проверить что `self.redis.get()` возвращает не coroutine
- Возможно, проблема в том, что `redis.client.Redis.from_url()` создаёт async клиент в redis-py 5.0.1

**Вариант B**: Если ошибка в обработке `last_parsed_at`
- `psycopg2` возвращает `datetime`, не строку
- Где-то код пытается вызвать `fromisoformat` на `datetime` объекте
- Нужно найти это место и исправить

**Вариант C**: Если проблема в конфликте async/sync Redis
- Разделить Redis клиенты: один для scheduler (sync), другой для parser (async)
- Не передавать sync клиент в async компонент

### 3. Временное решение
До исправления можно:
1. Отключить обработку gauge метрик (строка 507-521)
2. Упростить `_decide_mode()` - всегда возвращать `historical` для тестирования
3. Пропускать обработку HWM из Redis (строки 441-462)

## Файлы изменены

1. `telethon-ingest/tasks/parse_all_channels_task.py` - основной файл с scheduler
2. `telethon-ingest/services/channel_parser.py` - парсер каналов
3. `telethon-ingest/services/channel_parser.py:368-392` - метод `_get_since_date()`

## Команды для воспроизведения

```bash
# Проверить логи ошибки
docker compose logs telethon-ingest --since 5m | grep "fromisoformat"

# Проверить статус scheduler
docker compose exec telethon-ingest curl -s http://localhost:8011/health

# Проверить последний парсинг
docker compose exec supabase-db psql -U postgres -d postgres -c \
  "SELECT c.title, c.last_parsed_at, NOW() - c.last_parsed_at as time_since \
   FROM channels c WHERE c.is_active = true ORDER BY c.last_parsed_at DESC LIMIT 5;"

# Тестировать Redis клиент
docker compose exec telethon-ingest python3 -c \
  "from redis.client import Redis; c = Redis.from_url('redis://redis:6379'); print(type(c.get('test')))"
```

## Контакты и контекст

- **Проект**: Telegram Assistant - Channel Parser Bot
- **Архитектура**: Event-driven pipeline (Parsing → Tagging → Enrichment → Indexing)
- **База данных**: Supabase (PostgreSQL)
- **Очереди**: Redis Streams
- **Векторная БД**: Qdrant
- **Graph БД**: Neo4j

## Заключение

Проблема остаётся нерешённой после множества попыток исправления. Основная гипотеза: Redis клиент создаётся async, несмотря на все попытки использовать sync версию. Требуется детальная диагностика с полным traceback для точной локализации проблемы.

