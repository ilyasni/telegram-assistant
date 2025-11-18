# Схема интеграций, хранение артефактов и контуры контроля качества/безопасности

Документ фиксирует как мультиагентный пайплайн групповых дайджестов встраивается в инфраструктуру Telegram Assistant с учётом Context7 best practices. Опирается на:
- `docs/GROUP_DIGEST_EVENT_FLOW.md`, `docs/GROUP_DIGEST_PIPELINE.md`
- `worker/common/digest_state_store.py`, `worker/common/json_guard.py`, `worker/common/baseline_compare.py`
- Context7: LangGraph Storage (`/shak-shat/langgraph4context7` — Redis/Postgres saver, checkpoint версия), Context Engineering (`/davidkimai/context-engineering` — maintenance/self-repair), `docs/CONTEXT7_BEST_PRACTICES.md`.

## 1. Логические слои

| Слой | Компоненты | Назначение | Контроль |
| --- | --- | --- | --- |
| **Ingest & Messaging** | `telethon-ingest`, Redis Streams / Kafka (`group.message.ingested`, `group.window.ready`) | Идемпотентный приём сообщений, формирование окон | FloodWait backoff, rate-limit (Context7 `telethon-floodwait-001`), event tracing |
| **State & Artifacts** | Redis (оперативное хранилище стадий), Postgres `group_digest_stage_artifacts`, S3 (медиа), Qdrant (векторные признаки) | Быстрая идемпотентность и персистентность артефактов стадий | TTL, schema_version, RLS, checksum, версионирование prompt/model |
| **LangGraph Orchestration** | `GroupDigestOrchestrator` (LLMRouter, JSON guard, baseline compare, corrective loop) | Мультиагентная аналитика + self-heal | JSON Schema + repair-промпты, quota (Pro→Base fallback), baseline delta |
| **Quality & Delivery** | Quality evaluator, Delivery manager, RBAC (`DIGEST_DELIVERY_SCOPE`) | Оценка, retry-синтез, блокировки по SLA | Quality_threshold, baseline_delta, DLQ (Stage 3), audit trail |
| **Observability & Safety** | Prometheus, OpenTelemetry, Grafana, Structlog, Supabase RLS | Метрики, трассировка, хранение артефактов, аудит | Heatmaps (Stage 4), log sampling, PII redaction |

## 2. Хранение и схемы артефактов

### 2.1 Redis + Postgres (State Store)
- **Ключи**: `digest:{tenant}:{group}:{window}:{stage}` (Redis), уникальный индекс в PG (`ux_digest_stage_artifacts_stage`).
- **Метаданные**: `prompt_id`, `prompt_version`, `model_id`, `schema_version`, `stored_at`.
- **Артефакты**: JSON payload (темы, роли, метрики, synthesis, evaluation). В Redis — TTL 24 часа, в Postgres — постоянное хранение (используется в baseline compare).
- **Контекст Context7**: пример `_dump_blobs`/`BaseRedisSaver` — версия ключа для предотвращения конфликтов и облегчения очистки.

### 2.2 Supabase / Postgres (доменные таблицы)
- `group_digests`, `group_digest_topics`, `group_digest_participants`, `group_digest_metrics`.
- `payload.baseline_delta` — всё, что нужно для ретроспективы (coverage_change, topic_overlap, quality_delta).
- RLS: `tenant_id = auth.uid()`; `DIGEST_DELIVERY_SCOPE` контролирует доступ к результатам.

### 2.3 S3 / Qdrant / Neo4j
- Медиа/визуальные артефакты → S3 (`group/{tenant}/{group_id}/...`).
- Qdrant: коллекция `user_{tenant}_groups` (payload: participants, topic_embedding, sentiment_vector), используется для быстрого поиска контекста.
- Neo4j: `GroupDigestService._push_to_graph(...)` синхронизирует темы/участников (пакетная интеграция).

## 3. Контуры качества

| Контур | Механизм | Метрики / события |
| --- | --- | --- |
| **Self-validation** | `worker/common/json_guard.py` + JSON Schema + repair-промпт (Context7 Control Shell) | `json_guard.*` предупреждения, stage errors |
| **Baseline compare** | `worker/common/baseline_compare.py` (load_previous_snapshot + compute_delta) | `baseline_delta` → coverage_change, topic_overlap, quality_delta |
| **Corrective retry** | `digest_composer_retry_prompt_v1` + повторная оценка Quality | `digest_synthesis_fallback_total{reason="retry"}`, `synthesis_retry_used` |
| **Quality evaluator** | Оценка faithfulness/coherence/coverage/focus + overall quality_score | `digest_quality_score{metric}`, `digest_skipped_total` |
| **Observability** | Prometheus (метрики агента/качества), OTel спаны `digest.stage.*`, лог-сэмплирование | Grafana heatmaps (Stage 4), alerts: quality < SLA, fallback rate, quota |
- **Observability**
  - Prometheus: `digest_stage_latency_seconds{stage,status}`, `digest_stage_status_total{stage,status}`, `digest_quality_score{metric}`, `digest_dlq_total{stage,error_code}`, `digest_circuit_open_total{stage}`.
  - OTel: спаны `digest.stage.*` с атрибутами tenant/group/stage, корреляция через trace_id.
  - Лог-сэмплирование: `DIGEST_LOG_SAMPLE_RATE` (default 1%); события `digest_ingest_sample`, `digest_topics_sample`, `digest_evaluation_sample`.

