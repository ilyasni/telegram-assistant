# Инструкции и guardrails для агентов 0–8 мультиагентного пайплайна

Документ описывает назначение, входные/выходные контракты, ограничения и контрольные точki для каждого агента LangGraph-пайплайна групповых дайджестов. Все требования согласованы с `ARCHITECTURE_PRINCIPLES.md`, `ADR_GROUP_DIGESTS.md`, `docs/GROUP_DIGEST_EVENT_FLOW.md`, `docs/GROUP_DIGEST_PIPELINE.md`, а также Context7 best practices (`docs/CONTEXT7_BEST_PRAКТICES.md`, `/davidkimai/context-engineering`, `/shak-shat/langgraph4context7`).

> **Общие правила для всех агентов**
> - **Модель и температура** берутся из `worker/config/group_digest_models.yml` через `LLMRouter`.
> - **Формат ответа** — строгое соответствие JSON Schema. Проверяется `worker/common/json_guard.py` (self-validation + repair-loop).  
> - **PII Redaction**: входные данные проходят через `_node_ingest_validator` с `mask_pii()`; агент не должен раскрывать скрытую информацию.  
> - **Context service (Stage 5)**: контекст уже дедуплицирован и отсортирован `GroupContextService` (`DIGEST_CONTEXT_*`), агенты не повторяют очистку.
> - **Context7 Storage**: при включённом `DIGEST_CONTEXT_STORAGE_ENABLED` исторические сообщения уже подмешаны; агенты не должны делать внешние вызовы в Storage напрямую, опираются на `context_history_links`.
> - **Observability**: каждый вызов логирует `trace_id`, `tenant_id`, `window_id`, `model_id`, `prompt_id`, `attempt`. Метрики: `digest_generation_seconds`, `digest_tokens_total`, `digest_synthesis_fallback_total`.  
> - **Self-repair**: при провале JSON-валидации используется `*_REPAIR_PROMPT_V1`, ограничение retries ≤ `DIGEST_MAX_RETRIES` (по умолчанию 3).  
> - **Security/RBAC**: агенты не работают с tenant scope’ами напрямую; проверку выполняет delivery manager.  
> - **Context7 контроль**: перед запуском цепочки вызывается `check_boundary_integrity()` (порог ≥ 0.75) и, при необходимости, `repair_boundaries()` (см. `/davidkimai/context-engineering`).  

## Агент 0 — Ingest Validator (`ingest_validator`)
- **Назначение**: проверка входного окна, фильтрация service- сообщений, маскирование PII, формирование нормализованного payloadа.  
- **Вход**:  
  - Window summary (`window_id`, `group_id`, `tenant_id`, `window_start/end`, `message_count`, `scopes`).  
  - Messages[] с полями `id`, `posted_at`, `sender_username`, `sender_tg_id`, `content`, `reply_to`.  
- **Выход**:  
  ```json
  {
    "valid": true,
    "issues": [],
    "messages": [...filtered...],
    "policy_flags": []
  }
  ```  
- **Guardrails**:  
  - Reject окно, если `message_count < DIGEST_MIN_MESSAGES` или `> DIGEST_MAX_MESSAGES`.  
  - Отбрасывать сообщения с `is_service = true`, медиа без текста.  
  - Проверять наличие `tenant_id` у каждого сообщения; иначе — добавить в `policy_flags`.  
- **Observability**: `digest_ingest_filter_total{reason}`, `digest_ingest_redacted_tokens_total`.  
- **Связанные события**: `digest.stage.started/completed` (stage=`ingest_validator`).  

## Агент 1 — Thread Builder (`thread_builder`)
- **Назначение**: объединение сообщений в дискуссионные ветки.  
- **Вход**: нормализованные сообщения + `max_thread_len = DIGEST_THREAD_MAX_LEN`.  
- **Выход**:  
  ```json
  {
    "threads": [
      {
        "thread_id": "thread-001",
        "msg_ids": ["123", "..."],
        "start_ts": "...",
        "end_ts": "...",
        "reply_root": "122",
        "similarity_reason": "..."
      }
    ]
  }
  ```  
- **Guardrails**:  
  - Ограничение 20 сообщений на ветку (Context7: chunk guard для LangGraph).  
  - Не использовать эвристику по авто-вставке сообщений без уверенности (confidence < 0.6 ⇒ исключить).  
  - Поддержка part-суффиксов при разбиении (`thread-1-part2`).  
- **Observability**: метрики `digest_threads_total`, `digest_threads_messages_total`.  
- **Self-repair**: если JSON некорректен, повторный вызов со сжатым контекстом (топ-150 сообщений).  

