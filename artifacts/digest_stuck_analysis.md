# Анализ зависших дайджестов

**Дата:** 2025-11-25  
**Статус:** ⚠️ **Обнаружены зависшие дайджесты**

---

## Context

Проверка статуса дайджестов показала:
1. Дайджест `dac58a5d-522f-4e94-b803-caee803b53c6` в статусе `failed`
2. 3 старых дайджеста в статусе `pending` (возраст 8-13 дней)
3. Воркер дайджестов не обрабатывает события

---

## Наблюдения

### 1. Дайджест dac58a5d-522f-4e94-b803-caee803b53c6

- **Статус:** `failed`
- **Создан:** 2025-11-25 15:36:57 MSK
- **Posts count:** 0
- **Content length:** 0 (пустой)
- **Topics count:** 3
- **Проблема:** Дайджест упал при генерации, контент не создан

### 2. Старые зависшие дайджесты

| ID | Status | Digest Date | Created | Age (minutes) |
|---|---|---|---|---|
| `680d90bb-0f07-49a4-96f3-ad242cf0bde6` | pending | 2025-11-17 | 2025-11-17 13:06:25 | 11688 (8 дней) |
| `8d4290ce-ca30-46c2-8940-74603b317d3f` | pending | 2025-11-12 | 2025-11-12 16:17:44 | 18697 (13 дней) |
| `7a00bbf1-4482-4243-92a3-f6fa1e126a6e` | pending | 2025-11-12 | 2025-11-12 15:41:53 | 18733 (13 дней) |

**Проблема:** Дайджесты в статусе `pending` более недели - явно зависли

### 3. Очередь Redis

- **Стрим:** `stream:digests:generate` существует
- **Сообщений:** 65
- **Consumer group:** `digest-workers` (2 consumers)
- **Lag:** 0 (все обработано)
- **Pending:** 0 (нет зависших сообщений)
- **Последнее событие:** 2025-11-11 (14 дней назад)

**Проблема:** Новые события дайджестов не попадают в очередь

### 4. Воркер дайджестов

- **Логи:** Нет активности по дайджестам в последний час
- **Обработка:** Только посты (`posts.indexed`, `posts.tagged`, `posts.enriched`)
- **Проблема:** Воркер не обрабатывает события `digests.generate`

---

## Причины

### 1. Дайджест упал при генерации

**Возможные причины:**
- Отсутствие постов для генерации (`posts_count = 0`)
- Ошибка при вызове LLM
- Ошибка при отправке в Telegram
- Отсутствие `telegram_id` у пользователя

### 2. Старые зависшие дайджесты

**Возможные причины:**
- События не попали в очередь Redis
- Воркер не обрабатывал события в момент создания
- Ошибка при обработке, но статус не обновился

### 3. Новые события не попадают в очередь

**Возможные причины:**
- `EventPublisher` не публикует события
- Неправильное имя стрима
- Ошибка при публикации (тихо игнорируется)

---

## Решение

### 1. Очистка зависших дайджестов

```sql
-- Пометить старые pending дайджесты как failed
UPDATE digest_history
SET status = 'failed'
WHERE status = 'pending'
  AND created_at < NOW() - INTERVAL '7 days';
```

### 2. Проверка воркера дайджестов

```bash
# Проверить, запущен ли DigestWorker
docker logs telegram-assistant-worker-1 | grep -i "DigestWorker"

# Проверить обработку событий
docker logs telegram-assistant-worker-1 | grep -i "digest.*generate"
```

### 3. Ручной перезапуск дайджеста

```python
# Через API или скрипт
from api.tasks.scheduler_tasks import generate_digest_for_user

history = await generate_digest_for_user(
    user_id="6af5e7d3-e736-46da-bfc1-42786f2ab1c0",
    tenant_id="7df762e3-99b8-44d7-9a8e-08452d11ba90",
    topics=["тема1", "тема2", "тема3"],
    db=db,
    trigger="manual"
)
```

### 4. Проверка публикации событий

```python
# Добавить логирование в generate_digest_for_user
logger.info(
    "Digest event published",
    stream_name="digests.generate",
    event_id=event.idempotency_key,
    history_id=str(digest_history.id)
)
```

---

## Checks

### Проверка текущего состояния

```bash
# Зависшие дайджесты
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT id, status, digest_date, created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow' as created_msk
FROM digest_history 
WHERE status = 'pending' 
  AND created_at < NOW() - INTERVAL '1 day'
ORDER BY created_at DESC;
"

# Недавние failed дайджесты
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT id, user_id, digest_date, status, created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow' as created_msk, posts_count
FROM digest_history 
WHERE status = 'failed' 
  AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
"

# Проверка очереди
docker exec telegram-assistant-redis-1 redis-cli XINFO STREAM stream:digests:generate
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:digests:generate
```

---

## Impact / Rollback

### Impact

- **Зависшие дайджесты:** Пользователи не получают дайджесты
- **Failed дайджесты:** Нужно выяснить причину падения
- **Очередь:** Новые события не обрабатываются

### Rollback

Если нужно откатить изменения:
1. Очистка зависших дайджестов - безопасна (они все равно не обработаются)
2. Перезапуск воркера - безопасен
3. Ручной перезапуск дайджеста - безопасен

---

## Рекомендации

1. **Немедленно:** Очистить зависшие дайджесты (пометить как `failed`)
2. **Краткосрочно:** Проверить, почему воркер не обрабатывает события
3. **Долгосрочно:** Добавить мониторинг зависших дайджестов (alert при `pending > 1 hour`)

