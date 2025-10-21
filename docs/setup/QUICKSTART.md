# Быстрый старт Telegram Assistant

## Первый запуск

### 1. Запуск core сервисов

```bash
# Запуск базового стека (API + Worker + Telethon + Postgres + Redis + Qdrant)
docker-compose --profile core up -d

# Проверка статуса сервисов
docker-compose ps
```

### 2. Проверка здоровья сервисов

```bash
# API health check
curl http://localhost:8080/api/health

# Проверка БД
docker-compose exec supabase-db psql -U postgres -d postgres -c "\dt"

# Проверка Redis
docker-compose exec redis redis-cli ping

# Проверка Qdrant
curl http://localhost:6333/collections
```

### 3. Доступ к интерфейсам

- **API**: http://localhost:8080/api/
- **Supabase Studio**: http://localhost:8080/studio/
- **Qdrant Dashboard**: http://localhost:8080/qdrant/

### 4. Авторизация Telethon

```bash
# Интерактивная авторизация (потребуется ввод кода из Telegram)
docker-compose exec telethon-ingest python -c "
import asyncio
from services.telegram_client import TelegramIngestionService
async def auth():
    service = TelegramIngestionService()
    await service.start()
    await asyncio.sleep(5)
    await service.stop()
asyncio.run(auth())
"
```

### 5. Добавление канала через API

```bash
# Создание канала
curl -X POST http://localhost:8080/api/channels/ \
  -H "Content-Type: application/json" \
  -d '{
    "telegram_id": -1001234567890,
    "username": "testchannel",
    "title": "Test Channel",
    "settings": {"auto_parse": true}
  }' \
  --url-query "tenant_id=550e8400-e29b-41d4-a716-446655440000"
```

### 6. Проверка работы

```bash
# Получение списка каналов
curl http://localhost:8080/api/channels/?tenant_id=550e8400-e29b-41d4-a716-446655440000

# Получение списка постов
curl http://localhost:8080/api/posts/?tenant_id=550e8400-e29b-41d4-a716-446655440000

# Логи сервисов
docker-compose logs api telethon-ingest worker
```

## Дополнительные сервисы

### Запуск с аналитикой

```bash
# Запуск с Neo4j и Grafana
docker-compose --profile core --profile analytics up -d

# Доступ к интерфейсам
# Grafana: http://localhost:8080/grafana/
# Neo4j Browser: http://localhost:8080/neo4j/
```

## Остановка

```bash
# Остановка всех сервисов
docker-compose down

# Остановка с удалением данных
docker-compose down -v
```

## Troubleshooting

### Проблемы с портами

```bash
# Проверка занятых портов
netstat -tulpn | grep -E ':(8080|5432|6379|6333|3001)'

# Остановка конфликтующих сервисов
sudo systemctl stop postgresql redis-server
```

### Проблемы с авторизацией Telethon

```bash
# Очистка сессий
rm -rf telethon-sessions/*

# Перезапуск с интерактивной авторизацией
docker-compose restart telethon-ingest
```

### Проблемы с БД

```bash
# Сброс БД
docker-compose down -v
docker-compose --profile core up -d supabase-db

# Проверка подключения
docker-compose exec supabase-db psql -U postgres -d postgres -c "SELECT version();"
```

## Мониторинг

### Логи в реальном времени

```bash
# Все сервисы
docker-compose logs -f

# Конкретный сервис
docker-compose logs -f api
docker-compose logs -f telethon-ingest
docker-compose logs -f worker
```

### Метрики Prometheus

```bash
# API метрики
curl http://localhost:8080/api/metrics

# Worker метрики (если настроены)
curl http://localhost:8080/worker/metrics
```

## Следующие шаги

1. **Настройка каналов** — добавьте каналы через API
2. **Мониторинг** — настройте Grafana дашборды
3. **RAG тестирование** — проверьте поиск по содержимому
4. **Production** — настройте секреты и безопасность
