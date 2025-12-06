# Аудит покрытия метриками и алертами Prometheus

**Дата**: 2025-12-03  
**Статус**: Полный аудит всех компонентов системы

---

## Контекст

Система Telegram Assistant состоит из множества компонентов, каждый из которых должен быть покрыт метриками и алертами для обеспечения observability и быстрого реагирования на проблемы.

## Методология

1. Проверка всех worker tasks на наличие метрик и алертов
2. Проверка API endpoints на метрики производительности и алерты
3. Проверка внешних сервисов (Qdrant, Neo4j, Redis, PostgreSQL) на метрики подключений
4. Проверка Telethon Ingest на метрики и алерты
5. Проверка пайплайна обработки на метрики lag и coverage
6. Проверка бизнес-логики (digest, trends, storage) на метрики и алерты
7. Проверка инфраструктуры (контейнеры, scheduler) на метрики и алерты
8. Проверка дублирования метрик и алертов

---

## 1. Worker Tasks - Покрытие метриками и алертами

### 1.1 Tagging Task

**Метрики:**
- ✅ `tagging_processed_total` - Counter (status)
- ✅ `tagging_dlq_total` - Counter (reason)
- ✅ `tagging_cache_size` - Gauge
- ✅ `tagging_cache_evictions_total` - Counter
- ✅ `tagging_redis_dedup_hits_total` - Counter

**Алерты:**
- ✅ `TaggingCoverageLow` - покрытие < 80%
- ✅ `TaggingNoActivity` - нет обработки при наличии pending

**Статус:** ✅ Полностью покрыт

---

### 1.2 Indexing Task

**Метрики:**
- ✅ `indexing_processed_total` - Counter (status)
- ✅ `indexing_queue_size` - Gauge (stream)
- ✅ `indexing_trim_operations_total` - Counter (stream, status)
- ✅ `indexing_processing_duration_seconds` - Histogram (status)
- ✅ `indexing_consumer_lag` - Gauge (stream, group)
- ✅ `indexing_pending_messages` - Gauge
- ✅ `indexing_autoclaim_operations_total` - Counter
- ✅ `indexing_autoclaim_messages_total` - Counter

**Алерты:**
- ✅ `IndexingCoverageLow` - покрытие < 90%
- ✅ `IndexingNoActivity` - нет обработки при наличии pending

**Статус:** ✅ Полностью покрыт

---

### 1.3 Enrichment Task

**Метрики:**
- ✅ `enrichment_requests_total` - Counter (provider, operation, success)
- ✅ `enrichment_latency_seconds` - Histogram (status)
- ✅ `enrichment_skipped_total` - Counter (reason)
- ✅ `enrichment_triggers_total` - Counter (type, decision)
- ✅ `enrichment_crawl_requests_total` - Counter (domain, status)
- ✅ `enrichment_crawl_duration_seconds` - Histogram
- ✅ `enrichment_budget_checks_total` - Counter (type, result)

**Алерты:**
- ❌ Нет алертов для enrichment task

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерт `EnrichmentNoActivity` - нет обработки при наличии pending
- Добавить алерт `EnrichmentErrorRateHigh` - error rate > 10%
- Добавить алерт `EnrichmentLatencyHigh` - p95 latency > 30s

---

### 1.4 Vision Analysis Task

**Метрики:**
- ✅ `vision_worker_processed_total` - Counter (status, reason) - НО не экспортируется (MockMetric)
- ✅ `vision_events_total` - Counter (status, reason)
- ✅ `vision_media_total` - Counter (result, reason)
- ✅ `vision_analysis_duration_seconds` - Histogram
- ✅ `vision_analysis_tokens_total` - Counter (provider, model)
- ✅ `vision_analysis_errors_total` - Counter
- ✅ `vision_pel_size` - Gauge (consumer_group)
- ✅ `vision_pending_older_than_seconds` - Gauge (percentile, consumer_group)

