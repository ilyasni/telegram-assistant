#!/bin/bash
# Аудит процесса добавления постов и альбомов
# Context7: Комплексная диагностика потерь постов

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=================================================================================="
echo "АУДИТ ПРОЦЕССА ДОБАВЛЕНИЯ ПОСТОВ И АЛЬБОМОВ"
echo "Context7: Проверка цепочки от парсинга до сохранения"
echo "=================================================================================="
echo ""

# ============================================================================
# 1. СТАТИСТИКА ПОСТОВ В БД
# ============================================================================
echo "1. СТАТИСТИКА ПОСТОВ В БД"
echo "----------------------------------------------------------------------------------"

POSTS_STATS=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT 
    COUNT(*)::text || '|' ||
    COUNT(DISTINCT channel_id)::text || '|' ||
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours')::text || '|' ||
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour')::text || '|' ||
    MIN(created_at)::text || '|' ||
    MAX(created_at)::text
FROM posts;
" 2>/dev/null | tr -d ' ' || echo "")

if [ -n "$POSTS_STATS" ]; then
    IFS='|' read -r TOTAL CHANNELS POSTS_24H POSTS_1H OLDEST NEWEST <<< "$POSTS_STATS"
    echo "  Всего постов: $TOTAL"
    echo "  Каналов с постами: $CHANNELS"
    echo "  Постов за 24 часа: $POSTS_24H"
    echo "  Постов за 1 час: $POSTS_1H"
    echo "  Самый старый пост: $OLDEST"
    echo "  Самый новый пост: $NEWEST"
else
    echo -e "  ${RED}❌ Не удалось получить статистику${NC}"
fi

echo ""

# ============================================================================
# 2. СТАТИСТИКА АЛЬБОМОВ В БД
# ============================================================================
echo "2. СТАТИСТИКА АЛЬБОМОВ В БД"
echo "----------------------------------------------------------------------------------"

ALBUMS_STATS=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT 
    COUNT(*)::text || '|' ||
    COUNT(DISTINCT channel_id)::text || '|' ||
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours')::text || '|' ||
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour')::text
FROM albums;
" 2>/dev/null | tr -d ' ' || echo "")

if [ -n "$ALBUMS_STATS" ]; then
    IFS='|' read -r TOTAL CHANNELS ALBUMS_24H ALBUMS_1H <<< "$ALBUMS_STATS"
    echo "  Всего альбомов: $TOTAL"
    echo "  Каналов с альбомами: $CHANNELS"
    echo "  Альбомов за 24 часа: $ALBUMS_24H"
    echo "  Альбомов за 1 час: $ALBUMS_1H"
else
    echo -e "  ${YELLOW}⚠️  Альбомов нет или ошибка запроса${NC}"
fi

echo ""

# ============================================================================
# 3. ПРОВЕРКА ПОТЕРИ ПОСТОВ (GAPS)
# ============================================================================
echo "3. ПРОВЕРКА ПРОПУСКОВ ПОСТОВ (GAPS)"
echo "----------------------------------------------------------------------------------"

GAPS_CHECK=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
WITH post_gaps AS (
    SELECT 
        p.channel_id,
        c.title,
        p.telegram_message_id as prev_id,
        LEAD(p.telegram_message_id) OVER (PARTITION BY p.channel_id ORDER BY p.telegram_message_id) as next_id,
        p.posted_at,
        LEAD(p.posted_at) OVER (PARTITION BY p.channel_id ORDER BY p.telegram_message_id) as next_posted_at
    FROM posts p
    JOIN channels c ON p.channel_id = c.id
    WHERE c.is_active = true
)
SELECT COUNT(*)::text || '|' || 
       COUNT(*) FILTER (WHERE next_id - prev_id > 1)::text
FROM post_gaps
WHERE next_id IS NOT NULL
LIMIT 1;
" 2>/dev/null | tr -d ' ' || echo "")

if [ -n "$GAPS_CHECK" ]; then
    IFS='|' read -r TOTAL_PAIRS GAPS_COUNT <<< "$GAPS_CHECK"
    echo "  Всего пар постов: $TOTAL_PAIRS"
    echo "  Пропусков (gap > 1): $GAPS_COUNT"
    
    if [ "$GAPS_COUNT" -gt 0 ]; then
        echo -e "  ${YELLOW}⚠️  Обнаружены пропуски в message_id${NC}"
    else
        echo -e "  ${GREEN}✅ Пропусков не обнаружено${NC}"
    fi
fi

echo ""

# ============================================================================
# 4. ПРОВЕРКА ПОСТОВ С GROUPED_ID (АЛЬБОМЫ)
# ============================================================================
echo "4. ПРОВЕРКА ПОСТОВ В АЛЬБОМАХ"
echo "----------------------------------------------------------------------------------"

