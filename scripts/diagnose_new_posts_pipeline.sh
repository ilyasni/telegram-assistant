#!/bin/bash
# Диагностика проблемы: новые посты не обрабатываются в пайплайне
# Context7: Комплексная диагностика всего пайплайна от парсинга до worker

set -euo pipefail

echo "=================================================================================="
echo "DIAGNOSTIC: Новые посты не обрабатываются в пайплайне"
echo "Context7: Комплексная диагностика с использованием best practices"
echo "=================================================================================="
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ============================================================================
# 1. ПРОВЕРКА ПАРСИНГА И ПУБЛИКАЦИИ В REDIS STREAMS
# ============================================================================
echo "1. ПРОВЕРКА ПАРСИНГА И ПУБЛИКАЦИИ"
echo "----------------------------------------------------------------------------------"

# Последние публикации в логах
LAST_PUBLISH=$(docker compose logs telethon-ingest --since 2h 2>&1 | grep "Published.*post.parsed events" | tail -1)
if [ -n "$LAST_PUBLISH" ]; then
    PUBLISH_TIME=$(echo "$LAST_PUBLISH" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}' | head -1)
    PUBLISH_COUNT=$(echo "$LAST_PUBLISH" | grep -oE "Published [0-9]+" | grep -oE "[0-9]+" | head -1)
    echo "  Последняя публикация: $PUBLISH_TIME ($PUBLISH_COUNT событий)"
    echo -e "${GREEN}✅ События публикуются${NC}"
else
    echo -e "${RED}❌ Нет публикаций событий за последние 2 часа${NC}"
fi

# Проверка стрима
STREAM_LENGTH=$(docker compose exec -T redis redis-cli XLEN stream:posts:parsed 2>&1 | grep -v "ERR" || echo "0")
echo "  Длина стрима stream:posts:parsed: $STREAM_LENGTH"

# Последнее сообщение в стриме
LAST_MSG=$(docker compose exec -T redis redis-cli XREVRANGE stream:posts:parsed + - COUNT 1 2>&1 | grep -E "^[0-9]" | head -1 | cut -d' ' -f1 || echo "")
if [ -n "$LAST_MSG" ]; then
    echo "  Последний ID в стриме: $LAST_MSG"
    
    # Парсим timestamp из ID
    MSG_TS=$(echo "$LAST_MSG" | cut -d'-' -f1)
    MSG_DT=$(date -u -d "@$(($MSG_TS / 1000))" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "unknown")
    echo "  Время последнего сообщения: $MSG_DT"
else
    echo -e "${YELLOW}⚠️  Стрим пуст или недоступен${NC}"
fi

echo ""

# ============================================================================
# 2. ПРОВЕРКА WORKER CONSUMER GROUPS
# ============================================================================
echo "2. ПРОВЕРКА WORKER CONSUMER GROUPS"
echo "----------------------------------------------------------------------------------"

# Consumer groups для posts.parsed
GROUPS=$(docker compose exec -T redis redis-cli XINFO GROUPS stream:posts:parsed 2>&1 | grep "name" | sed 's/name//' | tr -d ' ')
if [ -n "$GROUPS" ]; then
    echo "$GROUPS" | while read -r group; do
        echo "  Consumer Group: $group"
        
        # Получаем информацию о группе
        GROUP_INFO=$(docker compose exec -T redis redis-cli XINFO GROUPS stream:posts:parsed 2>&1 | grep -A 10 "name.*$group" || echo "")
        
        LAST_DELIVERED=$(echo "$GROUP_INFO" | grep "last-delivered-id" | sed 's/last-delivered-id//' | tr -d ' ' || echo "")
        PENDING=$(echo "$GROUP_INFO" | grep "^pending" | sed 's/pending//' | tr -d ' ' || echo "")
        LAG=$(echo "$GROUP_INFO" | grep "^lag" | sed 's/lag//' | tr -d ' ' || echo "")
        
        echo "    Last delivered ID: $LAST_DELIVERED"
        echo "    Pending: $PENDING"
        echo "    Lag: $LAG"
        
        if [ "$LAG" = "0" ] && [ "$PENDING" = "0" ]; then
            echo -e "    ${GREEN}✅ Группа в норме (нет отставания)${NC}"
        elif [ "$PENDING" -gt 0 ]; then
            echo -e "    ${YELLOW}⚠️  Есть pending сообщения ($PENDING)${NC}"
        else
            echo -e "    ${GREEN}✅ Нет проблем${NC}"
        fi
    done
else
    echo -e "${RED}❌ Consumer groups не найдены${NC}"
fi

echo ""