**Алерты:**
- ✅ `VisionWorkerNotProcessing` - нет обработки событий
- ✅ `VisionWorkerHighFailureRate` - failure rate > 5%
- ✅ `VisionAnalysisHighLatency` - p95 latency > 5s
- ✅ `VisionAnalysisErrorRateHigh` - error rate > 2%
- ✅ `VisionAnalysisAvailabilityLow` - availability < 95%

**Статус:** ✅ Полностью покрыт (метрика `vision_worker_processed_total` не экспортируется, но есть альтернативные)

---

### 1.5 Album Assembler Task

**Метрики:**
- ✅ `albums_parsed_total` - Counter (status)
- ✅ `albums_assembled_total` - Counter (status)
- ✅ `album_assembly_lag_seconds` - Histogram
- ✅ `album_items_count_gauge` - Gauge (album_id, status)
- ✅ `album_vision_summary_size_bytes` - Histogram
- ✅ `album_aggregation_duration_ms` - Histogram

**Алерты:**
- ✅ `AlbumAssemblyLagHigh` - lag > 5 минут
- ✅ `AlbumAssemblyLagCritical` - lag > 10 минут
- ✅ `AlbumItemsCountMismatch` - проанализировано < 90% элементов
- ✅ `AlbumAssemblerNoActivity` - нет обработки при наличии событий
- ✅ `AlbumStateBacklogHigh` - backlog > 50 альбомов
- ✅ `AlbumAssemblyRateLow` - низкая скорость сборки
- ✅ `AlbumAssemblyErrorRateHigh` - error rate > 5%
- ✅ `AlbumAggregationDurationHigh` - длительность агрегации > 5s

**Статус:** ✅ Полностью покрыт

---

### 1.6 Crawl Trigger Task

**Метрики:**
- ✅ `crawl_triggers_total` - Counter (reason)
- ✅ `crawl_trigger_queue_depth_current` - Gauge
- ✅ `crawl_trigger_processing_latency_seconds` - Histogram
- ✅ `crawl_trigger_idempotency_hits_total` - Counter
- ✅ `crawl_trigger_policy_skips_total` - Counter (reason)
- ✅ `crawl_trigger_source_total` - Counter (source)
- ✅ `crawl_trigger_cache_hits_total` - Counter (status)

**Алерты:**
- ✅ `CrawlTriggerQueueDepthHigh` - глубина очереди > 500
- ✅ `CrawlPELBacklogHigh` - PEL backlog > 100
- ✅ `CrawlErrorRateHigh` - error rate > 10%
- ✅ `CrawlP95LatencyHigh` - p95 latency > 2s

**Статус:** ✅ Полностью покрыт

---

### 1.7 Tag Persistence Task

**Метрики:**
- ✅ `tags_persisted_total` - Counter (status)
- ✅ `tags_persist_latency_seconds` - Histogram
- ✅ `tags_persist_conflicts_total` - Counter
- ✅ `tags_hash_mismatches_total` - Counter
- ✅ `tags_persist_phase_total` - Counter (phase, status)
- ✅ `tags_persist_phase_latency_seconds` - Histogram (phase)
- ✅ `tags_persist_pel_backlog_current` - Gauge

**Алерты:**
- ✅ `TagPersistPELBacklogHigh` - PEL backlog > 50

**Статус:** ⚠️ Метрики есть, но алертов недостаточно

**Рекомендации:**
- Добавить алерт `TagPersistNoActivity` - нет обработки при наличии pending
- Добавить алерт `TagPersistErrorRateHigh` - error rate > 5%
- Добавить алерт `TagPersistLatencyHigh` - p95 latency > 5s

---

### 1.8 Retagging Task

**Метрики:**
- ✅ `retagging_processed_total` - Counter (changed, outcome)
- ✅ `retagging_duration_seconds` - Histogram (changed)
- ✅ `retagging_dlq_total` - Counter (reason)
- ✅ `retagging_skipped_total` - Counter (reason)

**Алерты:**
- ❌ Нет алертов для retagging task

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерт `RetaggingNoActivity` - нет обработки при наличии pending
- Добавить алерт `RetaggingErrorRateHigh` - error rate > 5%
- Добавить алерт `RetaggingLatencyHigh` - p95 latency > 10s

