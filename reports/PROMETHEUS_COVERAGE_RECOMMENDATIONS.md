# Рекомендации по улучшению покрытия метриками и алертами

**Дата**: 2025-12-03  
**Статус**: Детальные рекомендации с примерами кода

---

## Принципы реализации

### 1. Использование Context7

При реализации новых метрик и алертов рекомендуется:

1. **Использовать Context7 best practices** для мониторинга
2. **Проверять соответствие** метрик рекомендациям Context7
3. **Использовать Context7** для поиска лучших практик мониторинга
4. **Избегать дублирования** метрик и алертов
5. **Использовать единые naming conventions**

### 2. Избежание дублирования

**Правила:**
- Одна метрика для одного функционала
- Один алерт для одного сценария
- При обнаружении дублирования - определить основную метрику/алерт, остальные удалить или объединить

### 3. Naming Conventions

**Метрики:**
- Использовать суффиксы: `_total` для Counter, `_seconds` для Histogram, без суффикса для Gauge
- Использовать snake_case
- Использовать префиксы для группировки: `component_metric_name`

**Алерты:**
- Использовать PascalCase
- Использовать описательные имена: `ComponentProblemDescription`

---

## Рекомендации по компонентам

### 1. Graph Writer Task

**Файл:** `api/worker/tasks/graph_writer_task.py`

**Метрики:**
```python
from prometheus_client import Counter, Histogram, Gauge

# Метрики обработки событий
graph_writer_processed_total = Counter(
    'graph_writer_processed_total',
    'Total events processed by graph writer',
    ['status']  # success, error, skipped
)

graph_writer_latency_seconds = Histogram(
    'graph_writer_latency_seconds',
    'Graph writer processing latency',
    ['operation'],  # create_node, create_relationship
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)

# Метрики Neo4j операций
graph_writer_neo4j_operations_total = Counter(
    'graph_writer_neo4j_operations_total',
    'Total Neo4j operations',
    ['operation', 'status']  # create_node, create_relationship, success, error
)

graph_writer_neo4j_latency_seconds = Histogram(
    'graph_writer_neo4j_latency_seconds',
    'Neo4j operation latency',
    ['operation'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
)

# Метрики PEL
graph_writer_pel_size = Gauge(
    'graph_writer_pel_size',
    'Pending Entry List size for graph writer',
    ['consumer_group']
)

graph_writer_pending_older_than_seconds = Gauge(
    'graph_writer_pending_older_than_seconds',
    'Age of oldest pending message in seconds',
    ['consumer_group']
)
```

**Алерты:**
```yaml
- alert: GraphWriterNoActivity
  expr: |
    sum(rate(graph_writer_processed_total[10m])) == 0
    and
    sum(graph_writer_pel_size) > 10
  for: 10m
  labels:
    severity: critical
    component: graph_writer
  annotations:
    summary: "Graph writer не обрабатывает события"
    description: "Есть pending сообщения ({{ $value }}), но graph writer не обрабатывает. Проверить логи worker и состояние Neo4j."

- alert: GraphWriterErrorRateHigh
  expr: |
    (
      sum(rate(graph_writer_processed_total{status="error"}[5m]))
      /
      sum(rate(graph_writer_processed_total[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    component: graph_writer
  annotations:
    summary: "Graph writer error rate превышает 5%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние Neo4j."

- alert: GraphWriterPELBacklogHigh
  expr: sum(graph_writer_pel_size) > 100
  for: 5m
  labels:
    severity: warning
    component: graph_writer
  annotations:
    summary: "Graph writer PEL backlog высокий ({{ $value }} сообщений)"
    description: "Требуется проверка производительности graph writer."
```

---

### 2. Post Persistence Task

**Файл:** `api/worker/tasks/post_persistence_task.py`

