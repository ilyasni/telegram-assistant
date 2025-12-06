# Пробелы в покрытии метриками и алертами Prometheus

**Дата**: 2025-12-03  
**Статус**: Детальный список пробелов с приоритетами

---

## Критичные пробелы (Critical)

### 1. Graph Writer Task

**Компонент:** `api/worker/tasks/graph_writer_task.py`

**Пробелы:**
- ❌ Нет метрик для обработки событий
- ❌ Нет метрик для операций Neo4j
- ❌ Нет метрик для PEL backlog
- ❌ Нет алертов

**Приоритет:** Critical  
**Причина:** Критичный компонент для GraphRAG, отсутствие мониторинга может привести к потере данных

**Рекомендуемые метрики:**
```python
graph_writer_processed_total = Counter(
    'graph_writer_processed_total',
    'Total events processed by graph writer',
    ['status']  # success, error, skipped
)

graph_writer_latency_seconds = Histogram(
    'graph_writer_latency_seconds',
    'Graph writer processing latency',
    ['operation']  # create_node, create_relationship
)

graph_writer_neo4j_operations_total = Counter(
    'graph_writer_neo4j_operations_total',
    'Total Neo4j operations',
    ['operation', 'status']  # create_node, create_relationship, success, error
)

graph_writer_pel_size = Gauge(
    'graph_writer_pel_size',
    'Pending Entry List size for graph writer',
    ['consumer_group']
)
```

**Рекомендуемые алерты:**
- `GraphWriterNoActivity` - нет обработки при наличии pending > 10 сообщений в течение 10 минут
- `GraphWriterErrorRateHigh` - error rate > 5% в течение 5 минут
- `GraphWriterPELBacklogHigh` - PEL backlog > 100 сообщений в течение 5 минут

---

### 2. Post Persistence Task

**Компонент:** `api/worker/tasks/post_persistence_task.py`

**Пробелы:**
- ❌ Нет метрик для обработки событий
- ❌ Нет метрик для операций БД
- ❌ Нет метрик для PEL backlog
- ❌ Нет алертов

**Приоритет:** Critical  
**Причина:** Критичный компонент для сохранения постов, отсутствие мониторинга может привести к потере данных

**Рекомендуемые метрики:**
```python
post_persistence_processed_total = Counter(
    'post_persistence_processed_total',
    'Total posts persisted',
    ['status']  # success, error, skipped
)

post_persistence_latency_seconds = Histogram(
    'post_persistence_latency_seconds',
    'Post persistence latency',
    ['operation']  # upsert_post, upsert_channel
)

post_persistence_pel_size = Gauge(
    'post_persistence_pel_size',
    'Pending Entry List size for post persistence',
    ['consumer_group']
)

post_persistence_db_operations_total = Counter(
    'post_persistence_db_operations_total',
    'Total DB operations',
    ['operation', 'status']  # upsert_post, upsert_channel, success, error
)
```

**Рекомендуемые алерты:**
- `PostPersistenceNoActivity` - нет обработки при наличии pending > 10 сообщений в течение 10 минут
- `PostPersistenceErrorRateHigh` - error rate > 5% в течение 5 минут
- `PostPersistenceLatencyHigh` - p95 latency > 5s в течение 10 минут

---

### 3. PostgreSQL

**Компонент:** Все компоненты, использующие PostgreSQL

**Пробелы:**
- ❌ Нет метрик для операций БД
- ❌ Нет метрик для подключений
- ❌ Нет метрик для latency
- ❌ Нет алертов

**Приоритет:** Critical  
**Причина:** Критичная инфраструктура, отсутствие мониторинга может привести к проблемам с производительностью и доступностью

**Рекомендуемые метрики:**
```python
postgres_operations_total = Counter(
    'postgres_operations_total',
    'Total PostgreSQL operations',
    ['operation', 'status']  # select, insert, update, delete, success, error
)

postgres_operation_duration_seconds = Histogram(
    'postgres_operation_duration_seconds',
    'PostgreSQL operation duration',
    ['operation']
)

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
```