# ============================================================================
# 3. ПРОВЕРКА НОВЫХ СООБЩЕНИЙ В СТРИМЕ
# ============================================================================
echo "3. ПРОВЕРКА НОВЫХ СООБЩЕНИЙ"
echo "----------------------------------------------------------------------------------"

# Получаем последний delivered ID для tagging_workers
TAGGING_LAST=$(docker compose exec -T redis redis-cli XINFO GROUPS stream:posts:parsed 2>&1 | grep -A 10 "name.*tagging_workers" | grep "last-delivered-id" | sed 's/last-delivered-id//' | tr -d ' ' || echo "")

if [ -n "$TAGGING_LAST" ] && [ -n "$LAST_MSG" ]; then
    echo "  Worker последний ID: $TAGGING_LAST"
    echo "  Стрим последний ID: $LAST_MSG"
    
    # Сравниваем ID
    TAGGING_TS=$(echo "$TAGGING_LAST" | cut -d'-' -f1)
    STREAM_TS=$(echo "$LAST_MSG" | cut -d'-' -f1)
    
    if [ "$STREAM_TS" -gt "$TAGGING_TS" ]; then
        DIFF=$((STREAM_TS - TAGGING_TS))
        DIFF_SEC=$((DIFF / 1000))
        echo -e "  ${YELLOW}⚠️  Есть новые сообщения, которые не прочитаны (${DIFF_SEC} секунд разницы)${NC}"
        
        # Проверяем, есть ли сообщения после last-delivered
        NEW_MSGS=$(docker compose exec -T redis redis-cli XRANGE stream:posts:parsed "$TAGGING_LAST" + COUNT 10 2>&1 | grep -E "^[0-9]" | wc -l)
        echo "  Новых сообщений после last-delivered: $NEW_MSGS"
    elif [ "$STREAM_TS" -eq "$TAGGING_TS" ]; then
        echo -e "  ${GREEN}✅ Worker прочитал все сообщения${NC}"
    else
        echo -e "  ${YELLOW}⚠️  Worker читает сообщения быстрее, чем они публикуются${NC}"
    fi
fi

echo ""

# ============================================================================
# 4. ПРОВЕРКА АКТИВНОСТИ WORKER
# ============================================================================
echo "4. ПРОВЕРКА АКТИВНОСТИ WORKER"
echo "----------------------------------------------------------------------------------"

WORKER_LOGS=$(docker compose logs worker --since 30m 2>&1 | grep -iE "(tagging|posts\.parsed|processed)" | tail -10)
if [ -n "$WORKER_LOGS" ]; then
    PROCESSED_COUNT=$(echo "$WORKER_LOGS" | grep -oE "processed_count=[0-9]+" | grep -oE "[0-9]+" | head -1 || echo "0")
    echo "  Последние активности worker:"
    echo "$WORKER_LOGS" | head -5 | sed 's/^/    /'
    
    if [ "$PROCESSED_COUNT" = "0" ]; then
        echo -e "  ${YELLOW}⚠️  Worker активен, но не обрабатывает новые сообщения (processed_count=0)${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠️  Нет логов активности worker${NC}"
fi

echo ""

# ============================================================================
# 5. ПРОВЕРКА ПОСТОВ В БД
# ============================================================================
echo "5. ПРОВЕРКА ПОСТОВ В БД"
echo "----------------------------------------------------------------------------------"

POSTS_24H=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '24 hours';" 2>&1 | tr -d ' ' || echo "0")
POSTS_1H=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '1 hour';" 2>&1 | tr -d ' ' || echo "0")
LAST_POST=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "SELECT MAX(created_at) FROM posts;" 2>&1 | tr -d ' ' || echo "")

echo "  Посты в БД:"
echo "    - За 24 часа: $POSTS_24H"
echo "    - За 1 час: $POSTS_1H"
echo "    - Последний пост: $LAST_POST"

if [ "$POSTS_1H" -gt 0 ]; then
    echo -e "  ${GREEN}✅ Есть новые посты в БД${NC}"
else
    echo -e "  ${YELLOW}⚠️  Нет новых постов в БД за последний час${NC}"
fi

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""
echo "Проблема: Queue Depth показывает, что новые посты не обрабатываются"
echo ""
echo "Проверьте:"
echo "  1. Публикуются ли события в Redis Streams (stream:posts:parsed)"
echo "  2. Читает ли worker новые события (consumer group tagging_workers)"
echo "  3. Есть ли pending сообщения в PEL"
echo "  4. Работает ли worker (проверка логов)"
echo ""
echo "📄 Для детальной диагностики используйте:"
echo "  - scripts/comprehensive_pipeline_diagnosis.sh"
echo "  - scripts/check_pipeline_e2e.py"

