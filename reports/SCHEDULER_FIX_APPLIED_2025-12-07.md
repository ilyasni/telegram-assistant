# Исправление SchedulerTickStuck - применено

**Дата**: 2025-12-07 01:30 UTC  
**Статус**: ✅ Исправление применено, контейнер перезапущен

---

## Context

Исправлена проблема с экспортом метрик scheduler в Prometheus, которая вызывала ложный алерт `SchedulerTickStuck`.

---

## Исправления

### ✅ Импорт метрик scheduler в `main.py`

**Файл**: `telethon-ingest/main.py` (строки 51-64)

**Изменение**: Добавлен импорт метрик scheduler для их автоматической регистрации в Prometheus registry.

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

---

## Статус контейнера

- ✅ Контейнер перезапущен: `docker compose restart telethon-ingest`
- ✅ Статус: `Up About a minute (healthy)`
- ✅ Health check: проходит успешно

---

## Проверка результатов

### Через 2-3 минуты после перезапуска:

1. **Проверить метрики через Prometheus API**:
   ```bash
   curl -s 'http://prometheus:9090/api/v1/query?query=scheduler_last_tick_ts_seconds' | \
     python3 -m json.tool | grep -A 5 "result"
   ```

2. **Проверить метрики через /metrics endpoint** (из контейнера prometheus):
   ```bash
   docker compose exec prometheus wget -qO- http://telethon-ingest:8011/metrics | \
     grep scheduler_last_tick
   ```

3. **Проверить алерт в Grafana**:
   - Алерт `SchedulerTickStuck` должен исчезнуть через 5-10 минут после перезапуска
   - (Алерт проверяет условие в течение 5 минут)

4. **Проверить логи scheduler**:
   ```bash
   docker compose logs telethon-ingest --since 5m | grep -E "(Tick completed|Scheduler tick)"
   ```

---

## Ожидаемое поведение

После применения исправления:

1. ✅ Метрики `scheduler_last_tick_ts_seconds` и `scheduler_heartbeat_seconds` доступны через `/metrics` endpoint
2. ✅ Prometheus собирает метрики каждые 30 секунд
3. ✅ Алерт `SchedulerTickStuck` корректно отслеживает состояние scheduler
4. ✅ Метрика обновляется при каждом тике scheduler

---

## Примечания

- Scheduler продолжает работать нормально (проблема была только в экспорте метрик)
- Исправление не влияет на работу scheduler, только добавляет экспорт метрик
- Метрики будут доступны после полной инициализации scheduler (1-2 минуты после перезапуска)