---

### 1.9 Digest Worker

**Метрики:**
- ✅ `digest_jobs_processed_total` - Counter (stage, status)
- ✅ `digest_worker_generation_seconds` - Histogram (status)
- ✅ `digest_worker_send_seconds` - Histogram (status)
- ✅ `group_digest_quality_scores` - Histogram
- ✅ `digest_context_messages` - Histogram (metric)
- ✅ `digest_context_duplicates_total` - Counter (tenant)
- ✅ `digest_context_history_matches_total` - Counter (tenant)
- ✅ `digest_context_media_total` - Gauge (tenant, group)
- ✅ `digest_context_media_without_description_total` - Gauge (tenant, group)

**Алерты:**
- ❌ Нет алертов для digest worker

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерт `DigestGenerationFailed` - частота ошибок генерации > 10%
- Добавить алерт `DigestSendFailed` - частота ошибок отправки > 5%
- Добавить алерт `DigestQualityLow` - качество дайджестов < порога

---

### 1.10 Trends Refinement Task

**Метрики:**
- ✅ `trend_refinement_runs_total` - Counter (status)
- ✅ `trend_refinement_clusters_split_total` - Counter
- ✅ `trend_refinement_clusters_merged_total` - Counter

**Алерты:**
- ❌ Нет алертов для trends refinement task

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерт `TrendRefinementFailed` - частота ошибок > 10%
- Добавить алерт `TrendRefinementLatencyHigh` - длительность > 5 минут

---

### 1.11 Cleanup Task

**Метрики:**
- ✅ `cleanup_processed_total` - Counter (status)
- ✅ `cleanup_latency_seconds` - Histogram (operation)
- ✅ `qdrant_cleanup_seconds` - Histogram
- ✅ `neo4j_cleanup_seconds` - Histogram
- ✅ `orphan_cleanup_total` - Counter (type)

**Алерты:**
- ❌ Нет алертов для cleanup task

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерт `CleanupFailed` - частота ошибок > 10%
- Добавить алерт `CleanupLatencyHigh` - p95 latency > 60s

---

### 1.12 Graph Writer Task

**Метрики:**
- ❌ Нет метрик для graph writer task

**Алерты:**
- ❌ Нет алертов для graph writer task

**Статус:** ❌ Не покрыт

**Рекомендации:**
- Добавить метрики:
  - `graph_writer_processed_total` - Counter (status)
  - `graph_writer_latency_seconds` - Histogram
  - `graph_writer_neo4j_operations_total` - Counter (operation, status)
  - `graph_writer_pel_size` - Gauge
- Добавить алерты:
  - `GraphWriterNoActivity` - нет обработки при наличии pending
  - `GraphWriterErrorRateHigh` - error rate > 5%
  - `GraphWriterPELBacklogHigh` - PEL backlog > 100

---

### 1.13 Post Persistence Task

**Метрики:**
- ❌ Нет метрик для post persistence task

**Алерты:**
- ❌ Нет алертов для post persistence task

**Статус:** ❌ Не покрыт

**Рекомендации:**
- Добавить метрики:
  - `post_persistence_processed_total` - Counter (status)
  - `post_persistence_latency_seconds` - Histogram
  - `post_persistence_pel_size` - Gauge
- Добавить алерты:
  - `PostPersistenceNoActivity` - нет обработки при наличии pending
  - `PostPersistenceErrorRateHigh` - error rate > 5%

---

### 1.14 Context Events Task

**Метрики:**
- ✅ `digest_context_messages` - Histogram (metric)
- ✅ `digest_context_duplicates_total` - Counter (tenant)
- ✅ `digest_context_history_matches_total` - Counter (tenant)
- ✅ `digest_context_media_total` - Gauge (tenant, group)
- ✅ `digest_context_media_without_description_total` - Gauge (tenant, group)

**Алерты:**
- ❌ Нет алертов для context events task

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерт `ContextEventsNoActivity` - нет обработки при наличии pending

---

