# Следующие шаги: Исправление метрики Scheduler Freshness

**Дата**: 2025-12-03  
**Context7**: План действий по исправлению проблемы с обновлением метрики Scheduler Freshness в Grafana

---

## Проблема

Метрика "Scheduler Freshness" в Grafana показывает **10 минут**, хотя:
- Scheduler работает нормально
- Последний tick был 4 минуты назад
- Метрика обновляется в коде

**Причина**: Prometheus не собирает метрику `scheduler_last_tick_ts_seconds` из telethon-ingest.

---

## Диагностика

### Текущая конфигурация

1. **Prometheus конфиг** (`prometheus/prometheus.yml`):
   ```yaml
   - job_name: 'telethon-ingest'
     static_configs:
       - targets: ['telegram-assistant-telethon-ingest-1:8011']
     metrics_path: /metrics
   ```

2. **Telethon-ingest health server**:
   - Порт: `8011` (из `INGEST_HEALTH_PORT`)
   - Endpoint: `/metrics`

### Проблемы

1. **Неправильное имя хоста**: Используется полное имя контейнера `telegram-assistant-telethon-ingest-1` вместо имени сервиса `telethon-ingest`
2. **Неизвестна доступность метрики**: Нужно проверить, экспортируется ли метрика

---

## План действий

### 1. Исправить конфигурацию Prometheus ✅

**Задача**: Заменить имя хоста на имя сервиса Docker Compose

**Файл**: `prometheus/prometheus.yml`

**Изменение**:
```yaml
# Было:
- targets: ['telegram-assistant-telethon-ingest-1:8011']

# Должно быть:
- targets: ['telethon-ingest:8011']
```

**Причина**: Docker Compose создает DNS имена по имени сервиса, а не по полному имени контейнера.

---

### 2. Проверить экспорт метрики ✅

**Задача**: Убедиться, что метрика `scheduler_last_tick_ts_seconds` экспортируется

**Проверки**:
1. Метрика должна быть определена в `telethon-ingest/tasks/parse_all_channels_task.py` ✅
2. Метрика должна обновляться в `_run_tick()` ✅
3. Метрика должна экспортироваться через `/metrics` endpoint

**Команды для проверки**:
```bash
# Изнутри контейнера telethon-ingest
python3 -c "from prometheus_client import generate_latest; print(generate_latest().decode('utf-8'))" | grep scheduler_last_tick

# Через HTTP
curl http://telethon-ingest:8011/metrics | grep scheduler_last_tick
```

---

### 3. Проверить доступность endpoint ✅

**Задача**: Убедиться, что Prometheus может достучаться до telethon-ingest

**Проверки**:
1. Health server запущен на порту 8011
2. Endpoint `/metrics` доступен
3. Сеть Docker Compose настроена правильно

**Команда для проверки**:
```bash
docker compose exec -T prometheus wget -qO- http://telethon-ingest:8011/metrics | head -20
```

---

### 4. Перезагрузить Prometheus ✅

**Задача**: Применить изменения в конфигурации

**Варианты**:
1. **Hot reload** (если включен lifecycle):
   ```bash
   curl -X POST http://localhost:9090/-/reload
   ```

2. **Перезапуск контейнера**:
   ```bash
   docker compose restart prometheus
   ```

---

### 5. Проверить сбор метрики в Prometheus ✅

**Задача**: Убедиться, что Prometheus собирает метрику

**Проверки**:
1. В Prometheus UI: `http://localhost:9090`
2. Запрос: `scheduler_last_tick_ts_seconds`
3. Запрос: `up{job="telethon-ingest"}` - должен быть `1`

**Запрос для проверки freshness**:
```promql
time() - scheduler_last_tick_ts_seconds
```

---

### 6. Обновить Grafana Dashboard ✅

**Задача**: Убедиться, что панель использует правильный запрос

**Проверка**:
- Запрос в панели должен быть: `time() - scheduler_last_tick_ts_seconds`
- Data source должен быть: Prometheus

---

## Последовательность выполнения

1. ✅ **Исправить конфигурацию Prometheus** - заменить имя хоста
2. ✅ **Проверить экспорт метрики** - убедиться, что метрика доступна
3. ✅ **Перезагрузить Prometheus** - применить изменения
4. ✅ **Проверить сбор метрики** - в Prometheus UI
5. ✅ **Проверить Grafana** - обновление панели

---

## Ожидаемый результат

После исправления:
- ✅ Prometheus собирает метрику `scheduler_last_tick_ts_seconds` из telethon-ingest
- ✅ Метрика обновляется каждые 15 секунд (scrape_interval)
- ✅ Grafana показывает актуальное значение freshness
- ✅ Scheduler Freshness отражает реальное время с последнего tick

---

## Откат

Если что-то пойдет не так:
1. Вернуть старое имя хоста в `prometheus/prometheus.yml`
2. Перезагрузить Prometheus: `docker compose restart prometheus`

---

## Контекст

**Context7 Best Practices**:
- Использование имен сервисов Docker Compose для DNS
- Проверка доступности метрик перед изменением конфигурации
- Hot reload Prometheus для применения изменений без перезапуска

---

**Статус**: Готов к реализации

