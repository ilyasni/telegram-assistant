#!/bin/bash
# Проверка справедливости и полноты парсинга каналов
# Context7: Анализ fairness распределения парсинга

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=================================================================================="
echo "ПРОВЕРКА СПРАВЕДЛИВОСТИ И ПОЛНОТЫ ПАРСИНГА КАНАЛОВ"
echo "Context7: Анализ fairness и coverage парсинга"
echo "=================================================================================="
echo ""

# ============================================================================
# 1. ОСНОВНАЯ СТАТИСТИКА
# ============================================================================
echo "1. ОСНОВНАЯ СТАТИСТИКА КАНАЛОВ"
echo "----------------------------------------------------------------------------------"

CHANNELS_STATS=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT 
    COUNT(*)::text || '|' ||
    COUNT(*) FILTER (WHERE last_parsed_at IS NULL)::text || '|' ||
    COUNT(*) FILTER (WHERE last_parsed_at >= NOW() - INTERVAL '5 minutes')::text || '|' ||
    COUNT(*) FILTER (WHERE last_parsed_at < NOW() - INTERVAL '1 hour')::text
FROM channels
WHERE is_active = true
  AND (blocked_until IS NULL OR blocked_until < NOW());
" 2>/dev/null | tr -d ' ' || echo "")

if [ -n "$CHANNELS_STATS" ]; then
    IFS='|' read -r TOTAL NEVER_PARSED RECENTLY_PARSED NOT_PARSED_1H <<< "$CHANNELS_STATS"
    echo "  Всего активных каналов: $TOTAL"
    echo "  Никогда не парсились: $NEVER_PARSED"
    echo "  Парсились < 5 минут назад: $RECENTLY_PARSED"
    echo "  Не парсились > 1 часа: $NOT_PARSED_1H"
fi

# Получаем CHANNELS_PER_TICK
CHANNELS_PER_TICK=$(docker compose exec -T telethon-ingest printenv CHANNELS_PER_TICK 2>/dev/null || echo "50")
echo "  Каналов за тик (CHANNELS_PER_TICK): $CHANNELS_PER_TICK"

if [ -n "$TOTAL" ] && [ -n "$CHANNELS_PER_TICK" ]; then
    TICKS_NEEDED=$(( (TOTAL + CHANNELS_PER_TICK - 1) / CHANNELS_PER_TICK ))
    echo "  Тиков для обхода всех каналов: $TICKS_NEEDED"
    
    # Интервал тика = 5 минут
    TIME_NEEDED=$((TICKS_NEEDED * 5))
    echo "  Время для обхода всех каналов: ~${TIME_NEEDED} минут"
    
    if [ "$TICKS_NEEDED" -le 2 ]; then
        echo -e "  ${GREEN}✅ Все каналы должны обходиться за ~${TIME_NEEDED} минут${NC}"
    elif [ "$TICKS_NEEDED" -le 6 ]; then
        echo -e "  ${YELLOW}⚠️  Обход всех каналов займет ~${TIME_NEEDED} минут${NC}"
    else
        echo -e "  ${RED}❌ Обход всех каналов займет > 30 минут${NC}"
    fi
fi

echo ""

# ============================================================================
# 2. РАСПРЕДЕЛЕНИЕ ПО ВРЕМЕНИ С ПОСЛЕДНЕГО ПАРСИНГА
# ============================================================================
echo "2. РАСПРЕДЕЛЕНИЕ КАНАЛОВ ПО ВРЕМЕНИ С ПОСЛЕДНЕГО ПАРСИНГА"
echo "----------------------------------------------------------------------------------"

docker compose exec -T supabase-db psql -U postgres -d postgres -c "
WITH parsing_intervals AS (
    SELECT 
        c.id,
        c.username,
        c.title,
        c.last_parsed_at,
        CASE 
            WHEN c.last_parsed_at IS NULL THEN 'never'
            WHEN NOW() - c.last_parsed_at < INTERVAL '5 minutes' THEN '<5min'
            WHEN NOW() - c.last_parsed_at < INTERVAL '1 hour' THEN '5min-1h'
            WHEN NOW() - c.last_parsed_at < INTERVAL '6 hours' THEN '1h-6h'
            WHEN NOW() - c.last_parsed_at < INTERVAL '24 hours' THEN '6h-24h'
            ELSE '>24h'
        END as time_bucket
    FROM channels c
    WHERE c.is_active = true
      AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
)
SELECT 
    time_bucket,
    COUNT(*) as channels_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) as percentage
FROM parsing_intervals
GROUP BY time_bucket
ORDER BY 
    CASE 
        WHEN time_bucket = 'never' THEN 1
        WHEN time_bucket = '<5min' THEN 2
        WHEN time_bucket = '5min-1h' THEN 3
        WHEN time_bucket = '1h-6h' THEN 4
        WHEN time_bucket = '6h-24h' THEN 5
        ELSE 6
    END;
