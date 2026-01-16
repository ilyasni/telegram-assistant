# Проверка метрики Channel Processing Time

**Дата**: 2025-12-03  
**Задача**: Проверить корректность данных, которые приходят в панель "Channel Processing Time" в Grafana

---

## Контекст

Панель "⏱️ Channel Processing Time" в Grafana показывает перцентили времени обработки каналов (p50, p95, p99) по режимам и статусам.

---

## Проверка

### 1. Наличие метрики в Prometheus

✅ **Метрика существует**:
- Название: `parser_channel_processing_seconds_bucket`
- Тип: Histogram
- Labels: `mode` (incremental), `status` (ok)
- Buckets: 11 значений (0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, +Inf)

### 2. Запросы в Grafana

✅ **Запросы корректны**:

**Панель**: "⏱️ Channel Processing Time" (id: 12)

Запросы:
- **p50**: `histogram_quantile(0.50, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`
- **p95**: `histogram_quantile(0.95, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`
- **p99**: `histogram_quantile(0.99, sum(rate(parser_channel_processing_seconds_bucket[5m])) by (le, mode, status))`

**Текущие значения** (на момент проверки):
- p50: ~0.545 секунд
- p95: ~1.937 секунд
- p99: ~45 секунд

### 3. Обновление метрики в коде

✅ **Метрика обновляется правильно**:

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py`

**Строка**: 812

```python
process_duration = (datetime.now(timezone.utc) - process_start_time).total_seconds()
parser_channel_processing_seconds.labels(mode=mode or "unknown", status=status).observe(process_duration)
```

**Время измеряется**:
- От: `process_start_time = datetime.now(timezone.utc)` (строка 632)
- До: finally блок (строка 808-812)

**Включает в себя**:
- Получение telegram_id и tenant_id
- Получение Telegram клиента
- Создание парсера
- Парсинг канала
- Обработка результатов
- Обновление last_parsed_at

### 4. Определение метрики

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py`

**Строки**: 90-95

```python
parser_channel_processing_seconds = Histogram(
    'parser_channel_processing_seconds',
    'Time spent processing a single channel',
    ['mode', 'status'],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
)
```

---

## Анализ данных

### Разница между метриками

⚠️ **Важно**: В логах `channel_parser.py` может показывать `processing_time_seconds: 51.36 секунд`, но это время только парсинга сообщений, а не всего процесса обработки канала.

**Метрика `parser_channel_processing_seconds`** измеряет время всего процесса `parse_single_channel`, который включает:
1. Получение системного пользователя и tenant_id
2. Получение Telegram клиента
3. Создание парсера
4. Парсинг канала (включая FloodWait)
5. Обработка результатов
6. Обновление last_parsed_at

Это объясняет, почему метрика может показывать разные значения по сравнению с временем парсинга в логах.

---

## Выводы

✅ **Все в порядке**:

1. ✅ Метрика существует в Prometheus и собирается корректно
2. ✅ Запросы в Grafana синтаксически корректны
3. ✅ Метрика обновляется в коде правильно
4. ✅ Значения перцентилей находятся в разумных пределах (p50 < 1s, p95 < 2s, p99 < 60s)

### Рекомендации

1. ✅ **Никаких изменений не требуется** - метрика работает корректно
2. 📊 **Мониторинг**: Следить за p99 значениями - если они превышают 300 секунд, это может указывать на проблему
3. 📝 **Документация**: Уточнить, что метрика измеряет время всего процесса обработки канала, а не только парсинга сообщений

---

## Связанные файлы

- `grafana/dashboards/system_overview.json` - панель в Grafana
- `telethon-ingest/tasks/parse_all_channels_task.py` - определение и обновление метрики
- `scripts/check_channel_processing_time.sh` - скрипт для проверки метрики

---

**Статус**: ✅ Проверка завершена, все в порядке