**Рекомендуемые алерты:**
- `PostgresUnavailable` - недоступность PostgreSQL в течение 1 минуты
- `PostgresLatencyHigh` - p95 latency > 1s в течение 5 минут
- `PostgresErrorRateHigh` - error rate > 5% в течение 5 минут
- `PostgresConnectionsHigh` - активные соединения > 80% от лимита в течение 5 минут

---

### 4. Qdrant

**Компонент:** `api/worker/integrations/qdrant_client.py`

**Пробелы:**
- ❌ Нет метрик для операций (кроме cleanup)
- ❌ Нет метрик для подключений
- ❌ Нет метрик для latency операций
- ❌ Нет алертов

**Приоритет:** Critical  
**Причина:** Критичная инфраструктура для векторного поиска, отсутствие мониторинга может привести к проблемам с производительностью

**Рекомендуемые метрики:**
```python
qdrant_operations_total = Counter(
    'qdrant_operations_total',
    'Total Qdrant operations',
    ['operation', 'status']  # upsert, search, delete, success, error
)

qdrant_operation_duration_seconds = Histogram(
    'qdrant_operation_duration_seconds',
    'Qdrant operation duration',
    ['operation']
)

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

**Рекомендуемые алерты:**
- `QdrantUnavailable` - недоступность Qdrant в течение 1 минуты
- `QdrantLatencyHigh` - p95 latency > 2s в течение 5 минут
- `QdrantErrorRateHigh` - error rate > 5% в течение 5 минут

---

### 5. Neo4j

**Компонент:** `api/worker/integrations/neo4j_client.py`

**Пробелы:**
- ⚠️ Есть метрики, но нет алертов
- ❌ Нет алертов для недоступности
- ❌ Нет алертов для высокой latency
- ❌ Нет алертов для высокой error rate

**Приоритет:** Critical  
**Причина:** Критичная инфраструктура для GraphRAG, отсутствие алертов может привести к задержкам в реагировании на проблемы

**Рекомендуемые алерты:**
- `Neo4jUnavailable` - недоступность Neo4j в течение 1 минуты
- `Neo4jLatencyHigh` - p95 latency > 5s в течение 5 минут
- `Neo4jErrorRateHigh` - error rate > 5% в течение 5 минут

---

## Важные пробелы (Warning)

### 6. Enrichment Task

**Компонент:** `api/worker/tasks/enrichment_task.py`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Важный компонент для обогащения контента, отсутствие алертов может привести к задержкам в обработке

**Рекомендуемые алерты:**
- `EnrichmentNoActivity` - нет обработки при наличии pending > 10 сообщений в течение 10 минут
- `EnrichmentErrorRateHigh` - error rate > 10% в течение 5 минут
- `EnrichmentLatencyHigh` - p95 latency > 30s в течение 10 минут

---

### 7. Tag Persistence Task

**Компонент:** `api/worker/tasks/tag_persistence_task.py`

**Пробелы:**
- ✅ Есть метрики
- ⚠️ Есть только один алерт (PEL backlog)
- ❌ Нет алертов для no activity
- ❌ Нет алертов для error rate
- ❌ Нет алертов для latency

**Приоритет:** Warning  
**Причина:** Важный компонент для сохранения тегов, отсутствие алертов может привести к задержкам в обработке

**Рекомендуемые алерты:**
- `TagPersistNoActivity` - нет обработки при наличии pending > 10 сообщений в течение 10 минут
- `TagPersistErrorRateHigh` - error rate > 5% в течение 5 минут
- `TagPersistLatencyHigh` - p95 latency > 5s в течение 10 минут

---

### 8. Retagging Task

**Компонент:** `api/worker/tasks/retagging_task.py`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Важный компонент для ретеггинга после vision анализа, отсутствие алертов может привести к задержкам в обработке

**Рекомендуемые алерты:**
- `RetaggingNoActivity` - нет обработки при наличии pending > 10 сообщений в течение 10 минут
- `RetaggingErrorRateHigh` - error rate > 5% в течение 5 минут
- `RetaggingLatencyHigh` - p95 latency > 10s в течение 10 минут

---

### 9. Digest Worker

**Компонент:** `api/worker/tasks/digest_worker.py`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Важный компонент для генерации дайджестов, отсутствие алертов может привести к проблемам с доставкой дайджестов

**Рекомендуемые алерты:**
- `DigestGenerationFailed` - частота ошибок генерации > 10% в течение 10 минут
- `DigestSendFailed` - частота ошибок отправки > 5% в течение 10 минут
- `DigestQualityLow` - качество дайджестов < порога в течение 15 минут

---

### 10. Trends

**Компонент:** `api/worker/trends_worker.py`, `api/worker/trends_refinement_service.py`, `api/worker/trends_editor_agent.py`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Важный компонент для детекции трендов, отсутствие алертов может привести к проблемам с качеством трендов

**Рекомендуемые алерты:**
- `TrendDetectionFailed` - частота ошибок детекции > 10% в течение 10 минут
- `TrendClusteringFailed` - частота ошибок кластеризации > 10% в течение 10 минут
- `TrendRefinementFailed` - частота ошибок refinement > 10% в течение 10 минут

---

### 11. Telethon Ingest

**Компонент:** `telethon-ingest/`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Критичный компонент для парсинга каналов, отсутствие алертов может привести к потере постов

**Рекомендуемые алерты:**
- `TelethonIngestCrash` - получен crash signal (SIGSEGV, SIGABRT, SIGFPE)
- `TelethonIngestPostsLostHigh` - частота потерь постов > 5% в течение 10 минут
- `TelethonIngestChannelCoverageLow` - покрытие канала < 80% в течение 30 минут
- `TelethonIngestMediaProcessingFailed` - частота ошибок обработки медиа > 10% в течение 10 минут

---

### 12. Cleanup Task

**Компонент:** `api/worker/tasks/cleanup_task.py`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Важный компонент для очистки данных, отсутствие алертов может привести к проблемам с производительностью

**Рекомендуемые алерты:**
- `CleanupFailed` - частота ошибок > 10% в течение 10 минут
- `CleanupLatencyHigh` - p95 latency > 60s в течение 10 минут

---

### 13. Context Events Task

**Компонент:** `api/worker/tasks/context_events_task.py`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Важный компонент для подготовки контекста дайджестов, отсутствие алертов может привести к проблемам с качеством дайджестов

**Рекомендуемые алерты:**
- `ContextEventsNoActivity` - нет обработки при наличии pending > 10 сообщений в течение 10 минут

---

### 14. Trends Refinement Task

**Компонент:** `api/worker/tasks/trends_refinement_task.py`

**Пробелы:**
- ✅ Есть метрики
- ❌ Нет алертов

**Приоритет:** Warning  
**Причина:** Важный компонент для улучшения качества трендов, отсутствие алертов может привести к проблемам с качеством трендов

**Рекомендуемые алерты:**
- `TrendRefinementFailed` - частота ошибок > 10% в течение 10 минут
- `TrendRefinementLatencyHigh` - длительность > 5 минут в течение 10 минут

---

### 15. DLQ

**Компонент:** `api/worker/services/dlq_service.py`

**Пробелы:**
- ✅ Есть метрики
- ⚠️ Есть только один алерт (Vision DLQ)
- ❌ Нет алертов для общего DLQ backlog
- ❌ Нет алертов для auto-heal success rate

**Приоритет:** Warning  
**Причина:** Важный компонент для обработки failed событий, отсутствие алертов может привести к накоплению failed событий

**Рекомендуемые алерты:**
- `DLQBacklogHigh` - общий backlog > 1000 событий в течение 10 минут
- `DLQAutoHealFailed` - success rate < 50% в течение 15 минут

---

### 16. Redis

**Компонент:** Все компоненты, использующие Redis

**Пробелы:**
- ✅ Есть метрики для streams
- ❌ Нет метрик для операций Redis
- ❌ Нет метрик для использования памяти
- ⚠️ Есть алерты для streams, но нет для общей доступности

**Приоритет:** Warning  
**Причина:** Критичная инфраструктура, отсутствие метрик может привести к проблемам с производительностью

**Рекомендуемые метрики:**
```python
redis_operations_total = Counter(
    'redis_operations_total',
    'Total Redis operations',
    ['operation', 'status']  # get, set, xadd, xread, success, error
)

