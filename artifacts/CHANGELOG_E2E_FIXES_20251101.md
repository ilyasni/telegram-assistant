# Changelog: Исправления E2E тестов

**Дата**: 2025-11-01  
**Версия**: 1.0.0

---

## Изменения

### worker/tasks/indexing_task.py

**Изменение**: Обработка "Post not found" изменена с `failed` на `skipped`

- Статус индексации для удалённых постов: `failed` → `skipped`
- Добавлено structured logging с полем `reason`
- Добавлены метрики `indexing_processed_total{status="skipped"}`
- Пост помечается как обработанный для избежания повторных попыток

**Context7 маркер**: `[C7-ID: indexing-graceful-001]`

**Строки**: 358-373

---

### worker/ai_providers/embedding_service.py

**Изменение**: Добавлен health check для gpt2giga-proxy

- Новый метод `_check_proxy_health()` с кэшированием (TTL 30 секунд)
- Health check перед каждым запросом embeddings
- Использует `/v1/models` endpoint согласно документации gpt2giga
- Улучшено structured logging для диагностики

**Context7 маркер**: `[C7-ID: gigachat-resilience-001]`

**Строки**: 106-153, 245-251

---

## Контекст исправлений

1. **Retry механизм**: Уже исправлен ранее (убрана неправильная распаковка tuple)
2. **"Post not found"**: Теперь помечается как skipped, а не failed
3. **gpt2giga-proxy**: Добавлен proactive health check перед запросами

---

## Ожидаемые результаты

- Снижение количества failed постов на ~63 (17 retry + 46 not found)
- Улучшенная диагностика проблем с gpt2giga-proxy
- Graceful degradation для ожидаемых ситуаций (удалённые посты)

---

## Совместимость

- Обратная совместимость: да
- Требует миграции БД: нет
- Требует пересборки контейнера: да (worker)

---

## Проверка

После применения исправлений:

1. Worker контейнер пересобран и перезапущен ✅
2. Smoke тест пройден ✅
3. Логи не содержат ошибок retry ✅

Рекомендуется выполнить полные E2E тесты для проверки всех улучшений.

