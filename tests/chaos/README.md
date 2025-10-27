# Chaos Tests для Crawl Pipeline

[C7-ID: TEST-CHAOS-README-001]

## Обзор

Chaos tests проверяют устойчивость системы к различным типам сбоев и обеспечивают confidence в production-ready архитектуре.

## Структура тестов

### 1. `test_crawl_resilience.py`
**Основные chaos tests для crawl pipeline**

- **Redis restart recovery** - восстановление после перезапуска Redis
- **Supabase restart recovery** - восстановление после перезапуска Supabase
- **Network partition resilience** - устойчивость к network partition
- **OOM kill recovery** - восстановление после OOM kill
- **Consumer group recovery** - восстановление consumer group
- **Cascade failure recovery** - восстановление после каскадного сбоя

### 2. `test_redis_chaos.py`
**Redis-specific chaos tests**

- **Memory pressure handling** - обработка memory pressure
- **Connection pool exhaustion** - исчерпание connection pool
- **Slow consumer recovery** - восстановление после медленного consumer
- **Consumer crash recovery** - восстановление после краша consumer
- **Stream overflow handling** - обработка переполнения стрима
- **Consumer group consistency** - консистентность consumer group

### 3. `test_database_chaos.py`
**Database-specific chaos tests**

- **Connection pool exhaustion** - исчерпание connection pool
- **Long running query handling** - обработка долгих запросов
- **Deadlock recovery** - восстановление после deadlock
- **Lock timeout handling** - обработка lock timeout
- **Memory pressure handling** - обработка memory pressure
- **Concurrent transactions** - конкурентные транзакции
- **Rollback recovery** - восстановление после rollback

## Запуск тестов

### Предварительные требования

1. **Docker Compose** - для запуска инфраструктуры
2. **Python dependencies** - pytest, pytest-asyncio, redis, asyncpg
3. **Infrastructure** - Redis, Supabase, Prometheus, Grafana

### Команды запуска

```bash
# Запуск всей инфраструктуры
docker-compose up -d

# Запуск всех chaos tests
pytest tests/chaos/ -v -s

# Запуск конкретного теста
pytest tests/chaos/test_crawl_resilience.py::TestCrawlResilience::test_redis_restart_recovery -v -s

# Запуск с coverage
pytest tests/chaos/ --cov=src --cov-report=html

# Запуск с параллельностью
pytest tests/chaos/ -n auto
```

### Переменные окружения

```bash
# Redis
REDIS_URL=redis://localhost:6379

# Database
DATABASE_URL=postgresql://postgres:password@localhost:5432/telegram_assistant

# Test configuration
CHAOS_TEST_TIMEOUT=30
CHAOS_TEST_MESSAGES=100
CHAOS_TEST_CONCURRENCY=5
```

## Типы сбоев

### 1. Infrastructure Failures
- **Redis restart** - перезапуск Redis сервера
- **Supabase restart** - перезапуск Supabase/PostgreSQL
- **Network partition** - сетевые проблемы (tc netem)
- **OOM kill** - принудительное завершение процессов

### 2. Application Failures
- **Consumer crash** - краш consumer процессов
- **Slow consumer** - медленные consumer'ы
- **Connection pool exhaustion** - исчерпание пулов подключений
- **Memory pressure** - нехватка памяти

### 3. Database Failures
- **Deadlock** - взаимные блокировки
- **Lock timeout** - таймауты блокировок
- **Long running queries** - долгие запросы
- **Transaction rollback** - откат транзакций

## Метрики и мониторинг

### Prometheus метрики
- `chaos_test_duration_seconds` - длительность chaos тестов
- `chaos_test_success_total` - успешные тесты
- `chaos_test_failure_total` - неудачные тесты
- `chaos_test_recovery_time_seconds` - время восстановления

### Grafana дашборды
- **Chaos Test Overview** - обзор chaos тестов
- **Recovery Metrics** - метрики восстановления
- **Failure Patterns** - паттерны сбоев

## Best Practices

### 1. Test Design
- **Изолированность** - каждый тест независим
- **Идемпотентность** - тесты можно запускать многократно
- **Детерминированность** - предсказуемые результаты
- **Быстрота** - тесты выполняются быстро

### 2. Failure Simulation
- **Реалистичность** - симулируем реальные сбои
- **Контролируемость** - можем управлять сбоями
- **Воспроизводимость** - сбои воспроизводимы
- **Безопасность** - не ломаем production

### 3. Recovery Testing
- **Автоматическое восстановление** - система восстанавливается сама
- **Data consistency** - данные остаются консистентными
- **Service availability** - сервисы остаются доступными
- **Performance degradation** - приемлемое снижение производительности

## Troubleshooting

### Частые проблемы

1. **Redis connection refused**
   ```bash
   # Проверить статус Redis
   docker-compose ps redis
   docker-compose logs redis
   ```

2. **Database connection timeout**
   ```bash
   # Проверить статус Supabase
   docker-compose ps supabase-db
   docker-compose logs supabase-db
   ```

3. **Test timeout**
   ```bash
   # Увеличить timeout
   export CHAOS_TEST_TIMEOUT=60
   pytest tests/chaos/ --timeout=60
   ```

4. **Memory issues**
   ```bash
   # Очистить Docker
   docker system prune -a
   docker-compose down -v
   ```

### Логи и отладка

```bash
# Логи всех сервисов
docker-compose logs -f

# Логи конкретного сервиса
docker-compose logs -f redis
docker-compose logs -f supabase-db

# Логи тестов
pytest tests/chaos/ -v -s --log-cli-level=DEBUG
```

## Интеграция с CI/CD

### GitHub Actions

```yaml
name: Chaos Tests
on: [push, pull_request]

jobs:
  chaos-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio redis asyncpg
      - name: Start infrastructure
        run: docker-compose up -d
      - name: Wait for services
        run: sleep 30
      - name: Run chaos tests
        run: pytest tests/chaos/ -v --timeout=300
      - name: Cleanup
        run: docker-compose down -v
```

### GitLab CI

```yaml
chaos-tests:
  stage: test
  image: python:3.11
  services:
    - redis:7
    - postgres:15
  before_script:
    - pip install -r requirements.txt
    - pip install pytest pytest-asyncio redis asyncpg
  script:
    - pytest tests/chaos/ -v --timeout=300
  after_script:
    - docker-compose down -v
```

## Заключение

Chaos tests обеспечивают confidence в production-ready архитектуре crawl pipeline, проверяя устойчивость к различным типам сбоев и обеспечивая автоматическое восстановление системы.
