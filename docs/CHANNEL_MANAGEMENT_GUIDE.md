# Руководство по системе управления каналами

## Обзор

Система управления каналами Telegram обеспечивает автоматический парсинг, тегирование, обогащение и индексацию постов из подключенных каналов с использованием GigaChat, crawl4ai, Qdrant и Neo4j.

## Архитектура

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

## Компоненты системы

### 1. База данных (PostgreSQL/Supabase)

#### Основные таблицы

- `posts` - посты с полями для TTL и enrichment
- `channels` - каналы Telegram
- `user_channel` - подписки пользователей
- `post_enrichment` - данные обогащения
- `outbox_events` - события для надёжной доставки

#### Ключевые поля

```sql
-- posts
expires_at TIMESTAMPTZ GENERATED ALWAYS AS (posted_at + INTERVAL '90 days') STORED
content_hash VARCHAR(64)
enrichment_status ENUM('pending', 'tagged', 'enriched', 'indexed', 'failed', 'skipped')
idempotency_key VARCHAR(255) UNIQUE

-- outbox_events
event_type VARCHAR(100)
payload JSONB
processed_at TIMESTAMPTZ
retry_count INTEGER
```

### 2. Event Bus (Redis Streams)

#### Стримы событий

- `posts.parsed` - пост распарсен
- `posts.tagged` - пост протегирован
- `posts.enriched` - пост обогащён
- `posts.indexed` - пост проиндексирован
- `posts.deleted` - пост удалён

#### Схемы событий

```python
class PostParsedEvent(BaseEvent):
    user_id: str
    channel_id: str
    post_id: str
    tenant_id: str
    text: str
    urls: List[str]
    posted_at: datetime
    idempotency_key: str
```

### 3. AI Провайдеры

#### GigaChain адаптер

- **Primary**: GigaChat через `gpt2giga-proxy`
- **Fallback**: OpenRouter для резервирования
- **Batch processing**: Тегирование по 10 постов
- **Structured output**: JSON с тегами и метаданными

#### Конфигурация

```python
class ProviderConfig:
    name: str = "gigachat"
    api_key: str
    base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    model: str = "GigaChat:latest"
    max_tokens: int = 4000
    temperature: float = 0.1
    timeout: int = 30
    batch_size: int = 10
```

### 4. Enrichment (Crawl4AI)

#### Триггеры обогащения

- Наличие URL в посте
- Теги из whitelist: `longread`, `research`, `paper`, `release`, `law`
- Минимальное количество слов: 500
- Лимиты пользователя: 100 обогащений/день

#### Политики

```yaml
crawl4ai:
  trigger_tags: [longread, research, paper, release, law]
  min_word_count: 500
  max_tokens_per_day_per_user: 50000
  timeout_seconds: 30
  caching:
    enabled: true
    ttl_days: 7
```

### 5. Векторная база (Qdrant)

#### Коллекции

- Per-tenant коллекции: `tenant_{tenant_id}_posts`
- Размерность векторов: 1536 (GigaChat)
- Расстояние: COSINE
- Payload: post_id, channel_id, tags, posted_at, expires_at

#### Индексация

```python
payload = {
    "post_id": post['id'],
    "channel_id": post['channel_id'],
    "tags": post.get('tags', []),
    "posted_at": int(post['posted_at'].timestamp()),
    "expires_at": int((post['posted_at'].timestamp() + 90 * 24 * 3600))
}
```

### 6. Граф знаний (Neo4j)

#### Схема графа

```
(:User {id})-[:FOLLOWS]->(:Channel {channel_id, username})
(:Channel)-[:HAS_POST]->(:Post {post_id, content, posted_at})
(:Post)-[:TAGGED_AS]->(:Tag {name, confidence, category})
(:Post)-[:IN_CHANNEL]->(:Channel)
```

#### Автоматическая очистка

- DETACH DELETE узлов постов при TTL
- Периодическая очистка висячих тегов
- Каскадное удаление связей

## API Endpoints

### Управление каналами

#### Подписка на канал

```http
POST /api/channels/users/{user_id}/subscribe
Content-Type: application/json

{
  "username": "@channel_name",
  "title": "Channel Title",
  "settings": {}
}
```

#### Список каналов

```http
GET /api/channels/users/{user_id}/list?limit=100&offset=0
```

#### Отписка от канала

```http
DELETE /api/channels/users/{user_id}/unsubscribe/{channel_id}
```

#### Статистика подписок

```http
GET /api/channels/users/{user_id}/stats
```

### Триггеры парсинга

#### Ручной триггер

```http
POST /api/channels/{channel_id}/trigger-parsing
```

## Конфигурация

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/db
REDIS_URL=redis://localhost:6379

# AI Providers
GIGACHAT_API_KEY=your-gigachat-key
OPENROUTER_API_KEY=your-openrouter-key  # опционально

# Vector and Graph DBs
QDRANT_URL=http://localhost:6333
NEO4J_URL=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password

# Services
TENANT_ID=your-tenant-id
ENRICHMENT_CONFIG_PATH=worker/config/enrichment_policy.yml
```

### Docker Compose

```yaml
version: '3.8'
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: telegram_assistant
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage

  neo4j:
    image: neo4j:5.15
    environment:
      NEO4J_AUTH: neo4j/password
    ports:
      - "7687:7687"
      - "7474:7474"
    volumes:
      - neo4j_data:/data

  worker:
    build: ./worker
    environment:
      - DATABASE_URL=postgresql+asyncpg://user:password@postgres/telegram_assistant
      - REDIS_URL=redis://redis:6379
      - QDRANT_URL=http://qdrant:6333
      - NEO4J_URL=bolt://neo4j:7687
    depends_on:
      - postgres
      - redis
      - qdrant
      - neo4j
