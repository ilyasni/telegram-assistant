# 🚀 Быстрый старт E2E тестирования

## Три способа запуска

### 1. Локально (если зависимости установлены)

```bash
# Проверка зависимостей
python3 -c "import asyncpg, redis, structlog, qdrant_client, neo4j; print('✅ Все зависимости установлены')"

# Запуск
make test-smoke
```

### 2. С установкой зависимостей

```bash
# Установка
pip install -r scripts/requirements.txt

# Запуск
make test-smoke
```

### 3. Через Docker контейнер (рекомендуется для production)

**Вариант A: Использовать существующий контейнер worker**
```bash
docker compose exec worker python3 /opt/telegram-assistant/scripts/check_pipeline_e2e.py --mode smoke
```

**Вариант B: Использовать специальный E2E контейнер**
```bash
# Сборка
docker build -f scripts/Dockerfile.e2e -t telegram-assistant/e2e:latest .

# Запуск
docker run --rm --network=telegram_assistant_default \
  -e DATABASE_URL -e REDIS_URL -e QDRANT_URL \
  -e NEO4J_URI -e NEO4J_USER -e NEO4J_PASSWORD \
  telegram-assistant/e2e:latest --mode smoke
```

## Проверка сервисов (1 минута)

```bash
# Автоматическая проверка
./scripts/check_services.sh

# Или вручную
docker compose ps
docker compose exec redis redis-cli ping
curl http://qdrant:6333/health
```

## Первый запуск (2 минуты)

```bash
# Быстрая проверка (smoke test)
make test-smoke

# Полная проверка с результатами
make test-e2e

# Результаты будут в artifacts/
cat artifacts/e2e_full.json
```

## Что дальше?

- 📖 Полное руководство: `docs/E2E_TESTING_SETUP.md`
- 📋 Пороги SLO: `config/e2e_thresholds.json`
- 📚 Документация: `.cursor/rules/10-e2e-testing.mdc`

## Troubleshooting

**ModuleNotFoundError?**
```bash
pip install -r scripts/requirements.txt
# или используйте Docker: docker compose exec worker python3 scripts/check_pipeline_e2e.py --mode smoke
```

**Connection refused?**
```bash
./scripts/check_services.sh
docker compose ps
```

**Нет данных?**
```bash
# Проверьте наличие каналов и постов
docker compose exec supabase-db psql -U postgres -d telegram_assistant -c "SELECT COUNT(*) FROM channels; SELECT COUNT(*) FROM posts;"
```

