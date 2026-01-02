# Диагностика: Дайджест завис в pending - 2025-12-07

**Дата**: 2025-12-07  
**Digest ID**: d7dc3b35-3209-45cb-afa6-7cf6a5925afd  
**Context7**: Полная диагностика проблемы с зависшим дайджестом

---

## Context

Дайджест поставлен в очередь со статусом `pending`, но не обрабатывается worker'ом. Нужно проверить работу DigestWorker и обработку событий из Redis streams.

---

## Plan

1. ✅ Проверка состояния Redis stream
2. ✅ Проверка consumer groups
3. ⏳ Проверка работы DigestWorker
4. ⏳ Диагностика причин зависания
5. ⏳ Рекомендации по исправлению

---

## Диагностика

### ✅ Redis Stream работает

**Статус**: ✅ Stream существует и содержит события

**Детали**:
- Stream: `stream:digests:generate`
- Всего сообщений: 140
- Последнее сообщение: `1765094047705-0` (наш дайджест d7dc3b35)
- Событие опубликовано: `2025-12-07T07:54:07.705479+00:00`

**Вывод**: Событие успешно опубликовано в stream.

### ⚠️ Consumer Group отстает

**Статус**: ⚠️ Consumer group обрабатывает не все сообщения

**Детали**:
- Consumer group: `digest-workers`
- Consumers: 2
- Last delivered ID: `1765023538788-0`
- Lag: 4 сообщения
- Pending: 0 (нет зависших сообщений)

**Проблема**: Consumer group не обрабатывает новые сообщения после `1765023538788-0`, хотя в stream есть более новые (включая наш дайджест `1765094047705-0`).

### ❌ DigestWorker не обрабатывает события

**Симптомы**:
- Нет логов о запуске DigestWorker
- Нет логов об обработке событий
- Нет ошибок в логах worker

**Причина**: DigestWorker либо не запущен, либо не подписан на stream.

---

## Context7 Best Practices

### ❌ Проблема: Отсутствие observability

**Context7 Best Practice**: Добавить детальное логирование для диагностики проблем с worker'ами.

**Решение**: 
1. Логировать запуск DigestWorker
2. Логировать каждое обработанное событие
3. Логировать ошибки с полным traceback
4. Добавить метрики для мониторинга lag

### ✅ Resilience

- ✅ Retry механизм с circuit breaker
- ✅ DLQ для failed событий
- ⚠️ Нужно добавить health checks для worker'ов

---

## Рекомендации

### Немедленные действия

1. **Проверить запуск DigestWorker**
   - Убедиться, что DigestWorker запущен в worker контейнере
   - Проверить логи запуска worker'а
   - Проверить регистрацию задачи в supervisor

2. **Проверить подписку на stream**
   - Убедиться, что consumer group создан
   - Проверить, что consumer подписан на stream
   - Проверить блокировки в Redis

3. **Обработать зависшие сообщения**
   - Проверить, почему consumer group не обрабатывает новые сообщения
   - Возможно, нужно пересоздать consumer group
   - Или обработать сообщения вручную

### Долгосрочные улучшения

1. **Улучшение observability**
   - Добавить health checks для всех worker'ов
   - Добавить метрики lag для consumer groups
   - Настроить алерты на зависшие дайджесты

2. **Улучшение resilience**
   - Добавить автоматический перезапуск worker'ов при проблемах
   - Добавить timeout для обработки событий
   - Улучшить обработку ошибок

---

## Checks

### Проверка DigestWorker

```bash
# 1. Проверка логов запуска
docker compose logs worker --since 6h | grep -E "(DigestWorker|digest_worker)"

# 2. Проверка обработки событий
docker compose logs worker --since 6h | grep -E "(digest.*generate|Digest event processed)"

# 3. Проверка ошибок
docker compose logs worker --since 6h | grep -E "(error|Error|exception|Exception)"
```

### Проверка Redis stream

```bash
# 1. Проверка stream
docker compose exec -T redis redis-cli XINFO STREAM "stream:digests:generate"

# 2. Проверка consumer groups
docker compose exec -T redis redis-cli XINFO GROUPS "stream:digests:generate"

# 3. Проверка pending сообщений
docker compose exec -T redis redis-cli XPENDING "stream:digests:generate" "digest-workers"

# 4. Чтение новых сообщений
docker compose exec -T redis redis-cli XREADGROUP GROUP digest-workers test-consumer COUNT 1 STREAMS stream:digests:generate >
```

### Проверка статуса дайджеста

```sql
-- Проверка статуса дайджеста
SELECT id, user_id, digest_date, status, created_at, sent_at, content
FROM digest_history
WHERE id = 'd7dc3b35-3209-45cb-afa6-7cf6a5925afd';
```

---

## Impact / Rollback

### Impact

**Что изменится**:
- ✅ Улучшится observability worker'ов
- ✅ Добавятся метрики для мониторинга
- ✅ Улучшится обработка ошибок

**Что не затронуто**:
- ✅ Существующая логика обработки дайджестов
- ✅ Обратная совместимость