```

## Мониторинг и метрики

### Prometheus метрики

#### Основные метрики

- `posts_processed_total` - обработанные посты по стадиям
- `post_processing_duration_seconds` - время обработки
- `tagging_latency_seconds` - латентность тегирования
- `embedding_latency_seconds` - латентность эмбеддингов
- `cleanup_deleted_posts_total` - удалённые посты

#### Метрики очередей

- `posts_in_queue_total` - посты в очередях
- `queue_processing_time_seconds` - время в очередях

#### Метрики качества

- `duplicate_posts_total` - дубликаты постов
- `data_quality_score` - качество данных
- `enrichment_skipped_total` - пропущенные обогащения

### Grafana Dashboard

#### Панели

1. **Processing Pipeline**
   - Posts per stage (parsed → tagged → enriched → indexed)
   - Processing latency by stage
   - Error rates by stage

2. **AI Providers**
   - Request rates by provider
   - Latency by provider/model
   - Token usage
   - Fallback usage

3. **Enrichment**
   - Enrichment success rate
   - Skip reasons
   - Word count distribution
   - Cache hit rate

4. **Storage**
   - Qdrant collection sizes
   - Neo4j node counts
   - Database connection pools

5. **Cleanup**
   - Deleted posts by reason
   - Cleanup operation latency
   - Orphan nodes cleaned

## Troubleshooting

### Частые проблемы

#### 1. FloodWait ошибки

**Симптомы**: `FloodWaitError` в логах telethon-ingest

**Решение**:
```python
# В channel_parser.py
async def handle_flood_wait(self, error: errors.FloodWaitError):
    wait_time = min(error.seconds, self.config.max_flood_wait)
    await asyncio.sleep(wait_time)
```

#### 2. GigaChat rate limits

**Симптомы**: 429 ошибки от GigaChat API

**Решение**:
- Увеличить `batch_window_ms` в конфигурации
- Добавить exponential backoff
- Использовать fallback на OpenRouter

#### 3. Qdrant connection issues

**Симптомы**: `ConnectionError` при индексации

**Решение**:
```python
# Проверка соединения
async def health_check_qdrant():
    try:
        await qdrant_client.get_collections()
        return True
    except Exception:
        return False
```

#### 4. Neo4j memory issues

**Симптомы**: Медленные запросы к Neo4j

**Решение**:
- Увеличить heap memory: `NEO4J_server_memory_heap_initial__size=2G`
- Оптимизировать индексы
- Периодическая очистка orphan узлов

#### 5. Redis Streams lag

**Симптомы**: События накапливаются в стримах

**Решение**:
- Увеличить количество consumer'ов
- Оптимизировать обработку событий
- Мониторинг `posts_in_queue_total`

### Диагностические команды

#### Проверка состояния системы

```bash
# Проверка Redis Streams
redis-cli XLEN stream:posts:parsed
redis-cli XINFO STREAM stream:posts:parsed

# Проверка Qdrant
curl http://localhost:6333/collections

# Проверка Neo4j
cypher-shell "MATCH (n) RETURN count(n) as total_nodes"

# Проверка PostgreSQL
psql -c "SELECT enrichment_status, COUNT(*) FROM posts GROUP BY enrichment_status"
```

#### Логи и метрики

```bash
# Логи worker'а
docker logs telegram-assistant-worker-1 -f

# Метрики Prometheus
curl http://localhost:8000/metrics

# Health checks
curl http://localhost:8000/health
```

## Развёртывание

### 1. Подготовка инфраструктуры

```bash
# Создание директорий
mkdir -p data/{postgres,redis,qdrant,neo4j}

# Настройка прав
chmod 755 data/*
```

### 2. Запуск сервисов

```bash
# Запуск инфраструктуры
docker-compose up -d postgres redis qdrant neo4j

# Ожидание готовности
sleep 30

# Применение миграций
alembic upgrade head

# Запуск worker'ов
docker-compose up -d worker
```

### 3. Проверка работоспособности

```bash
# Health checks
curl http://localhost:8000/health

# Тестовая подписка на канал
curl -X POST http://localhost:8000/api/channels/users/test-user/subscribe \
  -H "Content-Type: application/json" \
  -d '{"username": "@test_channel"}'
```

## Масштабирование

### Горизонтальное масштабирование

#### Worker'ы

```yaml
# docker-compose.yml
worker:
  deploy:
    replicas: 3
  environment:
    - WORKER_ID=worker-${HOSTNAME}
```

#### Consumer Groups

```python
# Разные consumer'ы для разных tenant'ов
consumer_group = f"tagging-group-{tenant_id}"
consumer_name = f"worker-{worker_id}"
```

### Вертикальное масштабирование

#### Ресурсы

```yaml
worker:
  deploy:
    resources:
      limits:
        memory: 2G
        cpus: '1.0'
      reservations:
        memory: 1G
        cpus: '0.5'
```

#### Оптимизация

- Увеличение batch_size для AI операций
- Кеширование эмбеддингов в Redis
- Параллельная обработка в Neo4j
- BRIN индексы в PostgreSQL

## Безопасность

### Изоляция данных

- Per-tenant коллекции в Qdrant
- RLS политики в PostgreSQL
- Namespace'ы в Neo4j
- Tenant-specific Redis keys

### Аутентификация

- JWT токены для API
- API ключи для внешних сервисов
- Rotating credentials

### Аудит

- Логирование всех операций
- Трассировка через trace_id
- Метрики доступа к данным

## Roadmap

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
