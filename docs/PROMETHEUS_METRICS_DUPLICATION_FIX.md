# Исправление дублирования метрик Prometheus

**Дата**: 2025-01-22  
**Context7**: Исправление проблемы дублирования метрик Prometheus, которая блокировала запуск scheduler

---

## Context

При запуске scheduler loop возникала ошибка:
```
ValueError: Duplicated timeseries in CollectorRegistry: {'channel_not_found', 'channel_not_found_created', 'channel_not_found_total'}
```

Это блокировало запуск scheduler и останавливало парсинг постов и альбомов.

---

## Проблема

Метрики `channel_not_found_total`, `album_save_failures_total`, и `session_rollback_failures_total` были определены в двух местах:

1. **`telethon-ingest/services/channel_parser.py`**:
   - `channel_not_found_total` с labels `['exists_in_db']`
   - `album_save_failures_total` с labels `['error_type']`
   - `session_rollback_failures_total` с labels `['operation']`

2. **`telethon-ingest/services/atomic_db_saver.py`**:
   - `channel_not_found_total` с labels `['channel_id', 'exists_in_db']` (другая сигнатура!)
   - `album_save_failures_total` с labels `['channel_id', 'error_type']` (другая сигнатура!)
   - `session_rollback_failures_total` с labels `['operation']` (та же сигнатура)

**Проблема**: Prometheus не позволяет регистрировать метрики с одинаковым именем, даже если у них разные labels. Это создавало конфликт при импорте обоих модулей.

---

## Исправления

### 1. Исправление в `channel_parser.py`

**Файл**: `telethon-ingest/services/channel_parser.py`

**Изменения**:
- Добавлена функция `_get_or_create_counter()` для проверки существования метрики перед созданием
- Метрики теперь создаются только если они еще не зарегистрированы

**Код**:
```python
from prometheus_client import REGISTRY

def _get_or_create_counter(name, description, labels):
    """Получить существующую метрику или создать новую."""
    try:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing:
            return existing
    except (AttributeError, KeyError):
        pass
    return Counter(name, description, labels)

channel_not_found_total = _get_or_create_counter(
    'channel_not_found_total',
    'Total channel not found errors',
    ['exists_in_db']
)
```

### 2. Исправление в `atomic_db_saver.py`

**Файл**: `telethon-ingest/services/atomic_db_saver.py`

**Изменения**:
- Удалено дублирующее определение метрик
- Добавлен импорт метрик из `channel_parser.py`
- Добавлен fallback с `_get_or_create_counter()` на случай, если импорт не удался

**Код**:
```python
# Context7: Импортируем метрики из channel_parser для предотвращения дублирования
try:
    from services.channel_parser import (
        channel_not_found_total,
        album_save_failures_total,
        session_rollback_failures_total
    )
except ImportError:
    # Fallback: если channel_parser не импортирован, создаём метрики с проверкой на дублирование
    from prometheus_client import REGISTRY
    
    def _get_or_create_counter(name, description, labels):
        """Получить существующую метрику или создать новую."""
        try:
            existing = REGISTRY._names_to_collectors.get(name)
            if existing:
                return existing
        except (AttributeError, KeyError):
            pass
        return Counter(name, description, labels)
    
    channel_not_found_total = _get_or_create_counter(
        'channel_not_found_total',
        'Total channel not found errors',
        ['exists_in_db']  # Context7: Унифицированные labels
    )
    # ... аналогично для других метрик
```

### 3. Унификация labels

**Важно**: Метрики теперь используют унифицированные labels:
- `channel_not_found_total`: `['exists_in_db']` (без `channel_id` для контроля кардинальности)
- `album_save_failures_total`: `['error_type']` (без `channel_id` для контроля кардинальности)
- `session_rollback_failures_total`: `['operation']` (без изменений)

---

## Checks

### 1. Проверка отсутствия дублирования
```bash
# Проверить логи на наличие ошибок дублирования
docker compose logs --tail=200 telethon-ingest | grep -iE "Duplicated|error.*timeseries"
```

### 2. Проверка успешного запуска scheduler
```bash
# Проверить логи на успешный запуск scheduler
docker compose logs --tail=200 telethon-ingest | grep -iE "Scheduler loop|Starting all loops|ChannelParser initialized"
```

### 3. Проверка метрик в Prometheus
```bash
# Проверить доступность метрик
curl http://localhost:8002/metrics | grep -E "^channel_not_found|^album_save_failures|^session_rollback_failures"
```

---

## Impact / Rollback

### Impact
- ✅ Устранено дублирование метрик
- ✅ Scheduler теперь запускается без ошибок
- ✅ Метрики унифицированы и используют правильные labels

### Rollback
Если нужно откатить:
```bash
git checkout telethon-ingest/services/channel_parser.py telethon-ingest/services/atomic_db_saver.py
docker compose restart telethon-ingest
```

---

## Вывод

**Проблема**: Дублирование метрик Prometheus блокировало запуск scheduler.

**Решение**: 
1. Добавлена функция `_get_or_create_counter()` для безопасного создания метрик
2. Метрики в `atomic_db_saver.py` теперь импортируются из `channel_parser.py`
3. Labels унифицированы для предотвращения конфликтов

**Результат**: Scheduler запускается без ошибок, парсинг постов и альбомов возобновлен.

