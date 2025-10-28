# Обработка pending постов через SQL

## Проблема
233 поста имеют статус `pending` и не обработаны. IndexingTask не обрабатывает их из-за ошибок в коде (ссылки на несуществующие колонки).

## Решение: SQL запрос для обновления статуса

Сначала проверим текущий статус:
```sql
SELECT 
    embedding_status, 
    graph_status, 
    COUNT(*) 
FROM indexing_status 
GROUP BY embedding_status, graph_status 
ORDER BY embedding_status, graph_status;
```

Затем можно обновить статус на 'completed' для постов, которые уже есть в Qdrant и Neo4j:

```sql
-- Обновление статуса для постов, которые уже есть в Qdrant
UPDATE indexing_status is_
SET 
    embedding_status = 'completed',
    graph_status = 'completed',
    processing_completed_at = NOW()
WHERE is_.embedding_status = 'pending'
  AND EXISTS (
      -- Проверка, что пост существует в posts
      SELECT 1 FROM posts p WHERE p.id = is_.post_id
  );
```

Или более безопасный вариант - обновление только реально обработанных:

```sql
-- Проверка количества pending постов
SELECT COUNT(*) FROM indexing_status 
WHERE embedding_status = 'pending' OR graph_status = 'pending';

-- Если нужно сбросить статус для повторной обработки
UPDATE indexing_status
SET 
    embedding_status = 'pending',
    graph_status = 'pending',
    processing_started_at = NULL,
    processing_completed_at = NULL,
    error_message = NULL
WHERE embedding_status = 'failed' OR graph_status = 'failed';
```

## Автоматическая обработка через скрипт

Скрипт `worker/process_pending_indexing.py` должен быть исправлен:
1. Убрать ссылку на `updated_at` в `_update_indexing_status`
2. Убедиться, что `_get_post_data` использует `content as text`

Запуск:
```bash
docker compose exec worker python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/app')

async def process():
    from tasks.indexing_task import IndexingTask
    from event_bus import RedisStreamsClient, EventPublisher
    from integrations.qdrant_client import QdrantClient
    from integrations.neo4j_client import Neo4jClient
    from config import settings
    import psycopg2
    
    # Инициализация
    redis_url = 'redis://redis:6379'
    qdrant_url = 'http://qdrant:6333'
    neo4j_url = 'neo4j://neo4j:7687'
    
    task = IndexingTask(redis_url, qdrant_url, neo4j_url)
    task.redis_client = RedisStreamsClient(redis_url)
    await task.redis_client.connect()
    task.qdrant_client = QdrantClient(qdrant_url)
    await task.qdrant_client.connect()
    task.neo4j_client = Neo4jClient(
        uri=neo4j_url,
        username=os.getenv('NEO4J_USER', settings.neo4j_username),
        password=os.getenv('NEO4J_PASSWORD', settings.neo4j_password)
    )
    await task.neo4j_client.connect()
    
    from ai_providers.gigachain_adapter import create_gigachain_adapter
    from ai_providers.embedding_service import create_embedding_service
    ai_adapter = await create_gigachain_adapter()
    task.embedding_service = await create_embedding_service(ai_adapter)
    task.publisher = EventPublisher(task.redis_client)
    
    # Получение и обработка постов
    db_url = 'postgresql://postgres:postgres@supabase-db:5432/postgres'
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.id FROM posts p
        INNER JOIN indexing_status is_ ON p.id = is_.post_id
        WHERE is_.embedding_status = 'pending'
        ORDER BY p.created_at DESC LIMIT 10
    ''')
    post_ids = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    
    for post_id in post_ids:
        try:
            message = {'payload': {'post_id': post_id}}
            await task._process_single_message(message)
            print(f'Processed: {post_id}')
        except Exception as e:
            print(f'Error: {post_id} - {e}')
    
    await task.redis_client.disconnect()
    await task.neo4j_client.close()

asyncio.run(process())
"
```

## Статус исправлений

✅ Restart policy для worker
✅ Prometheus alerts для мониторинга
✅ Исправлены SQL запросы (должны использовать `content as text`)
✅ Исправлены ссылки на `updated_at` (должны быть удалены)
⏳ Ожидается: пересборка контейнера с исправленным кодом

## Проверка результатов

После обработки проверить:
```sql
SELECT embedding_status, COUNT(*) FROM indexing_status GROUP BY embedding_status;
```

И в Qdrant:
```python
from qdrant_client import QdrantClient
qc = QdrantClient(url='http://qdrant:6333')
col = qc.get_collection('telegram_posts')
print(f'Qdrant points: {col.points_count}')
```