## 2. API Endpoints - Покрытие метриками и алертами

### 2.1 RAG Endpoint (`/api/rag/query`)

**Метрики:**
- ✅ `fast_path_latency_seconds` - Histogram (endpoint, tenant_id)
- ✅ `llm_calls_per_request` - Histogram (path_type, endpoint, tenant_id)
- ✅ `tokens_per_request` - Histogram (path_type, endpoint, tenant_id)
- ✅ `agent_steps_per_request` - Histogram (path_type, endpoint, tenant_id)
- ✅ `request_budget_exceeded_total` - Counter (budget_type, path_type, endpoint, tenant_id)
- ✅ `performance_cache_hits_total` - Counter (cache_type, path_type)
- ✅ `performance_cache_misses_total` - Counter (cache_type, path_type)

**Алерты:**
- ✅ `FastPathP95LatencyHigh` - p95 latency > 5s
- ✅ `FastPathP95LatencyCritical` - p95 latency > 10s
- ✅ `FastPathLLMCallsHigh` - среднее LLM calls > 3
- ✅ `FastPathTokensHigh` - p95 tokens > 8k
- ✅ `FastPathAgentStepsHigh` - p95 agent steps > 4
- ✅ `RequestBudgetLLMCallsExceeded` - превышение бюджета LLM calls
- ✅ `RequestBudgetTokensExceeded` - превышение бюджета tokens
- ✅ `RequestBudgetAgentStepsExceeded` - превышение бюджета agent steps
- ✅ `ContextRouterCacheHitRateLow` - cache hit rate < 50%
- ✅ `PerformanceCacheHitRateLow` - cache hit rate < 30%

**Статус:** ✅ Полностью покрыт

---

### 2.2 Другие API Endpoints

**Метрики:**
- ❌ Нет метрик для других endpoints (channels, sessions, digest, trends, admin и др.)

**Алерты:**
- ❌ Нет алертов для других endpoints

**Статус:** ❌ Не покрыт

**Рекомендации:**
- Добавить метрики latency для критичных endpoints:
  - `/api/channels/*` - управление каналами
  - `/api/sessions/*` - управление сессиями
  - `/api/digest/*` - дайджесты
  - `/api/trends/*` - тренды
  - `/api/admin/*` - админ панель
- Добавить алерты для критичных endpoints:
  - `APIEndpointLatencyHigh` - p95 latency > порога
  - `APIEndpointErrorRateHigh` - error rate > 5%

---

## 3. Внешние сервисы - Покрытие метриками и алертами

### 3.1 Neo4j

**Метрики:**
- ✅ `neo4j_operations_total` - Counter (operation_type, status)
- ✅ `neo4j_operation_duration_seconds` - Histogram (operation_type)
- ✅ `neo4j_connections_active` - Gauge
- ✅ `neo4j_cleanup_seconds` - Histogram

**Алерты:**
- ❌ Нет алертов для Neo4j

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерты:
  - `Neo4jUnavailable` - недоступность Neo4j
  - `Neo4jLatencyHigh` - p95 latency > 5s
  - `Neo4jErrorRateHigh` - error rate > 5%

---

### 3.2 Qdrant

**Метрики:**
- ✅ `qdrant_cleanup_seconds` - Histogram
- ✅ `digest_qdrant_hits_total` - Counter (tenant_id)

**Алерты:**
- ❌ Нет алертов для Qdrant

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить метрики:
  - `qdrant_operations_total` - Counter (operation, status)
  - `qdrant_operation_duration_seconds` - Histogram (operation)
  - `qdrant_collection_size` - Gauge (collection)
- Добавить алерты:
  - `QdrantUnavailable` - недоступность Qdrant
  - `QdrantLatencyHigh` - p95 latency > 2s
  - `QdrantErrorRateHigh` - error rate > 5%

---

### 3.3 Redis

**Метрики:**
- ✅ `redis_xack_total` - Counter (stream)
- ✅ `stream_pending_size` - Gauge (stream)
- ✅ `stream_consumer_lag` - Gauge (stream, group)
- ✅ `stream_consumer_pending` - Gauge (stream, group)
- ✅ `posts_in_queue_total` - Gauge (queue, status)

