# Диагностика алерта SchedulerTickStuck

**Дата**: 2025-12-07  
**Алерт**: `SchedulerTickStuck` - scheduler tick застрял (1.765Gs с последнего тика)  
**Context7**: Критическая диагностика проблемы экспорта метрик

---

## Context

Получен критический алерт о том, что scheduler tick не обновлялся более 1.765Gs. При проверке обнаружена проблема с экспортом метрик Prometheus.

---

## Диагностика

### ✅ Scheduler работает нормально

**Результаты проверки**:
- Контейнер `telethon-ingest` работает (status: healthy)
- Последний tick был завершен в `01:26:14` (по времени контейнера)
- Scheduler активно обрабатывает каналы
- Heartbeat task запущен

**Логи показывают**:
```
[INFO 2025-12-07 01:26:14,093] tasks.parse_all_channels_task: 
{"channels_processed": 20, "channels_total": 50, "duration_seconds": 240.126212, 
"event": "Scheduler tick completed"}
```

### ❌ Проблема: Метрики не экспортируются в Prometheus

**Обнаружено**:
- Метрики `scheduler_last_tick_ts_seconds` и `scheduler_heartbeat_seconds` определены в `tasks/parse_all_channels_task.py`
- Метрики **НЕ импортируются** в `main.py`
- Health server использует `generate_latest()` из `prometheus_client`, который собирает только зарегистрированные метрики
- Метрики из `parse_all_channels_task.py` не попадают в Prometheus registry до импорта модуля

**Проверка**:
```bash
# Метрики не найдены в registry
docker compose exec telethon-ingest python -c \
  "from prometheus_client import REGISTRY; \
   [print(m) for m in REGISTRY._collector_to_names.keys() if 'scheduler' in str(m).lower()]"
# Результат: пусто

# Metrics endpoint недоступен
curl -s http://telethon-ingest:8011/metrics | grep scheduler_last_tick
# Результат: метрика не найдена
```

---

## Plan

### 1. ✅ Импортировать метрики scheduler в `main.py`

**Проблема**: Метрики определены в `parse_all_channels_task.py`, но не импортируются в `main.py`, поэтому не попадают в Prometheus registry.

**Решение**: Импортировать метрики scheduler в начале `main.py` после инициализации logger.

---

## Patch

### Изменение: Импорт метрик scheduler в `main.py`

**Файл**: `telethon-ingest/main.py`

**Добавлено после строки 49**:
```python
# Context7: Импортируем метрики scheduler для экспорта через /metrics endpoint
# Метрики должны быть доступны сразу при старте для мониторинга
try:
    from tasks.parse_all_channels_task import (
        scheduler_last_tick_ts_seconds,
        scheduler_heartbeat_seconds,
        parser_runs_total,
        parsing_duration_seconds,
        posts_parsed_total
    )
    logger.info("Scheduler metrics imported successfully for Prometheus export")
except ImportError as e:
    # Если модуль недоступен при импорте, метрики будут доступны после инициализации scheduler
    logger.warning("Failed to import scheduler metrics at startup", error=str(e))
```

**Причина**:
- Метрики из `prometheus_client` автоматически регистрируются в `REGISTRY` при создании
- Но они доступны через `generate_latest()` только если модуль импортирован
- Поскольку `ParseAllChannelsTask` импортируется только внутри `run_scheduler_loop()`, метрики недоступны до запуска scheduler
- Импорт метрик в начале `main.py` гарантирует их доступность через `/metrics` endpoint сразу

---

## Checks

### Проверка исправления

```bash
# 1. Перезапустить контейнер для применения изменений
docker compose restart telethon-ingest

# 2. Проверить, что метрики импортируются (в логах)
docker compose logs telethon-ingest | grep "Scheduler metrics imported"
# Ожидаемый результат: "Scheduler metrics imported successfully for Prometheus export"

# 3. Проверить доступность метрик через endpoint (из другого контейнера)
docker compose exec prometheus wget -qO- http://telethon-ingest:8011/metrics | grep scheduler_last_tick
# Ожидаемый результат: метрика найдена

# 4. Проверить метрики в Prometheus (через API)
curl -s 'http://prometheus:9090/api/v1/query?query=scheduler_last_tick_ts_seconds' | \
  python3 -m json.tool | grep -A 5 "result"

# 5. Проверить, что алерт исчез через 5-10 минут после перезапуска
# (так как алерт проверяет условие в течение 5 минут)
```

### Проверка работы scheduler

```bash
# 1. Проверить логи тиков
docker compose logs telethon-ingest --since 10m | grep -E "(Tick completed|Scheduler tick)"

# 2. Проверить heartbeat
docker compose logs telethon-ingest --since 5m | grep heartbeat

# 3. Проверить состояние контейнера
docker compose ps telethon-ingest
# Ожидаемый результат: STATUS = Up X minutes (healthy)
```

---

## Impact / Rollback

### Что затронуто:
- ✅ Экспорт метрик через `/metrics` endpoint - теперь метрики scheduler доступны
- ✅ Мониторинг Prometheus - алерт `SchedulerTickStuck` начнет корректно работать
- ✅ Health check - не изменен, продолжает работать как раньше

### Откат:
```bash
# Откатить изменения в main.py
git checkout HEAD -- telethon-ingest/main.py
docker compose restart telethon-ingest
```

### Дополнительные проверки:
1. Убедиться, что метрики обновляются при каждом тике
2. Проверить, что Prometheus собирает метрики каждые 30 секунд
3. Мониторить алерт в Grafana - должен исчезнуть после исправления

---

## Примечания

1. **Время в алерте (1.765Gs)**: Вероятно, это опечатка или проблема с форматированием. Реальное значение должно быть в секундах (например, 1765 секунд ≈ 29 минут).

2. **Scheduler работает нормально**: Проблема не в работе scheduler, а в экспорте метрик. Scheduler продолжает выполнять тики, но Prometheus не видит обновления метрики.

3. **Heartbeat метрика**: Также должна быть доступна после исправления, что позволит отслеживать активность scheduler в реальном времени.

---

## Статус

✅ **Исправление применено** - требуется перезапуск контейнера для применения изменений.

