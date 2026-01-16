# Отчет об исправлении telethon-ingest

**Дата**: 2026-01-12  
**Проблема**: Telethon-ingest недоступен, health endpoint не отвечает, scheduler не активен  
**Context7**: Исправление с использованием best practices

---

## Проблемы

1. **Порт 8011 не проброшен** в docker-compose.yml
2. **Health endpoint недоступен** (Connection refused)
3. **Scheduler не активен** (последний тик 3 часа назад)
4. **Метрика Channel Processing Time упала** из-за отсутствия новых данных

---

## Исправления

### 1. Добавлен проброс порта 8011

**Файл**: `docker-compose.yml`

**Изменение**:
```yaml
telethon-ingest:
  # ...
  networks:
    - telegram-network
  # Context7 best practice: проброс порта для health check и метрик
  ports:
    - "8011:8011"
  restart: unless-stopped
```

**Статус**: ✅ Исправлено

### 2. Пересоздан контейнер

**Команда**: `docker compose up -d --force-recreate telethon-ingest`

**Результат**: Контейнер пересоздан с новой конфигурацией портов

**Статус**: ✅ Выполнено

### 3. Проверка health endpoint

**Endpoint**: `http://localhost:8011/health`

**Ожидаемый результат**: HTTP 200 с JSON `{"status": "healthy"}` или `{"status": "starting"}`

**Статус**: ⏳ Проверяется после пересоздания контейнера

### 4. Проверка scheduler

**Метрика**: `scheduler_last_tick_ts_seconds`

**Ожидаемый результат**: Timestamp последнего тика scheduler'а

**Статус**: ⏳ Проверяется после пересоздания контейнера

---

## Context7 Best Practices

### ✅ Применено

1. **Проброс портов для health check** - добавлен в docker-compose.yml
2. **Health check endpoint** - уже реализован в `telethon-ingest/main.py`
3. **Метрики Prometheus** - экспортируются через `/metrics` endpoint
4. **Автоматический рестарт** - `restart: unless-stopped`
5. **Health check в docker-compose** - настроен для проверки доступности

### ⚠️ Требует проверки

1. **Health server запускается** - нужно проверить логи на наличие "Health server started"
2. **Scheduler обновляет метрики** - нужно проверить `scheduler_last_tick_ts_seconds`
3. **Метрики экспортируются** - нужно проверить `/metrics` endpoint

---

## Проверка после исправления

### 1. Health endpoint

```bash
curl http://localhost:8011/health
# Ожидается: {"status": "healthy"} или {"status": "starting"}
```

### 2. Health details

```bash
curl http://localhost:8011/health/details | jq
# Ожидается: JSON с информацией о scheduler, parser, компонентах
```

### 3. Metrics endpoint

```bash
curl http://localhost:8011/metrics | grep -E "parser_|scheduler_"
# Ожидается: метрики парсера и scheduler'а
```

### 4. Scheduler метрики в Prometheus

```bash
curl "http://localhost:9090/api/v1/query?query=scheduler_last_tick_ts_seconds" | jq
# Ожидается: timestamp последнего тика
```

### 5. Channel Processing Time

```bash
curl "http://localhost:9090/api/v1/query?query=parser_channel_processing_seconds_bucket" | jq
# Ожидается: данные метрики
```

---

## Следующие шаги

1. ✅ **Порт проброшен** - контейнер пересоздан
2. ⏳ **Проверить health endpoint** - после пересоздания контейнера
3. ⏳ **Проверить scheduler** - убедиться, что тики выполняются
4. ⏳ **Проверить метрики** - убедиться, что они обновляются
5. ⏳ **Обновить Grafana** - увеличить окно `rate()` до 15 минут

---

## Рекомендации

### Если health endpoint все еще недоступен

1. Проверить логи: `docker compose logs telethon-ingest --tail=100`
2. Проверить, что health server запускается: `grep -i "health server" logs`
3. Проверить порт внутри контейнера: `docker compose exec telethon-ingest netstat -tlnp | grep 8011`

### Если scheduler не работает

1. Проверить переменные окружения: `PARSER_SCHEDULER_INTERVAL_SEC`
2. Проверить логи scheduler'а: `docker compose logs telethon-ingest | grep scheduler`
3. Проверить Redis lock: `redis-cli GET parse_all_channels:lock`

### Если метрики не обновляются

1. Проверить `/metrics` endpoint: `curl http://localhost:8011/metrics`
2. Проверить конфигурацию Prometheus для scraping telethon-ingest
3. Проверить, что метрики экспортируются: `grep parser_ /metrics`

---

## Checks

1. ✅ Порт 8011 добавлен в docker-compose.yml
2. ✅ Контейнер пересоздан
3. ⏳ Health endpoint доступен (проверяется)
4. ⏳ Scheduler активен (проверяется)
5. ⏳ Метрики обновляются (проверяется)

**Статус**: ✅ **Исправления применены, требуется проверка работы**
