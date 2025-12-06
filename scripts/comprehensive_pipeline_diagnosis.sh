#!/bin/bash
# Комплексная диагностика всего пайплайна постов и альбомов
# Context7: Проверка всех этапов от парсинга до сохранения в БД, Qdrant и Neo4j

set -euo pipefail

echo "=================================================================================="
echo "COMPREHENSIVE PIPELINE DIAGNOSIS"
echo "Context7: Полная проверка пайплайна постов и альбомов"
echo "=================================================================================="
echo ""

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
REPORT_FILE="reports/pipeline_diagnosis_$(date +%Y%m%d_%H%M%S).md"

mkdir -p reports

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Функция для проверки статуса
check_status() {
    local status=$1
    local message=$2
    if [ "$status" = "healthy" ] || [ "$status" = "ok" ]; then
        echo -e "${GREEN}✅ $message${NC}"
    elif [ "$status" = "warning" ] || [ "$status" = "degraded" ]; then
        echo -e "${YELLOW}⚠️  $message${NC}"
    else
        echo -e "${RED}❌ $message${NC}"
    fi
}

# Счетчики
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

# ============================================================================
# 1. ПАРСИНГ ПОСТОВ И АЛЬБОМОВ
# ============================================================================
echo "1. ПАРСИНГ ПОСТОВ И АЛЬБОМОВ"
echo "----------------------------------------------------------------------------------"

# Определяем имя БД из переменных окружения
DB_NAME=${POSTGRES_DB:-postgres}

# Проверка постов в БД
POSTS_TOTAL=$(docker compose exec -T supabase-db psql -U postgres -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM posts;" 2>/dev/null | tr -d ' ' || echo "0")
POSTS_LAST_24H=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '24 hours';" 2>/dev/null | tr -d ' ' || echo "0")
POSTS_LAST_HOUR=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '1 hour';" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Посты в БД:"
echo "    - Всего: $POSTS_TOTAL"
echo "    - За 24 часа: $POSTS_LAST_24H"
echo "    - За 1 час: $POSTS_LAST_HOUR"

if [ "$POSTS_TOTAL" -gt 0 ]; then
    if [ "$POSTS_LAST_24H" -gt 0 ]; then
        check_status "healthy" "Парсинг работает (есть посты за 24 часа)"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        check_status "degraded" "Нет новых постов за 24 часа"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
else
    check_status "unhealthy" "Нет постов в БД"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# Проверка альбомов (media_groups)
ALBUMS_TOTAL=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "SELECT COUNT(*) FROM media_groups;" 2>/dev/null | tr -d ' ' || echo "0")
ALBUMS_LAST_24H=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "SELECT COUNT(*) FROM media_groups WHERE created_at > NOW() - INTERVAL '24 hours';" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Альбомы в БД:"
echo "    - Всего: $ALBUMS_TOTAL"
echo "    - За 24 часа: $ALBUMS_LAST_24H"

# Проверка Redis Streams для парсинга
PARSED_STREAM_LEN=$(docker compose exec -T redis redis-cli XLEN stream:posts:parsed 2>/dev/null || echo "0")
echo "  Redis Stream stream:posts:parsed: $PARSED_STREAM_LEN сообщений"

echo ""

# ============================================================================
# 2. VISION АНАЛИЗ
# ============================================================================
echo "2. VISION АНАЛИЗ"
echo "----------------------------------------------------------------------------------"

# Проверка Redis Streams для Vision
VISION_UPLOADED=$(docker compose exec -T redis redis-cli XLEN stream:posts:vision:uploaded 2>/dev/null || echo "0")
VISION_ANALYZED=$(docker compose exec -T redis redis-cli XLEN stream:posts:vision:analyzed 2>/dev/null || echo "0")

echo "  Redis Streams:"
echo "    - stream:posts:vision:uploaded: $VISION_UPLOADED"
echo "    - stream:posts:vision:analyzed: $VISION_ANALYZED"

if [ "$VISION_UPLOADED" -gt 0 ] && [ "$VISION_ANALYZED" -gt 0 ]; then
    LAG=$((VISION_UPLOADED - VISION_ANALYZED))
    echo "    - Lag: $LAG"
    
    if [ "$LAG" -gt 100 ]; then
        check_status "warning" "Высокий lag между uploaded и analyzed: $LAG"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    else
        check_status "healthy" "Vision анализ работает"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    fi
