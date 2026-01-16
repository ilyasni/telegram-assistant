# Исправление алерта VisionWorkerNotProcessing

**Дата**: 2025-01-03  
**Статус**: ✅ Исправлено

---

## Контекст

Алерт `VisionWorkerNotProcessing` срабатывал даже когда worker работал корректно, но просто не было новых событий для обработки (очередь была пуста или все сообщения обработаны).

## Проблема

### Исходное условие алерта:
```yaml
expr: |
  (
    sum(rate(vision_events_total[5m])) == 0
    and
    sum(rate(vision_analysis_duration_seconds_count[5m])) == 0
  )
```

**Проблема**: Алерт срабатывал даже когда:
- Очередь пуста (нет новых событий)
- Все сообщения обработаны (lag = 0, pending = 0)
- Worker работает, но просто нет работы

### Диагностика

1. **Очередь**: `stream:posts:vision` содержит 6998 сообщений, но все обработаны:
   - `lag: 0` - нет отставания
   - `pending: 0` - нет pending сообщений
   - `last-delivered-id: 1764792680979-0` - последнее сообщение обработано

2. **Метрики**:
   - `vision_events_total` = 8 (накопленное значение)
   - `vision_analysis_duration_seconds_count` = 45 (накопленное значение)
   - `rate()` = 0 за последние 5 минут (нет новых событий)

3. **Worker**: Работает корректно, но нет новых событий для обработки

## Решение

### Исправленное условие алерта:
```yaml
expr: |
  (
    sum(rate(vision_events_total[5m])) == 0
    and
    sum(rate(vision_analysis_duration_seconds_count[5m])) == 0
    and
    sum(vision_pel_size) > 0
  )
```

**Изменения**:
- Добавлена проверка `sum(vision_pel_size) > 0` - алерт срабатывает только если есть pending сообщения
- Если очередь пуста (vision_pel_size = 0), то отсутствие активности - это нормально

### Улучшенное описание:
```yaml
description: |
  Нет обработки событий за последние 10 минут при наличии pending сообщений (vision_pel_size > 0).
  Проверить:
  1. Логи worker: docker logs telegram-assistant-worker-1 | grep -i vision
  2. Состояние очереди: docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:posts:vision
  3. Pending сообщения: docker exec telegram-assistant-redis-1 redis-cli XPENDING stream:posts:vision vision_workers
  Если очередь пуста (vision_pel_size = 0), то отсутствие активности - это нормально.
```

## Результат

✅ **Алерт исправлен** - теперь срабатывает только при реальной проблеме:
- Есть pending сообщения (vision_pel_size > 0)
- И нет обработки событий (rate = 0)

✅ **Ложные срабатывания устранены** - алерт не срабатывает когда:
- Очередь пуста (vision_pel_size = 0)
- Все сообщения обработаны (lag = 0)

## Проверка

1. **Проверить условие алерта**:
   ```bash
   curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[] | select(.name | contains("vision")) | .rules[] | select(.name | contains("VisionWorkerNotProcessing"))'
   ```

2. **Проверить метрики**:
   ```bash
   curl -s "http://localhost:9090/api/v1/query?query=vision_pel_size"
   curl -s "http://localhost:9090/api/v1/query?query=rate(vision_events_total[5m])"
   ```

3. **Проверить состояние очереди**:
   ```bash
   docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:posts:vision
   ```

## Следующие шаги

1. ✅ Алерт исправлен и перезагружен в Prometheus
2. Мониторить алерт в течение 24 часов для подтверждения отсутствия ложных срабатываний
3. При необходимости можно добавить дополнительные проверки (например, проверка последнего обновления метрик)

---

**Дата исправления**: 2025-01-03  
**Статус**: ✅ Исправлено

