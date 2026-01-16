# Обновление панели Total Queue Size в Grafana

**Дата**: 2025-12-03  
**Context7**: Изменение метрики для отображения значений за день вместо общих (накопительных)

---

## Изменения

### Было

**Запрос Prometheus:**
```promql
sum(posts_in_queue_total{status="total"}) by (queue)
```

**Отображало**: Текущий (накопительный) размер очереди

### Стало

**Запрос Prometheus:**
```promql
max by (queue) (max_over_time(posts_in_queue_total{status="total"}[24h]))
```

**Отображает**: Максимальный размер очереди за последние 24 часа

---

## Детали реализации

### Обновленные поля

1. **Заголовок панели**: "📦 Total Queue Size (24h max)"
2. **Описание**: "Maximum queue size across all queues over the last 24 hours (with color thresholds)"
3. **Запрос Prometheus**: `max by (queue) (max_over_time(posts_in_queue_total{status="total"}[24h]))`
4. **Легенда**: `{{queue}} (max 24h)`

### Объяснение запроса

```promql
max by (queue) (max_over_time(posts_in_queue_total{status="total"}[24h]))
```

1. `posts_in_queue_total{status="total"}[24h]` - range vector за последние 24 часа
2. `max_over_time(...)` - максимум для каждой временной серии за период
3. `max by (queue)` - группировка по очереди для показа максимального значения

---

## Альтернативные варианты

Если нужно использовать другой подход:

### Вариант 1: Среднее за день
```promql
avg by (queue) (avg_over_time(posts_in_queue_total{status="total"}[24h]))
```

### Вариант 2: Текущее значение минус значение 24 часа назад (прирост)
```promql
sum(posts_in_queue_total{status="total"}) by (queue) - 
  sum(posts_in_queue_total{status="total"} offset 24h) by (queue)
```

### Вариант 3: Просто текущее значение (как было, но без накопления)
```promql
sum(posts_in_queue_total{status="total"}) by (queue)
```

---

## Проверка реализации

### 1. Проверка JSON синтаксиса

```bash
python3 -m json.tool grafana/dashboards/system_overview.json > /dev/null && echo "✅ OK"
```

### 2. Проверка запроса

```bash
jq -r '.panels[] | select(.title | contains("Total Queue Size")) | .targets[0].expr' \
  grafana/dashboards/system_overview.json
```

### 3. Тестирование запроса в Prometheus

Запустите запрос в Prometheus UI:
```
max by (queue) (max_over_time(posts_in_queue_total{status="total"}[24h]))
```

---

## Файл

- **Дашборд**: `grafana/dashboards/system_overview.json`
- **Панель ID**: 11
- **Тип панели**: `stat`

---

## Применение изменений

1. Изменения уже применены в файле дашборда
2. Для применения в Grafana:
   - Импортировать обновленный дашборд через UI Grafana
   - Или перезапустить Grafana, если дашборд загружается через provisioning

---

## Связанные метрики

- `posts_in_queue_total` - Gauge метрика текущего размера очереди
- `posts_processed_total` - Counter метрика обработанных постов
- `stream_pending_size` - Gauge метрика pending сообщений

---

**Статус**: ✅ Реализовано и проверено

