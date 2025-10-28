# Руководство по настройке E2E тестирования

## Обзор

E2E тестирование пайплайна включает три режима:
- **smoke** (≤30с) — быстрая проверка перед деплоем
- **e2e** (≤90с) — полная проверка пайплайна с SLO порогами
- **deep** (≤5мин) — детальная диагностика для troubleshooting

## Шаг 1: Установка зависимостей

### Вариант A: Использование существующих зависимостей (рекомендуется)

Если зависимости уже установлены для worker/telethon-ingest:

```bash
# Проверка установленных пакетов
python3 -c "import asyncpg, redis, structlog, qdrant_client, neo4j; print('✅ Все зависимости установлены')"
```

### Вариант B: Установка только для скрипта

```bash
# Установка в виртуальное окружение
python3 -m venv venv-e2e
source venv-e2e/bin/activate  # или `venv-e2e\Scripts\activate` на Windows

# Установка зависимостей
pip install -r scripts/requirements.txt
```

### Вариант C: Установка через Docker (если скрипт запускается в контейнере)

Добавьте в `docker-compose.yml` или используйте существующий контейнер worker:

```bash
# Запуск скрипта в контейнере worker (где уже установлены зависимости)
docker compose exec worker python3 /opt/telegram-assistant/scripts/check_pipeline_e2e.py --mode smoke
```

## Шаг 2: Проверка доступности сервисов

### Автоматическая проверка

```bash
chmod +x scripts/check_services.sh
./scripts/check_services.sh
```

### Ручная проверка

#### PostgreSQL
```bash
# В контейнере
docker compose exec supabase-db psql -U postgres -d telegram_assistant -c "SELECT 1"

# Снаружи (если порт прокинут)
psql "$DATABASE_URL" -c "SELECT 1"
```

#### Redis
```bash
# В контейнере
docker compose exec redis redis-cli ping

# Снаружи
redis-cli -u "$REDIS_URL" ping
```

#### Qdrant
```bash
# Проверка health endpoint
curl http://qdrant:6333/health

# Или снаружи (если порт прокинут)
curl http://localhost:6333/health
```

#### Neo4j
```bash
# В контейнере
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "RETURN 1"

# Или проверка порта
nc -z neo4j 7687  # или localhost:7687
```

## Шаг 3: Настройка переменных окружения и порогов

Скопируйте и настройте `.env`:

```bash
cp env.example .env
```

### Переменные окружения для E2E

**Приоритет порогов (от старшего к младшему):**
1. **ENV переменные** (высший приоритет)
2. **CLI `--thresholds`** (средний приоритет)  
3. **`config/e2e_thresholds.json`** (базовые пороги)

**Пример приоритетов:**

```bash
# config/e2e_thresholds.json
{
  "e2e": {
    "max_watermark_age_min": 30,
    "max_stream_pending": 50
  }
}

# ENV (перебивает JSON)
export E2E_MAX_WATERMARK_AGE_MIN=60  # Теперь будет 60, а не 30

# CLI --thresholds custom.json (перебивает дефолтный JSON, но не ENV)
python scripts/check_pipeline_e2e.py --thresholds custom.json --mode e2e
# Использует: E2E_MAX_WATERMARK_AGE_MIN=60 (из ENV) + остальное из custom.json
```

Проверьте ключевые переменные для E2E:

```bash
# Database
export DATABASE_URL="postgresql://postgres:postgres@supabase-db:5432/telegram_assistant"

# Redis
export REDIS_URL="redis://redis:6379"

# Qdrant
export QDRANT_URL="http://qdrant:6333"
export QDRANT_COLLECTION="posts"  # или ваша коллекция
export EMBEDDING_DIM=384  # размерность эмбеддингов

# Neo4j
export NEO4J_URI="neo4j://neo4j:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="changeme"  # ваш пароль

# E2E конфигурация (по умолчанию)
export E2E_THRESHOLDS_PATH="config/e2e_thresholds.json"
export E2E_MODE="e2e"

# E2E пороги (опционально, переопределяют config/e2e_thresholds.json)
# ВАЖНО: ENV переменные имеют ВЫСШИЙ приоритет над JSON
export E2E_MAX_WATERMARK_AGE_MIN=30
export E2E_MAX_STREAM_PENDING=50
export E2E_MIN_POSTS_24H=1
export E2E_MAX_EMBED_DIM_MISMATCH=0
export E2E_MAX_QDRANT_LAG_MIN=10
export E2E_MAX_SKEW_VS_PG_MIN=5
export E2E_QDRANT_MIN_PAYLOAD_COVERAGE=0.9

# Pushgateway (опционально)
export PROMETHEUS_PUSHGATEWAY_URL="http://pushgateway:9091"
export ENV="dev"  # или "prod"
```

**Пример кастомных порогов (JSON):**

Создайте `config/custom_thresholds.json`:

```json
{
  "e2e": {
    "max_check_time_sec": 120,
    "max_watermark_age_min": 60,
    "max_stream_pending": 100,
    "min_posts_24h": 5
  }
}
```

Использование:

```bash
# Использует custom_thresholds.json, но ENV всё равно перебивает
python scripts/check_pipeline_e2e.py --thresholds config/custom_thresholds.json --mode e2e

# Результат: E2E_MAX_WATERMARK_AGE_MIN из ENV (если есть) + остальное из custom_thresholds.json
```