## Агент 2 — Semantic Segmenter (`segmenter_agent`)
- **Назначение**: определить семантические блоки внутри каждой ветки (problem/solution/humor/decision и т.д.).  
- **Вход**: список веток + оригинальные сообщения.  
- **Выход**:  
  ```json
  {
    "segments": [
      {
        "thread_id": "thread-001",
        "units": [
          {"kind": "problem", "text": "...", "msg_ids": [...], "confidence": 0.87}
        ]
      }
    ]
  }
  ```  
- **Guardrails**:  
  - Список допустимых `kind`: `problem`, `solution`, `decision`, `risk`, `celebration`, `meta`, `humor`.  
  - Обязателен `confidence`; запрет на генерацию единиц с `confidence < 0.5`.  
  - Объединение сообщений должно ссылаться на существующие `msg_ids` (пересечение с thread builder).  
- **Observability**: `digest_segment_units_total{kind}`, `digest_segment_low_confidence_total`.  
- **Self-repair**: fallback — удалить сегмент с низким доверием, повторить на подмножестве веток.  

## Агент 3 — Emotion Analyzer (`emotion_agent`)
- **Назначение**: построить эмоциональный профиль веток и всего окна.  
- **Вход**: сегменты + сообщения.  
- **Выход**:  
  ```json
  {
    "emotions": [
      {"label": "positive", "score": 0.62, "support": 15},
      {"label": "stress", "score": 0.28, "support": 4}
    ],
    "notes": "Ключевые триггеры..."
  }
  ```  
- **Guardrails**:  
  - Разрешённые `label`: `positive`, `neutral`, `negative`, `stress`, `conflict`, `enthusiasm`, `sarcasm`.  
  - `score` ∈ [0,1], сумма may exceed 1 (разные измерения).  
- **Observability**: `digest_emotion_score{label}` (Prometheus histogram).  
- **Self-repair**: при несоответствии JSON — сократить контекст до top threads (max 5).  

## Агент 4 — Role Classifier (`roles_agent`)
- **Назначение**: определить роли и вклад участников (facilitator, blocker и т.п.).  
- **Вход**: сообщения + сегменты + метаданные участников.  
- **Выход**:  
  ```json
  {
    "participants": [
      {
        "telegram_id": "123",
        "username": "alice",
        "role": "initiator",
        "message_count": 12,
        "summary": "..."
      }
    ],
    "role_profile": {
      "initiator": 2,
      "supporter": 3,
      "critic": 1
    }
  }
  ```  
- **Guardrails**:  
  - Список ролей: `initiator`, `facilitator`, `subject_matter_expert`, `supporter`, `critic`, `lurker`, `moderator`, `troll`.  
  - `message_count` соответствует количеству сообщений пользователя в окне.  
  - Сумма `role_profile` = количество участников (проверяется пост-обработкой).  
- **Observability**: `digest_role_distribution{role}`.  
- **Self-repair**: при конфликте (`role_profile` ≠ len(participants)) → запустить режим перерасчёта с агрегированными данными.  

## Агент 5 — Topic Synthesizer (`topic_agent`)
- **Назначение**: выделить темы и приоритеты на основе сегментов, эмоций и ролей.  
- **Вход**: агрегированные результаты предыдущих агентов.  
- **Выход**:  
  ```json
  {
    "topics": [
      {
        "title": "Обновление релиза",
        "priority": "high",
        "threads": ["thread-001", "thread-003"],
        "msg_count": 12,
        "keywords": ["релиз", "дедлайн"],
        "actions": ["Подготовить changelog"]
      }
    ]
  }
  ```  
- **Guardrails**:  
  - `priority` ∈ {`critical`, `high`, `medium`, `low`}.  
  - `threads` — список существующих `thread_id`.  
  - `msg_count` = количество уникальных сообщений в указанных threads.  
  - `actions` — max 3 пункта, без персональных упоминаний (PII).  
- **Observability**: `digest_topics_total`, `digest_topic_priority_total{priority}`.  
- **Self-repair**: при превышении лимитов → уменьшить количество тем или указать `priority=low`.  

## Агент 6 — Digest Composer (`synthesis_agent`)
- **Назначение**: сформировать итоговый дайджест (Telegram HTML).  
- **Вход**: темы, участники, эмоции, сегменты, baseline digest (если существует).  
- **Выход**:  
  ```json
  {
    "summary": "<b>Главное</b>...",
    "metrics": {...},
    "structure": {
      "highlights": [...],
      "details": [...],
      "actions": [...]
    },
    "baseline_delta": {
      "coverage_change": 0.12,
      "novel_topics": 2
    }
  }
  ```  
