#!/bin/bash
# Мониторинг новых постов с OCR и проверка записи entities
# Context7: Автоматический мониторинг обработки OCR entities

echo "=== Мониторинг новых постов с OCR ==="
echo ""

# Проверка новых постов за последний час
echo "📊 Поиск новых постов с OCR (за последний час)..."
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    post_id,
    created_at,
    jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) as entities_count,
    LENGTH(data->'ocr'->>'text') as ocr_text_length,
    data->'ocr'->>'engine' as ocr_engine
FROM post_enrichment
WHERE kind = 'vision'
  AND data->'ocr' IS NOT NULL
  AND data->'ocr' != 'null'::jsonb
  AND data->'ocr'->>'text' IS NOT NULL
  AND created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC
LIMIT 10;
"

echo ""
echo "=== Проверка записи entities в Neo4j ==="
echo ""

# Проверка количества OCR entities
echo "📊 Общее количество OCR entities в Neo4j:"
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (e:Entity {source: 'ocr'}) RETURN count(e) as total_ocr_entities;" 2>&1 | grep -v "cypher-shell\|^$"

echo ""
echo "📊 Статистика по типам entities:"
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (e:Entity {source: 'ocr'}) RETURN e.type as type, count(e) as count ORDER BY count DESC LIMIT 10;" 2>&1 | grep -v "cypher-shell\|^$"

echo ""
echo "=== Проверка логов entity extraction (последние 10 минут) ==="
echo ""

# Проверка логов
docker logs telegram-assistant-worker-1 2>&1 --since 10m | grep -iE "entity extraction|extract_entities|LLM response.*entity" | tail -10

echo ""
echo "=== Проверка ошибок entity extraction ==="
echo ""

# Проверка ошибок
docker logs telegram-assistant-worker-1 2>&1 --since 10m | grep -iE "error.*entity|failed.*entity|Entity extraction failed" | tail -5

echo ""
echo "✅ Мониторинг завершен"