" 2>&1 | grep -v "rows)" | tail -10 | sed 's/^/  /'

echo ""

# ============================================================================
# 3. КАНАЛЫ, КОТОРЫЕ ДОЛГО НЕ ПАРСИЛИСЬ
# ============================================================================
echo "3. КАНАЛЫ, КОТОРЫЕ ДОЛГО НЕ ПАРСИЛИСЬ (> 6 часов)"
echo "----------------------------------------------------------------------------------"

LONG_UNPARSED=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT COUNT(*)
FROM channels
WHERE is_active = true
  AND (blocked_until IS NULL OR blocked_until < NOW())
  AND last_parsed_at < NOW() - INTERVAL '6 hours';
" 2>/dev/null | tr -d ' ' || echo "0")

if [ "$LONG_UNPARSED" -gt 0 ]; then
    echo -e "  ${YELLOW}⚠️  Найдено $LONG_UNPARSED каналов, которые не парсились > 6 часов${NC}"
    echo ""
    echo "  Топ-5 каналов с самым долгим временем без парсинга:"
    docker compose exec -T supabase-db psql -U postgres -d postgres -c "
    SELECT 
        c.title,
        c.username,
        c.last_parsed_at,
        NOW() - c.last_parsed_at as time_since_parse
    FROM channels c
    WHERE c.is_active = true
      AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
      AND c.last_parsed_at < NOW() - INTERVAL '6 hours'
    ORDER BY c.last_parsed_at ASC NULLS FIRST
    LIMIT 5;
    " 2>&1 | grep -v "rows)" | tail -6 | sed 's/^/    /'
else
    echo -e "  ${GREEN}✅ Все каналы парсились за последние 6 часов${NC}"
fi

echo ""

# ============================================================================
# 4. АНАЛИЗ СПРАВЕДЛИВОСТИ (FAIRNESS)
# ============================================================================
echo "4. АНАЛИЗ СПРАВЕДЛИВОСТИ РАСПРЕДЕЛЕНИЯ"
echo "----------------------------------------------------------------------------------"

echo "  Проверка распределения парсинга по tenant'ам:"
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    COALESCE(u.tenant_id::text, 'system') as tenant_id,
    COUNT(DISTINCT c.id) as channels_count,
    COUNT(*) FILTER (WHERE c.last_parsed_at >= NOW() - INTERVAL '1 hour') as parsed_recently,
    MIN(c.last_parsed_at) as oldest_parse,
    MAX(c.last_parsed_at) as newest_parse
FROM channels c
LEFT JOIN user_channel uc ON c.id = uc.channel_id AND uc.is_active = true
LEFT JOIN users u ON uc.user_id = u.id
WHERE c.is_active = true
  AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
GROUP BY COALESCE(u.tenant_id::text, 'system')
ORDER BY channels_count DESC;
" 2>&1 | grep -v "rows)" | tail -10 | sed 's/^/    /'

echo ""

# ============================================================================
# 5. ПРОВЕРКА ПРИОРИТЕТИЗАЦИИ
# ============================================================================
echo "5. КАНАЛЫ С НОВЫМИ ПОСТАМИ (приоритет парсинга)"
echo "----------------------------------------------------------------------------------"

docker compose exec -T supabase-db psql -U postgres -d postgres -c "
WITH channel_activity AS (
    SELECT 
        c.id,
        c.username,
        c.title,
        c.last_parsed_at,
        COUNT(p.id) FILTER (
            WHERE p.posted_at > COALESCE(c.last_parsed_at, '1970-01-01'::timestamp)
        ) as new_posts_count
    FROM channels c
    LEFT JOIN posts p ON p.channel_id = c.id
    WHERE c.is_active = true
      AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
    GROUP BY c.id, c.username, c.title, c.last_parsed_at
)
SELECT 
    COUNT(*) FILTER (WHERE new_posts_count > 0) as channels_with_new_posts,
    COUNT(*) as total_channels,
    SUM(new_posts_count) as total_new_posts
FROM channel_activity;
" 2>&1 | grep -v "rows)" | tail -4 | sed 's/^/  /'

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""

echo "Проблемы справедливости:"
echo "  1. Проверьте каналы, которые долго не парсились"
echo "  2. Проверьте распределение по tenant'ам"
echo "  3. Убедитесь, что все каналы получают равный доступ"
echo ""
echo "Рекомендации (Context7):"
echo "  1. Реализовать weighted round-robin для справедливости"
echo "  2. Добавить метрики fairness распределения"
echo "  3. Мониторить время без парсинга для каждого канала"
echo "  4. Реализовать гарантию минимальной частоты парсинга"

