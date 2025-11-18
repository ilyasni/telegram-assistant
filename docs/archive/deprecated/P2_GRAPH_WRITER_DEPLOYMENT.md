# Graph Writer Service - Deployment Guide (Context7 P2)

## Контекст

**Context7 P2**: Real-time Event Streaming и Graph-RAG Enrichment.

Graph Writer Service — отдельный микросервис для синхронизации данных из PostgreSQL/Redis Streams в Neo4j. Реализует принцип decoupling: Telethon не ходит напрямую в Neo4j, вместо этого события обрабатываются через Redis Streams.

## Архитектура

```
Telethon Ingestion (channel_parser)
    ↓ публикует события
Redis Streams (stream:posts:parsed)
    ↓ читает события
Graph Writer Service (graph_writer.py)
    ↓ создаёт графовые связи
Neo4j Graph Database
```

### Принципы Context7

1. **Event-driven**: Graph Writer читает события из Redis Streams
2. **Decoupling**: Telethon не зависит от Neo4j
3. **Идемпотентность**: MERGE операции с параметрами (не f-strings)
4. **Batch processing**: Обработка батчами для эффективности
5. **Backfilling**: Поддержка обработки существующих данных из PostgreSQL

## Компоненты

### 1. Neo4jClient расширения

**Файл**: `api/worker/integrations/neo4j_client.py`

**Новые методы**:
- `create_forward_relationship()` — создание связей FORWARDED_FROM
- `create_reply_relationship()` — создание связей REPLIES_TO
- `create_author_relationship()` — создание связей AUTHOR_OF

**Узлы**:
- `(:ForwardSource)` — источник форварда
- `(:Author)` — автор поста

**Связи**:
- `(:Post)-[:FORWARDED_FROM]->(:ForwardSource)`
- `(:Post)-[:FORWARDED_FROM_POST]->(:Post)` (если исходный пост найден)
- `(:Post)-[:REPLIES_TO {thread_id}]->(:Post)`
- `(:Author)-[:AUTHOR_OF]->(:Post)`

### 2. GraphWriter Service

**Файл**: `api/worker/services/graph_writer.py`

**Функциональность**:
- Чтение событий из Redis Streams (`stream:posts:parsed`)
- Consumer Group для распределённой обработки
- Batch processing для эффективности
- Поддержка backfilling из PostgreSQL
- Автоматическое обновление графа при появлении новых данных

### 3. Расширение схемы событий

**Файл**: `api/worker/events/schemas/posts_parsed_v1.py`

**Новые поля**:
- `forward_from_peer_id`, `forward_from_chat_id`, `forward_from_message_id`, `forward_date`, `forward_from_name`
- `reply_to_message_id`, `reply_to_chat_id`, `thread_id`
- `author_peer_id`, `author_name`, `author_type`

### 4. Обновление channel_parser

**Файл**: `telethon-ingest/services/channel_parser.py`

**Изменения**:
- `_prepare_parsed_event()` теперь включает forwards/replies/author данные
- События публикуются в Redis Streams с расширенными данными

## Развёртывание

### 1. Проверка Neo4j подключения

```bash
# Проверка доступности Neo4j
docker compose exec neo4j cypher-shell -u neo4j -p neo4j123 "RETURN 1"
```

### 2. Запуск Graph Writer как отдельного воркера

**Вариант 1: Standalone worker**

```python
# scripts/run_graph_writer.py
import asyncio
import redis.asyncio as redis
from worker.integrations.neo4j_client import Neo4jClient
from worker.services.graph_writer import GraphWriter

async def main():
    # Подключения
    redis_client = redis.from_url("redis://localhost:6379")
    neo4j_client = Neo4jClient()
    await neo4j_client.connect()
    
    # Graph Writer
    graph_writer = GraphWriter(
        neo4j_client=neo4j_client,
        redis_client=redis_client,
        consumer_group="graph_writer",
        batch_size=100
    )
    
    try:
        await graph_writer.start_consuming()
    except KeyboardInterrupt:
        await graph_writer.stop()
        await neo4j_client.close()
        await redis_client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
```

**Вариант 2: Интеграция в существующий worker**

Добавить в `api/worker/tasks/graph_writer_task.py`:

```python
from worker.services.graph_writer import GraphWriter

async def run_graph_writer():
    # ... инициализация ...
    graph_writer = GraphWriter(...)
    await graph_writer.start_consuming()
```

