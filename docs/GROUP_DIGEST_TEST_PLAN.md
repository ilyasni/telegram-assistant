# План тестирования группового дайджеста

## 1. Предусловия

- Настроена БД с миграциями для таблиц `group_*`.
- Доступны ключи GigaChat (`GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`, `GIGACHAT_BASE_URL`).
- Запущены сервисы: `telethon-ingest`, `worker`, `api`.
- В Redis очищены consumer groups `worker`, `worker-group-messages`, `digest-workers`.
- Установлены переменные окружения `DIGEST_CONTEXT_*` (см. `.env.example`) или приняты значения по умолчанию для Stage 5.
- При необходимости интеграции с Context7 Storage указаны `DIGEST_CONTEXT_STORAGE_*` (URL, токен); по умолчанию disabled.

## 2. Автоматические проверки

| Тип | Команда | Цель |
| --- | --- | --- |
| Unit | `pytest tests/test_group_digest_orchestrator.py` | Проверка оркестратора (stub LLM) |
| Unit | `pytest tests/test_group_digest_service.py` | Проверка сервиса (mock orchestrator) |
| Integration | `pytest tests/test_group_digest_flow.py` | e2e: POST /api/groups/{id}/digest → доставка в Redis/БД |
| Unit | `pytest tests/test_group_context_service.py` | Контекст-сервис (дедуп, скоринг, ranking) |
| Unit | `pytest tests/test_group_digest_feature_flag.py` | Проверка feature-flag rollout (глобальный toggle / canary) |
| Static | `ruff check worker/tasks/group_digest_agent.py` | Линтинг новых модулей |
| Static | `mypy worker/tasks/group_digest_agent.py` | Типизация LangGraph пайплайна |

### Структура тестов

- `tests/test_group_digest_orchestrator.py` — подменяет `GroupDigestOrchestrator.llm` на `StubLLM`, проверяет обработку пустых сообщений, генерацию topics/metrics.
- `tests/test_group_digest_service.py` — использует in-memory SQLite, мокает orchestrator, проверяет сохранение в `group_digests` и связанные таблицы.
- `tests/test_group_digest_flow.py` — запускает FastAPI TestClient, создает группу, вызывает `/groups/{id}/digest`, проверяет появление событий в Redis (mock Redis) и запись `DigestHistory`.

## 3. Manual sanity checks

1. **Ingestion**
   - Добавить тестовую группу через `/api/groups` (tenant=demo).
   - Отправить 3–4 сообщения в Telegram; убедиться, что `group_messages` заполнена.
2. **Embeddings**
   - Убедиться, что worker логирует `Group message embeddings processed`.
   - Проверить коллекцию Qdrant `tdemo_groups`.
3. **Digest trigger**
   - Вызвать `POST /api/groups/{group_id}/digest` (`window_size_hours=4`).
   - Проверить запись в `group_conversation_windows` (status=queued).
   - Убедиться, что Redis stream `stream:digests:generate` содержит событие с context=group.
4. **Digest delivery**
   - Проверить лог `Group digest auto-evaluation`.
   - Убедиться, что Telegram бот доставил сообщение в Markdown-формате.
   - Проверить `group_digests.delivery_status = 'sent'`.
5. **Context7 Storage (опционально)**
   - Включить `DIGEST_CONTEXT_STORAGE_ENABLED=1`, задать `DIGEST_CONTEXT_STORAGE_URL`.
   - После генерации окна проверить REST endpoint `/namespaces/{ns}/documents/search` — присутствует `window_id`, `messages`, `historical_links`.
6. **Context observer**
   - Убедиться, что task `digest_context_observer` запущен (`run_all_tasks.py`), в логах есть `digest_context_event_processed`.
   - Проверить метрики Prometheus: `digest_context_messages`, `digest_context_duplicates_total`, `digest_context_history_matches_total`.

## 4. Мониторинг

- Prometheus метрики:
  - `group_digest_evaluation_score{metric="faithfulness"}` — распределение оценок.
  - `digest_worker_generation_seconds{status="success"}` — латентность генерации.
  - `telethon_ingest_crash_signals_total` — отсутствие критичных сигналов после деплоя.
- Grafana:
  - Создать новый dashboard: показатели качества (faithfulness, relevance), статус окон, delivery backlog.

## 5. Rollout

1. **Canary**: включить для тестового tenant (1-2 группы), monitor logs/metrics 24 часа.
2. **Feature flag**: настроить `DIGEST_AGENT_ENABLED=0` и `DIGEST_AGENT_CANARY_TENANTS=["demo-tenant-uuid"]`, держать глобальный toggle выключенным до завершения canary; проверять, что вне allow-list API возвращает `403`.
3. **Alerting**:
   - SLO: `faithfulness >= 0.7`, `delivery_success_rate >= 0.95`.
   - PagerDuty: алерт при `group_digest_quality_scores{metric="faithfulness"} < 0.5` (p95) в течение 30 мин.
4. **Fallback**: в случае ошибок переключить context=channels (старый digest), отключив новые endpoints.

## 6. Регрессии

- Проверить, что существующие `/api/digest` endpoints работают (регулярные дайджесты).
- Контроль `DigestHistory` — новые записи не ломают аналитику отчётов.
- Проверить, что RLS/tenant фильтры корректно применяются (tenant_id обязателен в запросах).

