#!/bin/bash
# Диагностика логики парсинга: почему не находятся новые посты
# Context7: Комплексная диагностика с использованием best practices

set -euo pipefail

echo "=================================================================================="
echo "ДИАГНОСТИКА ЛОГИКИ ПАРСИНГА"
echo "Context7: Проверка почему не находятся новые посты"
echo "=================================================================================="
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ============================================================================
# 1. ПРОВЕРКА since_date vs last_parsed_at vs last_post_date
# ============================================================================
echo "1. ПРОВЕРКА since_date vs last_parsed_at vs last_post_date"
echo "----------------------------------------------------------------------------------"

# Получаем данные для канала "Нейро" как пример
CHANNEL_TITLE="Нейро"
LAST_PARSED=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "SELECT last_parsed_at FROM channels WHERE title = '$CHANNEL_TITLE' LIMIT 1;" 2>&1 | tr -d ' ' || echo "")
LAST_POST=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "SELECT MAX(posted_at) FROM posts WHERE channel_id = (SELECT id FROM channels WHERE title = '$CHANNEL_TITLE' LIMIT 1);" 2>&1 | tr -d ' ' || echo "")

if [ -n "$LAST_PARSED" ] && [ -n "$LAST_POST" ]; then
    echo "  Канал: $CHANNEL_TITLE"
    echo "  last_parsed_at: $LAST_PARSED"
    echo "  last_post_date: $LAST_POST"
    
    # Парсим даты (упрощенно)
    if [[ "$LAST_PARSED" > "$LAST_POST" ]]; then
        echo -e "  ${YELLOW}⚠️  last_parsed_at НОВЕЕ чем last_post_date${NC}"
        echo "     Это означает, что парсинг обновляет last_parsed_at даже без новых постов"
    else
        echo -e "  ${GREEN}✅ last_parsed_at соответствует last_post_date${NC}"
    fi
fi

echo ""

# ============================================================================
# 2. ПРОВЕРКА ЛОГИКИ since_date
# ============================================================================
echo "2. ПРОВЕРКА ЛОГИКИ since_date"
echo "----------------------------------------------------------------------------------"

echo "  Проблема:"
echo "  - since_date вычисляется от last_post_date (последний пост в БД)"
echo "  - Если last_post_date СТАРЫЙ, то since_date тоже будет СТАРЫЙ"
echo "  - Парсинг будет искать посты после СТАРОЙ даты, но их может не быть"

echo ""
echo "  Рекомендация (Context7):"
echo "  - Использовать last_parsed_at как базу для since_date"
echo "  - last_post_date использовать только как fallback"
echo "  - Или проверять, есть ли посты между last_post_date и now"

echo ""

# ============================================================================
# 3. ПРОВЕРКА ПОСТОВ ПОСЛЕ last_post_date
# ============================================================================
echo "3. ПРОВЕРКА ПОСТОВ ПОСЛЕ last_post_date"
echo "----------------------------------------------------------------------------------"

POSTS_AFTER_LAST=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
SELECT COUNT(*) FROM posts p
JOIN channels c ON p.channel_id = c.id
WHERE c.is_active = true
  AND p.posted_at > (
    SELECT MAX(pp.posted_at) 
    FROM posts pp 
    WHERE pp.channel_id = c.id
  ) - INTERVAL '1 hour'
  AND p.created_at > NOW() - INTERVAL '24 hours'
LIMIT 1;
" 2>&1 | tr -d ' ' || echo "0")

echo "  Постов, созданных в БД за 24 часа, но с posted_at близким к last_post_date: $POSTS_AFTER_LAST"

echo ""

# ============================================================================
# 4. ПРОВЕРКА ЛОГОВ ПАРСИНГА
# ============================================================================
echo "4. ПРОВЕРКА ЛОГОВ ПАРСИНГА"
echo "----------------------------------------------------------------------------------"

RECENT_PARSING=$(docker compose logs telethon-ingest --since 5m 2>&1 | grep -iE "(since_date|messages.*processed|Published)" | tail -5)
if [ -n "$RECENT_PARSING" ]; then
    echo "  Последние логи парсинга:"
    echo "$RECENT_PARSING" | sed 's/^/    /'
else
    echo -e "  ${YELLOW}⚠️  Нет недавних логов парсинга${NC}"
fi

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""
echo "Возможные проблемы:"
echo "  1. since_date вычисляется от last_post_date (может быть старым)"
echo "  2. Парсинг обновляет last_parsed_at даже без новых постов"
echo "  3. Фильтрация сообщений может пропускать новые посты"
echo ""
echo "Рекомендации (Context7):"
echo "  1. Использовать last_parsed_at как базу для since_date"
echo "  2. Проверять наличие постов между last_post_date и now перед парсингом"
echo "  3. Улучшить логирование для диагностики"

