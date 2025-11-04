# Проверка пайплайна альбомов на реальных данных

**Дата**: 2025-01-30

## Контекст

Проверка работы пайплайна альбомов на реальных данных после запуска ingestion.

## Команды для проверки

### 1. Проверка активных каналов

```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT id, telegram_channel_id, username, title, is_active, last_parsed_at 
FROM channels 
WHERE is_active = true 
ORDER BY created_at DESC 
LIMIT 10;
"
```

### 2. Проверка обработанных альбомов

```bash
# Количество альбомов
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total_albums,
    SUM(items_count) as total_items,
    AVG(items_count) as avg_items_per_album
FROM media_groups;
"

# Последние альбомы
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    id, 
    grouped_id, 
    channel_id, 
    items_count, 
    caption_text,
    posted_at,
    created_at,
    CASE 
        WHEN meta->>'enrichment' IS NOT NULL THEN 'yes' 
        ELSE 'no' 
    END as has_enrichment
FROM media_groups 
ORDER BY created_at DESC 
LIMIT 10;
"
```

### 3. Проверка Redis Streams

```bash
# Количество событий albums.parsed
docker exec telegram-assistant-redis-1 redis-cli XLEN stream:albums:parsed

# Количество событий album.assembled
docker exec telegram-assistant-redis-1 redis-cli XLEN stream:album:assembled

# Последние события albums.parsed
docker exec telegram-assistant-redis-1 redis-cli XREVRANGE stream:albums:parsed + - COUNT 5

# Активные состояния альбомов
docker exec telegram-assistant-redis-1 redis-cli KEYS "album:state:*" | wc -l
```

### 4. Проверка метрик

```bash
# Метрики альбомов
curl -s http://localhost:8001/metrics | grep -E "^albums_|^album_" | head -20

# Или через docker exec
docker exec telegram-assistant-worker-1 curl -s http://localhost:8001/metrics | grep -E "^albums_|^album_"
```

### 5. Проверка логов

```bash
# Логи ingestion (парсинг каналов)
docker logs telegram-assistant-telethon-ingest-1 --tail 100 | grep -i "album\|media_group"

# Логи worker (album_assembler_task)
docker logs telegram-assistant-worker-1 --tail 100 | grep -i "album"

# Проверка обработки vision
docker logs telegram-assistant-worker-1 --tail 100 | grep -i "vision.*album\|album.*vision"
```

### 6. Проверка health check

```bash
# Health check album_assembler_task
curl -s http://localhost:8000/health/detailed | jq '.tasks.album_assembler'

# Health check telethon-ingest
curl -s http://localhost:8011/health/details | jq '.scheduler'
```

## Что искать

### ✅ Признаки успешной работы

1. **В БД:**
   - `media_groups` содержит записи с `items_count > 1`
   - `media_group_items` содержит записи, связанные с `media_groups`
   - `media_groups.meta->>'enrichment'` присутствует для собранных альбомов

2. **В Redis:**
   - `stream:albums:parsed` содержит события после парсинга альбомов
   - `stream:album:assembled` содержит события после сборки альбомов
   - `album:state:*` ключи появляются и исчезают по мере обработки

3. **В метриках:**
   - `albums_parsed_total` > 0
   - `albums_assembled_total` > 0
   - `album_assembly_lag_seconds` показывает задержки сборки

4. **В логах:**
   - `telethon-ingest`: сообщения о сохранении media groups
   - `worker`: сообщения о сборке альбомов, агрегации vision summary

### ⚠️ Возможные проблемы

1. **Нет альбомов в БД:**
   - Проверить, что каналы активны и содержат альбомы
   - Проверить логи ingestion на ошибки парсинга

2. **Альбомы не собираются:**
   - Проверить, что `album_assembler_task` запущен
   - Проверить события `posts.vision.analyzed`
   - Проверить Redis state ключи

3. **Vision summary не сохраняется:**
   - Проверить S3 credentials
   - Проверить логи на ошибки сохранения

## Следующие шаги

После проверки:
1. ✅ Убедиться, что альбомы парсятся и сохраняются
2. ✅ Проверить, что album_assembler_task собирает альбомы
3. ✅ Убедиться, что vision summary агрегируется
4. ✅ Проверить индексацию в Qdrant и Neo4j

