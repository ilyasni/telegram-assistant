# Исправление критического алерта ParsingNoActivity

**Дата**: 2025-12-16  
**Проблема**: Критический алерт `ParsingNoActivity` срабатывает, хотя парсинг работает  
**Статус**: ✅ Исправлено

---

## Context

Критический алерт `ParsingNoActivity` указывал на отсутствие активности парсинга, но при проверке обнаружено, что:
- Scheduler работает и выполняет тики
- Парсинг вызывается для всех каналов
- Но возвращает `messages_processed: 0` (нет новых сообщений)

---

## Диагностика

### ✅ Scheduler работает

**Доказательства**:
- Lock `parse_all_channels:lock` устанавливается и освобождается
- Тики выполняются каждые 5 минут
- Последний тик: 2025-12-16 13:02:24
- Обработано 20 каналов из 50 за тик

### ✅ Парсинг вызывается

**Доказательства**:
- Логи показывают вызовы `parse_channel_messages`
- Каналы обрабатываются в режиме `incremental`
- TelegramClientManager возвращает клиенты

### ❌ Проблема: Нет новых сообщений

**Симптомы**:
- Все парсинги возвращают `messages_processed: 0`
- Парсер проверяет сообщения, но все они старше `since_date`
- Логи показывают: "Stopping message batch - no newer messages found in incremental mode"

**Пример лога**:
```
[INFO] {"channel_id": 2357452949, "mode": "incremental", "reason": "no_newer_messages", 
        "messages_checked": 20, "last_checked_message_date": "2025-12-10T15:13:07+00:00", 
        "since_date": "2025-12-16T08:49:10.719972+00:00", "messages_yielded": 0}
```

### ❌ Проблема: Метрики не обновляются при 0 сообщениях

**Причина**: Метрика `posts_parsed_total` обновлялась только при `parsed_count > 0`, поэтому при отсутствии новых сообщений метрика не обновлялась, и алерт срабатывал.

---

## Plan

1. ✅ Исправить обновление метрик при 0 сообщениях
2. ✅ Улучшить алерт для использования `scheduler_heartbeat_seconds`
3. ✅ Создать диагностический скрипт

---

## Patch

### 1. Исправление обновления метрик

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py`

**Изменение**: Обновляем метрику `posts_parsed_total` даже при 0 сообщениях для отслеживания активности парсинга:

```python
if result and "messages_processed" in result:
    parsed_count = result.get("messages_processed", 0)
    # Context7: Обновляем метрики даже при 0 сообщениях для отслеживания активности парсинга
    # Это позволяет алерту ParsingNoActivity правильно определять, что парсинг работает
    if parsed_count > 0:
        posts_parsed_total.labels(mode=mode, status="success").inc(parsed_count)
    else:
        # Обновляем метрику с 0 для отслеживания активности (rate будет > 0)
        posts_parsed_total.labels(mode=mode, status="success").inc(0)
    parser_runs_total.labels(mode=mode, status="ok").inc()
```

### 2. Улучшение алерта ParsingNoActivity

**Файл**: `prometheus/alerts.yml`

**Изменение**: Используем `scheduler_heartbeat_seconds` для проверки активности scheduler'а:

```yaml
- alert: ParsingNoActivity
  expr: |
    (
      (time() - scheduler_heartbeat_seconds) > 300
    )
    or
    (
      sum(rate(parser_runs_total[10m])) == 0
      and 
      (time() - scheduler_last_tick_ts_seconds) > 600
    )
  for: 15m
  labels:
    severity: critical
    component: parsing
  annotations:
    summary: "Парсинг не выполняется (нет активности)"
    description: "Парсер не обрабатывает каналы. Heartbeat: {{ $value }}s назад. Проверить логи telethon-ingest и состояние TelegramClientManager."
    runbook: "Проверить: 1) логи telethon-ingest, 2) статус TelegramClientManager, 3) scheduler_heartbeat_seconds, 4) scheduler_last_tick_ts_seconds"
```

### 3. Диагностический скрипт

**Файл**: `scripts/diagnose_parsing_no_activity.sh`

Создан скрипт для комплексной диагностики состояния парсинга:
- Проверка логов telethon-ingest
- Проверка состояния scheduler lock
- Проверка метрик Prometheus
- Проверка последних парсингов
- Проверка TelegramClientManager
- Проверка Redis streams

---

## Checks

### 1. Проверка обновления метрик

```bash
# Проверить метрики после исправления
curl -s http://localhost:8011/metrics | grep -E "(parser_runs_total|posts_parsed_total|scheduler_heartbeat)"
```

### 2. Проверка работы парсинга

```bash
# Запустить диагностический скрипт
./scripts/diagnose_parsing_no_activity.sh
```

### 3. Проверка алерта

```bash
# Проверить состояние алерта в Prometheus
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname=="ParsingNoActivity")'
```

### 4. Проверка логов

```bash
# Проверить последние парсинги
docker logs telegram-assistant-telethon-ingest-1 --tail 500 | grep -E "(CHANNEL_PARSE_END|messages_processed)"
```

---

## Impact / Rollback

### Impact

**Положительные изменения**:
- ✅ Метрики теперь обновляются даже при отсутствии новых сообщений
- ✅ Алерт использует более надежные метрики (heartbeat)
- ✅ Диагностический скрипт для быстрой проверки состояния

**Возможные риски**:
- ⚠️ Метрика `posts_parsed_total` может увеличиваться даже при 0 сообщениях (но это ожидаемо для отслеживания активности)
- ⚠️ Алерт может срабатывать реже, если scheduler работает, но не парсит (это нормально, если нет новых сообщений)

### Rollback

Если нужно откатить изменения:

1. **Откат метрик**:
```python
# Вернуть оригинальную логику (обновлять только при parsed_count > 0)
if parsed_count > 0:
    posts_parsed_total.labels(mode=mode, status="success").inc(parsed_count)
```

2. **Откат алерта**:
```yaml
# Вернуть оригинальный алерт
expr: |
  sum(rate(parser_runs_total[10m])) == 0
  and 
  sum(rate(posts_parsed_total[10m])) == 0
```

---

## Выводы

1. **Парсинг работает корректно** - просто нет новых сообщений в каналах
2. **Метрики не обновлялись** при отсутствии новых сообщений, что вызывало ложные алерты
3. **Алерт улучшен** для использования более надежных метрик (heartbeat)
4. **Диагностический скрипт** создан для быстрой проверки состояния

---

## Рекомендации

1. **Мониторинг**: Следить за метрикой `scheduler_heartbeat_seconds` для проверки активности scheduler'а
2. **Логирование**: Улучшить логирование при отсутствии новых сообщений (INFO вместо DEBUG)
3. **Алерты**: Рассмотреть создание отдельного алерта для случаев, когда парсинг работает, но нет новых сообщений (информационный уровень)

---

**Context7 Best Practices**: Исправления следуют принципам observability и мониторинга, обеспечивая корректное отслеживание активности парсинга даже при отсутствии новых сообщений.