| **Maintenance** | Nightly job: `maintenance_cycle()` (Context Engineering), анализ boundary integrity | `maintenance_status`, `boundary_integrity_improvement` |

## 4. Безопасность и соответствие
- **PII Redaction**: `_node_ingest_validator` + `mask_pii`, запрет на логирование открытых идентификаторов.
- **RBAC**: Delivery manager проверяет `DIGEST_DELIVERY_SCOPE`, блокирует доставку при отсутствии прав; событие `digest.blocked.v1`.
- **Quota & Cost Control**: `LLMRouter` (quota tracker + token budget), fallback @pro→@base, Prometheus `digest_pro_quota_exceeded_total`.
- **Audit trail**: `group_digest_stage_artifacts` (история артефактов), `GroupDigest.payload.errors`, delivery metadata (status/reason/trace_id).
- **Идемпотентность**: Redis lock + Postgres UniqueConstraint + `state_store` metadata → безопасный повтор стадий.
- **Data Retention**: TTL в Redis, архивирование артефактов (PG) по policy retention (90 дн); интеграция с `cleanup_old_posts` (событие retention).

## 5. Интеграционные потоки

1. **Событийный контур**: подробности в `docs/GROUP_DIGEST_EVENT_FLOW.md`. Рекомендуемые топики:
   - `group.digest.stage.started/completed`
   - `group.digest.blocked` (RBAC/quality/quota)
   - `group.digest.dlq` (Stage 3 — расширенный retry/compensation)
2. **API ↔ Orchestrator**: `GroupDigestService` передаёт окна → `generate_async`, получает state (topics, evaluation, baseline_delta, delivery).
3. **Orchestrator ↔ Storage**: `DigestStateStore` (Redis + PG), baseline compare → `load_previous_snapshot`.
4. **Orchestrator ↔ Observability**: Prometheus, OTel (тег `trace_id` для сквозного трейсинга).
5. **Orchestrator ↔ External Services**: GigaChat (через `langchain_gigachat`), Qdrant, Neo4j, S3.

## 6. Dev/Stage/Prod сценарии

| Окружение | Подсистемы | Особенности |
| --- | --- | --- |
| Dev | Docker Compose (Redis, Postgres, Qdrant, Neo4j optional) | Redis in-memory fallback, минимальные квоты, логирование DEBUG |
| Stage | Managed Redis/Postgres + ограниченные ключи GigaChat | Ограниченный размер окон, включенный baseline compare, тестовые алерты |
| Prod | HA Redis, Supabase (Postgres), Qdrant cluster, Neo4j Aura | Полный наблюдательный контур, DLQ + circuit breaker, Grafana dashboards, SLA alerts |

## 7. План дальнейших этапов

| Этап | Действия |
| --- | --- |
| **Stage 3** (Circuit breaker, DLQ, расширенный retry) | Внедрить `digest.dlq.v1`, ограничение LLM-пыток, компенсация (повторное окно / fallback summary) |
| **Stage 4** (Observability расширения) | Heatmaps (quality_score, latency), log sampling (1% success/100% errors), интеграция с Grafana alerts |
| **Stage 5** (Context service) | Отдельный сервис ранжирования контекста (`GroupContextService`), дедупликатор и скоринг, сохранение артефактов в Redis/PG (`DigestStateStore`), подготовка ranking/top-k окон |
| **Stage 6** (Тестирование) | Контрактные тесты (JSON Schema), E2E (`tests/test_group_digest_orchestrator.py` + интеграционные сценарии), chaos-тесты (imperfect data) |
| **Stage 7** (Документация & rollout) | Обновление API контрактов, feature-flag/canary (`DIGEST_AGENT_ENABLED`, `DIGEST_AGENT_CANARY_TENANTS`), миграция метрик (Grafana) |

## 8. Риски и меры

| Риск | Митигирующая мера |
| --- | --- |
| Несогласованность артефактов (Redis vs PG) | Версионирование `schema_version`, nightly reconciliation (сравнение keyspace/таблиц) |
| Рост расходов GigaChat | Quota + token budget, Prometheus alert, fallback на base, caching embeddings |
| Провал JSON guard | Repair-промпт + DLQ запись, ручной перезапуск через Admin Mini App |
| Падение качества | baseline_delta, corrective retry, manual review queue |
| Утечка PII | Маскирование на ingest, RLS, secret scanning, audit log |

