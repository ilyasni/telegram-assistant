# Реализация метрик и алертов Prometheus - Завершено

## Контекст

Реализованы все рекомендации из `reports/PROMETHEUS_COVERAGE_RECOMMENDATIONS.md` для улучшения покрытия метриками и алертами всех компонентов системы. Использованы Context7 best practices и избежано дублирование.

## Выполненные задачи

### Фаза 1: Critical (5 компонентов)

### 1. Graph Writer Task - Метрики и алерты ✅

**Метрики:** Уже существовали в `api/worker/services/graph_writer.py`:
- `graph_writer_processed_total` - Counter (operation_type, status)
- `graph_writer_errors_total` - Counter (error_type)
- `graph_writer_pel_size` - Gauge (stream, consumer_group)
- `graph_writer_pending_older_than_seconds` - Gauge (stream, consumer_group)
- `graph_writer_operation_duration_seconds` - Histogram (operation_type)

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `GraphWriterNoActivity` - критический, если нет обработки при наличии pending сообщений
- `GraphWriterErrorRateHigh` - warning, если error rate > 5%
- `GraphWriterPELBacklogHigh` - warning, если PEL backlog > 100

### 2. Post Persistence Task - Метрики и алерты ✅

**Метрики:** Добавлены в `api/worker/tasks/post_persistence_task.py`:
- `post_persistence_processed_total` - Counter (status)
- `post_persistence_latency_seconds` - Histogram (operation)
- `post_persistence_db_operations_total` - Counter (operation, status)
- `post_persistence_pel_size` - Gauge (consumer_group)

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `PostPersistenceNoActivity` - критический, если нет обработки при наличии pending
- `PostPersistenceErrorRateHigh` - warning, если error rate > 5%
- `PostPersistenceLatencyHigh` - warning, если p95 latency > 5s

**Интеграция:** Использован декоратор `@measure_postgres_operation` для автоматического измерения операций БД.

### 3. PostgreSQL - Метрики и алерты ✅

**Метрики:** Создан новый модуль `api/utils/postgres_metrics.py`:
- `postgres_operations_total` - Counter (operation, status)
- `postgres_operation_duration_seconds` - Histogram (operation)
- `postgres_connections_active` - Gauge (database)
- `postgres_connections_max` - Gauge (database)

**Декоратор:** `@measure_postgres_operation(operation)` для автоматического измерения операций.

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `PostgresUnavailable` - критический, если сервисы не могут подключиться
- `PostgresLatencyHigh` - warning, если p95 latency > 1s
- `PostgresErrorRateHigh` - warning, если error rate > 5%
- `PostgresConnectionsHigh` - warning, если использование соединений > 80%

**Интеграция:** Метрики интегрированы в `post_persistence_task.py` через декоратор.

### 4. Qdrant - Метрики и алерты ✅

**Метрики:** Добавлены в `api/worker/integrations/qdrant_client.py`:
- `qdrant_operations_total` - Counter (operation, status)
- `qdrant_operation_duration_seconds` - Histogram (operation)
- `qdrant_collection_size` - Gauge (collection)
- `qdrant_collection_indexed` - Gauge (collection)

**Интеграция:** Метрики добавлены в методы:
- `ensure_collection` - обновление метрик коллекции
- `upsert_vector` - измерение операций upsert
- `delete_vector` - измерение операций delete
- `search_vectors` - измерение операций search
- `get_collection_stats` - обновление метрик размера коллекции

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `QdrantUnavailable` - критический, если worker не может подключиться
- `QdrantLatencyHigh` - warning, если p95 latency > 2s
- `QdrantErrorRateHigh` - warning, если error rate > 5%

### 5. Neo4j - Алерты ✅

**Метрики:** Уже существовали в `api/worker/metrics.py`:
- `neo4j_operations_total` - Counter (operation_type, status)
- `neo4j_operation_duration_seconds` - Histogram (operation_type)

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `Neo4jUnavailable` - критический, если worker не может подключиться
- `Neo4jLatencyHigh` - warning, если p95 latency > 2s
- `Neo4jErrorRateHigh` - warning, если error rate > 5%

---

### Фаза 2: Warning (11 компонентов)

### 6. Enrichment Task - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `EnrichmentNoActivity` - warning, если нет обработки при наличии pending
- `EnrichmentErrorRateHigh` - warning, если error rate > 10%
- `EnrichmentLatencyHigh` - warning, если p95 latency > 30s

### 7. Tag Persistence Task - Дополнительные алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `TagPersistNoActivity` - warning, если нет обработки при наличии pending
- `TagPersistErrorRateHigh` - warning, если error rate > 5%
- `TagPersistLatencyHigh` - warning, если p95 latency > 5s

### 8. Retagging Task - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `RetaggingNoActivity` - warning, если нет обработки при наличии pending
- `RetaggingErrorRateHigh` - warning, если error rate > 5%
- `RetaggingLatencyHigh` - warning, если p95 latency > 10s

### 9. Digest Worker - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `DigestGenerationFailed` - warning, если error rate > 10%
- `DigestSendFailed` - warning, если error rate > 5%
- `DigestQualityLow` - info, если median quality < 0.7

### 10. Trends - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `TrendDetectionFailed` - warning, если error rate > 10%
- `TrendClusteringFailed` - warning, если rejection rate > 10%
- `TrendRefinementFailed` - warning, если error rate > 10%

