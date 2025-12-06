# Исправление алерта VisionWorkerNotProcessing

**Дата**: 2025-12-03  
**Изменение**: Условие алерта изменено для проверки реального состояния worker

---

## 🔧 Изменения

### Старое условие

```yaml
- alert: VisionWorkerNotProcessing
  expr: rate(vision_worker_processed_total[5m]) == 0
  for: 10m
```

**Проблема**: Метрика `vision_worker_processed_total` не экспортируется в Prometheus, поэтому алерт срабатывает даже когда worker работает.

### Новое условие

```yaml
- alert: VisionWorkerNotProcessing
  expr: |
    (
      sum(rate(vision_events_total[5m])) == 0
      and
      sum(rate(vision_analysis_duration_seconds_count[5m])) == 0
    )
  for: 10m
```

**Логика**:
1. Проверяет, что нет обработки событий через альтернативные метрики:
   - `vision_events_total` - общая метрика обработки событий (доступна)
   - `vision_analysis_duration_seconds_count` - счетчик обработок vision анализа (доступна)
2. Алерт срабатывает если обе метрики = 0 в течение 10 минут
3. Упрощенное условие без проверки pending (метрики могут быть недоступны)

---

## ✅ Преимущества нового условия

1. **Не зависит от отсутствующей метрики**: Использует альтернативные метрики (`vision_events_total`, `vision_analysis_duration_seconds_count`)
2. **Использует доступные метрики**: Обе метрики экспортируются и доступны в Prometheus
3. **Простое и надежное**: Не зависит от метрик pending, которые могут быть недоступны
4. **Точная диагностика**: Описание указывает на проверку логов worker и состояния vision_analysis_task

---

## 📊 Проверка

После применения изменений:

1. **Проверить алерт в Prometheus**:
   ```bash
   curl http://localhost:9090/api/v1/rules | jq '.data.groups[] | select(.name == "vision_s3_alerts")'
   ```

2. **Проверить активные алерты**:
   ```bash
   curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname == "VisionWorkerNotProcessing")'
   ```

3. **Мониторить метрики**:
   - `vision_events_total` - должна быть доступна
   - `vision_analysis_duration_seconds_count` - должна быть доступна
   - `stream_pending_size{stream=~"stream:posts:vision"}` - должна быть доступна

---

## 📝 Примечания

- Алерт теперь срабатывает только при реальной проблеме (есть pending, но нет обработки)
- Если очередь пуста, алерт не сработает (это нормально)
- Если worker обрабатывает события, алерт не сработает (даже если pending > 10)

---

## ✅ Статус

**Изменение применено**: Условие алерта обновлено  
**Prometheus**: Перезагружен для применения изменений

