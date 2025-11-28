# Исправление проверки подписки в AtomicDBSaver

**Дата**: 2025-11-27  
**Context7**: Исправление несоответствия в проверке подписки с добавлением observability

---

## Проблема

В `atomic_db_saver.py` проверка подписки не учитывала флаг `is_active`, что приводило к:
1. Несоответствию с логикой в `channel_parser.py` (где проверка включает `is_active = true`)
2. Отсутствию различения между "не подписан" и "подписка неактивна"
3. Отсутствию метрик для мониторинга проблем с подписками

## Исправление

### 1. Добавлена проверка `is_active`

**Файл**: `telethon-ingest/services/atomic_db_saver.py`

**Было**:
```python
check_subscription = await db_session.execute(
    text("""
        SELECT user_id FROM user_channel 
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1)
          AND channel_id = :channel_id
        LIMIT 1
    """),
    {"telegram_id": telegram_id, "channel_id": channel_id_uuid}
)

if not check_subscription.fetchone():
    return False, "user_not_subscribed", 0
```

**Стало**:
```python
# Context7: Проверка активной подписки (согласованно с channel_parser.py)
check_subscription = await db_session.execute(
    text("""
        SELECT user_id, is_active FROM user_channel 
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = :telegram_id LIMIT 1)
          AND channel_id = :channel_id
        LIMIT 1
    """),
    {"telegram_id": telegram_id, "channel_id": channel_id_uuid}
)

subscription_row = check_subscription.fetchone()
if not subscription_row:
    # Пользователь не подписан
    self.logger.warning("User not subscribed to channel, skipping post save",
                      channel_id=channel_id_uuid,
                      telegram_id=telegram_id,
                      reason="no_subscription")
    db_subscription_check_failures_total.labels(reason="no_subscription").inc()
    return False, "user_not_subscribed", 0

# Context7: Проверка активности подписки
if not subscription_row.is_active:
    # Подписка существует, но неактивна
    self.logger.warning("User subscription is inactive, skipping post save",
                      channel_id=channel_id_uuid,
                      telegram_id=telegram_id,
                      reason="subscription_inactive")
    db_subscription_check_failures_total.labels(reason="subscription_inactive").inc()
    return False, "subscription_inactive", 0
```

### 2. Добавлена метрика для observability

**Файл**: `telethon-ingest/services/atomic_db_saver.py`

```python
# Context7: Метрика для отслеживания проблем с подписками
db_subscription_check_failures_total = Counter(
    'db_subscription_check_failures_total',
    'Total subscription check failures',
    ['reason']  # 'no_subscription', 'subscription_inactive'
)
```

---

## Context7 Best Practices

### ✅ Согласованность
- Проверка подписки теперь согласована между `atomic_db_saver.py` и `channel_parser.py`
- Оба компонента проверяют `is_active = true`

### ✅ Observability
- Добавлена метрика `db_subscription_check_failures_total{reason}` для мониторинга
- Улучшено логирование с различением причин отказа

### ✅ Ясность
- Разделены случаи "не подписан" и "подписка неактивна"
- Логи содержат поле `reason` для диагностики

---

## Статистика

Из БД:
- Активных подписок: 56
- Неактивных подписок: 87
- Всего подписок: 143

Это объясняет, почему некоторые посты не сохраняются - у пользователей есть неактивные подписки.

---

## Checks

### Проверка исправления

1. **Проверка метрик**:
```bash
curl http://localhost:8000/metrics | grep db_subscription_check_failures_total
```

2. **Проверка логов**:
```bash
docker logs telegram-assistant-telethon-ingest-1 --tail 100 | grep -i "subscription"
```

3. **Проверка БД**:
```sql
SELECT 
    COUNT(*) FILTER (WHERE is_active = true) as active,
    COUNT(*) FILTER (WHERE is_active = false) as inactive
FROM user_channel;
```

---

## Impact / Rollback

### Impact
- Посты не будут сохраняться для неактивных подписок (ожидаемое поведение)
- Улучшена диагностика проблем с подписками через метрики
- Согласованность логики между компонентами

### Rollback
Если нужно откатить изменения:
1. Вернуть старую версию SQL запроса (без `is_active`)
2. Удалить метрику `db_subscription_check_failures_total`
3. Упростить логирование

---

## Рекомендации

1. **Активация подписок**: Проверить, почему у пользователей неактивные подписки
2. **Мониторинг**: Настроить алерты на `db_subscription_check_failures_total{reason="subscription_inactive"}`
3. **Очистка**: Рассмотреть удаление неактивных подписок старше N дней