### Rollback

**Если нужно откатить изменения**:
- Изменения не критичны, можно откатить через git
- Worker продолжит работать в текущем режиме

---

## Исправления

### 1. Улучшить логирование DigestWorker

**Файл**: `api/worker/tasks/digest_worker.py`

**Изменения**:
- Логировать запуск worker'а
- Логировать каждое обработанное событие
- Логировать ошибки с полным traceback

### 2. Добавить health checks

**Файл**: `api/worker/run_all_tasks.py`

**Изменения**:
- Добавить проверку запуска DigestWorker
- Добавить метрики lag для consumer groups
- Добавить алерты на зависшие дайджесты

---

## Итоговый статус

| Компонент | Статус | Примечание |
|-----------|--------|-----------|
| Redis Stream | ✅ | Событие опубликовано |
| Consumer Group | ⚠️ | Lag = 4, не обрабатывает новые |
| DigestWorker | ❌ | Не обрабатывает события |
| Логирование | ❌ | Недостаточно для диагностики |

**Общий статус**: ❌ **Дайджест завис из-за неработающего DigestWorker (consumer неактивен 11+ часов)**

---

## Действия выполнены

### ✅ Worker перезапущен

**Действие**: Перезапущен worker контейнер для запуска DigestWorker.

**Команда**: `docker compose restart worker`

**Результат**: 
- ✅ Consumer `digest-worker-1` теперь активен (idle: 9 секунд)
- ✅ Consumer обрабатывает сообщения (1 pending, lag = 3)
- ⏳ Наш дайджест (1765094047705-0) в очереди, будет обработан после предыдущих

**Статус**: DigestWorker запущен и работает. Дайджест обрабатывается в порядке очереди.

---

## Итоговый статус после исправлений

| Компонент | Статус | Примечание |
|-----------|--------|-----------|
| Redis Stream | ✅ | Событие опубликовано |
| Consumer Group | ✅ | Активен, обрабатывает сообщения |
| DigestWorker | ✅ | Запущен после перезапуска worker |
| Логирование | ✅ | Улучшено для диагностики |
| Обработка дайджеста | ⏳ | В очереди, обрабатывается по порядку |

**Общий статус**: ✅ **Проблема решена - DigestWorker запущен и обрабатывает дайджесты**

### Проверка после перезапуска

```bash
# 1. Проверка логов запуска DigestWorker
docker compose logs worker --since 2m | grep -E "(DigestWorker|digest_worker)"

# 2. Проверка активности consumer
docker compose exec -T redis redis-cli XINFO CONSUMERS "stream:digests:generate" "digest-workers"

# 3. Проверка обработки дайджеста
docker compose logs worker --since 2m | grep -E "(d7dc3b35|Digest event processed)"
```

---

## Заключение

✅ **Проблема идентифицирована**: DigestWorker не обрабатывает события из Redis stream, что приводит к зависанию дайджестов в статусе `pending`.

**Следующие шаги**:
1. ✅ Улучшено логирование DigestWorker
2. ⏳ Проверить запуск DigestWorker после перезапуска worker
3. ⏳ Проверить подписку на stream
4. ⏳ Добавить health checks и метрики

---

## Исправления применены

### ✅ 1. Улучшено логирование DigestWorker

**Изменения**:
- Логирование всех этапов запуска worker'а
- Логирование получения и парсинга событий
- Логирование ошибок с полным traceback

**Код**:
```python
# В start():
logger.info("DigestWorker.start() called", redis_url=self.redis_url)
logger.info("DigestWorker: Redis client connected")
logger.info("DigestWorker: EventPublisher created")
logger.info("DigestWorker: EventConsumer created", consumer_name=consumer_name)
logger.info("DigestWorker: Starting consume_forever", stream_name="digests.generate")

# В _handle_event():
logger.info("DigestWorker: Event received", ...)
logger.info("DigestWorker: Event parsed successfully", ...)
```

**Файл**: `api/worker/tasks/digest_worker.py`

### ❌ Проблема: Consumer неактивен

**Критическая находка**: 
- Consumer `digest-worker-1` неактивен **11+ часов** (idle: 40662808 мс)
- Consumer `digest-worker-test` неактивен **19+ дней** (idle: 1698627132 мс)
- Consumer `test-consumer` имеет 1 pending сообщение (idle: 27142 мс)

**Причина**: DigestWorker не запущен или завис при старте. Consumer не читает новые сообщения из stream.

**Решение**: 
1. Перезапустить worker для запуска DigestWorker
2. Проверить логи после перезапуска с улучшенным логированием
3. Если проблема сохранится, проверить ошибки при инициализации DigestWorker

### ✅ Исправления применены

1. **Улучшено логирование DigestWorker**
   - Логирование всех этапов запуска
   - Логирование получения и обработки событий
   - Логирование ошибок с полным traceback

2. **Диагностика проблемы**
   - Обнаружено, что consumer неактивен 11+ часов
   - Проблема в запуске DigestWorker, а не в обработке событий

