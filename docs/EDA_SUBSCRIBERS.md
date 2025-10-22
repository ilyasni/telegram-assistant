# EDA Subscribers - Контракты подписчиков

## Обзор

Данный документ описывает контракты подписчиков для Event-Driven Architecture (EDA) в Telegram Assistant. Каждый подписчик обрабатывает определенные типы событий и реализует специфичную бизнес-логику.

## Архитектура подписчиков

### Consumer Groups

| Consumer Group | Назначение | Обрабатываемые события |
|----------------|------------|------------------------|
| `rag-indexer` | Индексация контента для RAG | `channel.parsing.completed`, `rag.query.completed` |
| `webhook-notifier` | Webhook уведомления | `auth.login.authorized`, `channel.parsing.completed` |
| `tagging` | Автоматическое тегирование | `channel.parsing.completed` |
| `analytics` | Аналитика и метрики | Все события |

---

## RAG Indexer

### Назначение
Индексация постов каналов в векторную базу данных для RAG (Retrieval-Augmented Generation) поиска.

### Обрабатываемые события

#### channel.parsing.completed
```json
{
  "event_type": "channel.parsing.completed",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "payload": {
    "channel_id": "channel-789",
    "telegram_channel_id": "-1001234567890",
    "posts_parsed": 25,
    "posts_indexed": 23,
    "parsing_duration_ms": 60000
  }
}
```

**Обработка:**
1. Получение постов из БД по `channel_id`
2. Создание embeddings для текста постов
3. Сохранение в Qdrant с метаданными
4. Обновление статистики индексации

#### rag.query.completed
```json
{
  "event_type": "rag.query.completed",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "payload": {
    "query_id": "query-123",
    "query_text": "Что нового в ИИ?",
    "sources_count": 5,
    "processing_time_ms": 5000
  }
}
```

**Обработка:**
1. Сохранение аналитики запроса
2. Обновление метрик поиска
3. Обучение на feedback (если есть)

### Контракт обработки

```python
async def handle_rag_indexing(event: Dict[str, Any]):
    """Обработка событий для RAG индексации."""
    try:
        event_type = event["event_type"]
        
        if event_type == "channel.parsing.completed":
            await index_channel_posts(event)
        elif event_type == "rag.query.completed":
            await update_query_analytics(event)
        else:
            logger.debug("Unhandled event type", event_type=event_type)
            
    except Exception as e:
        logger.error("RAG indexing failed", error=str(e))
        raise
```

### Метрики
- `rag_indexing_events_total{event_type, status}`
- `rag_indexing_duration_seconds`
- `rag_indexing_errors_total{error_type}`

---

## Webhook Notifier

### Назначение
Отправка webhook уведомлений внешним системам при критических событиях.

### Обрабатываемые события

#### auth.login.authorized
```json
{
  "event_type": "auth.login.authorized",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "payload": {
    "qr_session_id": "qr-session-456",
    "session_id": "session-789",
    "telegram_user_id": "123456789",
    "invite_code": "ABC123XYZ456"
  }
}
```

**Обработка:**
1. Получение webhook URL из настроек tenant
2. Формирование payload для webhook
3. Отправка POST запроса с retry логикой
4. Логирование результата

#### channel.parsing.completed
```json
{
  "event_type": "channel.parsing.completed",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "payload": {
    "channel_id": "channel-789",
    "posts_parsed": 25,
    "posts_indexed": 23
  }
}
```

**Обработка:**
1. Проверка настроек уведомлений для канала
2. Отправка уведомления о завершении парсинга
3. Обновление статистики канала

### Контракт обработки

```python
async def handle_webhook_notifications(event: Dict[str, Any]):
    """Обработка событий для webhook уведомлений."""
    try:
        event_type = event["event_type"]
        
        if event_type == "auth.login.authorized":
            await notify_user_authorized(event)
        elif event_type == "channel.parsing.completed":
            await notify_parsing_completed(event)
        else:
            logger.debug("Unhandled event type", event_type=event_type)
            
    except Exception as e:
        logger.error("Webhook notification failed", error=str(e))
        raise
```

### Настройки webhook
```json
{
  "webhook_url": "https://example.com/webhook",
  "timeout": 30,
  "retry_count": 3,
  "retry_delay": 5,
  "events": ["auth.login.authorized", "channel.parsing.completed"]
}
```

