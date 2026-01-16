# Аудит Prometheus Alerts - 2025-12-07

**Дата**: 2025-12-07  
**Context7**: Проверка соответствия алертов реальным метрикам

---

## Context

Проверка всех Prometheus alerts на соответствие реальным метрикам, экспортируемым worker tasks и другими компонентами системы.

---

## Plan

1. ✅ Проверка синтаксиса алертов
2. ⏳ Проверка соответствия метрик в алертах реальным метрикам
3. ⏳ Выявление несоответствий
4. ⏳ Исправление несоответствий

---

## Результаты проверки

### ✅ Синтаксис алертов

**Статус**: ✅ Валиден

**Результат**: `promtool check rules` - SUCCESS: 82 rules found

---

## Найденные несоответствия

### ❌ 1. Vision Analysis Alerts

**Проблема**: В алертах используется `vision_analysis_requests_total`, но в коде есть `vision_worker_processed_total`

**Алерты**:
- `VisionAnalysisNoActivity` - использует `vision_analysis_requests_total`
- `VisionAnalysisErrorRateHigh` - использует `vision_analysis_requests_total{status="error"}`
- `VisionAnalysisCoverageLow` - использует `vision_analysis_requests_total{status="ok"}`

**Реальные метрики**:
- `vision_worker_processed_total` - Counter (status, reason)
- `vision_worker_duration_seconds` - Histogram (status)
- `vision_analysis_duration_seconds` - Histogram (без labels)

**Решение**: Заменить `vision_analysis_requests_total` на `vision_worker_processed_total`

---

### ⚠️ 2. Vision Analysis Latency Alert

**Проблема**: В алерте используется `vision_analysis_duration_seconds_bucket` с label `provider`, но метрика не имеет этого label

**Алерт**: `VisionAnalysisLatencyHigh`
- Использует: `vision_analysis_duration_seconds_bucket[10m]` by (le, provider)
- Реальная метрика: `vision_analysis_duration_seconds` - Histogram без labels

**Решение**: Убрать `provider` из группировки или использовать `vision_worker_duration_seconds` с label `status`

---

### ⚠️ 3. Vision Analysis Stream Name

**Проблема**: В алерте используется `stream:posts:vision:uploaded`, но VisionAnalysisTask использует `stream:posts:vision`

**Алерт**: `VisionAnalysisNoActivity`
- Использует: `stream_pending_size{stream=~"posts\\.vision\\.uploaded"}`
- Реальный stream: `stream:posts:vision`

**Решение**: Исправить stream name в алерте

---

### ✅ 4. Digest Worker Alerts

**Статус**: ✅ Соответствуют реальным метрикам

**Метрики в алертах**:
- `digest_jobs_processed_total` - ✅ Существует (stage, status)
- `group_digest_quality_scores` - ✅ Существует (metric)

---

### ✅ 5. Album Assembler Alerts

**Статус**: ✅ Соответствуют реальным метрикам

**Метрики в алертах**:
- `albums_parsed_total` - ✅ Существует (status)
- `albums_assembled_total` - ✅ Существует (status)
- `album_assembly_lag_seconds` - ✅ Существует (Histogram)
- `album_items_count_gauge` - ✅ Существует (album_id, status)

---

### ✅ 6. Digest Context Observer Alerts

**Статус**: ✅ Соответствуют реальным метрикам

**Метрики в алертах**:
- `digest_context_messages` - ✅ Существует (metric) - Histogram
- `stream_pending_size{stream=~"digest\\.context\\.prepared"}` - ✅ Stream существует

**Примечание**: `digest_context_messages` - это Histogram, а не Counter, поэтому `rate()` может работать некорректно. Нужно использовать `rate(digest_context_messages_count[10m])` или изменить на Counter.

---

## Context7 Best Practices

### ❌ Проблема: Несоответствие имен метрик

**Context7 Best Practice**: Имена метрик в алертах должны точно соответствовать реальным метрикам из кода.

**Решение**: 
1. Исправить имена метрик в алертах
2. Проверить labels в алертах
3. Убедиться, что stream names соответствуют реальным

---

## Исправления

### 1. Исправить Vision Analysis Alerts

