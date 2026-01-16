# Обновление Grafana Dashboards

## Контекст

Добавлены новые панели в `grafana/dashboards/system_overview.json` для визуализации новых метрик, реализованных согласно `reports/PROMETHEUS_COVERAGE_RECOMMENDATIONS.md`.

## Добавленные панели

### PostgreSQL метрики

1. **PostgreSQL Operations Rate**
   - Метрика: `rate(postgres_operations_total[5m])`
   - Тип: Stat
   - Показывает: скорость операций PostgreSQL по типам (operation, status)

2. **PostgreSQL Latency (p95)**
   - Метрика: `histogram_quantile(0.95, rate(postgres_operation_duration_seconds_bucket[5m]))`
   - Тип: Timeseries
   - Показывает: p95 latency операций PostgreSQL

3. **PostgreSQL Connections**
   - Метрика: `postgres_connections_active / postgres_connections_max`
   - Тип: Stat
   - Показывает: использование соединений PostgreSQL (%)

### Qdrant метрики

4. **Qdrant Operations Rate**
   - Метрика: `rate(qdrant_operations_total[5m])`
   - Тип: Stat
   - Показывает: скорость операций Qdrant по типам (operation, status)

5. **Qdrant Latency (p95)**
   - Метрика: `histogram_quantile(0.95, rate(qdrant_operation_duration_seconds_bucket[5m]))`
   - Тип: Timeseries
   - Показывает: p95 latency операций Qdrant

6. **Qdrant Collection Size**
   - Метрика: `qdrant_collection_size`
   - Тип: Stat
   - Показывает: размер коллекций Qdrant

### Post Persistence метрики

7. **Post Persistence Processed**
   - Метрика: `rate(post_persistence_processed_total[5m])`
   - Тип: Stat
   - Показывает: скорость обработки постов по статусам

8. **Post Persistence PEL Size**
   - Метрика: `post_persistence_pel_size`
   - Тип: Stat
   - Показывает: размер PEL для post persistence

### Graph Writer метрики

9. **Graph Writer Processed**
   - Метрика: `rate(graph_writer_processed_total[5m])`
   - Тип: Stat
   - Показывает: скорость обработки событий GraphWriter

10. **Graph Writer PEL Size**
    - Метрика: `graph_writer_pel_size`
    - Тип: Stat
    - Показывает: размер PEL для GraphWriter

### API Endpoint метрики

11. **API Endpoint Requests**
    - Метрика: `rate(api_endpoint_requests_total[5m])`
    - Тип: Stat
    - Показывает: скорость запросов к API endpoints

12. **API Endpoint Latency (p95)**
    - Метрика: `histogram_quantile(0.95, rate(api_endpoint_latency_seconds_bucket[5m]))`
    - Тип: Timeseries
    - Показывает: p95 latency запросов к API endpoints

## Расположение панелей

Все новые панели добавлены в конец dashboard `system_overview.json` с автоматическим расчетом координат.

## Использование

1. **Импорт dashboard в Grafana:**
   - Откройте Grafana UI
   - Перейдите в Dashboards → Import
   - Загрузите `grafana/dashboards/system_overview.json`

2. **Проверка метрик:**
   - Убедитесь, что Prometheus datasource настроен
   - Проверьте, что метрики экспортируются (после выполнения операций)

3. **Настройка алертов:**
   - Алерты уже настроены в Prometheus
   - Уведомления будут отправляться в Telegram через AlertManager

## Дополнительные рекомендации

1. **Создание отдельных dashboards:**
   - Рекомендуется создать отдельные dashboards для каждого компонента:
     - `postgres_dashboard.json` - для PostgreSQL метрик
     - `qdrant_dashboard.json` - для Qdrant метрик
     - `post_persistence_dashboard.json` - для Post Persistence метрик
     - `graph_writer_dashboard.json` - для Graph Writer метрик

2. **Настройка переменных:**
   - Добавить переменные для фильтрации по tenant_id, operation_type и т.д.

3. **Добавление аннотаций:**
   - Настроить аннотации для алертов Prometheus

---

**Дата обновления:** 2025-01-03  
**Версия:** 1.0