### Метрики
- `webhook_notifications_total{event_type, status}`
- `webhook_response_time_seconds`
- `webhook_retries_total{event_type}`

---

## Tagging

### Назначение
Автоматическое тегирование постов каналов с использованием ML моделей.

### Обрабатываемые события

#### channel.parsing.completed
```json
{
  "event_type": "channel.parsing.completed",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "payload": {
    "channel_id": "channel-789",
    "posts_parsed": 25,
    "posts_indexed": 23
  }
}
```

**Обработка:**
1. Получение постов из БД
2. Применение ML моделей для тегирования
3. Сохранение тегов в БД
4. Обновление индекса тегов

### ML модели для тегирования

#### Категории тегов
- **Тематика**: `tech`, `business`, `science`, `entertainment`
- **Тип контента**: `news`, `tutorial`, `announcement`, `discussion`
- **Язык**: `ru`, `en`, `mixed`
- **Тон**: `positive`, `neutral`, `negative`

#### Алгоритмы
- **Классификация**: BERT-based модели
- **Извлечение сущностей**: NER модели
- **Анализ тональности**: Sentiment analysis
- **Кластеризация**: K-means для группировки похожих постов

### Контракт обработки

```python
async def handle_tagging(event: Dict[str, Any]):
    """Обработка событий для тегирования."""
    try:
        event_type = event["event_type"]
        
        if event_type == "channel.parsing.completed":
            await tag_channel_posts(event)
        else:
            logger.debug("Unhandled event type", event_type=event_type)
            
    except Exception as e:
        logger.error("Tagging failed", error=str(e))
        raise
```

### Метрики
- `tagging_events_total{event_type, status}`
- `tagging_duration_seconds`
- `tagging_accuracy_score`

---

## Analytics

### Назначение
Сбор и анализ метрик для бизнес-аналитики и мониторинга.

### Обрабатываемые события
Все события системы для построения аналитических дашбордов.

### Типы аналитики

#### Пользовательская аналитика
- Регистрации и авторизации
- Активность пользователей
- Использование функций

#### Контентная аналитика
- Парсинг каналов
- Популярные темы
- Качество контента

#### Системная аналитика
- Производительность
- Ошибки и исключения
- Использование ресурсов

### Контракт обработки

```python
async def handle_analytics(event: Dict[str, Any]):
    """Обработка событий для аналитики."""
    try:
        # Логирование события
        logger.info("Analytics event", 
                   event_type=event["event_type"],
                   tenant_id=event.get("tenant_id"),
                   user_id=event.get("user_id"))
        
        # Сохранение в аналитическую БД
        await save_analytics_event(event)
        
        # Обновление агрегированных метрик
        await update_aggregated_metrics(event)
        
    except Exception as e:
        logger.error("Analytics processing failed", error=str(e))
        raise
```

### Агрегированные метрики

