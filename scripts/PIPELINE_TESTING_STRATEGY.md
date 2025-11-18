# Стратегия тестирования пайплайна Telegram Assistant

## Обзор

Этот документ описывает стратегию тестирования пайплайна и актуализацию скриптов проверки на основе Context7 best practices.

## Основные скрипты проверки

### 1. `check_pipeline_e2e.py` (103KB) - **ОСНОВНОЙ E2E ТЕСТ**

**Проверяет:**
- ✅ Scheduler (режим, HWM, последняя активность)
- ✅ Парсинг постов (posts.parsed)
- ✅ Тегирование (posts.parsed → posts.tagged)
- ✅ Обогащение (posts.tagged → posts.enriched via crawl4ai)
- ✅ Индексация (posts.enriched → Qdrant + Neo4j)
- ✅ Сквозной поток данных через все этапы
- ✅ Redis Streams (группы, лаги, PEL)
- ✅ DLQ индикаторы
- ✅ Qdrant (размерность, payload coverage)
- ✅ Neo4j (индексы, свежесть графа)
- ✅ S3 хранилище (media, vision, crawl префиксы)
- ✅ Vision анализ (streams, БД enrichments, S3 кэш)

**Режимы:**
- `smoke` (≤30с): базовая проверка сервисов
- `e2e` (≤90с): полная проверка пайплайна с порогами SLO
- `deep` (≤5мин): детальная диагностика (группы, PEL, DLQ, размерности)

**Статус:** ✅ Полностью функционален, использует Context7 best practices

### 2. `check_pipeline_health.py` (44KB) - **HEALTH CHECK**

**Проверяет:**
- ✅ Database health (enrichments, indexing status)
- ✅ Redis Streams health (лаги, PEL, gaps)
- ✅ Qdrant health (points, eligible for index, gaps)
- ✅ Neo4j health (nodes, relationships, gaps)
- ✅ Gap analysis (теги, vision, crawl, индексация)
- ✅ SLO breaches detection
- ✅ Prometheus Pushgateway metrics

**Режимы:**
- `smoke` (≤30s): базовая проверка сервисов
- `e2e` (≤90s): полная проверка пайплайна с порогами SLO
- `deep` (≤5min): детальная диагностика с gap analysis

**Статус:** ✅ Полностью функционален, использует Context7 best practices

### 3. Специализированные тесты

- `test_album_pipeline_full.py` - полный тест пайплайна альбомов
- `test_vision_pipeline.py` - тест vision пайплайна
- `test_multitenant_simple.py` - тест multi-tenant изоляции
- `test_vision_smoke_*.py` - серия smoke тестов для vision

## Пробелы и улучшения

### ❌ Отсутствующие проверки

1. **Multi-tenancy изоляция в E2E тестах**
   - Нет проверки изоляции данных между tenants
   - Нет проверки RLS политик
   - Нет проверки Redis namespacing (`t:{tenant_id}:*`)

2. **Безопасность**
   - Нет проверки SQL injection защита
   - Нет проверки XSS защита
   - Нет проверки RBAC политик
   - Нет проверки rate limiting per tenant

3. **Идемпотентность**
   - Нет проверки идемпотентности всех операций
   - Нет проверки дедупликации событий

4. **Производительность**
   - Нет бенчмарков для критичных операций
   - Нет проверки timeouts и retries

### ✅ Улучшения на основе Context7 best practices

1. **Pytest структура** (создан `conftest.py`)
   - Общие фикстуры для подключений (db_pool, redis_client, qdrant_client, neo4j_driver)
   - Session-scoped подключения для производительности
   - Параметризация тестов через markers

2. **Connection pooling**
   - asyncpg pool с lifecycle callbacks
   - Переиспользование подключений между тестами
   - Правильное управление lifecycle (init, setup, teardown)

3. **Идемпотентность**
   - trace_id для всех проверок
   - Безопасные операции Redis (SCAN вместо KEYS)
   - Prepared statements для повторяющихся запросов

4. **Observability**
   - Structured logging (structlog)
   - Prometheus Pushgateway metrics
   - JUnit XML для CI/CD интеграции

## Рекомендации по использованию

### Ежедневные проверки (CI/CD)