ALBUM_POSTS=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT 
    COUNT(*)::text || '|' ||
    COUNT(DISTINCT grouped_id)::text || '|' ||
    COUNT(DISTINCT channel_id)::text
FROM posts
WHERE grouped_id IS NOT NULL;
" 2>/dev/null | tr -d ' ' || echo "")

if [ -n "$ALBUM_POSTS" ]; then
    IFS='|' read -r TOTAL_ALBUM_POSTS ALBUM_GROUPS CHANNELS <<< "$ALBUM_POSTS"
    echo "  Постов в альбомах: $TOTAL_ALBUM_POSTS"
    echo "  Уникальных альбомов: $ALBUM_GROUPS"
    echo "  Каналов с альбомами: $CHANNELS"
fi

echo ""

# ============================================================================
# 5. МЕТРИКИ ПАРСИНГА (Prometheus)
# ============================================================================
echo "5. МЕТРИКИ ПАРСИНГА (Prometheus)"
echo "----------------------------------------------------------------------------------"

PARSED_POSTS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(posts_parsed_total)" 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '0')" 2>/dev/null || echo "0")

if [ -n "$PARSED_POSTS" ] && [ "$PARSED_POSTS" != "0" ]; then
    echo "  Всего обработано постов (метрика): $PARSED_POSTS"
    
    # Сравнение с БД
    if [ -n "$POSTS_STATS" ]; then
        DIFF=$((POSTS_STATS - PARSED_POSTS))
        if [ "$DIFF" -lt 0 ]; then
            DIFF=$((PARSED_POSTS - POSTS_STATS))
            echo -e "  ${YELLOW}⚠️  Разница: метрика показывает больше чем в БД (+$DIFF)${NC}"
        elif [ "$DIFF" -gt 100 ]; then
            echo -e "  ${YELLOW}⚠️  Разница: в БД больше чем в метрике (-$DIFF)${NC}"
        else
            echo -e "  ${GREEN}✅ Метрики соответствуют БД${NC}"
        fi
    fi
else
    echo -e "  ${YELLOW}⚠️  Метрики недоступны${NC}"
fi

echo ""

# ============================================================================
# 6. ПРОВЕРКА ЛОГОВ ПАРСИНГА
# ============================================================================
echo "6. АНАЛИЗ ЛОГОВ ПАРСИНГА"
echo "----------------------------------------------------------------------------------"

SKIPPED_COUNT=$(docker compose logs telethon-ingest --since 24h 2>&1 | grep -iE "(skipped|duplicate|already exists)" | wc -l)
PROCESSED_COUNT=$(docker compose logs telethon-ingest --since 24h 2>&1 | grep -iE "(messages.*processed|Published.*post)" | wc -l)

echo "  Пропущенных постов (по логам): $SKIPPED_COUNT"
echo "  Обработанных постов (по логам): $PROCESSED_COUNT"

if [ "$SKIPPED_COUNT" -gt 0 ]; then
    echo -e "  ${YELLOW}⚠️  Есть пропуски в логах${NC}"
    echo ""
    echo "  Примеры пропусков:"
    docker compose logs telethon-ingest --since 24h 2>&1 | grep -iE "(skipped|duplicate|already exists)" | head -3 | sed 's/^/    /'
fi

echo ""

# ============================================================================
# 7. ПРОВЕРКА КАНАЛОВ С МНОЖЕСТВЕННЫМИ ПОСТАМИ
# ============================================================================
echo "7. ТОП КАНАЛОВ ПО КОЛИЧЕСТВУ ПОСТОВ"
echo "----------------------------------------------------------------------------------"

docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    c.title,
    c.username,
    COUNT(p.id) as posts_count,
    MAX(p.posted_at) as last_post,
    COUNT(DISTINCT DATE(p.posted_at)) as days_active
FROM channels c
JOIN posts p ON p.channel_id = c.id
WHERE c.is_active = true
GROUP BY c.id, c.title, c.username
ORDER BY posts_count DESC
LIMIT 5;
" 2>&1 | grep -v "rows)" | tail -6 | sed 's/^/  /'

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""

echo "Обнаруженные проблемы:"
echo "  1. Проверьте пропуски в message_id (gap > 1)"
echo "  2. Сравните метрики парсинга с количеством в БД"
echo "  3. Проверьте логи на пропуски и дубликаты"
echo ""
echo "Рекомендации (Context7):"
echo "  1. Проверить логику фильтрации постов"
echo "  2. Проверить ON CONFLICT в bulk insert"
echo "  3. Проверить обработку альбомов"
echo "  4. Добавить метрики для потерь постов"

