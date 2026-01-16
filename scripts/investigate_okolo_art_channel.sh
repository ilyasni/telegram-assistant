#!/bin/bash
# Исследование проблемного канала "Около Искусства"
# Context7: Диагностика критической потери постов

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=================================================================================="
echo "ИССЛЕДОВАНИЕ ПРОБЛЕМНОГО КАНАЛА: Около Искусства (okolo_art)"
echo "Context7: Диагностика критической потери постов (99.99%)"
echo "=================================================================================="
echo ""

# ============================================================================
# 1. ОСНОВНАЯ ИНФОРМАЦИЯ О КАНАЛЕ
# ============================================================================
echo "1. ОСНОВНАЯ ИНФОРМАЦИЯ О КАНАЛЕ"
echo "----------------------------------------------------------------------------------"

CHANNEL_INFO=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT 
    c.id::text || '|' ||
    c.title || '|' ||
    COALESCE(c.username, 'NULL') || '|' ||
    c.tg_channel_id::text || '|' ||
    c.is_active::text || '|' ||
    COALESCE(c.last_parsed_at::text, 'NULL') || '|' ||
    COUNT(p.id)::text || '|' ||
    COALESCE(MIN(p.telegram_message_id)::text, 'NULL') || '|' ||
    COALESCE(MAX(p.telegram_message_id)::text, 'NULL')
FROM channels c
LEFT JOIN posts p ON p.channel_id = c.id
WHERE c.username = 'okolo_art' OR c.title LIKE '%Около Искусства%'
GROUP BY c.id, c.title, c.username, c.tg_channel_id, c.is_active, c.last_parsed_at
LIMIT 1;
" 2>/dev/null | tr -d ' ' || echo "")

if [ -n "$CHANNEL_INFO" ]; then
    IFS='|' read -r CHANNEL_ID TITLE USERNAME TG_CHANNEL_ID IS_ACTIVE LAST_PARSED POSTS_COUNT MIN_ID MAX_ID <<< "$CHANNEL_INFO"
    echo "  ID канала: $CHANNEL_ID"
    echo "  Название: $TITLE"
    echo "  Username: $USERNAME"
    echo "  Telegram ID: $TG_CHANNEL_ID"
    echo "  Активен: $IS_ACTIVE"
    echo "  Последний парсинг: $LAST_PARSED"
    echo "  Постов в БД: $POSTS_COUNT"
    echo "  Диапазон message_id: $MIN_ID - $MAX_ID"
    
    if [ "$MIN_ID" != "NULL" ] && [ "$MAX_ID" != "NULL" ]; then
        EXPECTED=$((MAX_ID - MIN_ID + 1))
        MISSING=$((EXPECTED - POSTS_COUNT))
        COVERAGE=$(echo "scale=2; $POSTS_COUNT * 100 / $EXPECTED" | bc 2>/dev/null || echo "0.01")
        echo "  Ожидается постов: $EXPECTED"
        echo "  Пропущено: $MISSING"
        echo "  Покрытие: ${COVERAGE}%"
    fi
else
    echo -e "  ${RED}❌ Канал не найден${NC}"
    exit 1
fi

echo ""

# ============================================================================
# 2. ПРОВЕРКА ПОДПИСОК
# ============================================================================
echo "2. ПРОВЕРКА ПОДПИСОК"
echo "----------------------------------------------------------------------------------"

SUBSCRIPTIONS=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT 
    COUNT(*)::text || '|' ||
    COUNT(*) FILTER (WHERE is_active = true)::text || '|' ||
    COUNT(*) FILTER (WHERE is_active = false)::text
FROM user_channel uc
JOIN channels c ON uc.channel_id = c.id
WHERE c.username = 'okolo_art' OR c.title LIKE '%Около Искусства%';
" 2>/dev/null | tr -d ' ' || echo "")

if [ -n "$SUBSCRIPTIONS" ]; then
    IFS='|' read -r TOTAL ACTIVE INACTIVE <<< "$SUBSCRIPTIONS"
    echo "  Всего подписок: $TOTAL"
    echo "  Активных: $ACTIVE"
    echo "  Неактивных: $INACTIVE"
    
    if [ "$ACTIVE" -eq 0 ]; then
        echo -e "  ${YELLOW}⚠️  Нет активных подписок${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠️  Подписки не найдены${NC}"