#### Ежедневные метрики
```sql
CREATE TABLE daily_metrics (
    date DATE,
    tenant_id VARCHAR(100),
    metric_name VARCHAR(100),
    metric_value NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### Еженедельные метрики
```sql
CREATE TABLE weekly_metrics (
    week_start DATE,
    tenant_id VARCHAR(100),
    metric_name VARCHAR(100),
    metric_value NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Метрики
- `analytics_events_total{event_type}`
- `analytics_processing_duration_seconds`
- `analytics_aggregation_duration_seconds`

---

## Обработка ошибок

### Стратегии retry

#### Exponential Backoff
```python
async def retry_with_backoff(func, max_retries=3, base_delay=1):
    """Retry с экспоненциальным backoff."""
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            
            delay = base_delay * (2 ** attempt)
            await asyncio.sleep(delay)
```

#### Circuit Breaker
```python
class CircuitBreaker:
    """Circuit breaker для защиты от каскадных сбоев."""
    
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
```

### Dead Letter Queue
```python
async def handle_failed_event(event: Dict[str, Any], error: Exception):
    """Обработка неудачных событий."""
    # Сохранение в DLQ для последующего анализа
    await save_to_dlq(event, error)
    
    # Уведомление администраторов
    await notify_admins(event, error)
```

---

## Мониторинг подписчиков

### Health Checks

#### RAG Indexer Health
```python
async def check_rag_indexer_health():
    """Проверка здоровья RAG indexer."""
    checks = {
        "qdrant_connection": await check_qdrant_connection(),
        "embedding_service": await check_embedding_service(),
        "database_connection": await check_database_connection()
    }
    
    return all(checks.values()), checks
```

#### Webhook Notifier Health
```python
async def check_webhook_notifier_health():
    """Проверка здоровья webhook notifier."""
    checks = {
        "webhook_endpoints": await check_webhook_endpoints(),
        "retry_queue": await check_retry_queue(),
        "rate_limits": await check_rate_limits()
    }
    
    return all(checks.values()), checks
```

### Метрики подписчиков

#### Общие метрики
- `subscriber_events_processed_total{consumer, event_type}`
- `subscriber_processing_duration_seconds{consumer}`
- `subscriber_errors_total{consumer, error_type}`
- `subscriber_lag_seconds{consumer}`

#### Специфичные метрики
- **RAG**: `rag_indexing_success_rate`, `rag_query_latency`
- **Webhook**: `webhook_delivery_success_rate`, `webhook_retry_rate`
- **Tagging**: `tagging_accuracy`, `tagging_throughput`
- **Analytics**: `analytics_events_per_second`, `analytics_aggregation_latency`

---

## Конфигурация подписчиков

### Environment Variables

```bash
# Redis Streams
REDIS_URL=redis://redis:6379/0
STREAM_KEY_PREFIX=events

# Consumer Groups
CONSUMER_GROUP_RAG_INDEXER=rag-indexer
CONSUMER_GROUP_WEBHOOK_NOTIFIER=webhook-notifier
CONSUMER_GROUP_TAGGING=tagging
CONSUMER_GROUP_ANALYTICS=analytics

# Retry Settings
MAX_RETRIES=3
RETRY_DELAY=5
CIRCUIT_BREAKER_THRESHOLD=5

# Webhook Settings
WEBHOOK_TIMEOUT=30
WEBHOOK_RETRY_COUNT=3
WEBHOOK_RATE_LIMIT=100

# RAG Settings
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=telegram_posts
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Analytics Settings
ANALYTICS_ENABLED=true
ANALYTICS_BATCH_SIZE=100
ANALYTICS_RETENTION_DAYS=365
```

### Docker Compose конфигурация

```yaml
services:
  event-worker:
    build: ./worker
    environment:
      - REDIS_URL=redis://redis:6379/0
      - DATABASE_URL=postgresql://postgres:postgres@supabase-db:54322/postgres
      - QDRANT_URL=http://qdrant:6333
    depends_on:
      - redis
      - supabase-db
      - qdrant
    restart: unless-stopped
    deploy:
      replicas: 2
```

---

## Тестирование подписчиков

### Unit тесты

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_rag_indexer_handles_parsing_completed():
    """Тест обработки события channel.parsing.completed."""
    event = {
        "event_type": "channel.parsing.completed",
        "tenant_id": "tenant-123",
        "payload": {
            "channel_id": "channel-789",
            "posts_parsed": 25
        }
    }
    
    with patch('worker.rag_indexer.index_posts') as mock_index:
        await handle_rag_indexing(event)
        mock_index.assert_called_once_with(event)
```

### Integration тесты

```python
@pytest.mark.asyncio
async def test_webhook_notifier_integration():
    """Тест интеграции webhook notifier."""
    # Создание тестового события
    event = create_test_event("auth.login.authorized")
    
    # Отправка события в Redis Stream
    await publish_event(event)
    
    # Ожидание обработки
    await asyncio.sleep(1)
    
    # Проверка отправки webhook
    assert webhook_was_sent()
```

### Load тесты

```python
@pytest.mark.asyncio
async def test_rag_indexer_load():
    """Нагрузочный тест RAG indexer."""
    # Создание 1000 событий
    events = [create_test_event() for _ in range(1000)]
    
    # Параллельная обработка
    start_time = time.time()
    await asyncio.gather(*[handle_rag_indexing(event) for event in events])
    duration = time.time() - start_time
    
    # Проверка производительности
    assert duration < 60  # Менее 60 секунд
    assert events_processed == 1000
```

---

## Заключение

Данная спецификация обеспечивает четкие контракты для всех подписчиков в event-driven архитектуре Telegram Assistant. Каждый подписчик имеет определенную ответственность, обрабатывает специфичные события и предоставляет метрики для мониторинга.

Регулярное тестирование и мониторинг подписчиков гарантирует надежность и производительность системы.
