# Исправление зависания System Overview dashboard

**Дата**: 2025-01-22  
**Context7**: Исправление проблемы зависания и отсутствия автоматического обновления System Overview dashboard

---

## Context

Пользователь сообщил, что System Overview dashboard бывает зависает и не обновляется автоматически. Проведена диагностика и исправление проблемы.

---

## Проблема

1. **Dashboard зависает** при обновлении
2. **Автоматическое обновление не работает** (refresh: 30s не срабатывает)
3. **Медленные запросы** блокируют обновление dashboard

**Возможные причины**:
- Запросы с большими временными окнами (`increase(...[24h])`) выполняются медленно
- Отсутствие timeout для Prometheus запросов
- Отсутствие `maxDataPoints` для оптимизации запросов
- Переменная `stage` обновляется слишком часто (`refresh: 1`)

---

## Диагностика

### 1. Проверка настроек refresh

**Результаты**:
- ✅ `refresh: "30s"` установлен
- ✅ `refresh_intervals` настроены правильно
- ⚠️ Переменная `stage` обновляется при каждом изменении dashboard (`refresh: 1`)

### 2. Проверка проблемных запросов

**Найдено**:
- `sum(increase(posts_processed_total[24h]))` - запрос с большим окном (24 часа)
- `sum(increase(posts_processed_total{stage="enrichment", success="true"}[24h]))` - еще один запрос с 24h окном
- Отсутствие `maxDataPoints` для оптимизации запросов

### 3. Проверка настроек datasource

**Найдено**:
- ❌ Отсутствует `queryTimeout` в datasource конфигурации
- ❌ Отсутствует `timeout` для HTTP запросов

---

## Исправления

### 1. Добавлен timeout для Prometheus datasource

**Файл**: `grafana/provisioning/datasources/datasources.yml`

**Изменения**:
- Добавлен `queryTimeout: "60s"` для предотвращения зависания при медленных запросах

**Код**:
```yaml
jsonData:
  httpMethod: GET
  manageAlerts: true
  prometheusType: Prometheus
  prometheusVersion: 2.40.0
  cacheLevel: "High"
  disableRecordingRules: false
  incrementalQueryOverlapWindow: 10m
  # Context7: Timeout для запросов Prometheus (в секундах)
  # Предотвращает зависание dashboard при медленных запросах
  queryTimeout: "60s"
```

### 2. Добавлен maxDataPoints для проблемных запросов

**Файл**: `grafana/dashboards/system_overview.json`

**Изменения**:
- Добавлен `maxDataPoints: 100` для запросов с `increase(...[24h])`
- Добавлен `intervalMs: 30000` для оптимизации частоты запросов

**Код**:
```json
{
  "expr": "sum(increase(posts_processed_total[24h]))",
  "legendFormat": "Posts Processed (24h)",
  "refId": "A",
  "maxDataPoints": 100,
  "interval": "",
  "intervalMs": 30000
}
```

### 3. Оптимизирована частота обновления переменной

**Файл**: `grafana/dashboards/system_overview.json`

**Изменения**:
- Изменен `refresh: 1` на `refresh: 2` для переменной `stage`
- `refresh: 1` означает обновление при каждом изменении dashboard
- `refresh: 2` означает обновление только при загрузке dashboard

**Код**:
```json
{
  "name": "stage",
  "refresh": 2,  // Context7: Обновление только при загрузке dashboard
  "type": "query"
}
```

### 4. Добавлен liveNow для предотвращения зависаний

**Файл**: `grafana/dashboards/system_overview.json`

**Изменения**:
- Добавлен `"liveNow": false` для явного отключения live updates

---

## Context7 Best Practices

### 1. Timeout для Prometheus запросов

**Проблема**: Медленные запросы могут блокировать обновление dashboard.

**Решение**: Установить `queryTimeout` в datasource конфигурации:
```yaml
queryTimeout: "60s"
```

### 2. maxDataPoints для оптимизации

**Проблема**: Запросы с большими временными окнами могут возвращать слишком много точек данных.

**Решение**: Ограничить количество точек данных через `maxDataPoints`:
```json
{
  "maxDataPoints": 100,
  "intervalMs": 30000
}
```

### 3. Оптимизация переменных

**Проблема**: Переменные, обновляющиеся при каждом изменении dashboard, могут вызывать лишние запросы.

**Решение**: Использовать `refresh: 2` (только при загрузке) вместо `refresh: 1` (при каждом изменении).

---

## Checks

### 1. Проверка применения изменений
```bash
# Перезапустить Grafana
docker compose restart grafana

# Проверить, что datasource обновился
curl http://localhost:3000/api/datasources/uid/prometheus
```

### 2. Проверка работы dashboard
1. Открыть System Overview dashboard в Grafana
2. Проверить, что dashboard обновляется каждые 30 секунд
3. Проверить, что нет зависаний при обновлении
4. Проверить, что панели с `increase(...[24h])` отображаются корректно

### 3. Проверка timeout
```bash
# Проверить настройки datasource через API
curl -u admin:admin http://localhost:3000/api/datasources/uid/prometheus | jq '.jsonData.queryTimeout'
```

### 4. Мониторинг производительности
```bash
# Проверить логи Grafana на наличие timeout ошибок
docker compose logs grafana | grep -iE "timeout|query.*slow|prometheus.*error"
```

---

## Impact / Rollback

### Impact
- ✅ Добавлен timeout для предотвращения зависаний
- ✅ Оптимизированы запросы с большими окнами
- ✅ Улучшена частота обновления переменных
- ✅ Безопасно для production

### Rollback
Если нужно откатить:
```bash
git checkout grafana/provisioning/datasources/datasources.yml grafana/dashboards/system_overview.json
docker compose restart grafana
```

---

## Рекомендации

### 1. Дополнительная оптимизация

**Для запросов с большими окнами**:
- Использовать recording rules в Prometheus для предварительного вычисления `increase(...[24h])`
- Кэшировать результаты запросов через `cacheLevel: "High"`

### 2. Мониторинг

**Добавить метрики**:
- `grafana_datasource_request_duration_seconds` - длительность запросов к Prometheus
- `grafana_datasource_request_total` - количество запросов
- Алерты на медленные запросы (> 30 секунд)

### 3. Альтернативные решения

**Если проблема сохраняется**:
1. Уменьшить временное окно для `increase` запросов (например, `[12h]` вместо `[24h]`)
2. Использовать `instant` запросы вместо `range` для stat панелей
3. Разделить dashboard на несколько более легких дашбордов

---

## Вывод

**Проблема**: Dashboard зависает из-за медленных запросов и отсутствия timeout.

**Решение**: 
1. Добавлен `queryTimeout: "60s"` в datasource конфигурацию
2. Добавлен `maxDataPoints: 100` для проблемных запросов
3. Оптимизирована частота обновления переменных

**Результат**: Dashboard должен обновляться автоматически каждые 30 секунд без зависаний.