- **Guardrails**:  
  - Использовать только Telegram HTML whitelist (`b`, `i`, `u`, `a`, `code`, `pre`, `blockquote`).  
  - Сравнить с baseline (`worker/common/baseline_compare.py`); при деградации >10% — поднять флаг `baseline_delta.degraded = true`.  
  - Генерировать `metrics` (coverage, coherence, focus) из агрегированных данных, не выдумывать.  
- **Observability**: `digest_synthesis_length_chars`, `digest_baseline_delta{type}`.  
- **Self-repair**: если качество < SLA → запускается корректирующий промпт с уменьшенным контекстом (Stage 2).  

## Агент 7 — Quality Evaluator (`evaluation_agent`)
- **Назначение**: оценить дайджест по критериям faithfulness, coherence, coverage, focus и выдать итоговый `quality_score`.  
- **Вход**: итоговый дайджест, исходные темы, сегменты.  
- **Выход**:  
  ```json
  {
    "scores": {
      "faithfulness": 0.82,
      "coherence": 0.78,
      "coverage": 0.74,
      "focus": 0.81
    },
    "quality_score": 0.79,
    "comments": "..."
  }
  ```  
- **Guardrails**:  
  - `quality_score` = среднее взвешенное (по умолчанию равномерное).  
  - Если любой критерий < 0.6 → добавить в `comments` причину и пометить `quality_score < DIGEST_QUALITY_THRESHOLD`.  
  - Не модифицировать `summary`.  
- **Observability**: `digest_quality_score`, `digest_quality_metric{metric}`.  
- **Self-repair**: если JSON invalid → повторная генерация с подсказкой об ошибке; max 2 попытки.  

## Агент 8 — Delivery Manager (`delivery_manager`)
- **Назначение**: принять решение о доставке (RBAC/scopes, качество, квоты GigaChat).  
- **Вход**: итоговый state (summary, evaluation, metrics, scopes, tenant quotas).  
- **Выход**:  
  ```json
  {
    "status": "ready|blocked_rbac|blocked_quality|blocked_quota",
    "reason": "missing_scope:DIGEST_READ",
    "quality_pass": true,
    "delivery_payload": {...}
  }
  ```  
- **Guardrails**:  
  - Проверка scope: `DIGEST_DELIVERY_SCOPE` ⊆ `window.scopes`.  
  - Если `quality_score < DIGEST_QUALITY_THRESHOLD` → `status=blocked_quality`.  
  - Контроль квот: сравнить `LLMRouter.quota_usage` с `DIGEST_PRO_QUOTA_*`.  
  - Обязательное заполнение `delivery_payload` при `status=ready`.  
- **Observability**: `digest_delivery_blocked_total{reason}`, `digest_delivery_ready_total`.  
- **Self-repair**: не применяется (решения детерминированные). В случае блокировки формируется событие `digest.blocked.v1`.  

## Дополнительные требования наблюдаемости и аудита
- **Tracing**: включить спаны `group_digest_orchestrator.<stage>`; propagate `trace_id` в событиях (`group.message.ingested`, `digest.stage.completed`, `digest.ready_for_delivery`).  
- **Logging**: использовать структурированный JSON (`structlog`), поля: `trace_id`, `tenant_id`, `stage`, `attempt`, `model_id`, `prompt_id`, `quality_score`. Маскирование PII согласно `docs/CONTEXT7_BEST_PRACTICES.md`.  
- **Metrics export**: каждая стадия пушит значения в Prometheus (см. `docs/GROUP_DIGEST_EVENT_FLOW.md`, раздел 6).  
- **Context7 hooks**: при деградации качества ≥ 3% подряд — запуск `maintenance_cycle()` (Context Engineering) и оповещение via `digest.dlq.v1`.  

## Контроль изменений и версионирование
- Все промпты и модели привязаны к `prompt_version`, `model_id`, сохраняются в `group_digest_stage_artifacts`.  
- Любое изменение инструкций требует обновления:  
  1. `worker/prompts/group_digest.py` (prompt_id*_V2).  
  2. `docs/GROUP_DIGEST_AGENT_GUARDRAILS.md` (соответствующий раздел).  
  3. Обновление Context7 записей (`LangGraph4Context7`/`Context Engineering` метка).  
  4. Проведение smoke-тестов `python tests/test_group_digest_orchestrator.py`.  

## План внедрения
1. Синхронизировать текущие промпты с указанными guardrails (Stage 2).  
2. Реализовать `json_guard` и `baseline_compare` (todo `todo-1762887311587-kxdnqleuz`).  
3. Добавить метрики и события в код (`docs/GROUP_DIGEST_EVENT_FLOW.md`).  
4. Настроить алерты Grafana (todo `todo-1762887311587-rlc9fxpcl`).  
5. Обновить API- и event-контракты (`docs/API_CONTRACTS.md`) с учётом статусов Delivery Manager.