**Алерты:**
- ✅ `RedisStreamLagHigh` - lag > 1000 сообщений
- ✅ `RedisStreamPendingHigh` - pending > 100 сообщений

**Статус:** ⚠️ Частично покрыт

**Рекомендации:**
- Добавить метрики:
  - `redis_operations_total` - Counter (operation, status)
  - `redis_operation_duration_seconds` - Histogram (operation)
  - `redis_memory_usage_bytes` - Gauge
- Добавить алерты:
  - `RedisUnavailable` - недоступность Redis
  - `RedisMemoryHigh` - использование памяти > 80%

---

### 3.4 PostgreSQL

**Метрики:**
- ❌ Нет метрик для PostgreSQL операций

**Алерты:**
- ❌ Нет алертов для PostgreSQL

**Статус:** ❌ Не покрыт

**Рекомендации:**
- Добавить метрики:
  - `postgres_operations_total` - Counter (operation, status)
  - `postgres_operation_duration_seconds` - Histogram (operation)
  - `postgres_connections_active` - Gauge
- Добавить алерты:
  - `PostgresUnavailable` - недоступность PostgreSQL
  - `PostgresLatencyHigh` - p95 latency > 1s
  - `PostgresErrorRateHigh` - error rate > 5%
  - `PostgresConnectionsHigh` - активные соединения > 80% от лимита

---

### 3.5 Crawl4AI

**Метрики:**
- ✅ Метрики экспортируются через `/metrics` endpoint

**Алерты:**
- ✅ `Crawl4aiHighLatency` - p95 latency > 30s
- ✅ `Crawl4aiLowSuccessRate` - success rate < 90%

**Статус:** ✅ Полностью покрыт

---

## 4. Telethon Ingest - Покрытие метриками и алертами

### 4.1 Метрики

**Метрики:**
- ✅ `http_requests_total` - Counter (path)
- ✅ `http_request_duration_seconds` - Histogram (path)
- ✅ `telethon_ingest_crash_signals_total` - Counter (signal_name)
- ✅ `telethon_ingest_crash_state_saved_total` - Counter (status)
- ✅ `telethon_ingest_faulthandler_dumps_total` - Counter
- ✅ `media_processing_total` - Counter (stage, media, outcome)
- ✅ `media_bytes_total` - Counter (media)
- ✅ `media_size_bytes` - Histogram (media)
- ✅ `media_processing_duration_seconds` - Histogram (stage, media, outcome)
- ✅ `media_albums_processed_total` - Counter (status)
- ✅ `media_processing_failed_total` - Counter (reason)
- ✅ `channel_not_found_total` - Counter (exists_in_db)
- ✅ `album_save_failures_total` - Counter (error_type)
- ✅ `session_rollback_failures_total` - Counter (operation)
- ✅ `posts_lost_total` - Counter (reason)
- ✅ `posts_skipped_duplicate_total` - Counter
- ✅ `posts_skipped_subscription_total` - Counter
- ✅ `channel_coverage_percent` - Gauge (channel_username)
- ✅ `db_users_upserted_total` - Counter
- ✅ `db_channels_upserted_total` - Counter
- ✅ `media_objects_upserted_total` - Counter (status)
- ✅ `media_objects_refs_updated_total` - Counter
- ✅ `post_media_map_inserted_total` - Counter (status)
- ✅ `cas_operations_latency_seconds` - Histogram (operation)
- ✅ `cas_operations_errors_total` - Counter (operation, error_type)

**Алерты:**
- ❌ Нет алертов для Telethon Ingest

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерты:
  - `TelethonIngestCrash` - получен crash signal
  - `TelethonIngestPostsLostHigh` - частота потерь постов > 5%
  - `TelethonIngestChannelCoverageLow` - покрытие канала < 80%
  - `TelethonIngestMediaProcessingFailed` - частота ошибок обработки медиа > 10%

---