fi

echo ""

# ============================================================================
# 3. АНАЛИЗ GAPS В MESSAGE_ID
# ============================================================================
echo "3. АНАЛИЗ ПРОПУСКОВ В MESSAGE_ID"
echo "----------------------------------------------------------------------------------"

docker compose exec -T supabase-db psql -U postgres -d postgres -c "
WITH post_gaps AS (
    SELECT 
        p.telegram_message_id as current_id,
        LEAD(p.telegram_message_id) OVER (ORDER BY p.telegram_message_id) as next_id,
        LEAD(p.telegram_message_id) OVER (ORDER BY p.telegram_message_id) - p.telegram_message_id as gap_size
    FROM posts p
    JOIN channels c ON p.channel_id = c.id
    WHERE (c.username = 'okolo_art' OR c.title LIKE '%Около Искусства%')
      AND c.is_active = true
    ORDER BY p.telegram_message_id
)
SELECT 
    CASE 
        WHEN gap_size <= 10 THEN '1-10'
        WHEN gap_size <= 100 THEN '11-100'
        WHEN gap_size <= 1000 THEN '101-1000'
        WHEN gap_size <= 10000 THEN '1001-10000'
        ELSE '>10000'
    END as gap_range,
    COUNT(*) as gap_count,
    MIN(gap_size) as min_gap,
    MAX(gap_size) as max_gap
FROM post_gaps
WHERE gap_size > 1
GROUP BY 
    CASE 
        WHEN gap_size <= 10 THEN '1-10'
        WHEN gap_size <= 100 THEN '11-100'
        WHEN gap_size <= 1000 THEN '101-1000'
        WHEN gap_size <= 10000 THEN '1001-10000'
        ELSE '>10000'
    END
ORDER BY min_gap;
" 2>&1 | grep -v "rows)" | tail -10 | sed 's/^/  /'

echo ""

# ============================================================================
# 4. ПРОВЕРКА ЛОГОВ ПАРСИНГА
# ============================================================================
echo "4. АНАЛИЗ ЛОГОВ ПАРСИНГА"
echo "----------------------------------------------------------------------------------"

LOG_COUNT=$(docker compose logs telethon-ingest --since 24h 2>&1 | grep -iE "(okolo_art|Около Искусства)" | wc -l)
echo "  Упоминаний в логах (24ч): $LOG_COUNT"

if [ "$LOG_COUNT" -gt 0 ]; then
    echo ""
    echo "  Последние упоминания:"
    docker compose logs telethon-ingest --since 24h 2>&1 | grep -iE "(okolo_art|Около Искусства)" | tail -5 | sed 's/^/    /'
else
    echo -e "  ${YELLOW}⚠️  Канал не упоминается в логах за последние 24 часа${NC}"
fi

echo ""

# ============================================================================
# 5. ПРОВЕРКА ПОСЛЕДНИХ ПОСТОВ
# ============================================================================
echo "5. ПОСЛЕДНИЕ ПОСТЫ В БД"
echo "----------------------------------------------------------------------------------"

docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    telegram_message_id,
    posted_at,
    created_at,
    has_media,
    CASE WHEN content IS NULL OR content = '' THEN 'empty' ELSE 'has_text' END as content_status
FROM posts p
JOIN channels c ON p.channel_id = c.id
WHERE (c.username = 'okolo_art' OR c.title LIKE '%Около Искусства%')
  AND c.is_active = true
ORDER BY telegram_message_id DESC
LIMIT 10;
" 2>&1 | grep -v "rows)" | tail -12 | sed 's/^/  /'

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""

echo "Обнаруженные проблемы:"
echo "  1. Проверьте диапазон message_id (возможно, слишком большой)"
echo "  2. Проверьте наличие активных подписок"
echo "  3. Проверьте логи парсинга на наличие ошибок"
echo "  4. Проверьте, парсится ли канал вообще"
echo ""
echo "Рекомендации (Context7):"
echo "  1. Проверить логику парсинга для больших диапазонов message_id"
echo "  2. Проверить фильтрацию постов"
echo "  3. Добавить специальное логирование для этого канала"
echo "  4. Проверить, не блокируется ли канал"

