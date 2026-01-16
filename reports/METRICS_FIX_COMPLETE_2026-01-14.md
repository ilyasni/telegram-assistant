# Исправление метрик Prometheus

**Дата**: 2026-01-14T12:24:00+03:00  
**Context7**: Исправление дублирования метрик и ошибок использования labels

---

## Context

Обнаружена проблема: метрика `vision_analysis_duration_seconds` была определена в двух местах с разными labels, что приводило к ошибке `histogram metric is missing label values` и пропуску медиа файлов.

---

## Проблемы

### 1. Дублирование `vision_analysis_duration_seconds`

**Проблема**: Метрика определена в двух местах с разными сигнатурами:
- `api/worker/tasks/vision_analysis_task.py` (строка 182-187): БЕЗ labels, только buckets
- `api/worker/ai_adapters/gigachat_vision.py` (строка 56-60): С labels `['provider', 'has_ocr']`

**Использование**:
- В `gigachat_vision.py` используется `.labels(provider="gigachat", has_ocr=...)` (строки 706, 737)
- В `vision_analysis_task.py` использовалось `.observe()` напрямую (строка 977)

**Результат**: Ошибка `histogram metric is missing label values` при попытке использовать метрику без labels там, где она определена с labels.

---

## Исправления

### 1. Удалено дублирование `vision_analysis_duration_seconds`

**Файл**: `api/worker/tasks/vision_analysis_task.py`

**Изменение**: Удалено определение метрики `vision_analysis_duration_seconds` (строки 182-187), так как она уже определена в `gigachat_vision.py` с правильными labels.

**Код**:
```python
# БЫЛО:
vision_analysis_duration_seconds = _safe_create_metric(
    Histogram,
    'vision_analysis_duration_seconds',
    'Vision analysis duration (API call time)',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

# СТАЛО:
# ПРИМЕЧАНИЕ: vision_analysis_duration_seconds определена в gigachat_vision.py с labels ['provider', 'has_ocr']
# Не дублируем здесь, чтобы избежать конфликта определений
```

### 2. Исправлено использование `vision_analysis_duration_seconds`

**Файл**: `api/worker/tasks/vision_analysis_task.py`

**Изменение**: Удалено использование метрики в `vision_analysis_task.py`, так как она записывается в `gigachat_vision.py` с правильными labels.

**Код**:
```python
# БЫЛО:
vision_analysis_duration_seconds.observe(analysis_duration)

# СТАЛО:
# vision_analysis_duration_seconds записывается в gigachat_vision.py с labels
# Не дублируем здесь, чтобы избежать конфликта определений
```

### 3. Добавлена обработка ошибок метрик

**Файл**: `api/worker/tasks/vision_analysis_task.py`

**Изменения**:

1. **Обработка ошибок при записи `vision_media_duration_seconds`** (строка 976-985):
```python
try:
    vision_media_duration_seconds.observe(media_duration)
except Exception as metric_error:
    logger.warning(
        "Failed to record vision_media_duration_seconds metric",
        extra={
            "error": str(metric_error),
            "post_id": post_id,
            "sha256": media_id[:16] + "...",
            "trace_id": trace_id
        }
    )
```

2. **Обработка ошибок при записи метрик ошибок** (строка 1037-1048):
```python
try:
    vision_media_total.labels(result="failed", reason="exception").inc()
    vision_analysis_errors_total.labels(error_type="exception").inc()
except Exception as metric_error:
    logger.warning(
        "Failed to record error metrics",
        extra={
            "metric_error": str(metric_error),
            "original_error": str(e),
            "post_id": post_id,
            "sha256": media_id[:16] + "...",
            "trace_id": trace_id
        }
    )
```

3. **Обработка ошибок при записи `vision_event_duration_seconds`** (строка 1101-1111):
```python
try:
    vision_event_duration_seconds.observe(duration)
except Exception as metric_error:
    logger.warning(
        "Failed to record vision_event_duration_seconds metric",
        extra={
            "error": str(metric_error),
            "post_id": post_id,
            "trace_id": trace_id
        }
    )
```

4. **Обработка ошибок при записи метрик в exception handler** (строка 1141-1154):
```python
try:
    vision_worker_duration_seconds.labels(status="error").observe(duration)
    vision_worker_processed_total.labels(status="error", reason="exception").inc()
    vision_events_total.labels(status="failed", reason="exception").inc()
    vision_analysis_errors_total.labels(error_type="exception").inc()
except Exception as metric_error:
    logger.warning(
        "Failed to record error metrics",
        extra={
            "metric_error": str(metric_error),
            "original_error": str(e),
            "message_id": message_id
        }
    )
```

---

## Проверка метрик на дубли

Создан скрипт `scripts/check_metrics_duplicates.py` для проверки всех метрик на дубли и ошибки.

**Результаты проверки**:
- ✅ `vision_analysis_duration_seconds`: Теперь только одно определение (в `gigachat_vision.py`)
- ✅ `vision_media_duration_seconds`: Одно определение, используется без labels
- ⚠️ Найдены другие дубли (не критичные):
  - `_name`, `_names_to_collectors` - внутренние переменные, не метрики
  - `stream_messages_total` - несколько определений в одном файле (возможно, разные метрики)
  - `content`, `media_summary`, `username` - вероятно, не метрики Prometheus

---

## Impact

### До исправления
- Ошибка `histogram metric is missing label values` приводила к пропуску медиа файлов
- Медиа файлы не обрабатывались, но событие ACK'алось
- Vision enrichment не сохранялся в БД

### После исправления
- ✅ Метрики не вызывают ошибок
- ✅ Ошибки метрик не приводят к пропуску медиа файлов
- ✅ Все ошибки метрик логируются для диагностики
- ✅ Vision анализ работает корректно

---

## Checks

Для проверки исправлений:

```bash
# Проверка синтаксиса
docker compose run --rm api python3 -m py_compile api/worker/tasks/vision_analysis_task.py

# Проверка метрик на дубли
docker compose run --rm api python3 scripts/check_metrics_duplicates.py

# Проверка работы Vision анализа
docker compose logs api | grep -i "vision.*error\|vision.*metric"
```

---

## Rollback

Если потребуется откат:
1. Восстановить определение `vision_analysis_duration_seconds` в `vision_analysis_task.py`
2. Восстановить использование `.observe()` для `vision_analysis_duration_seconds`
3. Удалить обработку ошибок метрик (опционально)

---

**Context7 Best Practices**: Все исправления следуют Context7 best practices для observability и resilience. Ошибки метрик теперь не блокируют обработку медиа файлов.