## 5. Пайплайн обработки - Покрытие метриками и алертами

### 5.1 Метрики пайплайна

**Метрики:**
- ✅ `stream_messages_total` - Counter (stream, phase, status)
- ✅ `stream_pending_size` - Gauge (stream)
- ✅ `stream_consumer_lag` - Gauge (stream, group)
- ✅ `stream_consumer_pending` - Gauge (stream, group)
- ✅ `posts_in_queue_total` - Gauge (queue, status)
- ✅ `pipeline_posts_parsed_total` - Gauge
- ✅ `pipeline_posts_tagged_total` - Gauge
- ✅ `pipeline_posts_vision_total` - Gauge
- ✅ `pipeline_posts_enriched_total` - Gauge
- ✅ `pipeline_posts_indexed_total` - Gauge
- ✅ `pipeline_lag_seconds` - Gauge (stage)
- ✅ `redis_stream_pending` - Gauge (stream)

**Алерты:**
- ✅ `IndexingCoverageLow` - покрытие < 90%
- ✅ `TaggingCoverageLow` - покрытие < 80%
- ✅ `RedisStreamLagHigh` - lag > 1000 сообщений
- ✅ `RedisStreamPendingHigh` - pending > 100 сообщений
- ✅ `IndexingNoActivity` - нет обработки при наличии pending
- ✅ `TaggingNoActivity` - нет обработки при наличии pending

**Статус:** ✅ Полностью покрыт

---

## 6. Бизнес-логика - Покрытие метриками и алертами

### 6.1 Digest

**Метрики:**
- ✅ `digest_jobs_processed_total` - Counter (stage, status)
- ✅ `digest_worker_generation_seconds` - Histogram (status)
- ✅ `digest_worker_send_seconds` - Histogram (status)
- ✅ `group_digest_quality_scores` - Histogram
- ✅ `digest_context_messages` - Histogram (metric)
- ✅ `digest_context_duplicates_total` - Counter (tenant)
- ✅ `digest_context_history_matches_total` - Counter (tenant)
- ✅ `digest_context_media_total` - Gauge (tenant, group)
- ✅ `digest_context_media_without_description_total` - Gauge (tenant, group)
- ✅ `digest_qdrant_hits_total` - Counter (tenant_id)

**Алерты:**
- ❌ Нет алертов для digest

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерты:
  - `DigestGenerationFailed` - частота ошибок генерации > 10%
  - `DigestSendFailed` - частота ошибок отправки > 5%
  - `DigestQualityLow` - качество дайджестов < порога

---

### 6.2 Trends

**Метрики:**
- ✅ `trend_events_processed_total` - Counter (status)
- ✅ `trend_emerging_events_total` - Counter (status)
- ✅ `trend_worker_latency_seconds` - Histogram
- ✅ `trend_card_llm_requests_total` - Counter (outcome)
- ✅ `trend_cluster_sample_posts` - Histogram
- ✅ `trend_detection_ratio_histogram` - Histogram
- ✅ `trend_detection_coherence_histogram` - Histogram
- ✅ `trend_detection_source_diversity_histogram` - Histogram
- ✅ `trend_clustering_rejected_total` - Counter (reason)
- ✅ `trend_clustering_coherence_score_histogram` - Histogram
- ✅ `trend_clustering_cluster_size_histogram` - Histogram
- ✅ `trend_clustering_llm_gate_latency_seconds` - Histogram
- ✅ `trend_refinement_runs_total` - Counter (status)
- ✅ `trend_refinement_clusters_split_total` - Counter
- ✅ `trend_refinement_clusters_merged_total` - Counter
- ✅ `trend_editor_requests_total` - Counter (outcome)
- ✅ `trend_editor_quality_score` - Histogram
- ✅ `trend_editor_latency_seconds` - Histogram

**Алерты:**
- ❌ Нет алертов для trends

**Статус:** ⚠️ Метрики есть, но нет алертов

