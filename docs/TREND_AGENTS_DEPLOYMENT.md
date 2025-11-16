# Развертывание агентов трендов

**Дата**: 2025-11-16  
**Context7**: Инструкция по применению миграции, проверке работы и настройке мониторинга

## 1. Применение миграции БД

### Автоматический способ (рекомендуется)

```bash
# Применить миграцию через скрипт
./scripts/apply_trend_agents_migration.sh
```

Скрипт:
- Проверяет наличие Docker
- Запускает контейнер API (если не запущен)
- Применяет миграцию через Alembic
- Проверяет созданные таблицы

### Ручной способ

```bash
# Применить миграцию через контейнер API
docker-compose exec api alembic upgrade head

# Или через docker compose (новый синтаксис)
docker compose exec api alembic upgrade head
```

### Проверка миграции

```bash
# Проверить текущую версию миграции
docker-compose exec api alembic current

# Проверить созданные таблицы
docker-compose exec supabase-db psql -U postgres -d postgres -c "
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('user_trend_profiles', 'trend_interactions', 'trend_threshold_suggestions')
ORDER BY table_name;
"
```

## 2. Проверка работы агентов

### Автоматическая проверка

```bash
# Запустить скрипт проверки
./scripts/check_trend_agents.sh
```

Скрипт проверяет:
- Логи Trend Editor Agent
- Метрики Prometheus
- Таблицы в БД
- API endpoints
- Конфигурацию

### Ручная проверка

#### 2.1. Проверка логов

```bash
# Логи Trend Editor Agent
docker-compose logs worker | grep -i "trend_editor" | tail -20

# Логи API (QA Agent)
docker-compose logs api | grep -i "trend_qa" | tail -20
```

#### 2.2. Проверка метрик Prometheus

Откройте Prometheus UI: http://localhost:9090

Проверьте метрики:
- `trend_editor_requests_total`
- `trend_editor_quality_score`
- `trend_editor_latency_seconds`
- `trend_qa_filtered_total`
- `trend_qa_latency_seconds`

#### 2.3. Проверка API endpoints

```bash
# Проверка endpoint для взаимодействий
curl -X POST http://localhost:8000/api/trends/interactions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "00000000-0000-0000-0000-000000000001",
    "cluster_id": "00000000-0000-0000-0000-000000000002",
    "interaction_type": "view"
  }'
```

#### 2.4. Проверка конфигурации

Убедитесь, что в `.env` файле установлены переменные:

```bash
# Trend Editor Agent
TREND_EDITOR_ENABLED=true
TREND_EDITOR_MIN_SCORE=0.6
TREND_EDITOR_LLM_MODEL=GigaChat

# Trend QA/Filter Agent
TREND_QA_ENABLED=true
TREND_QA_MIN_SCORE=0.6
TREND_QA_LLM_MODEL=GigaChat

# Другие агенты
TREND_PERSONALIZER_ENABLED=true
TREND_TAXONOMY_ENABLED=true
TREND_THRESHOLD_TUNER_ENABLED=true
```

## 3. Настройка мониторинга

### 3.1. Grafana Dashboard

Дашборд `Trend Agents Monitoring` автоматически загружается при запуске Grafana.

**Доступ к дашборду:**
1. Откройте Grafana: http://localhost:3000
2. Перейдите в раздел "Dashboards"
3. Найдите "Trend Agents Monitoring"

**Метрики в дашборде:**
- Trend Editor Agent - Requests Total
- Trend Editor Agent - Quality Score Distribution
- Trend Editor Agent - Latency
- Trend QA Agent - Filtered Trends
- Trend QA Agent - Latency
- User Trend Profiles Count
- Trend Interactions Count

### 3.2. Обновление дашборда

Если дашборд не появился автоматически:

```bash
# Перезапустить Grafana для загрузки дашборда
docker-compose restart grafana

# Или применить через скрипт (если есть)
./scripts/update_all_grafana_dashboards.sh
```

### 3.3. Проверка метрик в Prometheus

```bash
# Проверить, что метрики экспортируются
curl http://localhost:9090/api/v1/query?query=trend_editor_requests_total

# Проверить все метрики агентов
curl http://localhost:9090/api/v1/label/__name__/values | grep trend
```

## 4. Troubleshooting

### Проблема: Миграция не применяется

**Решение:**
1. Проверьте подключение к БД:
   ```bash
   docker-compose exec supabase-db psql -U postgres -d postgres -c "SELECT 1;"
   ```

2. Проверьте логи Alembic:
   ```bash
   docker-compose exec api alembic upgrade head --verbose
   ```

### Проблема: Агенты не запускаются

**Решение:**
1. Проверьте логи worker:
   ```bash
   docker-compose logs worker | tail -50
   ```

2. Проверьте переменные окружения:
   ```bash
   docker-compose exec worker env | grep TREND
   ```

3. Убедитесь, что Redis доступен:
   ```bash
   docker-compose exec worker python -c "import redis; r = redis.Redis.from_url('redis://redis:6379'); r.ping()"
   ```

### Проблема: Метрики не появляются в Prometheus

**Решение:**
1. Проверьте, что Prometheus собирает метрики:
   ```bash
   curl http://localhost:9090/api/v1/targets
   ```

2. Проверьте конфигурацию Prometheus:
   ```bash
   docker-compose exec prometheus cat /etc/prometheus/prometheus.yml
   ```

3. Перезапустите Prometheus:
   ```bash
   docker-compose restart prometheus
   ```

### Проблема: Дашборд Grafana не загружается

**Решение:**
1. Проверьте, что файл дашборда существует:
   ```bash
   ls -la grafana/dashboards/trend_agents.json
   ```

2. Проверьте логи Grafana:
   ```bash
   docker-compose logs grafana | grep -i "dashboard\|trend"
   ```

3. Перезапустите Grafana:
   ```bash
   docker-compose restart grafana
   ```

## 5. Следующие шаги

После успешного развертывания:

1. **Мониторинг**: Настройте алерты в Grafana для критических метрик
2. **Тестирование**: Запустите тесты для проверки функциональности
3. **Документация**: Обновите документацию API с новыми endpoints
4. **Оптимизация**: Настройте пороги на основе данных Threshold Tuner Agent

## 6. Откат миграции (если необходимо)

```bash
# Откатить миграцию
docker-compose exec api alembic downgrade -1

# Или откатить до конкретной версии
docker-compose exec api alembic downgrade 20251115_enhance_trend_clusters
```

**Внимание**: Откат миграции удалит все данные из новых таблиц!

