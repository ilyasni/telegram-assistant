# Диагностика проблемы с Channel Processing Time

**Дата**: 2026-01-12  
**Проблема**: Метрика Channel Processing Time упала  
**Context7**: Диагностика падения метрики `parser_channel_processing_seconds`

---

## Context

Метрика `parser_channel_processing_seconds` измеряет время обработки одного канала парсером. Падение метрики может означать:
1. Парсер не обрабатывает каналы
2. Нет новых данных для обработки
3. Проблема с экспортом метрики в Prometheus
4. Парсер работает, но не обновляет метрику

---

## Проверка компонентов

### 1. Метрика `parser_channel_processing_seconds`

**Определение**: `telethon-ingest/tasks/parse_all_channels_task.py:90-95`

```python
parser_channel_processing_seconds = Histogram(
    'parser_channel_processing_seconds',
    'Time spent processing a single channel',
    ['mode', 'status'],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
)
```

**Обновление**: `telethon-ingest/tasks/parse_all_channels_task.py:861`

```python
process_duration = (datetime.now(timezone.utc) - process_start_time).total_seconds()
parser_channel_processing_seconds.labels(mode=mode or "unknown", status=status).observe(process_duration)
```

**Включает в себя**:
- Получение telegram_id и tenant_id
- Получение Telegram клиента
- Создание парсера
- Парсинг канала (включая FloodWait)
- Обработка результатов
- Обновление last_parsed_at

### 2. Запросы в Grafana

**Панель**: "⏱️ Channel Processing Time"

**Запросы**:
- **p50**: `histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`
- **p95**: `histogram_quantile(0.95, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`
- **p99**: `histogram_quantile(0.99, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`

**Важно**: Запросы используют `rate()` с окном 5 минут. Если нет новых данных за 5 минут, метрика будет показывать 0 или NaN.

---

## Диагностика

### Шаг 1: Проверка метрики в Prometheus

```bash
# Проверка наличия метрики
curl "http://localhost:9090/api/v1/query?query=parser_channel_processing_seconds_bucket"

# Проверка перцентилей
curl "http://localhost:9090/api/v1/query?query=histogram_quantile(0.50,%20sum(rate(parser_channel_processing_seconds_bucket[5m]))%20by%20(le,%20mode,%20status))"

# Проверка последних значений за 1 час
curl "http://localhost:9090/api/v1/query_range?query=parser_channel_processing_seconds_bucket&start=$(date -d '1 hour ago' +%s)&end=$(date +%s)&step=60s"
```

### Шаг 2: Проверка статуса telethon-ingest

```bash
# Проверка health endpoint
curl http://localhost:8011/health

# Проверка статуса scheduler
curl http://localhost:8011/health/details | jq '.scheduler'

# Проверка логов
docker-compose logs telethon-ingest --tail=100 | grep -E "CHANNEL_PARSE|scheduler|tick"
```

### Шаг 3: Проверка активности парсинга

```sql
-- Проверка последних обновлений last_parsed_at
SELECT 
    id,
    username,
    title,
    last_parsed_at,
    EXTRACT(EPOCH FROM (NOW() - last_parsed_at)) / 3600 as hours_ago
FROM channels
WHERE last_parsed_at IS NOT NULL
ORDER BY last_parsed_at DESC
LIMIT 10;

-- Проверка постов за последние 24 часа
SELECT COUNT(*) 
FROM posts 
WHERE created_at > NOW() - INTERVAL '24 hours';

-- Проверка последних постов
SELECT 
    p.id,
    p.channel_id,
    p.created_at,
    c.username,
    EXTRACT(EPOCH FROM (NOW() - p.created_at)) / 3600 as hours_ago
FROM posts p
LEFT JOIN channels c ON c.id = p.channel_id
ORDER BY p.created_at DESC
LIMIT 5;
```

### Шаг 4: Проверка других метрик парсера

```bash
# Проверка parser_runs_total
curl "http://localhost:9090/api/v1/query?query=parser_runs_total"

# Проверка posts_parsed_total
curl "http://localhost:9090/api/v1/query?query=posts_parsed_total"

# Проверка scheduler статуса
curl "http://localhost:9090/api/v1/query?query=scheduler_last_tick_ts_seconds"
```

---