### 3. Backfilling существующих данных

```python
# scripts/backfill_graph_from_postgres.py
from worker.services.graph_writer import GraphWriter
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

async def backfill_graph():
    # ... инициализация ...
    
    # Получаем все посты с forwards/replies
    result = await db_session.execute(
        text("""
            SELECT id FROM posts
            WHERE forward_from_peer_id IS NOT NULL
               OR reply_to_message_id IS NOT NULL
        """)
    )
    post_ids = [str(row.id) for row in result.fetchall()]
    
    # Обработка батчами
    stats = await graph_writer.process_batch_from_postgres(post_ids, batch_size=100)
    print(f"Processed: {stats['processed']}, Failed: {stats['failed']}")
```

## Проверка работы

### 1. Проверка событий в Redis Streams

```bash
# Проверка наличия событий
docker compose exec redis redis-cli XINFO STREAM stream:posts:parsed

# Чтение последних событий
docker compose exec redis redis-cli XREAD COUNT 10 STREAMS stream:posts:parsed 0
```

### 2. Проверка графовых связей в Neo4j

```cypher
// Проверка forwards связей
MATCH (p:Post)-[r:FORWARDED_FROM]->(fs:ForwardSource)
RETURN p.post_id, fs.source_id, fs.source_type, r.forward_date
LIMIT 10

// Проверка replies связей
MATCH (p1:Post)-[r:REPLIES_TO]->(p2:Post)
RETURN p1.post_id, p2.post_id, r.thread_id
LIMIT 10

// Проверка author связей
MATCH (a:Author)-[r:AUTHOR_OF]->(p:Post)
RETURN a.author_id, a.author_type, p.post_id
LIMIT 10

// Статистика графа
MATCH (p:Post)
OPTIONAL MATCH (p)-[:FORWARDED_FROM]->(fs:ForwardSource)
OPTIONAL MATCH (p)-[:REPLIES_TO]->(p2:Post)
OPTIONAL MATCH (a:Author)-[:AUTHOR_OF]->(p)
RETURN 
    count(DISTINCT p) as posts,
    count(DISTINCT fs) as forward_sources,
    count(DISTINCT p2) as reply_targets,
    count(DISTINCT a) as authors
```

### 3. Мониторинг Graph Writer

**Метрики Prometheus** (опционально):

```python
# Добавить в graph_writer.py
from prometheus_client import Counter, Histogram

graph_events_processed_total = Counter(
    'graph_events_processed_total',
    'Total events processed by graph writer',
    ['status']
)

graph_processing_duration_seconds = Histogram(
    'graph_processing_duration_seconds',
    'Time spent processing events',
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0]
)
```

## Troubleshooting

### Проблема: События не обрабатываются

**Диагностика**:
1. Проверить наличие событий в Redis Streams
2. Проверить создание consumer group
3. Проверить логи Graph Writer

**Решение**:
```bash
# Проверка consumer group
docker compose exec redis redis-cli XINFO GROUPS stream:posts:parsed

# Проверка pending messages
docker compose exec redis redis-cli XPENDING stream:posts:parsed graph_writer
```

### Проблема: Не создаются связи в Neo4j

**Диагностика**:
1. Проверить доступность Neo4j
2. Проверить наличие Post узлов в Neo4j
3. Проверить логи Neo4jClient

**Решение**:
```cypher
// Проверка наличия Post узлов
MATCH (p:Post) RETURN count(p) as posts_count

// Проверка конкретного поста
MATCH (p:Post {post_id: 'YOUR_POST_ID'}) RETURN p
```

### Проблема: Дублирование связей

**Решение**: Используется MERGE, дублирование не должно возникать. Если возникает — проверить уникальность ключей в Cypher запросах.

## Итоговая сводка

✅ **Расширен Neo4jClient** для поддержки forwards/replies/author
✅ **Создан GraphWriter Service** для обработки событий из Redis Streams
✅ **Расширена схема событий** PostParsedEventV1 для включения forwards/replies данных
✅ **Обновлён channel_parser** для публикации расширенных данных в событиях

**Готово к использованию**:
- Graph Writer может работать как отдельный воркер
- Поддерживает backfilling из PostgreSQL
- Создаёт графовые связи для forwards/replies/author

**Impact**:
- Обратная совместимость: новые поля optional в событиях
- Производительность: batch processing для эффективности
- Надёжность: идемпотентность через MERGE

