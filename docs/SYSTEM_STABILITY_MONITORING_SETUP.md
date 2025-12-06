# Настройка мониторинга стабильности системы

**Дата**: 2025-12-03  
**Статус**: Готово к использованию

---

## Обзор

Настроен комплексный мониторинг стабильности системы с дашбордами Grafana, метриками Prometheus и алертами.

### Компоненты мониторинга

1. **Grafana Dashboard** - визуализация метрик
2. **Prometheus Metrics** - сбор метрик
3. **AlertManager** - обработка алертов
4. **Health Endpoints** - API для проверки состояния

---

## Grafana Dashboard

### Доступ к дашборду

```bash
# URL дашборда (после настройки)
https://grafana.${DOMAIN}/d/system-stability

# Или через локальный доступ
http://localhost:3000/d/system-stability
```

### Панели дашборда

1. **Scheduler Status** - статус запуска scheduler (0/1)
2. **Scheduler Jobs** - количество активных jobs
3. **Scheduler Startup Duration** - время запуска scheduler (p95)
4. **Health Check Duration** - длительность health checks по типам (p95)
5. **Health Check Failures** - ошибки health checks по типам
6. **Health Check Cache Hits** - попадания в кэш
7. **Container Health Status** - статус health checks контейнеров
8. **Container Uptime** - время работы контейнеров
9. **Pipeline Posts Status** - статус постов (parsed/tagged/indexed)
10. **Redis Stream Pending Messages** - pending сообщения в streams
11. **Circuit Breaker State** - состояние circuit breakers
12. **Container Restarts** - частота перезапусков контейнеров

---

## Настройка Grafana

### 1. Проверка provisioning

Убедитесь, что файлы provisioning настроены:

```bash
# Проверить datasource
cat grafana/provisioning/datasources/datasources.yml

# Проверить dashboards provider
cat grafana/provisioning/dashboards/dashboards.yml
```

### 2. Перезапуск Grafana

```bash
# Перезапустить Grafana для загрузки нового дашборда
docker compose restart grafana

# Проверить логи
docker compose logs grafana | tail -20
```

### 3. Проверка дашборда

```bash
# Проверить, что дашборд загружен
# Открыть Grafana UI: http://localhost:3000
# Перейти в Dashboards → System Stability Monitoring
```

---

## Prometheus Metrics

### Новые метрики

#### Scheduler
- `scheduler_running` (Gauge) - статус запуска (0/1)
- `scheduler_startup_duration_seconds` (Histogram) - время запуска
- `scheduler_jobs_total` (Gauge) - количество jobs

#### Health Checks
- `health_check_duration_seconds` (Histogram) - длительность проверок
- `health_check_failures_total` (Counter) - ошибки проверок
- `health_check_cache_hits_total` (Counter) - попадания в кэш

#### Containers
- `container_restart_count_total` (Counter) - количество перезапусков
- `container_uptime_seconds` (Gauge) - время работы
- `container_health_status` (Gauge) - статус health check (0/1)

#### Pipeline
- `pipeline_posts_parsed_total` (Gauge) - всего постов распарсено
- `pipeline_posts_tagged_total` (Gauge) - всего постов с тегами
- `pipeline_posts_vision_total` (Gauge) - всего постов с vision
- `pipeline_posts_enriched_total` (Gauge) - всего постов обогащено
- `pipeline_posts_indexed_total` (Gauge) - всего постов проиндексировано
- `pipeline_lag_seconds` (Gauge) - lag между этапами
- `redis_stream_pending_messages` (Gauge) - pending сообщения

### Проверка метрик

```bash
# Проверить доступность метрик
curl http://localhost:8001/metrics | grep -E "(scheduler|health_check|container|pipeline)"

# Проверить в Prometheus UI
# Открыть: http://localhost:9090
# Найти метрику: scheduler_running
```

---

## Prometheus Alerts

### Настроенные алерты

Алерты добавлены в `prometheus/alerts.yml`:

1. **SchedulerNotRunning** (critical)
   - Условие: `scheduler_running == 0` более 1 минуты
   - Действие: Отправить критическое уведомление

2. **HealthCheckFailuresHigh** (warning)
   - Условие: `rate(health_check_failures_total[5m]) > 5` более 5 минут
   - Действие: Отправить предупреждение

3. **RedisStreamPendingHigh** (warning)
   - Условие: `redis_stream_pending_messages > 100` более 5 минут
   - Действие: Отправить предупреждение

4. **ContainerRestartsHigh** (warning)
   - Условие: `rate(container_restart_count_total[1h]) > 3` более 1 часа
   - Действие: Отправить предупреждение

### Проверка алертов

```bash
# Проверить статус алертов в Prometheus
curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname | contains("Scheduler") or contains("Health") or contains("Container"))'

# Или открыть Prometheus UI
# http://localhost:9090/alerts
```

### Настройка AlertManager

Для отправки уведомлений нужно настроить AlertManager:

```yaml
# prometheus/alertmanager.yml
route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 12h
  receiver: 'web.hook'
receivers:
  - name: 'web.hook'
    webhook_configs:
      - url: 'http://your-webhook-url'
```

