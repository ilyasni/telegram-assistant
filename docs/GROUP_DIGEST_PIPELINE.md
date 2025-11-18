# Пайплайн мультиагентного группового дайджеста

## Обзор

- **Назначение:** генерация дайджестов разговоров в Telegram-группах с поддержкой окон 4/6/12/24 часа.
- **Архитектура:** LangGraph‑оркестратор (`worker/tasks/group_digest_agent.py`) + сервис сохранения (`api/services/group_digest_service.py`).
- **LLM:** GigaChat через `langchain_gigachat` (обязательное требование — отсутствие локальных моделей).
- **Контроль качества:** автоматическая оценка (LLM-as-judge), сохранение метрик в `group_digest_metrics` и `GroupDigest.history`.

## Поток данных

1. **Инициирование**  
   - API публикует `GroupDigestRequestedEventV1` (контекст `group`).
   - Событие `digests.generate` получает дополнительные поля `group_window_id`, `window_size_hours`, `delivery_channel`.

2. **Ingestion**  
   - `telethon-ingest` сохраняет сообщения в `group_messages` + `group_message_analytics`.
   - Векторизация сообщений выполняется в `worker/tasks/embeddings.py` (`process_group_message_embeddings`) с загрузкой в Qdrant (`t{tenant}_groups`).

3. **Агрегация окон**  
   - Планировщик формирует записи `group_conversation_windows` и считает индикаторы (кол-во сообщений, участников).

4. **Мультиагентная аналитика**  
   - `GroupContextService` формирует окно контекста (PII mask, dedup, ranking), при включённом `DIGEST_CONTEXT_STORAGE_ENABLED` дополнительно подкачивает историю из Context7 Storage перед дедупликацией; результаты сохраняются в `group_digest_stage_artifacts`.
   - Оркестратор LangGraph запускает цепочки:
     - `topic_agent` → JSON с темами и приоритетами.
     - `participant_agent` → список активных участников и ролей.
     - `metrics_agent` → тональность, conflict/collaboration/stress/enthusiasm.
     - `synthesis_agent` → Markdown дайджест согласно формату из задания.
     - `evaluation_agent` → автооценка (`faithfulness`, `answer_relevance`, `coherence`, `completeness`).

5. **Сохранение результатов**  
   - `GroupDigestService.generate` сохраняет данные в таблицы:
     - `group_digests`
     - `group_digest_topics`
     - `group_digest_participants`
     - `group_digest_metrics`
   - `delivery_status` выставляется в `pending`, позже обновляется воркером.

6. **Доставка**  
   - `DigestWorker` отправляет дайджест через Telegram бот (поддержка других каналов на этапе планирования).
   - После успешной доставки обновляет `group_digests.delivered_at`, `delivery_status`.

## Метрики и наблюдаемость

- `digest_worker_generation_seconds{status="success|failed"}` — длительность генерации.
- `digest_worker_send_seconds{status="success|failed|telegram_error"}` — длительность отправки.
- `group_digest_metrics` — сохраненные значения настроений, индикаторов.
- Ошибки multi-agent пайплайна пишутся в `group_digests.payload.errors`.

## Требования и ограничения

- Все вызовы LLM/embeddings идут через GigaChat (`gigachain`).
- Нет зависимости от локальных GPU или Ollama.
- Сессии Telethon и Postgres мультиарендные (контроль `tenant_id` на каждом этапе).
- В случае ошибок генерации/доставки событие не ретраится автоматически, запись переносится в DLQ (`digests.generate.dlq`) с указанием причины.

## Интеграция с планом разработки

- Соответствует фазам 4–5 из `group-f09746`.
- Новые модели БД описаны в `docs/DATABASE_SCHEMA.md`.
- Обновленные контракты событий — `docs/API_CONTRACTS.md`.

