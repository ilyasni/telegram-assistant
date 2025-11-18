# Сопоставление старого пайплайна и новой архитектуры груповых дайджестов

## 1. Источники анализа
- `docs/OLD_SYSTEM_PIPELINE.md`, `docs/OLD_SYSTEM_SPECIFICATION.md` — описание монолитного n8n/Flowise пайплайна версии 3.1.
- `docs/ARCHITECTURE_PRINCIPLES.md`, `docs/ADR_GROUP_DIGESTS.md` — целевые принципы LangGraph-first, event-driven и приоритеты безопасности.
- Context7 best practices:
  - `docs/CONTEXT7_BEST_PRACTICES.md` — Telethon/FastAPI/RLS/идемпотентность (маркеры `telethon-floodwait-001`, `security-idempotency-002` и др.).
  - `/davidkimai/context-engineering` — `ResilientMultiAgentSystem`, протоколы self-repair и проверки границ агентов (boundary integrity ≥ 0.75, coordination repair).

## 2. Сопоставление ключевых подсистем

| Старая спецификация (v3.1) | Новая архитектура (LangGraph/Event-driven) | Точки миграции | Контекст7 / принцип |
| --- | --- | --- | --- |
| **Telethon Ingest**: индивидуальные клиенты, Flowise триггеры, ручные backoff | `telethon-ingest` сервис + Redis Streams/Kafka, FloodWait backoff, QR TTL (`telethon-ingest/main.py`) | 1) Убедиться, что group ingest использует те же `FloodWait`/TTL паттерны.<br>2) Привязать `tenant_id` к RLS (`security-owner-verify-001`). | `telethon-floodwait-001`, `telethon-qr-ttl-002`, принцип *Security first* |
| **n8n orchestrator**: ветка `generate_daily_digests`, отсутствие идемпотентности | LangGraph `GroupDigestOrchestrator`, `DigestStateStore` (Redis + PG), schema_version | 1) Перенести правила повторов/компенсаций из n8n в LangGraph (см. todo Stage 2/3).<br>2) Закрыть gap по schema_version для всех артефактов (already added, нужно завязать на миграцию). | `security-idempotency-002`, Context7 self-repair (boundary check перед запуском) |
| **LLM маршрутизация**: GigaChat single model, fallback через n8n | `worker/config/group_digest_models.yml` + `LLMRouter` с квотами и fallback `pro→base` | 1) Синхронизировать лимиты с прежними `digest_settings` (per-tenant quotas).<br>2) Подготовить мониторинг по `gigachat_requests_total`. | ARCHITECTURE_PRINCIPLES §Security → Observability, Context7 `ResilientMultiAgentSystem` (assignment + repair) |
| **Темы/эмоции/роли**: единая LLM сводка без агентов | Агенты `segmenter`, `emotion`, `roles`, `topic`, `synthesis`, `evaluation`, `delivery` | 1) Проверить, что каждый агент имеет guardrails (JSON schema + boundary check).<br>2) Подготовить инструкции на основе контекста из старой ветки (раздел 7.4). | Context7 `check_boundary_integrity` → подтверждение scope агентów, ADR §3 |
| **Хранилище артефактов**: Supabase таблицы `group_digests`, отсутствие стадий | Новая таблица `group_digest_stage_artifacts` + Redis cache | 1) Перенести исторические данные (если нужны) или задокументировать миграцию `NULL → stage artifacts`.<br>2) Настроить `app.current_tenant` во всех worker-миграциях. | Принцип мульти-тенантности + RLS, Context7 RLS best practices |
| **Доставка**: Telegram + e-mail, без scope-проверок | `_node_delivery` с `DIGEST_DELIVERY_SCOPE`, RBAC из окна | 1) Сопоставить старые роли (`admin`, `member`, `viewer`) со scope.<br>2) Описать fallback (ошибка доставки → DLQ). | ARCHITECTURE_PRINCIPLES §Security, ADR §4.3 |
| **Observability**: Grafana dashboard для n8n, логи без трассировки | Prometheus метрики (`digest_generation_seconds`, `digest_tokens_total`) + OpenTelemetry | 1) Сверить существующие алерты (см. `OLD_SYSTEM_SPECIFICATION.md` §8) с новыми метриками.<br>2) Добавить heatmaps и sampling согласно todo Stage 4. | `docs/GRAFANA_CONTEXT7_BEST_PRACTICES.md`, принцип *Observability second* |
| **Quality loop**: ручной QA, без автоматической переоценки | Stage 2 — self-validation/repair, baseline сравнение | 1) Импортировать критерии из старого QA чек-листа (`OLD_SYSTEM_SPECIFICATION.md` §7.6).<br>2) Применить Context7 self-repair протоколы для автоматического исправления агентов. | `/davidkimai/context-engineering` maintenance_cycle |