**Метрики:**
```python
from prometheus_client import Counter, Histogram, Gauge

# Метрики обработки событий
post_persistence_processed_total = Counter(
    'post_persistence_processed_total',
    'Total posts persisted',
    ['status']  # success, error, skipped
)

post_persistence_latency_seconds = Histogram(
    'post_persistence_latency_seconds',
    'Post persistence latency',
    ['operation'],  # upsert_post, upsert_channel
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
)

# Метрики БД операций
post_persistence_db_operations_total = Counter(
    'post_persistence_db_operations_total',
    'Total DB operations',
    ['operation', 'status']  # upsert_post, upsert_channel, success, error
)

# Метрики PEL
post_persistence_pel_size = Gauge(
    'post_persistence_pel_size',
    'Pending Entry List size for post persistence',
    ['consumer_group']
)
```

**Алерты:**
```yaml
- alert: PostPersistenceNoActivity
  expr: |
    sum(rate(post_persistence_processed_total[10m])) == 0
    and
    sum(post_persistence_pel_size) > 10
  for: 10m
  labels:
    severity: critical
    component: post_persistence
  annotations:
    summary: "Post persistence не обрабатывает события"
    description: "Есть pending сообщения ({{ $value }}), но post persistence не обрабатывает. Проверить логи worker и состояние БД."

- alert: PostPersistenceErrorRateHigh
  expr: |
    (
      sum(rate(post_persistence_processed_total{status="error"}[5m]))
      /
      sum(rate(post_persistence_processed_total[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    component: post_persistence
  annotations:
    summary: "Post persistence error rate превышает 5%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние БД."

- alert: PostPersistenceLatencyHigh
  expr: |
    histogram_quantile(0.95, rate(post_persistence_latency_seconds_bucket[5m])) > 5
  for: 10m
  labels:
    severity: warning
    component: post_persistence
  annotations:
    summary: "Post persistence p95 latency превышает 5 секунд"
    description: "P95 latency = {{ $value }}s. Проверить производительность БД."
```

---

### 3. PostgreSQL

**Файл:** Создать новый `api/utils/postgres_metrics.py`

**Метрики:**
```python
from prometheus_client import Counter, Histogram, Gauge
from functools import wraps
import time

# Метрики операций
postgres_operations_total = Counter(
    'postgres_operations_total',
    'Total PostgreSQL operations',
    ['operation', 'status']  # select, insert, update, delete, success, error
)

postgres_operation_duration_seconds = Histogram(
    'postgres_operation_duration_seconds',
    'PostgreSQL operation duration',
    ['operation'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
)

# Метрики подключений
postgres_connections_active = Gauge(
    'postgres_connections_active',
    'Active PostgreSQL connections',
    ['database']
)

postgres_connections_max = Gauge(
    'postgres_connections_max',
    'Maximum PostgreSQL connections',
    ['database']
)

# Декоратор для измерения операций
def measure_postgres_operation(operation: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                postgres_operations_total.labels(operation=operation, status='success').inc()
                return result
            except Exception as e:
                postgres_operations_total.labels(operation=operation, status='error').inc()
                raise
            finally:
                duration = time.time() - start_time
                postgres_operation_duration_seconds.labels(operation=operation).observe(duration)
        return wrapper
    return decorator
```

**Алерты:**
```yaml
- alert: PostgresUnavailable
  expr: up{job="api"} == 0 or up{job="worker"} == 0
  for: 1m
  labels:
    severity: critical
    component: postgres
  annotations:
    summary: "PostgreSQL недоступен"
    description: "Сервисы не могут подключиться к PostgreSQL. Проверить состояние БД."

- alert: PostgresLatencyHigh
  expr: |
    histogram_quantile(0.95, rate(postgres_operation_duration_seconds_bucket[5m])) > 1
  for: 5m
  labels:
    severity: warning
    component: postgres
  annotations:
    summary: "PostgreSQL p95 latency превышает 1 секунду"
    description: "P95 latency = {{ $value }}s. Проверить производительность БД."

- alert: PostgresErrorRateHigh
  expr: |
    (
      sum(rate(postgres_operations_total{status="error"}[5m]))
      /
      sum(rate(postgres_operations_total[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    component: postgres
  annotations:
    summary: "PostgreSQL error rate превышает 5%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи и состояние БД."

- alert: PostgresConnectionsHigh
  expr: |
    (
      sum(postgres_connections_active)
      /
      sum(postgres_connections_max)
    ) > 0.80
  for: 5m
  labels:
    severity: warning
    component: postgres
  annotations:
    summary: "PostgreSQL connections превышают 80% от лимита"
    description: "Использование соединений = {{ $value | humanizePercentage }}. Проверить connection pool."
```

