# Чек-лист раскатки группового дайджеста

## 1. Подготовка окружения

- [ ] Применить миграции БД (Alembic) для таблиц `group_*`.
- [ ] Обновить `.env`:
  - `DIGEST_AGENT_ENABLED=0` (оставляем выключенным на этапе canary).
  - `DIGEST_AGENT_CANARY_TENANTS=["demo-tenant-uuid"]` (CSV/JSON allow-list арендаторов).
  - `STREAM_GROUP_MESSAGE_CREATED=stream:groups:messages` (если используется конфиг).
  - Ключи GigaChat (`GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`, `GIGACHAT_BASE_URL`).
  - (Опционально) `DIGEST_CONTEXT_*` — если требуется отклониться от дефолтов Stage 5 (пороги dedup/top-k).
  - (Опционально) `DIGEST_CONTEXT_STORAGE_*` — включение Context7 Storage (URL, токен, history limits).
- [ ] Перезапустить сервисы: `telethon-ingest`, `worker`, `api`.
- [ ] Очистить consumer groups Redis: `worker`, `worker-group-messages`, `digest-workers`.

## 2. Canary (тенант `demo`)

- [ ] Добавить тестовую группу через `/api/groups`.
- [ ] Сгенерировать тестовые сообщения (минимум 20 штук).
- [ ] Вызвать `/api/groups/{id}/digest` (окно 4 часа).
- [ ] Проверить:
  - `group_conversation_windows.status = queued → completed`.
  - `group_digests.delivery_status = sent`.
  - Telegram бот доставил сообщение.
- [ ] Мониторинг:
  - `group_digest_evaluation_score{metric="faithfulness"} >= 0.7`.
  - Нет ошибок в `digest_worker_generation_seconds{status="failed"}`.
  - Метрики контекста: `digest_context_messages`, `digest_context_duplicates_total`.

## 3. Расширение покрытия

- [ ] Включить фичу для 10% тенантов (обновить `DIGEST_AGENT_CANARY_TENANTS`, оставить `DIGEST_AGENT_ENABLED=0`).
- [ ] Собрать обратную связь, оценить метрики качества.
- [ ] Обновить Grafana:
  - Dashboard `Group Digest Overview`.
  - Панель `Quality Scores` (faithfulness, relevance, completeness).
- [ ] Настроить PagerDuty алерты:
  - `faithfulness p95 < 0.6` (30 мин).
  - `delivery_status="failed"` > 5 за 10 минут.

## 4. Полный rollout

- [ ] Включить `DIGEST_AGENT_ENABLED=1` глобально (очистить canary-список).
- [ ] Обновить документацию (FAQ, onboarding).
- [ ] Уведомить поддержку/продакт-менеджеров.
- [ ] Закрыть canary флаг, оставить алерты активными.

## 5. Rollback план

| Шаг | Действие |
| --- | --- |
| 1 | Выключить `DIGEST_AGENT_ENABLED` (env/Configmap) |
| 2 | Остановить consumer group `worker-group-messages` |
| 3 | Сбросить незавершённые `group_digests` → статус `failed` |
| 4 | Очистить Redis stream `stream:groups:messages` (по необходимости) |
| 5 | Переключить UI на legacy-доступ (`/digest` каналов) |

- Проверить, что канальные дайджесты продолжают работать.

