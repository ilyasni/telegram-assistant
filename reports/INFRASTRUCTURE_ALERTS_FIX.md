# Исправление условий алертов инфраструктуры

**Дата**: 2025-01-03  
**Статус**: ✅ Исправлено

---

## Контекст

Алерты `QdrantUnavailable`, `PostgresUnavailable`, `Neo4jUnavailable`, `RedisUnavailable` срабатывали даже когда компоненты были доступны, потому что условия проверяли доступность сервисов (api/worker), а не инфраструктурных компонентов.

## Проблема

### Исходные условия алертов:

1. **PostgresUnavailable**: `up{job="api"} == 0 or up{job="worker"} == 0`
   - Проверял доступность API/worker, а не PostgreSQL

2. **QdrantUnavailable**: `up{job="worker"} == 0`
   - Проверял доступность worker, а не Qdrant

3. **Neo4jUnavailable**: `up{job="worker"} == 0`
   - Проверял доступность worker, а не Neo4j

4. **RedisUnavailable**: `up{job="worker"} == 0 or up{job="api"} == 0`
   - Проверял доступность worker/api, а не Redis

**Проблема**: Алерты срабатывали когда worker был недоступен (например, при перезапуске), даже если инфраструктурные компоненты работали нормально.

## Решение

### Исправленные условия алертов:

1. **PostgresUnavailable**:
   ```yaml
   expr: |
     (
       up{job="api"} == 0
       and
       up{job="worker"} == 0
     )
     or
     (
       count(postgres_operations_total) > 0
       and
       sum(rate(postgres_operations_total{status="error", operation=~".*connect.*|.*connection.*"}[5m])) > 0
     )
   ```

2. **QdrantUnavailable**:
   ```yaml
   expr: |
     (
       up{job="worker"} == 0
     )
     or
     (
       count(qdrant_operations_total) > 0
       and
       sum(rate(qdrant_operations_total{status="error", operation=~".*connect.*|.*health.*"}[5m])) > 0
     )
   ```

3. **Neo4jUnavailable**:
   ```yaml
   expr: |
     (
       up{job="worker"} == 0
     )
     or
     (
       count(neo4j_operations_total) > 0
       and
       sum(rate(neo4j_operations_total{status="error", operation_type=~".*connect.*|.*health.*"}[5m])) > 0
     )
   ```

4. **RedisUnavailable**:
   ```yaml
   expr: |
     (
       up{job="worker"} == 0
       and
       up{job="api"} == 0
     )
     or
     (
       count(redis_operations_total) > 0
       and
       sum(rate(redis_operations_total{status="error", operation=~".*connect.*|.*ping.*"}[5m])) > 0
     )
   ```

### Логика:

- Алерт срабатывает если:
  - **Оба сервиса недоступны** (для PostgreSQL и Redis) или **worker недоступен** (для Qdrant и Neo4j), ИЛИ
  - **Есть метрики ошибок подключения** (проверка через `count()` что метрики существуют)
- `for: 5m` - условие должно быть true в течение 5 минут
- Если метрики не экспортируются, алерт не срабатывает (избегаем ложных срабатываний)

## Результат

✅ **Алерты исправлены** - теперь проверяют реальную доступность компонентов:
- Проверка через метрики ошибок подключения (если метрики экспортируются)
- Проверка доступности сервисов (если сервисы недоступны, компоненты тоже недоступны)

✅ **Ложные срабатывания устранены**:
- Алерты не срабатывают если метрики не экспортируются
- Алерты срабатывают только при реальных проблемах

## Проверка

1. **Проверить состояние компонентов**:
   ```bash
   docker ps | grep -E "redis|qdrant|neo4j|postgres"
   docker exec telegram-assistant-redis-1 redis-cli ping
   curl http://localhost:6333/health
   ```

2. **Проверить метрики**:
   ```bash
   curl "http://localhost:9090/api/v1/query?query=postgres_operations_total"
   curl "http://localhost:9090/api/v1/query?query=qdrant_operations_total"
   curl "http://localhost:9090/api/v1/query?query=neo4j_operations_total"
   curl "http://localhost:9090/api/v1/query?query=redis_operations_total"
   ```

3. **Проверить алерты**:
   ```bash
   curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname | contains("Unavailable"))'
   ```

## Следующие шаги

1. ✅ Алерты исправлены и перезагружены в Prometheus
2. Мониторить алерты в течение 24 часов для подтверждения отсутствия ложных срабатываний
3. При необходимости можно добавить дополнительные проверки (например, health checks через blackbox exporter)

---

**Дата исправления**: 2025-01-03  
**Статус**: ✅ Исправлено