---

### 4. Qdrant

**Файл:** `api/worker/integrations/qdrant_client.py`

**Метрики:**
```python
from prometheus_client import Counter, Histogram, Gauge

# Метрики операций
qdrant_operations_total = Counter(
    'qdrant_operations_total',
    'Total Qdrant operations',
    ['operation', 'status']  # upsert, search, delete, success, error
)

qdrant_operation_duration_seconds = Histogram(
    'qdrant_operation_duration_seconds',
    'Qdrant operation duration',
    ['operation'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)

# Метрики коллекций
qdrant_collection_size = Gauge(
    'qdrant_collection_size',
    'Number of vectors in collection',
    ['collection']
)

qdrant_collection_indexed = Gauge(
    'qdrant_collection_indexed',
    'Number of indexed vectors in collection',
    ['collection']
)
```

**Алерты:**
```yaml
- alert: QdrantUnavailable
  expr: up{job="worker"} == 0
  for: 1m
  labels:
    severity: critical
    component: qdrant
  annotations:
    summary: "Qdrant недоступен"
    description: "Worker не может подключиться к Qdrant. Проверить состояние Qdrant."

- alert: QdrantLatencyHigh
  expr: |
    histogram_quantile(0.95, rate(qdrant_operation_duration_seconds_bucket[5m])) > 2
  for: 5m
  labels:
    severity: warning
    component: qdrant
  annotations:
    summary: "Qdrant p95 latency превышает 2 секунды"
    description: "P95 latency = {{ $value }}s. Проверить производительность Qdrant."

- alert: QdrantErrorRateHigh
  expr: |
    (
      sum(rate(qdrant_operations_total{status="error"}[5m]))
      /
      sum(rate(qdrant_operations_total[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    component: qdrant
  annotations:
    summary: "Qdrant error rate превышает 5%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи и состояние Qdrant."
```

---

### 5. Enrichment Task - Алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: EnrichmentNoActivity
  expr: |
    sum(rate(enrichment_requests_total[10m])) == 0
    and
    sum(stream_pending_size{stream=~"posts\\.tagged"}) > 10
  for: 10m
  labels:
    severity: warning
    component: enrichment
  annotations:
    summary: "Enrichment не обрабатывает события"
    description: "Есть pending сообщения в posts.tagged ({{ $value }}), но enrichment не обрабатывает. Проверить логи worker и состояние crawl4ai."

- alert: EnrichmentErrorRateHigh
  expr: |
    (
      sum(rate(enrichment_requests_total{success="false"}[5m]))
      /
      sum(rate(enrichment_requests_total[5m]))
    ) > 0.10
  for: 5m
  labels:
    severity: warning
    component: enrichment
  annotations:
    summary: "Enrichment error rate превышает 10%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние crawl4ai."

- alert: EnrichmentLatencyHigh
  expr: |
    histogram_quantile(0.95, rate(enrichment_latency_seconds_bucket[5m])) > 30
  for: 10m
  labels:
    severity: warning
    component: enrichment
  annotations:
    summary: "Enrichment p95 latency превышает 30 секунд"
    description: "P95 latency = {{ $value }}s. Проверить производительность crawl4ai."