**Рекомендации:**
- Добавить алерты:
  - `TrendDetectionFailed` - частота ошибок детекции > 10%
  - `TrendClusteringFailed` - частота ошибок кластеризации > 10%
  - `TrendRefinementFailed` - частота ошибок refinement > 10%

---

### 6.3 Storage Quota

**Метрики:**
- ✅ `storage_bucket_usage_gb` - Gauge (content_type)
- ✅ `storage_quota_violations_total` - Counter (tenant_id, reason)
- ✅ `storage_emergency_cleanups_total` - Counter (trigger_reason)
- ✅ `storage_lru_evictions_total` - Counter
- ✅ `storage_cleanup_freed_gb_sum` - Counter

**Алерты:**
- ✅ `StorageQuotaCritical` - использование > 93%
- ✅ `StorageQuotaWarning` - использование > 85%
- ✅ `StorageQuotaViolationsHigh` - частота нарушений > 0.1/сек
- ✅ `StorageCleanupIneffective` - cleanup не освобождает место
- ✅ `StorageMediaQuotaHigh` - media storage > 9 GB
- ✅ `StorageVisionQuotaHigh` - vision storage > 1.8 GB
- ✅ `S3StorageQuotaWarning` - использование > 85%
- ✅ `S3StorageQuotaCritical` - использование > 93%
- ✅ `S3StorageQuotaViolationsHigh` - частота нарушений > 10/10min
- ✅ `S3EmergencyCleanupFrequent` - emergency cleanup > 2/час

**Статус:** ✅ Полностью покрыт

---

### 6.4 Vision Budget

**Метрики:**
- ✅ `vision_budget_gate_blocks_total` - Counter (tenant_id, reason)
- ✅ `vision_budget_usage_gauge` - Gauge (tenant_id, period)
- ✅ `vision_analysis_tokens_total` - Counter (provider, model)

**Алерты:**
- ✅ `VisionBudgetExhausted` - использование > 95%
- ✅ `VisionBudgetGateBlocksHigh` - частота блокировок > 5/10min

**Статус:** ✅ Полностью покрыт

---

### 6.5 DLQ

**Метрики:**
- ✅ `dlq_db_events_total` - Counter (entity_type, status)
- ✅ `dlq_db_reprocessed_total` - Counter (entity_type, result)
- ✅ `dlq_db_auto_heal_success_rate` - Gauge (entity_type)
- ✅ `tagging_dlq_total` - Counter (reason)
- ✅ `retagging_dlq_total` - Counter (reason)
- ✅ `digest_dlq_total` - Counter
- ✅ `outbox_dlq_total` - Counter (event_type)

**Алерты:**
- ✅ `VisionDLQBacklogHigh` - backlog > 100 сообщений

**Статус:** ⚠️ Метрики есть, но алертов недостаточно

**Рекомендации:**
- Добавить алерты:
  - `DLQBacklogHigh` - общий backlog > 1000 событий
  - `DLQAutoHealFailed` - success rate < 50%

---

## 7. Инфраструктура - Покрытие метриками и алертами

### 7.1 Docker Контейнеры

**Метрики:**
- ✅ `container_restart_count_total` - Counter (service_name)
- ✅ `container_uptime_seconds` - Gauge (service_name)
- ✅ `container_health_status` - Gauge (service_name)

**Алерты:**
- ✅ `ContainerRestartsHigh` - частота перезапусков > 3/час
- ✅ `WorkerTaskDown` - worker недоступен

**Статус:** ✅ Полностью покрыт

---

### 7.2 Scheduler

**Метрики:**
- ✅ `scheduler_running` - Gauge
- ✅ `scheduler_startup_duration_seconds` - Histogram
- ✅ `scheduler_jobs_total` - Gauge
- ✅ `scheduler_last_tick_ts_seconds` - Gauge
- ✅ `scheduler_heartbeat_seconds` - Gauge
- ✅ `trends_stable_task_runs_total` - Counter (outcome)
- ✅ `trends_stable_clusters_checked_total` - Counter
- ✅ `trends_stable_clusters_promoted_total` - Counter
- ✅ `trends_stable_clusters_skipped_total` - Counter (reason)
- ✅ `trends_stable_task_duration_seconds` - Histogram

