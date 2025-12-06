# Исправление проблемы SchedulerFreshness: застрявший lock после перезапуска

**Дата**: 2025-12-05  
**Проблема**: Scheduler застрял из-за lock, оставшегося после перезапуска контейнера

---

## Context

Пользователь сообщил, что давно не было новых постов. Диагностика показала:
- Scheduler Freshness превысил 15 минут (947 секунд)
- Lock установлен, но ticks не завершаются
- Контейнер перезапустился, но lock остался в Redis

---

## Проблема

### Симптомы

1. **Scheduler Freshness критически высокий**: 947 секунд (15+ минут)
2. **Lock застрял**: Lock установлен со значением `6a21a6484468` (HOSTNAME контейнера)
3. **Ticks не завершаются**: 6 попыток запустить tick, 0 завершенных
4. **Много ошибок**: 118 ошибок, 20 таймаутов за последние 10 минут

### Причина

При перезапуске контейнера:
1. Lock остался в Redis с TTL ~600 секунд
2. Новый экземпляр имеет тот же HOSTNAME (`6a21a6484468`)
3. Логика `_acquire_lock()` использует `nx=True` (set if not exists)
4. Если lock уже существует, он не может быть перезаписан
5. Новый экземпляр не может получить lock, даже если он принадлежит ему же

**Результат**: Scheduler не может работать, так как не может получить lock.

---

## Решение

### 1. Немедленное исправление

Сбросил lock вручную:
```bash
docker compose exec -T redis redis-cli DEL "parse_all_channels:lock"
```

### 2. Долгосрочное исправление

Улучшена логика `_acquire_lock()` в `telethon-ingest/tasks/parse_all_channels_task.py`:

**Изменения**:
- Добавлена проверка: если lock принадлежит тому же `instance_id`, это означает, что контейнер перезапустился
- В этом случае lock перезаписывается с новым TTL (reclaim)
- Добавлена метрика `scheduler_lock_acquired_total` со статусом `reclaimed`

**Код**:
```python
# Проверяем, если lock принадлежит тому же instance_id
if existing_lock == instance_id:
    logger.warning("Lock held by same instance_id (container restarted), reclaiming lock")
    # Перезаписываем lock с новым TTL
    await self.redis.set(lock_key, instance_id, ex=ttl)
    scheduler_lock_acquired_total.labels(status="reclaimed").inc()
    return True
```

---

## Проверка

### Команды для проверки

1. **Проверить состояние scheduler**:
```bash
bash scripts/check_scheduler_freshness.sh
```

2. **Проверить lock**:
```bash
docker compose exec -T redis redis-cli GET "parse_all_channels:lock"
docker compose exec -T redis redis-cli TTL "parse_all_channels:lock"
```

3. **Проверить логи**:
```bash
docker compose logs telethon-ingest --since 5m | grep -iE "(lock|tick|scheduler)"
```

### Ожидаемое поведение

После исправления:
- ✅ Scheduler должен автоматически перезаписать lock при перезапуске контейнера
- ✅ Lock должен освобождаться после завершения tick'а
- ✅ Метрика `scheduler_lock_acquired_total{status="reclaimed"}` должна увеличиваться при перезапуске

---

## Impact / Rollback

### Затронутые компоненты

- `telethon-ingest/tasks/parse_all_channels_task.py`: метод `_acquire_lock()`
- Метрика `scheduler_lock_acquired_total`: новый статус `reclaimed`

### Риски

- **Низкий риск**: Изменение только улучшает логику получения lock
- **Обратная совместимость**: Полная, старые статусы (`acquired`, `missed`) остаются

### Rollback

Если возникнут проблемы, можно откатить изменения:
```bash
git checkout telethon-ingest/tasks/parse_all_channels_task.py
docker compose restart telethon-ingest
```

---

## Рекомендации

### Мониторинг

1. **Добавить алерт** для частых reclaim'ов lock'а (может указывать на частые перезапуски):
```yaml
- alert: SchedulerLockReclaimedFrequently
  expr: rate(scheduler_lock_acquired_total{status="reclaimed"}[5m]) > 0.1
  for: 5m
  labels:
    severity: warning
    component: scheduler
  annotations:
    summary: "Scheduler lock часто перезаписывается (контейнер часто перезапускается)"
```

2. **Мониторить метрику** `scheduler_lock_acquired_total{status="reclaimed"}` в Grafana

### Дополнительные улучшения (опционально)

1. **Heartbeat механизм**: Добавить периодическое обновление lock'а (heartbeat) во время выполнения tick'а
2. **Lock ownership verification**: Проверять, что lock действительно принадлежит текущему процессу перед освобождением

---

## Статус

✅ **Исправлено**: Lock теперь автоматически перезаписывается при перезапуске контейнера  
✅ **Проверено**: Линтер не нашел ошибок  
⏳ **Ожидается**: Scheduler должен начать работать после следующего tick'а

---

**Следующие шаги**:
1. Мониторить работу scheduler'а в течение следующих 10-15 минут
2. Проверить, что новые посты начинают появляться
3. Рассмотреть добавление алерта для частых reclaim'ов lock'а