redis_operation_duration_seconds = Histogram(
    'redis_operation_duration_seconds',
    'Redis operation duration',
    ['operation']
)

redis_memory_usage_bytes = Gauge(
    'redis_memory_usage_bytes',
    'Redis memory usage in bytes'
)

redis_memory_max_bytes = Gauge(
    'redis_memory_max_bytes',
    'Redis maximum memory in bytes'
)
```

**Рекомендуемые алерты:**
- `RedisUnavailable` - недоступность Redis в течение 1 минуты
- `RedisMemoryHigh` - использование памяти > 80% в течение 5 минут

---

## Информационные пробелы (Info)

### 17. API Endpoints

**Компонент:** `api/routers/*.py`

**Пробелы:**
- ✅ Есть метрики для RAG endpoint
- ❌ Нет метрик для других endpoints (channels, sessions, digest, trends, admin и др.)

**Приоритет:** Info  
**Причина:** Полезно для мониторинга производительности API, но не критично

**Рекомендуемые метрики:**
```python
api_endpoint_latency_seconds = Histogram(
    'api_endpoint_latency_seconds',
    'API endpoint latency',
    ['endpoint', 'method', 'status_code']
)

api_endpoint_requests_total = Counter(
    'api_endpoint_requests_total',
    'Total API endpoint requests',
    ['endpoint', 'method', 'status_code']
)
```

**Рекомендуемые алерты:**
- `APIEndpointLatencyHigh` - p95 latency > порога для критичных endpoints
- `APIEndpointErrorRateHigh` - error rate > 5% для критичных endpoints

---

### 18. Дублирование метрик

**Пробелы:**
- ⚠️ `stream_pending_size` и `posts_in_queue_total` - оба измеряют pending сообщения
- ⚠️ `vision_worker_processed_total` и `vision_events_total` - оба измеряют обработку vision событий

**Приоритет:** Info  
**Причина:** Дублирование может привести к путанице, но не критично

**Рекомендации:**
- Объединить `stream_pending_size` и `posts_in_queue_total` в одну метрику с едиными labels
- Использовать только `vision_events_total` (так как `vision_worker_processed_total` не экспортируется)

---

### 19. Дублирование алертов

**Пробелы:**
- ⚠️ `StorageQuotaWarning` и `S3StorageQuotaWarning` - оба проверяют storage quota
- ⚠️ `StorageQuotaCritical` и `S3StorageQuotaCritical` - аналогично

**Приоритет:** Info  
**Причина:** Дублирование может привести к путанице, но не критично

**Рекомендации:**
- Объединить алерты storage quota в единые с разными severity уровнями

---

## Итоговая статистика пробелов

### По приоритетам

- **Critical:** 5 пробелов
- **Warning:** 11 пробелов
- **Info:** 3 пробела

**Всего:** 19 пробелов

### По типам

- **Метрики:** 8 пробелов
- **Алерты:** 18 пробелов
- **Дублирование:** 2 пробела

---

## План исправления

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

**Следующие шаги:**
1. Создать детальный отчет с рекомендациями (`PROMETHEUS_COVERAGE_RECOMMENDATIONS.md`)
2. Приоритизировать исправления пробелов
3. Начать реализацию с Critical приоритета