**Изменения**:
- Заменить `vision_analysis_requests_total` на `vision_worker_processed_total`
- Исправить stream name с `posts.vision.uploaded` на `posts.vision`
- Исправить latency alert для использования правильной метрики

### 2. Проверить Digest Context Observer Alert

**Изменения**:
- Проверить использование Histogram в rate() - возможно, нужно использовать Counter

---

## Checks

### Проверка метрик

```bash
# Проверка vision метрик
curl -s http://localhost:8001/metrics | grep -E "^vision_worker"

# Проверка digest метрик
curl -s http://localhost:8001/metrics | grep -E "^digest_"

# Проверка album метрик
curl -s http://localhost:8001/metrics | grep -E "^albums_"
```

### Проверка алертов

```bash
# Проверка синтаксиса
docker compose exec -T prometheus promtool check rules /etc/prometheus/alerts.yml

# Проверка активных алертов
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.state == "firing")'
```

---

## Impact / Rollback

### Impact

**Что изменится**:
- ✅ Алерты будут использовать правильные имена метрик
- ✅ Алерты будут корректно срабатывать
- ✅ Улучшится observability

**Что не затронуто**:
- ✅ Существующие метрики
- ✅ Обратная совместимость

### Rollback

**Если нужно откатить изменения**:
- Изменения в алертах можно откатить через git
- Prometheus перезагрузит конфигурацию автоматически

---

## Итоговый статус

| Компонент | Статус | Проблемы |
|-----------|--------|----------|
| Vision Analysis | ✅ | Исправлено |
| Digest Worker | ✅ | Соответствует |
| Album Assembler | ✅ | Соответствует |
| Digest Context Observer | ✅ | Исправлено (используется _count) |
| Trend Refinement | ✅ | Соответствует |

**Общий статус**: ✅ **Все несоответствия исправлены**

---

## Заключение

✅ **Синтаксис алертов валиден**, но есть несоответствия:
- ❌ Vision Analysis alerts используют неправильные имена метрик
- ⚠️ Vision Analysis alerts используют неправильный stream name
- ⚠️ Digest Context Observer alert использует Histogram в rate()

**Следующие шаги**:
1. ✅ Исправлены Vision Analysis alerts
2. ✅ Исправлен Digest Context Observer alert
3. ✅ Prometheus конфигурация проверена (SUCCESS: 82 rules found)

---

## Исправления применены

### ✅ 1. Исправлены Vision Analysis Alerts

**Изменения**:
- Заменен `vision_analysis_requests_total` на `vision_worker_processed_total`
- Исправлен stream name с `posts.vision.uploaded` на `posts.vision`
- Исправлен latency alert для использования `vision_worker_duration_seconds` с label `status`

**Алерты**:
- `VisionAnalysisNoActivity` - ✅ Исправлен
- `VisionAnalysisErrorRateHigh` - ✅ Исправлен
- `VisionAnalysisLatencyHigh` - ✅ Исправлен
- `VisionAnalysisCoverageLow` - ✅ Исправлен

**Файл**: `prometheus/alerts.yml`

### ✅ 2. Исправлен Digest Context Observer Alert

**Изменения**:
- Заменен `rate(digest_context_messages[10m])` на `rate(digest_context_messages_count[10m])`
- Используется `_count` суффикс для Histogram метрики

**Алерт**: `ContextEventsNoActivity` - ✅ Исправлен

**Файл**: `prometheus/alerts.yml`

### ✅ 3. Проверка синтаксиса

**Результат**: ✅ SUCCESS: 82 rules found

**Статус**: Все алерты валидны после исправлений

### ✅ 4. Проверка других алертов

**Результаты**:
- ✅ Trend Refinement alerts - соответствуют метрикам (`trend_refinement_runs_total` существует)
- ✅ WorkerTaskDown alert - использует стандартную метрику `up{job="worker"}`
- ✅ Все остальные алерты проверены и соответствуют метрикам

---

## Итоговый статус

| Компонент | Статус | Исправления |
|-----------|--------|-------------|
| Vision Analysis | ✅ | Исправлены 4 алерта |
| Digest Context Observer | ✅ | Исправлен 1 алерт |
| Все остальные | ✅ | Соответствуют метрикам |

**Общий статус**: ✅ **Все несоответствия исправлены, алерты готовы к использованию**

