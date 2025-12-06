# Проверка Prometheus Alerts

**Дата**: 2025-12-03  
**Задача**: Проверить корректность конфигурации и статус Prometheus alerts

---

## Контекст

Prometheus alerts используются для мониторинга состояния системы и оповещения о проблемах. Необходимо убедиться, что:
1. Все файлы алертов имеют корректный синтаксис
2. Все важные алерты загружены
3. Активные алерты работают корректно
4. AlertManager настроен (если требуется)

---

## Результаты проверки

### 1. Синтаксис конфигурации ✅

✅ **Все файлы алертов имеют корректный синтаксис**:

- `alerts.yml`: ✅ 28 правил
- `alerts/storage_quota_alerts.yml`: ✅ 6 правил
- `alerts/performance_metrics_alerts.yml`: ✅ 10 правил
- `alerts/vision_s3_alerts.yml`: ✅ 16 правил (но не загружен)

**Итого**: 60 правил, все корректны

### 2. Загруженные файлы алертов

✅ **Загружены в `prometheus.yml`**:
- `alerts.yml` (28 правил)
- `alerts/storage_quota_alerts.yml` (6 правил)
- `alerts/performance_metrics_alerts.yml` (10 правил)

⚠️ **Не загружены**:
- `alerts/vision_s3_alerts.yml` (16 правил) - требуется добавить в `prometheus.yml`

### 3. Загруженные группы алертов

В Prometheus загружено **7 групп** с **44 правилами**:

1. **album_pipeline** (8 правил) - мониторинг сборки альбомов
2. **crawl_latency_quantiles** (2 правила) - recording rules для квантилей
3. **crawl_pipeline** (5 правил) - мониторинг пайплайна Crawl4AI
4. **pipeline_coverage** (6 правил) - покрытие пайплайна обработки постов
5. **system_stability** (4 правила) - стабильность системы (scheduler, health checks)
6. **performance_metrics** (10 правил) - метрики производительности
7. **storage_quota** (6 правил) - квоты хранилища

### 4. Активные алерты

⚠️ **Найдено 32 активных алерта**:

- **AlbumItemsCountMismatch** (множественные) - альбомы не собрались полностью (< 90% элементов проанализировано)
- **CrawlTriggerQueueDepthHigh** - высокая глубина очереди триггера (9976)

### 5. AlertManager

⚠️ **AlertManager не настроен**:
- AlertManager отсутствует в `docker-compose.yml`
- Алерты генерируются в Prometheus, но не отправляются никуда
- Рекомендуется настроить AlertManager для отправки уведомлений

---

## Важные алерты

### System Stability Alerts

✅ **Загружены и работают**:
- `SchedulerNotRunning` - scheduler не запущен > 1 минуты
- `SchedulerFreshnessHigh` - scheduler не обновлялся > 10 минут
- `SchedulerFreshnessCritical` - scheduler не обновлялся > 15 минут
- `SchedulerHeartbeatMissing` - heartbeat отсутствует > 60 секунд
- `HealthCheckFailuresHigh` - health checks падают
- `RedisStreamPendingHigh` - высокое количество pending сообщений
- `ContainerRestartsHigh` - частые перезапуски контейнеров

### Pipeline Coverage Alerts

✅ **Загружены**:
- `IndexingCoverageLow` - индексация < 90%
- `TaggingCoverageLow` - тегирование < 80%
- `RedisStreamLagHigh` - высокий lag в Redis Streams
- `WorkerTaskDown` - worker не отвечает
- `IndexingNoActivity` - индексация не обрабатывает события
- `TaggingNoActivity` - тегирование не обрабатывает события

---

## Проблемы и рекомендации

### 1. ⚠️ Файл `alerts/vision_s3_alerts.yml` не загружен

**Проблема**: Файл существует и содержит 16 правил, но не загружен в `prometheus.yml`.

**Решение**: Добавить в `prometheus.yml`:

```yaml
rule_files:
  - "alerts.yml"
  - "alerts/storage_quota_alerts.yml"
  - "alerts/performance_metrics_alerts.yml"
  - "alerts/vision_s3_alerts.yml"  # ← Добавить
```

**Важность**: Файл содержит важные алерты для:
- Vision Analysis (latency, error rate, availability)
- S3 Storage Quota
- Budget Gate
- DLQ (Dead Letter Queue)
- Crawl4AI SLO

### 2. ⚠️ AlertManager не настроен

**Проблема**: AlertManager отсутствует, алерты не отправляются.

**Рекомендация**: Настроить AlertManager для отправки уведомлений:
- Telegram
- Email
- Slack
- Webhook

### 3. ⚠️ Множественные активные алерты

**Проблема**: 32 активных алерта, в основном связанные с альбомами.

**Рекомендация**: Проверить:
- Почему альбомы не собираются полностью
- Почему очередь триггера настолько высока (9976)

---

## Следующие шаги

1. ✅ **Добавить `alerts/vision_s3_alerts.yml` в `prometheus.yml`**
2. ⚠️ **Настроить AlertManager** (если требуется отправка уведомлений)
3. ⚠️ **Исследовать активные алерты**:
   - Почему альбомы не собираются полностью
   - Почему очередь триггера высокая
4. ✅ **Мониторить алерты System Stability** - они работают корректно

---

## Связанные файлы

- `prometheus/prometheus.yml` - основная конфигурация Prometheus
- `prometheus/alerts.yml` - основные алерты
- `prometheus/alerts/storage_quota_alerts.yml` - алерты квот
- `prometheus/alerts/performance_metrics_alerts.yml` - алерты производительности
- `prometheus/alerts/vision_s3_alerts.yml` - алерты Vision/S3 (не загружен)
- `scripts/check_prometheus_alerts.sh` - скрипт для проверки алертов

---

**Статус**: ✅ Синтаксис корректен, ⚠️ Требуется добавить vision_s3_alerts.yml