## Возможные причины падения метрики

### 1. Нет новых каналов для парсинга

**Симптомы**:
- Метрика `parser_runs_total` не увеличивается
- `last_parsed_at` не обновляется
- Нет новых постов в БД

**Решение**: 
- Проверить, есть ли активные каналы для парсинга
- Проверить, не все ли каналы уже обработаны

### 2. Парсер не запущен или завис

**Симптомы**:
- Health endpoint недоступен
- Scheduler статус != "ok"
- Нет логов парсинга

**Решение**:
- Проверить статус контейнера: `docker-compose ps telethon-ingest`
- Проверить логи: `docker-compose logs telethon-ingest`
- Перезапустить сервис при необходимости

### 3. Проблема с экспортом метрик

**Симптомы**:
- Метрика не найдена в Prometheus
- Другие метрики парсера тоже отсутствуют

**Решение**:
- Проверить, что telethon-ingest экспортирует метрики на порту 8011
- Проверить конфигурацию Prometheus для scraping метрик
- Проверить доступность `/metrics` endpoint

### 4. Нет данных за последние 5 минут

**Симптомы**:
- Метрика существует, но `rate()` возвращает 0 или NaN
- Есть старые данные, но нет новых

**Решение**:
- Это нормально, если парсер не обрабатывает каналы в данный момент
- Проверить, когда был последний тик scheduler'а
- Увеличить окно `rate()` до 15 минут для более стабильных значений

### 5. Все каналы пропущены (skipped)

**Симптомы**:
- `parser_runs_total{status="skipped"}` увеличивается
- `parser_runs_total{status="ok"}` не увеличивается
- Метрика `parser_channel_processing_seconds` обновляется только для skipped каналов

**Решение**:
- Проверить логи на причины пропуска каналов
- Проверить, не все ли каналы в cooldown
- Проверить rate limiting

---

## Автоматическая диагностика

Создан скрипт для автоматической проверки:

```bash
python3 /opt/telegram-assistant/scripts/check_channel_processing_time_issue.py
```

Скрипт проверяет:
1. Наличие метрики в Prometheus
2. Значения перцентилей (p50, p95, p99)
3. Статус telethon-ingest сервиса
4. Активность парсинга (последние обновления last_parsed_at)
5. Другие метрики парсера (parser_runs_total, posts_parsed_total)

---

## Рекомендации

### Если метрика упала из-за отсутствия данных:

1. **Увеличить окно rate()** в Grafana до 15 минут:
   ```
   histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[15m])) by (le, mode, status))
   ```

2. **Использовать `increase()` вместо `rate()`** для абсолютных значений:
   ```
   histogram_quantile(0.50, sum(increase(parser_channel_processing_seconds_bucket[1h])) by (le, mode, status))
   ```

3. **Добавить проверку на NaN** в Grafana:
   ```
   histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status)) or vector(0)
   ```

### Если парсер не работает:

1. Проверить логи: `docker-compose logs telethon-ingest --tail=100`
2. Проверить статус: `curl http://localhost:8011/health`
3. Перезапустить сервис: `docker-compose restart telethon-ingest`

### Если проблема с метриками:

1. Проверить экспорт метрик: `curl http://localhost:8011/metrics | grep parser_channel_processing`
2. Проверить конфигурацию Prometheus для scraping
3. Проверить доступность endpoint'а

---

## Checks

1. **Проверка метрики**:
   ```bash
   curl "http://localhost:9090/api/v1/query?query=parser_channel_processing_seconds_bucket" | jq
   ```

2. **Проверка статуса сервиса**:
   ```bash
   curl http://localhost:8011/health | jq
   ```

3. **Проверка активности**:
   ```bash
   python3 /opt/telegram-assistant/scripts/check_channel_processing_time_issue.py
   ```

4. **Проверка логов**:
   ```bash
   docker-compose logs telethon-ingest --tail=100 | grep -E "CHANNEL_PARSE|scheduler"
   ```

---

## Impact / Rollback

- **Диагностика**: Только проверка, без изменений
- **Скрипт**: Создан для автоматической диагностики
- **Отчет**: Сохранен для анализа

**Следующие шаги**:
1. Запустить скрипт диагностики
2. Проверить результаты
3. Принять меры в зависимости от найденных проблем
