# Следующие шаги после реализации исправлений стабильности

**Дата**: 2025-12-03  
**Статус**: Готово к тестированию

---

## 1. Тестирование реализованных изменений

### 1.1 Проверка Scheduler

```bash
# Проверить логи запуска scheduler
docker compose logs api | grep -i scheduler

# Проверить метрики scheduler
curl http://localhost:8001/metrics | grep scheduler

# Проверить статус через health endpoint
curl http://localhost:8000/api/health | jq '.checks.scheduler'
```

**Ожидаемый результат:**
- `scheduler_running` = 1
- `scheduler_jobs_total` > 0
- В health endpoint: `"running": true`, `"jobs_count" > 0`

### 1.2 Проверка Health Checks с кэшированием

```bash
# Проверить метрики health checks
curl http://localhost:8001/metrics | grep health_check

# Проверить health endpoint (должен кэшироваться)
time curl http://localhost:8000/api/health
time curl http://localhost:8000/api/health  # Второй запрос должен быть быстрее

# Проверить circuit breaker
curl http://localhost:8001/metrics | grep circuit_breaker
```

**Ожидаемый результат:**
- Второй запрос к `/api/health` выполняется быстрее (кэш работает)
- Метрики `health_check_cache_hits_total` увеличиваются
- Circuit breaker в состоянии CLOSED

### 1.3 Проверка мониторинга контейнеров

```bash
# Проверить endpoint мониторинга контейнеров
curl http://localhost:8000/api/monitoring/containers | jq

# Проверить метрики контейнеров
curl http://localhost:8001/metrics | grep container

# Проверить конкретный контейнер
curl http://localhost:8000/api/monitoring/containers/api | jq
```

**Ожидаемый результат:**
- Список всех критичных контейнеров
- Статус и uptime для каждого контейнера
- Метрики `container_uptime_seconds`, `container_health_status`

### 1.4 Проверка Pipeline Health

```bash
# Проверить endpoint проверки пайплайна
curl http://localhost:8000/api/pipeline/health | jq

# Проверить метрики пайплайна
curl http://localhost:8001/metrics | grep pipeline

# Проверить метрики Redis streams
curl http://localhost:8001/metrics | grep redis_stream_pending
```

**Ожидаемый результат:**
- Статистика по всем этапам пайплайна
- Информация о Redis Streams (длина, pending)
- Статистика БД (посты, обогащения)
- Информация о Qdrant и Neo4j

### 1.5 Проверка Metrics Summary

```bash
# Проверить endpoint summary
curl http://localhost:8000/api/metrics/summary | jq

# Проверить проблемы и рекомендации
curl http://localhost:8000/api/metrics/summary | jq '.issues'
curl http://localhost:8000/api/metrics/summary | jq '.recommendations'
```

**Ожидаемый результат:**
- Агрегированная информация по всем компонентам
- Список проблем (если есть)
- Рекомендации по улучшению

---

## 2. Проверка Prometheus метрик

### 2.1 Проверить доступность метрик

```bash
# Проверить все новые метрики
curl http://localhost:8001/metrics | grep -E "(scheduler|health_check|container|pipeline)"

# Проверить метрики в Prometheus UI
# Открыть http://localhost:9090
# Найти метрики:
# - scheduler_running
# - scheduler_startup_duration_seconds
# - health_check_duration_seconds
# - container_uptime_seconds
# - pipeline_posts_parsed_total
```

### 2.2 Проверить алерты

```bash
# Проверить статус алертов в Prometheus
curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname | contains("Scheduler") or contains("Health") or contains("Container"))'

# Или открыть Prometheus UI: http://localhost:9090/alerts
```

**Проверить алерты:**
- `SchedulerNotRunning` - должен быть в состоянии "inactive" (scheduler работает)
- `HealthCheckFailuresHigh` - должен быть в состоянии "inactive"
- `RedisStreamPendingHigh` - проверить при наличии pending сообщений
- `ContainerRestartsHigh` - проверить при перезапусках

---

## 3. Интеграционное тестирование

### 3.1 Симуляция проблем для проверки resilience

```bash
# Остановить Redis для проверки circuit breaker
docker compose stop redis

# Проверить health endpoint (должен вернуть ошибку, но не упасть)
curl http://localhost:8000/api/health

# Проверить, что circuit breaker открылся
curl http://localhost:8001/metrics | grep circuit_breaker_state

# Включить Redis обратно
docker compose start redis

# Подождать recovery_timeout (60 секунд)
# Проверить, что circuit breaker закрылся
sleep 60
curl http://localhost:8001/metrics | grep circuit_breaker_state
```

### 3.2 Проверка кэширования

```bash
# Первый запрос - медленный (без кэша)
time curl -s http://localhost:8000/api/health > /dev/null

# Последующие запросы - быстрые (из кэша)
for i in {1..5}; do
  time curl -s http://localhost:8000/api/health > /dev/null
done

# Проверить метрику cache hits
curl http://localhost:8001/metrics | grep health_check_cache_hits_total
```

---

## 4. Проверка логов

### 4.1 Проверить логи scheduler

```bash
# Проверить логи запуска scheduler
docker compose logs api | grep -i "scheduler" | tail -20

# Должны быть логи:
# - "Starting scheduler initialization..."
# - "Scheduler started successfully"
# - "Scheduler status verified"
```