else
    check_status "warning" "Vision streams пусты или не работают"
    WARNING_CHECKS=$((WARNING_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# Проверка Vision enrichments в БД
VISION_ENRICHMENTS=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(DISTINCT post_id) 
    FROM post_enrichment 
    WHERE kind = 'vision' AND status = 'ok';
" 2>/dev/null | tr -d ' ' || echo "0")

VISION_FAILED=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(*) 
    FROM post_enrichment 
    WHERE kind = 'vision' AND status = 'error';
" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Vision enrichments в БД:"
echo "    - Успешных: $VISION_ENRICHMENTS"
echo "    - Ошибок: $VISION_FAILED"

if [ "$VISION_ENRICHMENTS" -gt 0 ]; then
    SUCCESS_RATE=$(echo "scale=2; $VISION_ENRICHMENTS * 100 / ($VISION_ENRICHMENTS + $VISION_FAILED)" | bc 2>/dev/null || echo "0")
    echo "    - Success rate: ${SUCCESS_RATE}%"
    
    if (( $(echo "$SUCCESS_RATE > 90" | bc -l) )); then
        check_status "healthy" "Vision enrichments успешно сохраняются"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        check_status "degraded" "Низкий success rate: ${SUCCESS_RATE}%"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
else
    check_status "unhealthy" "Нет vision enrichments в БД"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 3. ТЕГИРОВАНИЕ
# ============================================================================
echo "3. ТЕГИРОВАНИЕ"
echo "----------------------------------------------------------------------------------"

# Проверка Redis Stream
TAGGED_STREAM=$(docker compose exec -T redis redis-cli XLEN stream:posts:tagged 2>/dev/null || echo "0")
echo "  Redis Stream stream:posts:tagged: $TAGGED_STREAM сообщений"

# Проверка tag enrichments в БД
TAG_ENRICHMENTS=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(DISTINCT post_id) 
    FROM post_enrichment 
    WHERE kind = 'tags' AND status = 'ok';
" 2>/dev/null | tr -d ' ' || echo "0")

# Проверка постов с тегами в таблице posts
# Проверка постов с тегами (теги могут быть в post_enrichment или в другом поле)
POSTS_WITH_TAGS=$(docker compose exec -T supabase-db psql -U postgres -d "$DB_NAME" -t -c "
    SELECT COUNT(DISTINCT post_id) 
    FROM post_enrichment 
    WHERE kind = 'tags' AND status = 'ok';
" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Tag enrichments в БД: $TAG_ENRICHMENTS"
echo "  Посты с тегами: $POSTS_WITH_TAGS"

if [ "$TAG_ENRICHMENTS" -gt 0 ]; then
    check_status "healthy" "Тегирование работает"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    check_status "unhealthy" "Нет tag enrichments в БД"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 4. ОБОГАЩЕНИЕ CRAWL4AI
# ============================================================================
echo "4. ОБОГАЩЕНИЕ CRAWL4AI"
echo "----------------------------------------------------------------------------------"

# Проверка Redis Streams
CRAWL_STREAM=$(docker compose exec -T redis redis-cli XLEN stream:posts:crawl 2>/dev/null || echo "0")
ENRICHED_STREAM=$(docker compose exec -T redis redis-cli XLEN stream:posts:enriched 2>/dev/null || echo "0")

echo "  Redis Streams:"
echo "    - stream:posts:crawl: $CRAWL_STREAM"
echo "    - stream:posts:enriched: $ENRICHED_STREAM"

# Проверка crawl enrichments в БД
CRAWL_ENRICHMENTS=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(DISTINCT post_id) 
    FROM post_enrichment 
    WHERE kind = 'crawl' AND status = 'ok';
" 2>/dev/null | tr -d ' ' || echo "0")

CRAWL_LAST_24H=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(DISTINCT post_id) 
    FROM post_enrichment 
    WHERE kind = 'crawl' AND status = 'ok' AND created_at > NOW() - INTERVAL '24 hours';
" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Crawl enrichments в БД:"
echo "    - Всего: $CRAWL_ENRICHMENTS"
echo "    - За 24 часа: $CRAWL_LAST_24H"

if [ "$CRAWL_ENRICHMENTS" -gt 0 ]; then
    check_status "healthy" "Crawl4AI обогащение работает"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    check_status "warning" "Нет crawl enrichments (может быть нормально)"
    WARNING_CHECKS=$((WARNING_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 5. СОХРАНЕНИЕ В БД
# ============================================================================
echo "5. СОХРАНЕНИЕ В БД"
echo "----------------------------------------------------------------------------------"

# Проверка всех enrichments
ALL_ENRICHMENTS=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(*) 
    FROM post_enrichment;
" 2>/dev/null | tr -d ' ' || echo "0")

UNIQUE_POSTS_ENRICHED=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(DISTINCT post_id) 
    FROM post_enrichment;
" 2>/dev/null | tr -d ' ' || echo "0")

ENRICHMENT_KINDS=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(DISTINCT kind) 
    FROM post_enrichment;
" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Enrichments в БД:"
echo "    - Всего записей: $ALL_ENRICHMENTS"
echo "    - Уникальных постов: $UNIQUE_POSTS_ENRICHED"
echo "    - Типов обогащений: $ENRICHMENT_KINDS"

# Проверка постов с текстом
POSTS_WITH_TEXT=$(docker compose exec -T supabase-db psql -U postgres -d "$DB_NAME" -t -c "
    SELECT COUNT(*) 
    FROM posts 
    WHERE content IS NOT NULL AND content != '';
" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Посты с текстом: $POSTS_WITH_TEXT / $POSTS_TOTAL"

if [ "$POSTS_TOTAL" -gt 0 ] && [ "$ALL_ENRICHMENTS" -gt 0 ]; then
    check_status "healthy" "Данные сохраняются в БД"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    check_status "unhealthy" "Проблемы с сохранением в БД"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 6. ИНДЕКСАЦИЯ В QDRANT
# ============================================================================
echo "6. ИНДЕКСАЦИЯ В QDRANT"
echo "----------------------------------------------------------------------------------"

# Проверка Qdrant через API
QDRANT_COLLECTIONS=$(curl -s http://localhost:6333/collections 2>/dev/null | jq -r '.result.collections[]?.name' 2>/dev/null | wc -l || echo "0")
echo "  Коллекций в Qdrant: $QDRANT_COLLECTIONS"

# Получаем количество векторов (для первой коллекции или всех)
QDRANT_TOTAL_VECTORS=0
for collection in $(curl -s http://localhost:6333/collections 2>/dev/null | jq -r '.result.collections[]?.name' 2>/dev/null | head -5); do
    vectors=$(curl -s "http://localhost:6333/collections/$collection" 2>/dev/null | jq -r '.result.points_count' 2>/dev/null || echo "0")
    echo "    - $collection: $vectors векторов"
    QDRANT_TOTAL_VECTORS=$((QDRANT_TOTAL_VECTORS + vectors))
done

echo "  Всего векторов: $QDRANT_TOTAL_VECTORS"

# Проверка indexed статуса в БД
INDEXED_POSTS=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(DISTINCT post_id) 
    FROM post_enrichment 
    WHERE indexing_status = 'indexed';
" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Постов с indexing_status='indexed' в БД: $INDEXED_POSTS"

if [ "$QDRANT_TOTAL_VECTORS" -gt 0 ]; then
    if [ "$INDEXED_POSTS" -gt 0 ]; then
        DIFF=$((QDRANT_TOTAL_VECTORS - INDEXED_POSTS))
        if [ "${DIFF#-}" -lt 100 ]; then
            check_status "healthy" "Индексация в Qdrant работает"
            PASSED_CHECKS=$((PASSED_CHECKS + 1))
        else
            check_status "warning" "Несоответствие: Qdrant=$QDRANT_TOTAL_VECTORS, БД=$INDEXED_POSTS"
            WARNING_CHECKS=$((WARNING_CHECKS + 1))
        fi
    else
        check_status "warning" "Есть векторы в Qdrant, но нет indexing_status в БД"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
else
    check_status "unhealthy" "Нет векторов в Qdrant"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 7. ИНДЕКСАЦИЯ В NEO4J
# ============================================================================
echo "7. ИНДЕКСАЦИЯ В NEO4J"
echo "----------------------------------------------------------------------------------"

# Проверка Neo4j через curl (если доступен health endpoint)
NEO4J_POSTS=$(curl -s -u neo4j:changeme http://localhost:7474/db/neo4j/tx/commit -H "Content-Type: application/json" -d '{"statements":[{"statement":"MATCH (p:Post) RETURN count(p) as count"}]}' 2>/dev/null | jq -r '.results[0].data[0].row[0]' 2>/dev/null || echo "0")

NEO4J_CHANNELS=$(curl -s -u neo4j:changeme http://localhost:7474/db/neo4j/tx/commit -H "Content-Type: application/json" -d '{"statements":[{"statement":"MATCH (c:Channel) RETURN count(c) as count"}]}' 2>/dev/null | jq -r '.results[0].data[0].row[0]' 2>/dev/null || echo "0")

NEO4J_TAGS=$(curl -s -u neo4j:changeme http://localhost:7474/db/neo4j/tx/commit -H "Content-Type: application/json" -d '{"statements":[{"statement":"MATCH (t:Tag) RETURN count(t) as count"}]}' 2>/dev/null | jq -r '.results[0].data[0].row[0]' 2>/dev/null || echo "0")

NEO4J_RELATIONSHIPS=$(curl -s -u neo4j:changeme http://localhost:7474/db/neo4j/tx/commit -H "Content-Type: application/json" -d '{"statements":[{"statement":"MATCH ()-[r]->() RETURN count(r) as count"}]}' 2>/dev/null | jq -r '.results[0].data[0].row[0]' 2>/dev/null || echo "0")

echo "  Узлы в Neo4j:"
echo "    - Posts: $NEO4J_POSTS"
echo "    - Channels: $NEO4J_CHANNELS"
echo "    - Tags: $NEO4J_TAGS"
echo "  Связей: $NEO4J_RELATIONSHIPS"

if [ "$NEO4J_POSTS" -gt 0 ]; then
    check_status "healthy" "Индексация в Neo4j работает"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    check_status "unhealthy" "Нет постов в Neo4j"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 8. ПАЙПЛАЙН АЛЬБОМОВ
# ============================================================================
echo "8. ПАЙПЛАЙН АЛЬБОМОВ"
echo "----------------------------------------------------------------------------------"

# Проверка Redis Streams для альбомов
ALBUMS_PARSED_STREAM=$(docker compose exec -T redis redis-cli XLEN stream:albums:parsed 2>/dev/null || echo "0")
ALBUMS_ASSEMBLED_STREAM=$(docker compose exec -T redis redis-cli XLEN stream:albums:assembled 2>/dev/null || echo "0")

echo "  Redis Streams:"
echo "    - stream:albums:parsed: $ALBUMS_PARSED_STREAM"
echo "    - stream:albums:assembled: $ALBUMS_ASSEMBLED_STREAM"

# Проверка альбомов с enrichment
ALBUMS_WITH_ENRICHMENT=$(docker compose exec -T supabase-db psql -U postgres -d telegram_assistant -t -c "
    SELECT COUNT(*) 
    FROM media_groups 
    WHERE meta->>'enrichment' IS NOT NULL;
" 2>/dev/null | tr -d ' ' || echo "0")

echo "  Альбомов с enrichment: $ALBUMS_WITH_ENRICHMENT / $ALBUMS_TOTAL"

if [ "$ALBUMS_TOTAL" -gt 0 ]; then
    if [ "$ALBUMS_WITH_ENRICHMENT" -gt 0 ]; then
        check_status "healthy" "Пайплайн альбомов работает"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        check_status "warning" "Альбомы есть, но без enrichment"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
else
    check_status "warning" "Нет альбомов (может быть нормально)"
    WARNING_CHECKS=$((WARNING_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 9. REDIS STREAMS - PENDING MESSAGES
# ============================================================================
echo "9. REDIS STREAMS - PENDING MESSAGES"
echo "----------------------------------------------------------------------------------"

# Проверка pending сообщений в основных streams
STREAMS=(
    "stream:posts:parsed"
    "stream:posts:tagged"
    "stream:posts:vision:analyzed"
    "stream:posts:enriched"
    "stream:posts:indexed"
)

TOTAL_PENDING=0
for stream in "${STREAMS[@]}"; do
    # Получаем группы и их pending
    GROUPS=$(docker compose exec -T redis redis-cli XINFO GROUPS "$stream" 2>/dev/null | grep -E "^name|^pending" | paste - - | awk '{print $2" "$4}' || echo "")
    
    if [ -n "$GROUPS" ]; then
        while IFS= read -r line; do
            GROUP_NAME=$(echo "$line" | awk '{print $1}')
            PENDING=$(echo "$line" | awk '{print $2}')
            
            if [ "$PENDING" -gt 0 ]; then
                echo "    - $stream ($GROUP_NAME): $PENDING pending"
                TOTAL_PENDING=$((TOTAL_PENDING + PENDING))
            fi
        done <<< "$GROUPS"
    fi
done

echo "  Всего pending сообщений: $TOTAL_PENDING"

if [ "$TOTAL_PENDING" -gt 100 ]; then
    check_status "warning" "Высокое количество pending сообщений: $TOTAL_PENDING"
    WARNING_CHECKS=$((WARNING_CHECKS + 1))
elif [ "$TOTAL_PENDING" -gt 0 ]; then
    check_status "healthy" "Есть pending сообщения, но в пределах нормы"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    check_status "healthy" "Нет pending сообщений"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo "Timestamp: $TIMESTAMP"
echo ""
echo "Total checks: $TOTAL_CHECKS"
echo -e "${GREEN}Passed: $PASSED_CHECKS${NC}"
echo -e "${YELLOW}Warnings: $WARNING_CHECKS${NC}"
echo -e "${RED}Failed: $FAILED_CHECKS${NC}"
echo ""

# Определяем общий статус
if [ "$FAILED_CHECKS" -eq 0 ] && [ "$WARNING_CHECKS" -eq 0 ]; then
    OVERALL_STATUS="healthy"
    echo -e "${GREEN}Overall Status: HEALTHY ✅${NC}"
elif [ "$FAILED_CHECKS" -eq 0 ]; then
    OVERALL_STATUS="degraded"
    echo -e "${YELLOW}Overall Status: DEGRADED ⚠️${NC}"
else
    OVERALL_STATUS="unhealthy"
    echo -e "${RED}Overall Status: UNHEALTHY ❌${NC}"
fi

echo ""
echo "=================================================================================="

# Сохраняем отчет
cat > "$REPORT_FILE" << EOF
# Комплексная диагностика пайплайна

**Дата**: $TIMESTAMP  
**Общий статус**: $OVERALL_STATUS

## Статистика

- Всего проверок: $TOTAL_CHECKS
- ✅ Пройдено: $PASSED_CHECKS
- ⚠️  Предупреждения: $WARNING_CHECKS
- ❌ Ошибок: $FAILED_CHECKS

## Результаты проверок

### 1. Парсинг постов и альбомов
- Всего постов: $POSTS_TOTAL
- Постов за 24 часа: $POSTS_LAST_24H
- Постов за 1 час: $POSTS_LAST_HOUR
- Всего альбомов: $ALBUMS_TOTAL
- Альбомов за 24 часа: $ALBUMS_LAST_24H
- Redis Stream parsed: $PARSED_STREAM_LEN

### 2. Vision анализ
- Stream uploaded: $VISION_UPLOADED
- Stream analyzed: $VISION_ANALYZED
- Vision enrichments: $VISION_ENRICHMENTS
- Ошибок: $VISION_FAILED

### 3. Тегирование
- Stream tagged: $TAGGED_STREAM
- Tag enrichments: $TAG_ENRICHMENTS
- Постов с тегами: $POSTS_WITH_TAGS

### 4. Обогащение Crawl4AI
- Stream crawl: $CRAWL_STREAM
- Stream enriched: $ENRICHED_STREAM
- Crawl enrichments: $CRAWL_ENRICHMENTS
- За 24 часа: $CRAWL_LAST_24H

### 5. Сохранение в БД
- Всего enrichments: $ALL_ENRICHMENTS
- Уникальных постов: $UNIQUE_POSTS_ENRICHED
- Типов обогащений: $ENRICHMENT_KINDS
- Постов с текстом: $POSTS_WITH_TEXT

### 6. Индексация в Qdrant
- Коллекций: $QDRANT_COLLECTIONS
- Всего векторов: $QDRANT_TOTAL_VECTORS
- Постов indexed в БД: $INDEXED_POSTS

### 7. Индексация в Neo4j
- Posts: $NEO4J_POSTS
- Channels: $NEO4J_CHANNELS
- Tags: $NEO4J_TAGS
- Связей: $NEO4J_RELATIONSHIPS

### 8. Пайплайн альбомов
- Stream parsed: $ALBUMS_PARSED_STREAM
- Stream assembled: $ALBUMS_ASSEMBLED_STREAM
- Альбомов с enrichment: $ALBUMS_WITH_ENRICHMENT

### 9. Redis Streams Pending
- Всего pending: $TOTAL_PENDING

EOF

echo "📄 Полный отчет сохранен в: $REPORT_FILE"
echo ""

# Возвращаем код выхода
if [ "$FAILED_CHECKS" -eq 0 ]; then
    exit 0
else
    exit 1
fi