```

---

### 6. Tag Persistence Task - Дополнительные алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: TagPersistNoActivity
  expr: |
    sum(rate(tags_persisted_total[10m])) == 0
    and
    sum(tags_persist_pel_backlog_current) > 10
  for: 10m
  labels:
    severity: warning
    component: tag_persistence
  annotations:
    summary: "Tag persistence не обрабатывает события"
    description: "Есть pending сообщения ({{ $value }}), но tag persistence не обрабатывает. Проверить логи worker и состояние БД."

- alert: TagPersistErrorRateHigh
  expr: |
    (
      sum(rate(tags_persisted_total{status="error"}[5m]))
      /
      sum(rate(tags_persisted_total[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    component: tag_persistence
  annotations:
    summary: "Tag persistence error rate превышает 5%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние БД."

- alert: TagPersistLatencyHigh
  expr: |
    histogram_quantile(0.95, rate(tags_persist_latency_seconds_bucket[5m])) > 5
  for: 10m
  labels:
    severity: warning
    component: tag_persistence
  annotations:
    summary: "Tag persistence p95 latency превышает 5 секунд"
    description: "P95 latency = {{ $value }}s. Проверить производительность БД."
```

---

### 7. Retagging Task - Алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: RetaggingNoActivity
  expr: |
    sum(rate(retagging_processed_total[10m])) == 0
    and
    sum(stream_pending_size{stream=~"posts\\.vision\\.analyzed"}) > 10
  for: 10m
  labels:
    severity: warning
    component: retagging
  annotations:
    summary: "Retagging не обрабатывает события"
    description: "Есть pending сообщения в posts.vision.analyzed ({{ $value }}), но retagging не обрабатывает. Проверить логи worker и состояние AI провайдеров."

