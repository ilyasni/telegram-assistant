# Улучшения диагностики Trend Agents

**Дата**: 2025-01-22  
**Context7**: Добавление детального логирования и метрик для диагностики Trend Agents

---

## Context

Добавлены улучшения для диагностики проблем с обработкой событий и детекцией трендов в TrendDetectionWorker.

---

## Добавленные улучшения

### 1. ✅ Детальное логирование обработки событий

**Изменения в `_handle_message()`**:
- Логирование начала обработки каждого события
- Логирование значений порогов для диагностики
- Логирование причин пропуска трендов
- Логирование успешной обработки с временем выполнения

**Примеры логов**:
```python
logger.debug(
    "trend_worker_processing_event",
    post_id=post_id,
    tenant_id=payload.get("tenant_id"),
)

logger.debug(
    "trend_worker_detection_values",
    post_id=post_id,
    cluster_key=cluster_key,
    ratio=ratio,
    coherence=coherence,
    source_diversity=source_diversity,
    freq_short=freq_short,
    expected_baseline=expected_short_baseline,
    freq_ratio_threshold=self.freq_ratio_threshold,
    coherence_threshold=self.similarity_threshold,
    min_source_diversity=self.min_source_diversity,
)

logger.debug(
    "trend_worker_thresholds_not_met",
    cluster_key=cluster_key,
    ratio=ratio,
    ratio_threshold=self.freq_ratio_threshold,
    source_diversity=source_diversity,
    min_source_diversity=self.min_source_diversity,
    coherence=coherence,
    coherence_threshold=self.similarity_threshold,
    reasons=reasons,
)
```

---

### 2. ✅ Метрики для диагностики порогов

**Новые Histogram метрики**:

```python
trend_detection_ratio_histogram = Histogram(
    "trend_detection_ratio",
    "Burst ratio (freq_short / expected_baseline) for trend detection",
    buckets=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, float("inf")),
)

trend_detection_coherence_histogram = Histogram(
    "trend_detection_coherence",
    "Coherence (similarity) for trend detection",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0),
)

trend_detection_source_diversity_histogram = Histogram(
    "trend_detection_source_diversity",
    "Source diversity (number of unique channels) for trend detection",
    buckets=(0, 1, 2, 3, 5, 10, 15, 20, 30, 50, 100),
)
```

**Использование**:
- Показывают распределение значений `ratio`, `coherence`, `source_diversity`
- Помогают понять, почему тренды не проходят пороги
- Можно использовать в Grafana для визуализации

---

### 3. ✅ Метрики причин пропуска трендов

**Новый Counter метрик**:

```python
trend_detection_threshold_reasons = Counter(
    "trend_detection_threshold_reasons",
    "Reasons why trends are not emitted (threshold checks)",
    ["reason"],  # reason: ratio_too_low|source_diversity_too_low|coherence_too_low|cooldown|all_passed
)
```

**Labels**:
- `ratio_too_low` - ratio < freq_ratio_threshold (по умолчанию 3.0)
- `source_diversity_too_low` - source_diversity < min_source_diversity (по умолчанию 3)
- `coherence_too_low` - coherence < similarity_threshold (по умолчанию 0.55)
- `cooldown` - кластер в cooldown периоде
- `all_passed` - все пороги пройдены, тренд должен быть опубликован

**Использование**:
- Показывает, какой порог чаще всего блокирует публикацию трендов
- Помогает настроить пороги для оптимальной детекции

---

### 4. ✅ Метрика успешной обработки

**Изменения в `trend_events_processed_total`**:

Теперь метрика обновляется с правильными статусами:
- `status="processed"` - успешно обработано
- `status="invalid"` - отсутствует post_id
- `status="missing_post"` - пост не найден в БД
- `status="album_duplicate"` - пропущен как дубликат альбома
- `status="error"` - ошибка обработки
- `status="ready"` - инициализация worker'а (только при старте)

**Использование**:
- Показывает реальное количество обработанных событий
- Помогает отслеживать проблемы с обработкой

---

## Проверка работы

### 1. Проверка метрик

```bash
# Проверка новых метрик
curl http://localhost:8001/metrics | grep "trend_detection"

# Проверка статусов обработки
curl http://localhost:8001/metrics | grep "trend_events_processed_total"
```

### 2. Проверка логов

```bash
# Логи обработки событий
docker compose logs worker | grep "trend_worker"

# Логи значений порогов
docker compose logs worker | grep "trend_worker_detection_values"

# Логи причин пропуска
docker compose logs worker | grep "trend_worker_thresholds_not_met"
```

### 3. Проверка в Grafana

Добавить панели для новых метрик:
- `trend_detection_ratio_histogram` - распределение ratio
- `trend_detection_coherence_histogram` - распределение coherence
- `trend_detection_source_diversity_histogram` - распределение source_diversity
- `trend_detection_threshold_reasons` - причины пропуска трендов
- `trend_events_processed_total{status="processed"}` - успешно обработано

---

## Следующие шаги

1. ✅ Добавлено детальное логирование
2. ✅ Добавлены метрики для диагностики
3. ⏳ Мониторить метрики в течение нескольких часов
4. ⏳ Анализировать распределение значений порогов
5. ⏳ Настроить пороги на основе реальных данных (если нужно)

---

## Вывод

**Все улучшения добавлены и применены.**

Теперь можно:
- Видеть реальное количество обработанных событий (`status="processed"`)
- Понимать, почему тренды не публикуются (метрики `trend_detection_threshold_reasons`)
- Анализировать распределение значений порогов (Histogram метрики)
- Отслеживать обработку событий через детальные логи

**Рекомендация**: Мониторить метрики в течение нескольких часов и анализировать, какие пороги чаще всего блокируют публикацию трендов.

