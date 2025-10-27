# 🚀 Telegram Assistant - Channel Management System

## Обзор системы

Система управления каналами Telegram обеспечивает автоматический парсинг, тегирование, обогащение и индексацию постов из подключенных каналов с использованием GigaChat, crawl4ai, Qdrant и Neo4j.

## 🏗️ Архитектура

### Микросервисная структура

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ telethon-ingest │ -> │     worker      │ -> │   rag-service   │
│   (парсинг)     │    │ (тегирование +  │    │ (индексация)    │
│                 │    │  обогащение)    │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         v                       v                       v
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Redis Streams  │    │   PostgreSQL    │    │ Qdrant + Neo4j  │
│  (события)      │    │   (данные)      │    │ (векторы + граф)│
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Поток данных

1. **Парсинг**: `telethon-ingest` получает сообщения из каналов
2. **Тегирование**: `worker` анализирует контент через GigaChat
3. **Обогащение**: `worker` обогащает посты через crawl4ai
4. **Индексация**: `rag-service` создаёт эмбеддинги и индексирует в Qdrant/Neo4j
5. **Очистка**: TTL-очистка через `pg_cron` с деиндексацией

## 🚀 Быстрый старт

### 1. Подготовка окружения

```bash
# Клонирование репозитория
git clone <repository-url>
cd telegram-assistant

# Копирование конфигурации
cp env.example .env

# Редактирование конфигурации
nano .env
```

### 2. Настройка переменных окружения

Обязательные переменные в `.env`:

```bash
# AI Провайдеры
GIGACHAT_API_KEY=your_gigachat_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here

# Безопасность
JWT_SECRET_KEY=your_jwt_secret_key_here

# Базы данных
POSTGRES_PASSWORD=your_secure_password_here
NEO4J_PASSWORD=your_neo4j_password_here
GRAFANA_PASSWORD=your_grafana_password_here
```

### 3. Запуск системы

```bash
# Автоматический запуск с проверками
./scripts/start.sh

# Или ручной запуск
docker-compose up -d
```

### 4. Проверка работоспособности

```bash
# Проверка статуса сервисов
docker-compose ps

# Просмотр логов
docker-compose logs -f

# Health checks
curl http://localhost:8000/health
```

## 📊 Мониторинг

### Grafana Dashboard

- **URL**: http://localhost:3000
- **Логин**: admin
- **Пароль**: из переменной `GRAFANA_PASSWORD`

### Панели мониторинга

1. **Processing Pipeline** - Обработка постов по стадиям
2. **AI Providers** - Производительность GigaChat/OpenRouter
3. **Enrichment** - Статистика обогащения контента
4. **Storage** - Размеры коллекций Qdrant/Neo4j
5. **Cleanup** - Операции очистки данных

### Prometheus Metrics

- **URL**: http://localhost:9090
- **Endpoints**: `/metrics` на всех сервисах

## 🤖 Bot Commands

### Основные команды

- `/add_channel` - Добавить канал
- `/my_channels` - Список каналов
- `/channel_stats` - Статистика

### Пример использования

```
/add_channel
> Отправьте username канала (например: @channel_name)
@ai_news
> ✅ Канал успешно добавлен!
```

## 📱 Mini App

### Доступ

- **URL**: http://localhost:8000/webapp/channels.html
- **Функции**: Полное управление каналами через веб-интерфейс

### Возможности

- ➕ Добавление каналов по username или Telegram ID
- 📊 Статистика подписок и постов
- 🔄 Триггер парсинга каналов
- ❌ Отписка от каналов
- 📈 Мониторинг активности

## 🔧 API Endpoints

### Управление каналами

```bash
# Подписка на канал
POST /api/channels/users/{user_id}/subscribe
{
  "username": "@channel_name",
  "title": "Channel Title"
}

# Список каналов
GET /api/channels/users/{user_id}/list

# Отписка от канала
DELETE /api/channels/users/{user_id}/unsubscribe/{channel_id}

# Статистика
GET /api/channels/users/{user_id}/stats

# Триггер парсинга
POST /api/channels/{channel_id}/trigger-parsing
```

## 🗄️ База данных

### PostgreSQL (Supabase)

- **Порт**: 5432
- **База**: telegram_assistant
- **Пользователь**: telegram_user

### Redis

- **Порт**: 6379
- **Использование**: Event Bus (Redis Streams)

### Qdrant

- **Порт**: 6333
- **Использование**: Векторная база для эмбеддингов

### Neo4j

- **Порт**: 7474 (Web), 7687 (Bolt)
- **Использование**: Граф знаний для связей

## 🔄 Event Flow

### События Redis Streams