- alert: RetaggingErrorRateHigh
  expr: |
    (
      sum(rate(retagging_processed_total{outcome="err"}[5m]))
      /
      sum(rate(retagging_processed_total[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    component: retagging
  annotations:
    summary: "Retagging error rate превышает 5%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние AI провайдеров."

- alert: RetaggingLatencyHigh
  expr: |
    histogram_quantile(0.95, rate(retagging_duration_seconds_bucket[5m])) > 10
  for: 10m
  labels:
    severity: warning
    component: retagging
  annotations:
    summary: "Retagging p95 latency превышает 10 секунд"
    description: "P95 latency = {{ $value }}s. Проверить производительность AI провайдеров."
```

---

### 8. Digest Worker - Алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: DigestGenerationFailed
  expr: |
    (
      sum(rate(digest_jobs_processed_total{stage="generate", status="failed"}[10m]))
      /
      sum(rate(digest_jobs_processed_total{stage="generate"}[10m]))
    ) > 0.10
  for: 10m
  labels:
    severity: warning
    component: digest
  annotations:
    summary: "Digest generation error rate превышает 10%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние AI провайдеров."

- alert: DigestSendFailed
  expr: |
    (
      sum(rate(digest_jobs_processed_total{stage="send", status="failed"}[10m]))
      /
      sum(rate(digest_jobs_processed_total{stage="send"}[10m]))
    ) > 0.05
  for: 10m
  labels:
    severity: warning
    component: digest
  annotations:
    summary: "Digest send error rate превышает 5%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние Telegram API."

- alert: DigestQualityLow
  expr: |
    histogram_quantile(0.50, rate(group_digest_quality_scores_bucket[15m])) < 0.7
  for: 15m
  labels:
    severity: info
    component: digest
  annotations:
    summary: "Digest quality ниже порога (median < 0.7)"
    description: "Median quality = {{ $value }}. Проверить качество генерации дайджестов."
```

---

### 9. Trends - Алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: TrendDetectionFailed
  expr: |
    (
      sum(rate(trend_events_processed_total{status="error"}[10m]))
      /
      sum(rate(trend_events_processed_total[10m]))
    ) > 0.10
  for: 10m
  labels:
    severity: warning
    component: trends
  annotations:
    summary: "Trend detection error rate превышает 10%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker."

- alert: TrendClusteringFailed
  expr: |
    (
      sum(rate(trend_clustering_rejected_total[10m]))
      /
      sum(rate(trend_events_processed_total{status="processed"}[10m]))
    ) > 0.10
  for: 10m
  labels:
    severity: warning
    component: trends
  annotations:
    summary: "Trend clustering rejection rate превышает 10%"
    description: "Rejection rate = {{ $value | humanizePercentage }}. Проверить логи worker и качество кластеризации."

- alert: TrendRefinementFailed
  expr: |
    (
      sum(rate(trend_refinement_runs_total{status="error"}[10m]))
      /
      sum(rate(trend_refinement_runs_total[10m]))
    ) > 0.10
  for: 10m
  labels:
    severity: warning
    component: trends
  annotations:
    summary: "Trend refinement error rate превышает 10%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker."
```

---

### 10. Telethon Ingest - Алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: TelethonIngestCrash
  expr: |
    sum(rate(telethon_ingest_crash_signals_total[5m])) > 0
  for: 1m
  labels:
    severity: critical
    component: telethon_ingest
  annotations:
    summary: "Telethon Ingest получил crash signal"
    description: "Получен crash signal ({{ $labels.signal_name }}). Проверить логи telethon-ingest и состояние системы."

- alert: TelethonIngestPostsLostHigh
  expr: |
    (
      sum(rate(posts_lost_total[10m]))
      /
      (sum(rate(posts_lost_total[10m])) + sum(rate(db_users_upserted_total[10m])))
    ) > 0.05
  for: 10m
  labels:
    severity: warning
    component: telethon_ingest
  annotations:
    summary: "Telethon Ingest частота потерь постов превышает 5%"
    description: "Loss rate = {{ $value | humanizePercentage }}. Проверить логи telethon-ingest и причины потерь."

- alert: TelethonIngestChannelCoverageLow
  expr: |
    avg(channel_coverage_percent) < 80
  for: 30m
  labels:
    severity: warning
    component: telethon_ingest
  annotations:
    summary: "Telethon Ingest покрытие канала ниже 80%"
    description: "Coverage = {{ $value }}%. Проверить логи telethon-ingest и причины низкого покрытия."

- alert: TelethonIngestMediaProcessingFailed
  expr: |
    (
      sum(rate(media_processing_failed_total[10m]))
      /
      sum(rate(media_processing_total[10m]))
    ) > 0.10
  for: 10m
  labels:
    severity: warning
    component: telethon_ingest
  annotations:
    summary: "Telethon Ingest частота ошибок обработки медиа превышает 10%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи telethon-ingest и причины ошибок."
```

---

### 11. Cleanup Task - Алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: CleanupFailed
  expr: |
    (
      sum(rate(cleanup_processed_total{status="error"}[10m]))
      /
      sum(rate(cleanup_processed_total[10m]))
    ) > 0.10
  for: 10m
  labels:
    severity: warning
    component: cleanup
  annotations:
    summary: "Cleanup error rate превышает 10%"
    description: "Error rate = {{ $value | humanizePercentage }}. Проверить логи worker и состояние Qdrant/Neo4j."

- alert: CleanupLatencyHigh
  expr: |
    histogram_quantile(0.95, rate(cleanup_latency_seconds_bucket[10m])) > 60
  for: 10m
  labels:
    severity: warning
    component: cleanup
  annotations:
    summary: "Cleanup p95 latency превышает 60 секунд"
    description: "P95 latency = {{ $value }}s. Проверить производительность Qdrant/Neo4j."
```

---

### 12. Context Events Task - Алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: ContextEventsNoActivity
  expr: |
    sum(rate(digest_context_messages[10m])) == 0
    and
    sum(stream_pending_size{stream=~"digest\\.context\\.prepared"}) > 10
  for: 10m
  labels:
    severity: warning
    component: context_events
  annotations:
    summary: "Context events не обрабатывает события"
    description: "Есть pending сообщения в digest.context.prepared ({{ $value }}), но context events не обрабатывает. Проверить логи worker."
```

---

### 13. DLQ - Дополнительные алерты

**Файл:** `prometheus/alerts.yml`

**Алерты:**
```yaml
- alert: DLQBacklogHigh
  expr: |
    sum(dlq_db_events_total{status="pending"}) > 1000
  for: 10m
  labels:
    severity: warning
    component: dlq
  annotations:
    summary: "DLQ backlog превышает 1000 событий"
    description: "Backlog = {{ $value }} событий. Требуется ручная обработка или увеличение workers."

- alert: DLQAutoHealFailed
  expr: |
    avg(dlq_db_auto_heal_success_rate) < 0.50
  for: 15m
  labels:
    severity: warning
    component: dlq
  annotations:
    summary: "DLQ auto-heal success rate ниже 50%"
    description: "Success rate = {{ $value | humanizePercentage }}. Проверить причины неудачных попыток auto-heal."
```

---

### 14. Redis - Метрики и алерты

**Файл:** Создать новый `api/utils/redis_metrics.py`

**Метрики:**
```python
from prometheus_client import Counter, Histogram, Gauge
from functools import wraps
import time

# Метрики операций
redis_operations_total = Counter(
    'redis_operations_total',
    'Total Redis operations',
    ['operation', 'status']  # get, set, xadd, xread, success, error
)

redis_operation_duration_seconds = Histogram(
    'redis_operation_duration_seconds',
    'Redis operation duration',
    ['operation'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
)

# Метрики памяти
redis_memory_usage_bytes = Gauge(
    'redis_memory_usage_bytes',
    'Redis memory usage in bytes'
)

redis_memory_max_bytes = Gauge(
    'redis_memory_max_bytes',
    'Redis maximum memory in bytes'
)

# Декоратор для измерения операций
def measure_redis_operation(operation: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                redis_operations_total.labels(operation=operation, status='success').inc()
                return result
            except Exception as e:
                redis_operations_total.labels(operation=operation, status='error').inc()
                raise
            finally:
                duration = time.time() - start_time
                redis_operation_duration_seconds.labels(operation=operation).observe(duration)
        return wrapper
    return decorator
```

**Алерты:**
```yaml
- alert: RedisUnavailable
  expr: up{job="worker"} == 0 or up{job="api"} == 0
  for: 1m
  labels:
    severity: critical
    component: redis
  annotations:
    summary: "Redis недоступен"
    description: "Сервисы не могут подключиться к Redis. Проверить состояние Redis."

- alert: RedisMemoryHigh
  expr: |
    (
      redis_memory_usage_bytes
      /
      redis_memory_max_bytes
    ) > 0.80
  for: 5m
  labels:
    severity: warning
    component: redis
  annotations:
    summary: "Redis использование памяти превышает 80%"
    description: "Использование памяти = {{ $value | humanizePercentage }}. Проверить использование памяти и настройки eviction."
```

---

### 15. API Endpoints - Метрики и алерты

**Файл:** Создать middleware `api/middleware/metrics_middleware.py`

**Метрики:**
```python
from prometheus_client import Counter, Histogram
from fastapi import Request
import time

api_endpoint_requests_total = Counter(
    'api_endpoint_requests_total',
    'Total API endpoint requests',
    ['endpoint', 'method', 'status_code']
)

api_endpoint_latency_seconds = Histogram(
    'api_endpoint_latency_seconds',
    'API endpoint latency',
    ['endpoint', 'method', 'status_code'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

# Middleware для измерения
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    
    endpoint = request.url.path
    method = request.method
    status_code = response.status_code
    
    api_endpoint_requests_total.labels(
        endpoint=endpoint,
        method=method,
        status_code=status_code
    ).inc()
    
    api_endpoint_latency_seconds.labels(
        endpoint=endpoint,
        method=method,
        status_code=status_code
    ).observe(duration)
    
    return response
```

**Алерты:**
```yaml
- alert: APIEndpointLatencyHigh
  expr: |
    histogram_quantile(0.95, 
      rate(api_endpoint_latency_seconds_bucket{endpoint=~"/api/(channels|sessions|digest|trends|admin).*"}[5m])
    ) > 5
  for: 5m
  labels:
    severity: warning
    component: api
  annotations:
    summary: "API endpoint p95 latency превышает 5 секунд"
    description: "P95 latency для {{ $labels.endpoint }} = {{ $value }}s. Проверить производительность endpoint."

- alert: APIEndpointErrorRateHigh
  expr: |
    (
      sum(rate(api_endpoint_requests_total{status_code=~"5..", endpoint=~"/api/(channels|sessions|digest|trends|admin).*"}[5m]))
      /
      sum(rate(api_endpoint_requests_total{endpoint=~"/api/(channels|sessions|digest|trends|admin).*"}[5m]))
    ) > 0.05
  for: 5m
  labels:
    severity: warning
    component: api
  annotations:
    summary: "API endpoint error rate превышает 5%"
    description: "Error rate для {{ $labels.endpoint }} = {{ $value | humanizePercentage }}. Проверить логи API."
```

---

## Исправление дублирования

### 1. Объединение метрик stream_pending_size и posts_in_queue_total

**Проблема:**
- `stream_pending_size` - Gauge (stream)
- `posts_in_queue_total` - Gauge (queue, status)

**Решение:**
Использовать только `posts_in_queue_total` с едиными labels:
```python
posts_in_queue_total = Gauge(
    'posts_in_queue_total',
    'Current posts in queue',
    ['stream', 'status']  # stream: posts.parsed, posts.tagged, etc.; status: total, pending, new
)
```

Удалить `stream_pending_size` и обновить все использования.

---

### 2. Объединение алертов Storage Quota

**Проблема:**
- `StorageQuotaWarning` и `S3StorageQuotaWarning`
- `StorageQuotaCritical` и `S3StorageQuotaCritical`

**Решение:**
Использовать единые алерты с разными severity:
```yaml
- alert: StorageQuotaWarning
  expr: storage_bucket_usage_gb / 15.0 > 0.85
  for: 10m
  labels:
    severity: warning
    component: storage

- alert: StorageQuotaCritical
  expr: storage_bucket_usage_gb / 15.0 > 0.93
  for: 5m
  labels:
    severity: critical
    component: storage
```

Удалить `S3StorageQuotaWarning` и `S3StorageQuotaCritical`.

---

## Приоритизация реализации

### Фаза 1: Critical (1-2 недели)

1. Graph Writer Task - метрики и алерты
2. Post Persistence Task - метрики и алерты
3. PostgreSQL - метрики и алерты
4. Qdrant - метрики и алерты
5. Neo4j - алерты

### Фаза 2: Warning (2-3 недели)

1. Enrichment Task - алерты
2. Tag Persistence Task - дополнительные алерты
3. Retagging Task - алерты
4. Digest Worker - алерты
5. Trends - алерты
6. Telethon Ingest - алерты
7. Cleanup Task - алерты
8. Context Events Task - алерты
9. Trends Refinement Task - алерты
10. DLQ - дополнительные алерты
11. Redis - метрики и алерты

### Фаза 3: Info (1-2 недели)

1. API Endpoints - метрики и алерты
2. Дублирование метрик - объединение
3. Дублирование алертов - объединение

---

## Использование Context7

При реализации рекомендуется:

1. **Использовать Context7 MCP** для поиска best practices
2. **Проверять соответствие** метрик рекомендациям Context7
3. **Использовать единые naming conventions**
4. **Избегать дублирования** метрик и алертов
5. **Тестировать** метрики и алерты перед деплоем

---

**Следующие шаги:**
1. Начать реализацию с Critical приоритета
2. Тестировать метрики и алерты на dev окружении
3. Документировать изменения в CHANGELOG