### 4.2 Проверить логи health checks

```bash
# Проверить логи health checks
docker compose logs api | grep -i "health_check" | tail -20

# Должны быть логи:
# - "Health check cache hit" (для кэшированных запросов)
# - "Database circuit breaker is OPEN" (при проблемах с БД)
```

---

## 5. Настройка Grafana Dashboard

### 5.1 ✅ Дашборд создан

Дашборд **System Stability Monitoring** уже создан:
- **Файл**: `grafana/dashboards/system_stability.json`
- **UID**: `system-stability`
- **URL**: `https://grafana.${DOMAIN}/d/system-stability`

**Панели дашборда:**
1. ✅ Scheduler Status - статус запуска scheduler
2. ✅ Scheduler Jobs - количество активных jobs
3. ✅ Scheduler Startup Duration - время запуска (p95)
4. ✅ Health Check Duration - длительность проверок (p95)
5. ✅ Health Check Failures - ошибки проверок
6. ✅ Health Check Cache Hits - попадания в кэш
7. ✅ Container Health Status - статус контейнеров
8. ✅ Container Uptime - время работы контейнеров
9. ✅ Pipeline Posts Status - статус постов
10. ✅ Redis Stream Pending Messages - pending сообщения
11. ✅ Circuit Breaker State - состояние circuit breakers
12. ✅ Container Restarts - частота перезапусков

### 5.2 Активация дашборда

```bash
# Перезапустить Grafana для загрузки дашборда
docker compose restart grafana

# Проверить логи
docker compose logs grafana | tail -20

# Открыть Grafana UI
# http://localhost:3000
# Перейти в: Dashboards → System Stability Monitoring
```

### 5.3 Документация

Подробная документация по настройке: [`docs/SYSTEM_STABILITY_MONITORING_SETUP.md`](../docs/SYSTEM_STABILITY_MONITORING_SETUP.md)

---

## 6. Мониторинг в production

### 6.1 Настроить алерты в AlertManager

Убедиться, что AlertManager настроен и отправляет уведомления для:
- `SchedulerNotRunning` (critical)
- `HealthCheckFailuresHigh` (warning)
- `RedisStreamPendingHigh` (warning)
- `ContainerRestartsHigh` (warning)

### 6.2 Настроить регулярные проверки

Рекомендуется настроить регулярные проверки:
- Ежедневная проверка метрик summary: `curl http://localhost:8000/api/metrics/summary`
- Еженедельный обзор проблем: анализ логов и метрик
- Ежемесячный review алертов: проверка эффективности алертов

---

## 7. Документация

### 7.1 Обновить документацию API

Добавить информацию о новых endpoints в документацию:

```markdown
## Monitoring Endpoints

### GET /api/monitoring/containers
Получить статус всех критичных контейнеров

### GET /api/monitoring/containers/{service_name}
Получить статус конкретного контейнера

### GET /api/pipeline/health
Комплексная проверка здоровья пайплайна

### GET /api/metrics/summary
Агрегированная информация по метрикам системы
```

### 7.2 Создать runbook

Создать runbook с инструкциями по:
- Диагностике проблем scheduler
- Диагностике проблем health checks
- Анализу метрик пайплайна
- Реагированию на алерты

---

## 8. Дополнительные улучшения (опционально)

### 8.1 Расширение мониторинга контейнеров

- Добавить проверку использования ресурсов (CPU, память)
- Добавить проверку логов контейнеров на ошибки
- Добавить проверку сетевых соединений между контейнерами

### 8.2 Улучшение метрик пайплайна

- Добавить метрики latency для каждого этапа пайплайна
- Добавить метрики error rate по этапам
- Добавить метрики throughput (posts/sec)

### 8.3 Автоматическое тестирование

- Создать интеграционные тесты для новых endpoints
- Создать тесты для circuit breaker
- Создать тесты для кэширования health checks

---

## Чеклист готовности к production

- [ ] Все endpoints протестированы
- [ ] Метрики доступны в Prometheus
- [ ] Алерты настроены и проверены
- [ ] Логи проверены на наличие ошибок
- [ ] Circuit breaker работает корректно
- [ ] Кэширование health checks работает
- [ ] Grafana dashboard создан
- [ ] AlertManager настроен
- [ ] Документация обновлена
- [ ] Runbook создан

---

## Быстрый старт проверки

```bash
# 1. Проверить все endpoints одной командой
echo "=== Health Check ===" && curl -s http://localhost:8000/api/health | jq '.checks.scheduler'
echo "=== Containers ===" && curl -s http://localhost:8000/api/monitoring/containers | jq '.total_services'
echo "=== Pipeline ===" && curl -s http://localhost:8000/api/pipeline/health | jq '.status'
echo "=== Metrics Summary ===" && curl -s http://localhost:8000/api/metrics/summary | jq '.status'

# 2. Проверить метрики
curl -s http://localhost:8001/metrics | grep -E "(scheduler_running|health_check_cache_hits|container_uptime)" | head -10

# 3. Проверить логи
docker compose logs api --tail=50 | grep -E "(scheduler|health_check)" | tail -10
```

---

**Готово к тестированию!** 🚀