1. `posts.parsed` - Пост распарсен
2. `posts.tagged` - Пост протегирован
3. `posts.enriched` - Пост обогащён
4. `posts.indexed` - Пост проиндексирован
5. `posts.deleted` - Пост удалён

### Схемы событий

```python
class PostParsedEvent(BaseModel):
    user_id: UUID
    channel_id: UUID
    post_id: UUID
    tenant_id: UUID
    text: str
    urls: List[str]
    posted_at: datetime
    idempotency_key: str
```

## 🛠️ Разработка

### Структура проекта

```
telegram-assistant/
├── api/                    # FastAPI приложение
│   ├── routers/           # API endpoints
│   ├── bot/               # Telegram bot handlers
│   └── webapp/            # Mini App UI
├── worker/                # Worker сервисы
│   ├── tasks/             # Обработчики событий
│   ├── ai_providers/      # AI провайдеры
│   └── config/            # Конфигурация
├── telethon-ingest/       # Парсинг каналов
├── supabase/              # Миграции БД
├── grafana/               # Дашборды
└── scripts/               # Скрипты запуска
```

### Локальная разработка

```bash
# Запуск только инфраструктуры
docker-compose up -d postgres redis qdrant neo4j

# Запуск API в dev режиме
cd api
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Запуск worker'ов
cd worker
python -m worker.main
```

## 🔍 Troubleshooting

### Частые проблемы

#### 1. FloodWait ошибки

```bash
# Проверка логов telethon-ingest
docker-compose logs telethon-ingest

# Решение: Увеличить задержки в конфигурации
```

#### 2. GigaChat rate limits

```bash
# Проверка метрик
curl http://localhost:9090/api/v1/query?query=tagging_requests_total

# Решение: Настроить batch processing
```

#### 3. Qdrant connection issues

```bash
# Проверка Qdrant
curl http://localhost:6333/health

# Проверка коллекций
curl http://localhost:6333/collections
```

#### 4. Neo4j memory issues

```bash
# Проверка Neo4j
docker-compose exec neo4j cypher-shell -u neo4j -p neo4j_password "RETURN 1"

# Увеличение heap memory в docker-compose.yml
```

### Диагностические команды

```bash
# Статус всех сервисов
docker-compose ps

# Логи конкретного сервиса
docker-compose logs -f worker

# Проверка Redis Streams
docker-compose exec redis redis-cli XLEN stream:posts:parsed

# Проверка PostgreSQL
docker-compose exec postgres psql -U telegram_user -d telegram_assistant -c "SELECT COUNT(*) FROM posts;"
```

## 📈 Масштабирование

### Горизонтальное масштабирование

```yaml
# docker-compose.yml
worker:
  deploy:
    replicas: 3
  environment:
    - WORKER_ID=worker-${HOSTNAME}
```

### Вертикальное масштабирование

```yaml
worker:
  deploy:
    resources:
      limits:
        memory: 2G
        cpus: '1.0'
```

## 🔒 Безопасность

### Изоляция данных

- Per-tenant коллекции в Qdrant
- RLS политики в PostgreSQL
- Namespace'ы в Neo4j
- Tenant-specific Redis keys

### Аутентификация

- JWT токены для API
- API ключи для внешних сервисов
- Rotating credentials

## 📚 Документация

- [Channel Management Guide](docs/CHANNEL_MANAGEMENT_GUIDE.md)
- [Database Schema](docs/DATABASE_SCHEMA.md)
- [API Contracts](docs/API_CONTRACTS.md)
- [Context7 Best Practices](docs/CONTEXT7_BEST_PRACTICES.md)

## 🤝 Поддержка

### Логи и метрики

```bash
# Все логи
docker-compose logs -f

# Метрики Prometheus
curl http://localhost:9090/metrics

# Health checks
curl http://localhost:8000/health
```

### Мониторинг

- **Grafana**: http://localhost:3000
- **Prometheus**: http://localhost:9090
- **API Docs**: http://localhost:8000/docs

## 🎯 Roadmap

### Краткосрочные цели (1-2 месяца)

- [ ] Полная интеграция с GigaChain
- [ ] A/B тестирование AI провайдеров
- [ ] Улучшение enrichment политик
- [ ] Расширенная аналитика

### Среднесрочные цели (3-6 месяцев)

- [ ] Миграция на Kafka/NATS
- [ ] Multi-region развёртывание
- [ ] Advanced GraphRAG
- [ ] Real-time dashboards

### Долгосрочные цели (6+ месяцев)

- [ ] ML-оптимизация pipeline'ов
- [ ] Автоматическое масштабирование
- [ ] Advanced security features
- [ ] Enterprise integrations

---

**Context7 Best Practices**: Все компоненты следуют принципам Context7 для надёжности, масштабируемости и maintainability.
