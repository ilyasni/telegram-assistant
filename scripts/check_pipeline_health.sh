#!/bin/bash
# Скрипт для проверки здоровья пайплайна постов и альбомов

echo "🔍 Проверка пайплайна постов и альбомов..."
echo ""

# Redis Streams
echo "1. Redis Streams (длина и pending):"
STREAMS=(
    "stream:posts:parsed"
    "stream:posts:tagged"
    "stream:posts:vision:analyzed"
    "stream:posts:enriched"
    "stream:posts:indexed"
    "stream:albums:parsed"
    "stream:album:assembled"
)

for stream in "${STREAMS[@]}"; do
    LEN=$(docker compose exec -T redis redis-cli XLEN "$stream" 2>/dev/null || echo "0")
    echo "   $stream: $LEN сообщений"
    
    # Проверяем pending для всех групп
    GROUPS=$(docker compose exec -T redis redis-cli XINFO GROUPS "$stream" 2>/dev/null | grep "name" | awk '{print $2}')
    for group in $GROUPS; do
        PENDING=$(docker compose exec -T redis redis-cli XPENDING "$stream" "$group" 2>/dev/null | head -1 | awk '{print $1}')
        if [ "$PENDING" != "0" ] && [ -n "$PENDING" ]; then
            echo "      ⚠️  Группа $group: $PENDING pending сообщений"
        fi
    done
done
echo ""

# DLQ (Dead Letter Queues)
echo "2. Dead Letter Queues:"
DLQ_STREAMS=(
    "stream:posts:parsed:dlq"
    "stream:posts:tagged:dlq"
    "stream:posts:enriched:dlq"
    "stream:posts:indexed:dlq"
    "stream:posts:vision:analyzed:dlq"
    "stream:albums:parsed:dlq"
    "stream:album:assembled:dlq"
)

HAS_DLQ=0
for dlq in "${DLQ_STREAMS[@]}"; do
    LEN=$(docker compose exec -T redis redis-cli XLEN "$dlq" 2>/dev/null || echo "0")
    if [ "$LEN" != "0" ] && [ -n "$LEN" ]; then
        echo "   ❌ $dlq: $LEN сообщений"
        HAS_DLQ=1
    fi
done
if [ $HAS_DLQ -eq 0 ]; then
    echo "   ✅ Нет сообщений в DLQ"
fi
echo ""

# Consumer Groups Lag
echo "3. Lag в Consumer Groups:"
for stream in "${STREAMS[@]}"; do
    GROUPS_INFO=$(docker compose exec -T redis redis-cli XINFO GROUPS "$stream" 2>/dev/null)
    if [ -n "$GROUPS_INFO" ]; then
        LAG=$(echo "$GROUPS_INFO" | grep "lag" | head -1 | awk '{print $2}')
        if [ "$LAG" != "0" ] && [ -n "$LAG" ]; then
            GROUP_NAME=$(echo "$GROUPS_INFO" | grep -B 2 "lag.*$LAG" | grep "name" | head -1 | awk '{print $2}')
            echo "   ⚠️  $stream ($GROUP_NAME): lag $LAG"
        fi
    fi
done
echo ""

# Сервисы
echo "4. Статус сервисов:"
echo "   Worker: $(docker compose ps worker --format '{{.Status}}' 2>/dev/null || echo 'не найден')"
echo "   Telethon-ingest: $(docker compose ps telethon-ingest --format '{{.Status}}' 2>/dev/null || echo 'не найден')"
echo "   Crawl4AI: $(docker compose ps crawl4ai --format '{{.Status}}' 2>/dev/null || echo 'не найден')"
echo "   Qdrant: $(docker compose ps qdrant --format '{{.Status}}' 2>/dev/null || echo 'не найден')"
echo "   Neo4j: $(docker compose ps neo4j --format '{{.Status}}' 2>/dev/null || echo 'не найден')"
echo ""

# Ошибки в логах
echo "5. Ошибки в логах (последний час):"
WORKER_ERRORS=$(docker compose logs worker --since 1h 2>&1 | grep -E "ERROR|CRITICAL" -i | wc -l)
TELETHON_ERRORS=$(docker compose logs telethon-ingest --since 1h 2>&1 | grep -E "ERROR|CRITICAL" -i | wc -l)
echo "   Worker: $WORKER_ERRORS ошибок"
echo "   Telethon-ingest: $TELETHON_ERRORS ошибок"
echo ""

# База данных
echo "6. База данных (последние посты/альбомы):"
LAST_POST=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "SELECT MAX(created_at) FROM posts;" 2>/dev/null | xargs)
LAST_ALBUM=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "SELECT MAX(created_at) FROM albums;" 2>/dev/null | xargs)
echo "   Последний пост: ${LAST_POST:-не найден}"
echo "   Последний альбом: ${LAST_ALBUM:-не найден}"
echo ""

# Health checks
echo "7. Health checks:"
if timeout 3 curl -s http://localhost:6333/health >/dev/null 2>&1; then
    echo "   ✅ Qdrant доступен"
else
    echo "   ❌ Qdrant недоступен"
fi

if timeout 3 curl -s http://localhost:8008/health >/dev/null 2>&1; then
    echo "   ✅ Crawl4AI доступен"
else
    echo "   ⚠️  Crawl4AI недоступен"
fi
echo ""

# Сводка
echo "📊 Сводка:"
if [ $HAS_DLQ -eq 0 ] && [ "$WORKER_ERRORS" = "0" ] && [ "$TELETHON_ERRORS" = "0" ]; then
    echo "   ✅ Пайплайн работает нормально"
else
    echo "   ⚠️  Обнаружены проблемы в пайплайне"
fi