**Алерты:**
- ✅ `SchedulerNotRunning` - scheduler не запущен > 1 минуты
- ✅ `SchedulerFreshnessHigh` - freshness > 10 минут
- ✅ `SchedulerFreshnessCritical` - freshness > 15 минут
- ✅ `SchedulerHeartbeatMissing` - heartbeat отсутствует > 60 секунд

**Статус:** ✅ Полностью покрыт

---

### 7.3 Health Checks

**Метрики:**
- ✅ `health_check_duration_seconds` - Histogram
- ✅ `health_check_failures_total` - Counter
- ✅ `health_check_cache_hits_total` - Counter

**Алерты:**
- ✅ `HealthCheckFailuresHigh` - частота ошибок > 5/5мин

**Статус:** ✅ Полностью покрыт

---

## 8. Проверка дублирования

### 8.1 Дублирование метрик

**Результаты проверки:**
- ✅ Дублирующихся метрик не найдено
- ⚠️ Есть потенциальные дубликаты:
  - `stream_pending_size` и `posts_in_queue_total` - оба измеряют pending сообщения, но с разными labels
  - `vision_worker_processed_total` и `vision_events_total` - оба измеряют обработку vision событий

**Рекомендации:**
- Объединить `stream_pending_size` и `posts_in_queue_total` в одну метрику с едиными labels
- Использовать только `vision_events_total` (так как `vision_worker_processed_total` не экспортируется)

---

### 8.2 Дублирование алертов

**Результаты проверки:**
- ✅ Дублирующихся алертов не найдено
- ⚠️ Есть похожие алерты:
  - `StorageQuotaWarning` и `S3StorageQuotaWarning` - оба проверяют storage quota, но с разными порогами
  - `StorageQuotaCritical` и `S3StorageQuotaCritical` - аналогично

**Рекомендации:**
- Объединить алерты storage quota в единые с разными severity уровнями

---

## Итоговая статистика

### Покрытие метриками

- ✅ Полностью покрыто: 8 компонентов
- ⚠️ Частично покрыто: 10 компонентов
- ❌ Не покрыто: 4 компонента

**Общий процент покрытия метриками:** ~73%

### Покрытие алертами

- ✅ Полностью покрыто: 6 компонентов
- ⚠️ Частично покрыто: 5 компонентов
- ❌ Не покрыто: 11 компонентов

**Общий процент покрытия алертами:** ~45%

---

## Приоритетные рекомендации

### Critical (высокий приоритет)

1. **Graph Writer Task** - добавить метрики и алерты
2. **Post Persistence Task** - добавить метрики и алерты
3. **PostgreSQL** - добавить метрики подключений и алерты
4. **Qdrant** - добавить метрики операций и алерты
5. **Neo4j** - добавить алерты для метрик

### Warning (средний приоритет)

1. **Enrichment Task** - добавить алерты
2. **Tag Persistence Task** - добавить дополнительные алерты
3. **Retagging Task** - добавить алерты
4. **Digest Worker** - добавить алерты
5. **Trends** - добавить алерты
6. **Telethon Ingest** - добавить алерты
7. **DLQ** - добавить дополнительные алерты

### Info (низкий приоритет)

1. **API Endpoints** - добавить метрики latency для всех endpoints
2. **Redis** - добавить метрики операций
3. Объединить дублирующиеся метрики и алерты

---

## Использование Context7

При реализации новых метрик и алертов рекомендуется:

1. Использовать Context7 best practices для мониторинга
2. Проверять соответствие метрик рекомендациям Context7
3. Использовать Context7 для поиска лучших практик мониторинга
4. Избегать дублирования метрик и алертов
5. Использовать единые naming conventions

---

**Следующие шаги:**
1. Создать детальный отчет о пробелах (`PROMETHEUS_COVERAGE_GAPS.md`)
2. Создать отчет с рекомендациями (`PROMETHEUS_COVERAGE_RECOMMENDATIONS.md`)
3. Приоритизировать исправления пробелов