## Шаг 4: Первый запуск

### Вариант 1: Через Makefile (рекомендуется)

```bash
# Быстрая проверка (smoke test)
make test-smoke

# Полная проверка
make test-e2e

# Детальная диагностика
make test-deep
```

### Вариант 2: Прямой запуск скрипта

```bash
# Smoke test
python3 scripts/check_pipeline_e2e.py --mode smoke --json

# E2E с выводом в файлы
python3 scripts/check_pipeline_e2e.py \
  --mode e2e \
  --thresholds config/e2e_thresholds.json \
  --output artifacts/e2e_result.json \
  --junit artifacts/e2e_result.xml

# Deep диагностика с большим лимитом
python3 scripts/check_pipeline_e2e.py \
  --mode deep \
  --limit 20 \
  --output artifacts/deep_result.json \
  --junit artifacts/deep_result.xml
```

### Вариант 3: Запуск в Docker контейнере

```bash
# Используя контейнер worker (где уже установлены зависимости)
docker compose exec worker python3 /opt/telegram-assistant/scripts/check_pipeline_e2e.py \
  --mode smoke \
  --json \
  --output /tmp/e2e_smoke.json

# Копирование результатов обратно
docker compose cp worker:/tmp/e2e_smoke.json ./artifacts/
```

## Шаг 5: Интерпретация результатов

### JSON результаты

Результаты сохраняются в `artifacts/e2e_*.json`:

```json
{
  "scheduler": {
    "status": "running",
    "hwm_count": 5,
    "max_age_minutes": 15.2
  },
  "streams": {
    "stream:posts:parsed": {
      "xlen": 1234,
      "groups": [...],
      "pending_summary": {"total": 5}
    }
  },
  "checks": [
    {
      "name": "scheduler.max_watermark_age",
      "ok": true,
      "message": null
    },
    {
      "name": "streams.stream:posts:parsed.pending",
      "ok": true,
      "message": null
    }
  ],
  "summary": {
    "pipeline_complete": true
  }
}
```

**Проверка статуса:**
- `checks[].ok == true` для всех проверок → ✅ Успех
- `checks[].ok == false` → ❌ Нарушение порогов
- `summary.pipeline_complete == true` → Пост прошёл все этапы

### JUnit XML

Результаты для CI/CD в `artifacts/e2e_*.xml`:

```xml
<testsuite name="e2e-e2e" tests="5" failures="1">
  <testcase name="scheduler.max_watermark_age"/>
  <testcase name="streams.stream:posts:parsed.pending">
    <failure message="Pending 60 > 50"/>
  </testcase>
</testsuite>
```

### Prometheus метрики

Если настроен Pushgateway, метрики отправляются автоматически:
- `e2e_watermark_age_seconds`
- `e2e_stream_pending_total`
- `e2e_posts_last24h_total`
- `e2e_qdrant_vectors_total`
- `e2e_qdrant_payload_coverage_ratio`
- `e2e_neo4j_skew_vs_pg_minutes`

## Шаг 6: Интеграция в CI/CD

### GitHub Actions

```yaml
- name: E2E Test
  run: |
    pip install -r scripts/requirements.txt
    make test-e2e
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
    REDIS_URL: ${{ secrets.REDIS_URL }}
    QDRANT_URL: ${{ secrets.QDRANT_URL }}
    NEO4J_URI: ${{ secrets.NEO4J_URI }}
  
- name: Upload artifacts
  uses: actions/upload-artifact@v3
  if: always()
  with:
    path: artifacts/e2e_*.json,artifacts/e2e_*.xml
```

### Pre-deploy проверка

```bash
#!/bin/bash
# В скрипте деплоя

echo "Running smoke tests..."
if ! make test-smoke; then
    echo "❌ Smoke tests failed, aborting deploy"
    exit 1
fi

echo "✅ Smoke tests passed, proceeding with deploy"
```

### Nightly deep диагностика (cron)

```bash
# Добавить в crontab
0 2 * * * cd /opt/telegram-assistant && make test-deep >> /var/log/e2e_deep.log 2>&1
```

## Шаг 7: Troubleshooting

### Ошибка: ModuleNotFoundError

```bash
# Установите зависимости
pip install -r scripts/requirements.txt

# Или используйте контейнер worker
docker compose exec worker python3 scripts/check_pipeline_e2e.py --mode smoke
```

### Ошибка: Connection refused

Проверьте доступность сервисов:

```bash
./scripts/check_services.sh
# или
docker compose ps
```

### Ошибка: Timeout

Увеличьте таймауты в `config/e2e_thresholds.json`:

```json
{
  "e2e": {
    "max_check_time_sec": 120  // увеличить с 90
  }
}
```

### Нет данных для проверки

Если пайплайн не обрабатывает посты:
1. Убедитесь, что scheduler запущен
2. Проверьте наличие каналов: `SELECT * FROM channels;`
3. Проверьте наличие постов: `SELECT COUNT(*) FROM posts;`
4. Запустите парсинг вручную или подождите следующего тика scheduler

## Дополнительные ресурсы

- Полная документация: `.cursor/rules/10-e2e-testing.mdc`
- Пороги SLO: `config/e2e_thresholds.json`
- Troubleshooting команды: `.cursor/rules/99-troubleshooting.mdc`