## 3. Группы задач миграции

1. **Инфраструктура ingest**  
   - Провести аудит Telethon-паттернов в группах vs каналах (FloodWait, TTL, rate limit).  
   - Добавить метрики `telethon_group_ingest_*`, контролировать через Prometheus (Context7 паттерны).

2. **Стейт и идемпотентность**  
   - Завершить миграцию `group_digest_stage_artifacts` (применено).  
   - Встроить `schema_version` / `prompt_version` в LangGraph состояние и синхронизацию с Supabase (todo Stage 2/3).  
   - Установить политику `app.current_tenant` во всех воркерах (RLS).

3. **Агентные роли и guardrails**  
   - Сравнить старые JSON-форматы (темы, участники, метрики) с новыми схемами; подготовить mapping.  
   - Использовать Context7 `boundary_integrity` проверки перед запуском self-heal петли.  
   - Задокументировать инструкции и ограничения (см. следующий todo).

4. **Observability и cost control**  
   - Сопоставить старые алерты (`group_digest_latency`, `gigachat_cost`) с новыми метриками.  
   - Настроить `digest_synthesis_fallback_total` в Grafana dashboards.  
   - Продумать budget gate (quota) vs старый `usage_limiter`.

5. **Quality loop / baseline**  
   - Перетащить baseline-логику (сравнение с прошлым дайджестом) из n8n: в старом пайплайне использовалось поле `last_digest_summary`.  
   - Интегрировать Context7 maintenance-cycle идеи: регулярный self-check агентов, корректирующий retry.

6. **Context service (Stage 5)**  
   - `GroupContextService` вынесен в `worker/services/group_context_service.py`: маскирование PII, дедупликатор (`DIGEST_CONTEXT_*`), скоринг и top-k.  
   - `_node_ingest_validator` использует сервис, сохраняет `context_stats`, `context_ranking`, `context_duplicates`, `context_history_links` в PG/Redis.  
   - API (`GroupDigestContent`) возвращает агрегаты + ranking + history links; docs/API_CONTRACTS обновлены.  
   - `Context7StorageClient` обеспечивает `fetch_recent_context` и `upsert_window_context` для повторного использования исторических окон (`DIGEST_CONTEXT_STORAGE_*`).
   - `DigestContextObserver` (worker/tasks/context_events_task.py) подписан на `digest.context.prepared` и обновляет метрики (`digest_context_messages`, `digest_context_duplicates_total`, `digest_context_history_matches_total`).

6. **Delivery & RBAC**  
   - Согласовать scopes с `OLD_SYSTEM_SPECIFICATION.md` (админ → `DIGEST_ADMIN`, модератор → `DIGEST_READ`).  
  - Добавить DLQ при блокировке доставки (todo Stage 3).

## 4. Приоритетные next steps (для других todo)
1. Подготовить диаграмму event-driven data-flow, отражающую точки миграции (следующий пункт backlog).  
2. Описать инструкции/guardrails для агентов с ссылками на Context7 self-heal паттерны.  
3. Зафиксировать baseline сравнение и self-validation (Stage 2).  
4. Спланировать circuit breaker + DLQ (Stage 3).

## 5. Риски
- Несогласованность схем (старый формат тем/метрик) → требуется миграционный скрипт/adapter.
- Недостаток мониторинга на повторное использование GigaChat Pro → нужно связать квоты и алерты.
- Неполная реализация self-repair loop может вызвать хаос в LangGraph → применить Context7 `repair_boundaries` перед ретраями.