```bash
# Smoke тест (быстрый)
python scripts/check_pipeline_e2e.py --mode smoke

# E2E тест (полный)
python scripts/check_pipeline_e2e.py --mode e2e --output test_results/e2e.json --junit test_results/e2e.xml
```

### Weekly health checks

```bash
# Deep диагностика
python scripts/check_pipeline_health.py --mode deep --window-seconds 3600 --output-json test_results/health.json --output-md test_results/health.md
```

### При деплое

```bash
# Полная проверка всех компонентов
python scripts/check_pipeline_e2e.py --mode deep --output test_results/deploy.json
python scripts/check_pipeline_health.py --mode deep --output-json test_results/health.json
```

## Планируемые улучшения

### 1. Добавить проверки multi-tenancy

```python
async def check_multitenant_isolation():
    """Проверка изоляции данных между tenants."""
    # Создать тестовые tenants
    # Проверить RLS политики
    # Проверить Redis namespacing
    # Проверить Qdrant collections per tenant
```

### 2. Добавить проверки безопасности

```python
async def check_security():
    """Проверка безопасности системы."""
    # SQL injection защита
    # XSS защита
    # RBAC политики
    # Rate limiting
```

### 3. Создать единый test suite

```python
# test_suite_pipeline.py
@pytest.mark.asyncio
async def test_pipeline_complete():
    """Полный тест пайплайна с использованием pytest."""
    # Использование фикстур из conftest.py
    # Параметризация для разных режимов
    # Parallel execution
```

### 4. Интеграция с pytest

- Миграция основных скриптов на pytest структуру
- Использование фикстур для переиспользования подключений
- Параметризация для разных режимов и окружений
- Parallel execution для ускорения тестов

## Best practices из Context7

### 1. asyncpg Connection Pool

```python
# ✅ Правильно - с lifecycle callbacks
pool = await asyncpg.create_pool(
    DATABASE_URL,
    min_size=2,
    max_size=10,
    command_timeout=30,
    max_inactive_connection_lifetime=300.0,
    init=init_connection  # Lifecycle callback
)

# ❌ Неправильно - без lifecycle
pool = await asyncpg.create_pool(DATABASE_URL)
```

### 2. Redis SCAN вместо KEYS

```python
# ✅ Правильно - безопасно для production
async for key in scan_iter(client, "pattern:*", count=200):
    # обработка

# ❌ Неправильно - блокирует Redis
keys = await client.keys("pattern:*")
```

### 3. Идемпотентные проверки

```python
# ✅ Правильно - с trace_id
trace_id = str(uuid.uuid4())
logger.info("Starting check", trace_id=trace_id)

# ❌ Неправильно - без trace_id
logger.info("Starting check")
```

### 4. Structured Logging

```python
# ✅ Правильно - structured logging
logger.info("Check completed", 
            check_name="scheduler",
            status="ok",
            duration_ms=123)

# ❌ Неправильно - plain logging
print("Check completed")
```

## Метрики и SLO

### Пороги SLO

- **Max watermark age**: 60 минут
- **Enrichment tags coverage**: 80%
- **Enrichment vision coverage**: 50%
- **Enrichment crawl coverage**: 30%
- **Indexing failed rate**: < 20%
- **Indexing pending rate**: < 30%
- **Stream lag**: < 5 секунд
- **Stream PEL**: < 100 сообщений

### Метрики Prometheus

- `e2e_watermark_age_seconds` - возраст последнего парсинга
- `e2e_stream_pending_total` - количество pending сообщений
- `e2e_posts_last24h_total` - количество постов за 24 часа
- `e2e_qdrant_vectors_total` - количество векторов в Qdrant
- `e2e_qdrant_payload_coverage_ratio` - покрытие payload в Qdrant

## Заключение

Основные скрипты проверки (`check_pipeline_e2e.py` и `check_pipeline_health.py`) уже используют Context7 best practices и полностью функциональны. 

**Приоритеты для улучшения:**
1. Добавить проверки multi-tenancy изоляции
2. Добавить проверки безопасности
3. Создать единый test suite на pytest
4. Добавить проверки идемпотентности

**Рекомендация:** Использовать `check_pipeline_e2e.py --mode e2e` для ежедневных проверок и `check_pipeline_health.py --mode deep` для weekly health checks.


