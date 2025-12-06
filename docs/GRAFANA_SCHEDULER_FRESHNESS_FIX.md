# Исправление некорректных значений Scheduler Freshness в Grafana

**Дата**: 2025-12-03  
**Проблема**: При перезагрузке API значение "55.9 years" появляется в панелях Scheduler Freshness

---

## Проблема

При перезагрузке API контейнера метрика `scheduler_last_tick_ts_seconds` может быть не инициализирована (отсутствует или равна 0), что приводит к огромному значению в запросе `time() - scheduler_last_tick_ts_seconds`.

**Пример**: Если метрика равна 0 или отсутствует, то `time() - 0` = текущее время (миллиарды секунд) = десятки/сотни лет.

---

## Решение

### 1. Ограничение максимального значения

Использовать функцию `clamp_max()` для ограничения максимального значения до разумного предела (24 часа = 86400 секунд):

```promql
clamp_max(time() - scheduler_last_tick_ts_seconds, 86400)
```

### 2. Обновление панелей

**Затронутые панели**:
- `system_overview.json`: "⏰ Scheduler Freshness" (id: 13)
- `system_overview.json`: "📊 Scheduler Freshness History" (id: 14)
  - "Scheduler Freshness (Last Tick)"
  - "Scheduler Heartbeat"
- `parser_streams.json`: "Scheduler Freshness"

**Изменения**:
- Запрос: `time() - scheduler_last_tick_ts_seconds` 
  → `clamp_max(time() - scheduler_last_tick_ts_seconds, 86400)`
- Запрос: `time() - scheduler_heartbeat_seconds`
  → `clamp_max(time() - scheduler_heartbeat_seconds, 86400)`

---

## Ожидаемый результат

После применения изменений:
- ✅ Значения больше 24 часов будут ограничены до 24 часов
- ✅ Некорректные значения "55.9 years" больше не будут отображаться
- ✅ Панели будут показывать максимальное значение 24 часа для некорректных случаев

---

## Применение изменений

1. Обновлены запросы в `system_overview.json`
2. Необходимо обновить запросы в `parser_streams.json`
3. Изменения применятся автоматически при следующей загрузке dashboard в Grafana

---

**Статус**: ✅ Частично применено (требуется обновление parser_streams.json)

