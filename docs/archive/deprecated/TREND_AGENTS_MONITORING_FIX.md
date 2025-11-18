# Исправление Trend Agents Monitoring

**Дата**: 2025-01-17  
**Проблема**: В Trend Agents Monitoring не отображаются новые данные

---

## Проблема

В дашборде Trend Agents Monitoring не отображались новые данные из-за:
1. Запросы Prometheus возвращали пустые результаты при отсутствии данных
2. Отсутствие fallback значений в запросах
3. Недостаточная настройка временных опций

---

## Исправления

### 1. Добавлен fallback для всех запросов Prometheus

**Файл**: `grafana/dashboards/trend_agents.json`

**Изменения**:
- Добавлен `or vector(0)` ко всем запросам с `rate()` для предотвращения пустых результатов
- Это гарантирует, что даже при отсутствии новых данных будут отображаться нулевые значения вместо пустых графиков

**Пример**:
```promql
# Было:
sum(rate(trend_editor_requests_total[5m])) by (outcome)

# Стало:
sum(rate(trend_editor_requests_total[5m])) by (outcome) or vector(0)
```

### 2. Улучшена настройка временных опций

**Изменения**:
- Добавлены `time_options` для более гибкого выбора временного диапазона
- Удален дубликат `timepicker` секции

---

## Проверка исправлений

### 1. Проверка метрик в Prometheus

```bash
# Проверка метрик Trend Editor Agent
curl http://localhost:9090/api/v1/query?query=trend_editor_requests_total

# Проверка метрик Trend QA Agent
curl http://localhost:9090/api/v1/query?query=trend_qa_filtered_total
```

### 2. Проверка дашборда в Grafana

1. Откройте дашборд: `Trend Agents Monitoring`
2. Проверьте, что все панели отображают данные (даже если они нулевые)
3. Убедитесь, что данные обновляются каждые 30 секунд (refresh interval)

### 3. Проверка обновления метрик

```bash
# Проверка логов Trend Editor Agent
docker compose logs worker | grep -i "trend_editor"

# Проверка метрик через Prometheus API
curl http://localhost:9090/api/v1/query?query=rate(trend_editor_requests_total[5m])
```

---

## Возможные причины отсутствия данных

### 1. Метрики не обновляются

**Проверка**:
- Убедитесь, что Trend Editor Agent запущен: `docker compose ps worker`
- Проверьте логи на наличие ошибок: `docker compose logs worker | grep -i error`

### 2. Prometheus не собирает метрики

**Проверка**:
- Убедитесь, что Prometheus запущен: `docker compose ps prometheus`
- Проверьте targets в Prometheus UI: http://localhost:9090/targets
- Убедитесь, что worker target доступен и собирает метрики

### 3. Дашборд не обновляется

**Проверка**:
- Убедитесь, что refresh interval установлен на 30s
- Проверьте временной диапазон (должен быть "Last 1 hour")
- Попробуйте обновить дашборд вручную (F5)

---

## Дополнительные улучшения

### Рекомендации для мониторинга

1. **Добавить алерты** на отсутствие данных:
   ```promql
   absent(trend_editor_requests_total[5m])
   ```

2. **Добавить панель с общим количеством запросов**:
   ```promql
   sum(increase(trend_editor_requests_total[1h]))
   ```

3. **Добавить панель с последним временем обновления**:
   ```promql
   time() - max(trend_editor_requests_total)
   ```

---

## Откат изменений (если необходимо)

Если исправления вызвали проблемы:

```bash
# Откат изменений в дашборде
git checkout HEAD -- grafana/dashboards/trend_agents.json

# Перезапуск Grafana для применения изменений
docker compose restart grafana
```

---

## Контакты

При возникновении проблем:
1. Проверить логи: `docker compose logs worker | grep -i trend`
2. Проверить Prometheus targets: http://localhost:9090/targets
3. Проверить метрики напрямую: http://localhost:8001/metrics (worker)