## 9. Resilience (Stage 3)

- **Circuit breaker**: `DIGEST_CIRCUIT_FAILURE_THRESHOLD`, `DIGEST_CIRCUIT_RECOVERY_SECONDS`; метрика `digest_circuit_open_total{agent}`; fallback → downgrade Pro→Base.
- **Retry policy**: экспоненциальный backoff (`DIGEST_RETRY_*`), jitter, трейсинг через `digest_synthesis_fallback_total{reason}`.
- **DLQ**: публикация `digests.generate.dlq` (payload_snippet + stack trace, retry_count, next_retry_at), метрика `digest_dlq_total{stage,error_code}`.
- **Compensation**: fallback summary + `delivery.status=blocked_failure`, baseline сравнение сохраняется для расследования.

## 10. Quality & Testing (Stage 6)

- **Контрактные тесты**: `tests/test_group_digest_quality.py` валидирует JSON Schema стадий (topics/roles/evaluation) через `JSON_SCHEMAS`.
- **E2E smoke**: orchestrator под `StubRouter` проверяет baseline_delta, quality_score, delivery.
- **Chaos/negative**: Low-quality и failing synthesis роутеры → `dlq_events` + `delivery.status=blocked_*`.
- **Auto-evaluation**: baseline_delta (`coverage_change`, `topic_overlap`, `quality_delta`) сохраняется в результатах и проверяется тестами; используется для анализа на Grafana/Prometheus.

## 11. Context Service (Stage 5)

- **GroupContextService** (`worker/services/group_context_service.py`) выносит сбор контекста за пределы оркестратора:
  - Санитизация (`mask_pii`), нормализация времён (`parse_timestamp`), enrich `reaction_count`.
  - Дедупликация (жёсткий порог `DIGEST_CONTEXT_SIMILARITY`, мягкий `DIGEST_CONTEXT_SOFT_SIMILARITY`, time-gap `DIGEST_CONTEXT_DEDUP_TIME`).
  - Скоринг (`ContextScoringWeights`) → recency half-life (`DIGEST_CONTEXT_HALF_LIFE`), reply boost, длина, реакции.
  - Возвращает `context_stats`, `context_ranking`, `context_duplicates`; данные пишутся в `group_digest_stage_artifacts`.
- **Интеграция**:
-  - `_node_ingest_validator` использует сервис и логирует `context_dedup_removed`, `context_trimmed`, `historical_matches`.
-  - `GroupDigestService` сохраняет `context_stats`, top-k ranking и связи с историей в payload (`context_history_links`).
-  - По умолчанию top-k (`DIGEST_CONTEXT_TOP_RANKED`) = 150, лимит сообщений после dedup (`DIGEST_CONTEXT_MAX_MESSAGES`) = 400.
- **Hook в Storage/API**:
  - `Context7StorageClient` (`worker/services/context7_storage_client.py`) поддерживает `upsert_window_context` и `fetch_recent_context` (`/namespaces/{ns}/documents/search`).
  - При включённом флаге (`DIGEST_CONTEXT_STORAGE_ENABLED=1`) исторические сообщения подгружаются перед дедупликацией (config: `history_windows`, `history_message_limit`).
  - Сохранённые артефакты доступны для downstream-анализов и внешних retrieval (`list_namespaces`, `search`).
- **Observability**:
  - `DigestContextObserver` (`worker/tasks/context_events_task.py`) потребляет `digest.context.prepared` и обновляет метрики: `digest_context_messages`, `digest_context_duplicates_total`, `digest_context_history_matches_total`.
- **Config / ENV**:
  - См. `worker/config/group_digest_models.yml::context` и `env.example` (`DIGEST_CONTEXT_*`).
- **Тестирование**:
  - `tests/test_group_context_service.py` покрывает сценарии dedup/ranking; `tests/test_group_digest_orchestrator.py` проверяет наличие `context_stats`.

---

**Контактные точки**
- Архитектура / Observability: DevOps команда + maintainer Orchestrator.
- Безопасность / RBAC: Security/Infra.
- LLM провайдеры: ML Ops (наблюдение за квотами GigaChat).
- Контекст сервис (Stage 5): Data Engineering.

**Ссылки**
- `docs/GROUP_DIGEST_EVENT_FLOW.md` — полный event-driven граф.
- `docs/GROUP_DIGEST_AGENT_GUARDRAILS.md` — инструкции и guardrails агентов.
- `docs/CONTEXT7_BEST_PRACTICES.md` — базовые практики проекта.