---

## Health Endpoints

### API Endpoints

1. **GET /api/health** - общий health check
   - Проверка БД, Redis, Scheduler
   - С кэшированием и circuit breaker

2. **GET /api/monitoring/containers** - мониторинг контейнеров
   - Список всех контейнеров
   - Статус, uptime, недавние перезапуски

3. **GET /api/monitoring/containers/{service_name}** - статус конкретного контейнера

4. **GET /api/pipeline/health** - проверка пайплайна
   - Redis Streams, БД, Qdrant, Neo4j

5. **GET /api/metrics/summary** - сводка по метрикам
   - Агрегированная информация
   - Проблемы и рекомендации

### Примеры использования

```bash
# Health check
curl http://localhost:8000/api/health | jq

# Мониторинг контейнеров
curl http://localhost:8000/api/monitoring/containers | jq

# Pipeline health
curl http://localhost:8000/api/pipeline/health | jq

# Metrics summary
curl http://localhost:8000/api/metrics/summary | jq
```

---

## Быстрая проверка настроек

### 1. Проверить дашборд

```bash
# Проверить, что дашборд существует
ls -lh grafana/dashboards/system_stability.json

# Проверить структуру JSON
jq '.dashboard.uid' grafana/dashboards/system_stability.json
# Должно быть: "system-stability"
```

### 2. Проверить метрики

```bash
# Проверить доступность метрик
curl -s http://localhost:8001/metrics | grep scheduler_running
curl -s http://localhost:8001/metrics | grep health_check_duration
curl -s http://localhost:8001/metrics | grep container_uptime
```

### 3. Проверить алерты

```bash
# Проверить конфигурацию алертов
grep -A 10 "SchedulerNotRunning" prometheus/alerts.yml

# Проверить в Prometheus
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | .labels.alertname' | grep -i scheduler
```

### 4. Проверить endpoints

```bash
# Health check
curl -s http://localhost:8000/api/health | jq '.checks.scheduler'

# Containers
curl -s http://localhost:8000/api/monitoring/containers | jq '.total_services'

# Pipeline
curl -s http://localhost:8000/api/pipeline/health | jq '.status'

# Metrics summary
curl -s http://localhost:8000/api/metrics/summary | jq '.status'
```

---

## Автоматизация

### Скрипт быстрой проверки

```bash
#!/bin/bash
# Проверка всех компонентов мониторинга

echo "=== Проверка Grafana Dashboard ==="
ls -lh grafana/dashboards/system_stability.json && echo "✅ Дашборд существует"

echo ""
echo "=== Проверка метрик ==="
curl -s http://localhost:8001/metrics | grep -q scheduler_running && echo "✅ Метрики доступны" || echo "❌ Метрики недоступны"

echo ""
echo "=== Проверка endpoints ==="
curl -s http://localhost:8000/api/health | jq -e '.checks.scheduler' > /dev/null && echo "✅ Health endpoint работает" || echo "❌ Health endpoint не работает"

echo ""
echo "=== Проверка алертов ==="
grep -q "SchedulerNotRunning" prometheus/alerts.yml && echo "✅ Алерты настроены" || echo "❌ Алерты не настроены"
```

---

## Troubleshooting

### Дашборд не отображается

1. Проверить логи Grafana:
   ```bash
   docker compose logs grafana | grep -i "dashboard\|error"
   ```

2. Проверить UID дашборда:
   ```bash
   jq '.dashboard.uid' grafana/dashboards/system_stability.json
   ```

3. Перезапустить Grafana:
   ```bash
   docker compose restart grafana
   ```

### Метрики не отображаются

1. Проверить доступность метрик:
   ```bash
   curl http://localhost:8001/metrics | grep scheduler_running
   ```

2. Проверить Prometheus scrape config:
   ```bash
   grep -A 5 "api:" prometheus/prometheus.yml
   ```

3. Проверить метрики в Prometheus UI:
   - Открыть: http://localhost:9090
   - Ввести метрику: `scheduler_running`

### Алерты не срабатывают

1. Проверить конфигурацию алертов:
   ```bash
   promtool check rules prometheus/alerts.yml
   ```

2. Проверить статус алертов:
   ```bash
   curl http://localhost:9090/api/v1/alerts
   ```

3. Проверить логи Prometheus:
   ```bash
   docker compose logs prometheus | grep -i "alert\|error"
   ```

---

## Следующие шаги

1. ✅ Настроить AlertManager для отправки уведомлений
2. ✅ Создать дополнительные дашборды при необходимости
3. ✅ Настроить retention политики для метрик
4. ✅ Добавить пользовательские алерты при необходимости

---

## Дополнительные ресурсы

- [Grafana Dashboard JSON Schema](https://grafana.com/docs/grafana/latest/dashboards/json-model/)
- [Prometheus Alerting Rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
- [Context7 Best Practices](./GRAFANA_CONTEXT7_BEST_PRACTICES.md)

---

**Мониторинг настроен и готов к использованию!** 🎉