### 11. Telethon Ingest - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `TelethonIngestCrash` - критический, если получен crash signal
- `TelethonIngestPostsLostHigh` - warning, если loss rate > 5%
- `TelethonIngestChannelCoverageLow` - warning, если coverage < 80%
- `TelethonIngestMediaProcessingFailed` - warning, если error rate > 10%

### 12. Cleanup Task - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `CleanupFailed` - warning, если error rate > 10%
- `CleanupLatencyHigh` - warning, если p95 latency > 60s

### 13. Context Events Task - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `ContextEventsNoActivity` - warning, если нет обработки при наличии pending

### 14. DLQ - Дополнительные алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `DLQBacklogHigh` - warning, если backlog > 1000 событий
- `DLQAutoHealFailed` - warning, если success rate < 50%

---

### Фаза 3: Info (3 компонента)

### 15. API Endpoints - Метрики и алерты ✅

**Метрики:** Создан новый middleware `api/middleware/metrics_middleware.py`:
- `api_endpoint_requests_total` - Counter (endpoint, method, status_code)
- `api_endpoint_latency_seconds` - Histogram (endpoint, method, status_code)

**Интеграция:** Middleware зарегистрирован в `api/main.py`.

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `APIEndpointLatencyHigh` - warning, если p95 latency > 5s для критичных endpoints
- `APIEndpointErrorRateHigh` - warning, если error rate > 5% для критичных endpoints

### 16. Redis - Алерты ✅

**Алерты:** Добавлены в `prometheus/alerts.yml`:
- `RedisUnavailable` - критический, если сервисы не могут подключиться
- `RedisMemoryHigh` - warning, если использование памяти > 80%

**Примечание:** Метрики Redis можно добавить позже при необходимости.

### 17. Дублирование алертов Storage Quota - Исправлено ✅

**Исправление:** Удалены дубликаты алертов `S3StorageQuotaWarning` и `S3StorageQuotaCritical` из `prometheus/alerts/vision_s3_alerts.yml`. Используются единые алерты из `prometheus/alerts/storage_quota_alerts.yml`.

---

## Созданные файлы

1. **`api/utils/postgres_metrics.py`** - модуль метрик PostgreSQL с декоратором для измерения операций
2. **`api/middleware/metrics_middleware.py`** - middleware для метрик API endpoints

## Обновленные файлы

1. **`prometheus/alerts.yml`** - добавлены все алерты (21 группа алертов)
2. **`api/worker/tasks/post_persistence_task.py`** - добавлены метрики и интеграция с PostgreSQL метриками
3. **`api/worker/integrations/qdrant_client.py`** - добавлены метрики для всех операций
4. **`api/main.py`** - добавлен metrics middleware
5. **`api/worker/run_all_tasks.py`** - добавлены импорты метрик для автоматической регистрации
6. **`prometheus/alerts/vision_s3_alerts.yml`** - удалены дубликаты алертов Storage Quota

## Использованные Context7 best practices

1. **Безопасное создание метрик** - функция `_safe_create_metric` для избежания дублирования
2. **Декораторы для измерения** - `@measure_postgres_operation` для автоматического измерения операций
3. **Единые naming conventions** - все метрики следуют единому формату именования
4. **Избежание дублирования** - проверка существования метрик перед созданием
5. **Периодическое обновление метрик** - обновление PEL метрик каждые 30 секунд

## Статистика реализации

- **Всего групп алертов:** 21
- **Всего алертов:** ~60+
- **Созданных файлов:** 2
- **Обновленных файлов:** 6
- **Добавленных метрик:** ~20+
- **Исправленных дубликатов:** 2 (Storage Quota алерты)

## Проверка результата

1. **Проверить метрики в Prometheus:**
   ```bash
   curl http://localhost:9090/api/v1/targets
   ```

2. **Проверить алерты:**
   ```bash
   curl http://localhost:9090/api/v1/alerts
   ```

3. **Проверить конфигурацию Prometheus:**
   ```bash
   docker exec prometheus promtool check config /etc/prometheus/prometheus.yml
   ```

4. **Проверить конфигурацию AlertManager:**
   ```bash
   docker exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
   ```

## Impact / Rollback

### Возможные проблемы:

1. **Дублирование метрик** - если метрика уже существует, используется существующая (через `_safe_create_metric`)
2. **Производительность** - периодическое обновление метрик может влиять на производительность (интервал 30-60 секунд)
3. **Память** - большое количество метрик может увеличить использование памяти

### Откат:

1. Удалить созданные файлы:
   - `api/utils/postgres_metrics.py`
   - `api/middleware/metrics_middleware.py`

2. Откатить изменения в обновленных файлах через git:
   ```bash
   git checkout -- api/worker/tasks/post_persistence_task.py
   git checkout -- api/worker/integrations/qdrant_client.py
   git checkout -- api/main.py
   git checkout -- api/worker/run_all_tasks.py
   git checkout -- prometheus/alerts.yml
   git checkout -- prometheus/alerts/vision_s3_alerts.yml
   ```

3. Перезапустить сервисы:
   ```bash
   docker-compose restart api worker prometheus alertmanager
   ```

---

## Следующие шаги

1. Протестировать метрики и алерты на dev окружении
2. Проверить, что все метрики экспортируются в Prometheus
3. Проверить, что алерты корректно срабатывают
4. Обновить Grafana dashboards с новыми метриками
5. Документировать новые метрики и алерты

---

**Дата завершения:** 2025-01-03  
**Статус:** ✅ Завершено

