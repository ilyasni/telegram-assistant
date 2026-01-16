# Справочник метрик Prometheus

## Контекст

Документация всех метрик Prometheus, добавленных в систему согласно `reports/PROMETHEUS_COVERAGE_RECOMMENDATIONS.md`.

## PostgreSQL метрики

**Модуль:** `api/utils/postgres_metrics.py`

### postgres_operations_total
- **Тип:** Counter
- **Labels:** `operation`, `status`
- **Описание:** Общее количество операций PostgreSQL
- **Примеры операций:** `upsert_post`, `upsert_channel`, `select`, `insert`, `update`, `delete`

### postgres_operation_duration_seconds
- **Тип:** Histogram
- **Labels:** `operation`
- **Описание:** Длительность операций PostgreSQL
- **Buckets:** [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]

### postgres_connections_active
- **Тип:** Gauge
- **Labels:** `database`
- **Описание:** Активные подключения к PostgreSQL

### postgres_connections_max
- **Тип:** Gauge
- **Labels:** `database`
- **Описание:** Максимальное количество подключений к PostgreSQL

**Использование:**
```python
from api.utils.postgres_metrics import measure_postgres_operation

@measure_postgres_operation('upsert_post')
async def upsert_post(conn, data):
    await conn.execute(...)
```

---

## Qdrant метрики

**Модуль:** `api/worker/integrations/qdrant_client.py`

### qdrant_operations_total
- **Тип:** Counter
- **Labels:** `operation`, `status`
- **Описание:** Общее количество операций Qdrant
- **Примеры операций:** `upsert`, `search`, `delete`, `ensure_collection`

### qdrant_operation_duration_seconds
- **Тип:** Histogram
- **Labels:** `operation`
- **Описание:** Длительность операций Qdrant
- **Buckets:** [0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0]

### qdrant_collection_size
- **Тип:** Gauge
- **Labels:** `collection`
- **Описание:** Количество векторов в коллекции

### qdrant_collection_indexed
- **Тип:** Gauge
- **Labels:** `collection`
- **Описание:** Количество проиндексированных векторов в коллекции

---

## Post Persistence метрики

**Модуль:** `api/worker/tasks/post_persistence_task.py`

### post_persistence_processed_total
- **Тип:** Counter
- **Labels:** `status`
- **Описание:** Общее количество обработанных постов
- **Статусы:** `success`, `error`, `skipped`

### post_persistence_latency_seconds
- **Тип:** Histogram
- **Labels:** `operation`
- **Описание:** Длительность операций post persistence
- **Операции:** `upsert_post`, `upsert_channel`

### post_persistence_db_operations_total
- **Тип:** Counter
- **Labels:** `operation`, `status`
- **Описание:** Общее количество операций БД
- **Операции:** `upsert_post`, `upsert_channel`
- **Статусы:** `success`, `error`

### post_persistence_pel_size
- **Тип:** Gauge
- **Labels:** `consumer_group`
- **Описание:** Размер PEL (Pending Entry List) для post persistence

---

## Graph Writer метрики

**Модуль:** `api/worker/services/graph_writer.py`

### graph_writer_processed_total
- **Тип:** Counter
- **Labels:** `operation_type`, `status`
- **Описание:** Общее количество обработанных событий GraphWriter
- **Операции:** `forward`, `reply`, `author`, `batch`, `persona`, `dlq`

### graph_writer_errors_total
- **Тип:** Counter
- **Labels:** `error_type`
- **Описание:** Общее количество ошибок GraphWriter
- **Типы ошибок:** `parse_error`, `neo4j_error`, `redis_error`, `processing_error`, `dlq_error`

### graph_writer_pel_size
- **Тип:** Gauge
- **Labels:** `stream`, `consumer_group`
- **Описание:** Размер PEL для GraphWriter

### graph_writer_pending_older_than_seconds
- **Тип:** Gauge
- **Labels:** `stream`, `consumer_group`
- **Описание:** Возраст самого старого pending сообщения в секундах

### graph_writer_operation_duration_seconds
- **Тип:** Histogram
- **Labels:** `operation_type`
- **Описание:** Длительность операций GraphWriter
- **Buckets:** [0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]

---

## API Endpoint метрики

**Модуль:** `api/middleware/metrics_middleware.py`

### api_endpoint_requests_total
- **Тип:** Counter
- **Labels:** `endpoint`, `method`, `status_code`
- **Описание:** Общее количество запросов к API endpoints

### api_endpoint_latency_seconds
- **Тип:** Histogram
- **Labels:** `endpoint`, `method`, `status_code`
- **Описание:** Длительность обработки запросов к API endpoints
- **Buckets:** [0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]

**Примечание:** Метрики автоматически собираются через middleware для всех endpoints, кроме `/metrics`, `/health`, `/health/auth`, `/health/bot`.

---

## Neo4j метрики

**Модуль:** `api/worker/metrics.py`

### neo4j_operations_total
- **Тип:** Counter
- **Labels:** `operation_type`, `status`
- **Описание:** Общее количество операций Neo4j

### neo4j_operation_duration_seconds
- **Тип:** Histogram
- **Labels:** `operation_type`
- **Описание:** Длительность операций Neo4j

---

## Алерты

Все алерты определены в `prometheus/alerts.yml` и сгруппированы по компонентам:

### Critical алерты:
- `GraphWriterNoActivity` - Graph Writer не обрабатывает события
- `PostPersistenceNoActivity` - Post Persistence не обрабатывает события
- `PostgresUnavailable` - PostgreSQL недоступен
- `QdrantUnavailable` - Qdrant недоступен
- `Neo4jUnavailable` - Neo4j недоступен
- `RedisUnavailable` - Redis недоступен
- `TelethonIngestCrash` - Telethon Ingest получил crash signal

### Warning алерты:
- `GraphWriterErrorRateHigh` - Graph Writer error rate > 5%
- `GraphWriterPELBacklogHigh` - Graph Writer PEL backlog > 100
- `PostPersistenceErrorRateHigh` - Post Persistence error rate > 5%
- `PostPersistenceLatencyHigh` - Post Persistence p95 latency > 5s
- `PostgresLatencyHigh` - PostgreSQL p95 latency > 1s
- `PostgresErrorRateHigh` - PostgreSQL error rate > 5%
- `PostgresConnectionsHigh` - PostgreSQL connections > 80%
- `QdrantLatencyHigh` - Qdrant p95 latency > 2s
- `QdrantErrorRateHigh` - Qdrant error rate > 5%
- `Neo4jLatencyHigh` - Neo4j p95 latency > 2s
- `Neo4jErrorRateHigh` - Neo4j error rate > 5%
- И другие...

Полный список алертов см. в `prometheus/alerts.yml`.

---

## Использование

### Проверка метрик в Prometheus:
```bash
# Все метрики
curl http://localhost:9090/api/v1/label/__name__/values

# Конкретная метрика
curl "http://localhost:9090/api/v1/query?query=postgres_operations_total"

# Метрики с labels
curl "http://localhost:9090/api/v1/query?query=qdrant_operations_total{operation='search'}"
```

### Проверка алертов:
```bash
# Активные алерты
curl http://localhost:9090/api/v1/alerts

# Правила алертов
curl http://localhost:9090/api/v1/rules
```

### Использование скриптов:
```bash
# Проверка метрик
./scripts/check_prometheus_metrics.sh

# Проверка алертов
./scripts/check_prometheus_alerts.sh
```

---

**Дата создания:** 2025-01-03  
**Версия:** 1.0

