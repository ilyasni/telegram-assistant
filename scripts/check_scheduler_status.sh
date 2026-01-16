#!/bin/bash
# Проверка статуса Scheduler (Context7 best practices)

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "=================================================================================="
echo "ПРОВЕРКА СТАТУСА SCHEDULER"
echo "Context7: Комплексная диагностика работы scheduler"
echo "=================================================================================="
echo ""

# ============================================================================
# 1. СТАТУС КОНТЕЙНЕРА
# ============================================================================
echo "1. СТАТУС КОНТЕЙНЕРА"
echo "----------------------------------------------------------------------------------"

STATUS=$(docker compose ps telethon-ingest --format json 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('State', 'unknown'))" 2>/dev/null || echo "unknown")

if [ "$STATUS" = "running" ]; then
    echo -e "  ${GREEN}✅ Контейнер работает${NC}"
else
    echo -e "  ${RED}❌ Контейнер не работает: $STATUS${NC}"
fi

HEALTH=$(docker compose ps telethon-ingest --format json 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('Health', 'unknown'))" 2>/dev/null || echo "unknown")
if [ "$HEALTH" = "healthy" ]; then
    echo -e "  ${GREEN}✅ Health check: healthy${NC}"
elif [ "$HEALTH" = "starting" ]; then
    echo -e "  ${YELLOW}⚠️  Health check: starting${NC}"
else
    echo -e "  ${YELLOW}⚠️  Health check: $HEALTH${NC}"
fi

echo ""

# ============================================================================
# 2. РЕЖИМ ПАРСЕРА
# ============================================================================
echo "2. РЕЖИМ ПАРСЕРА"
echo "----------------------------------------------------------------------------------"

PARSER_MODE=$(docker compose exec -T telethon-ingest printenv PARSER_MODE_OVERRIDE 2>/dev/null || echo "auto")
FEATURE_ENABLED=$(docker compose exec -T telethon-ingest printenv FEATURE_INCREMENTAL_PARSING_ENABLED 2>/dev/null || echo "true")
INTERVAL=$(docker compose exec -T telethon-ingest printenv PARSER_SCHEDULER_INTERVAL_SEC 2>/dev/null || echo "300")

echo "  PARSER_MODE_OVERRIDE: $PARSER_MODE"
echo "  FEATURE_INCREMENTAL_PARSING_ENABLED: $FEATURE_ENABLED"
echo "  PARSER_SCHEDULER_INTERVAL_SEC: $INTERVAL секунд ($(($INTERVAL / 60)) минут)"

if [ "$PARSER_MODE" = "auto" ]; then
    echo -e "  ${GREEN}✅ Режим: AUTO (автоопределение)${NC}"
elif [ "$PARSER_MODE" = "incremental" ]; then
    echo -e "  ${GREEN}✅ Режим: INCREMENTAL${NC}"
elif [ "$PARSER_MODE" = "historical" ]; then
    echo -e "  ${YELLOW}⚠️  Режим: HISTORICAL${NC}"
fi

echo ""

# ============================================================================
# 3. ПОСЛЕДНИЙ ТИК
# ============================================================================
echo "3. ПОСЛЕДНИЙ ТИК"
echo "----------------------------------------------------------------------------------"

LAST_TICK_JSON=$(curl -s "http://localhost:9090/api/v1/query?query=scheduler_last_tick_ts_seconds" 2>/dev/null || echo "")
if [ -n "$LAST_TICK_JSON" ]; then
    LAST_TICK_TS=$(echo "$LAST_TICK_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '')" 2>/dev/null || echo "")
    
    if [ -n "$LAST_TICK_TS" ] && [ "$LAST_TICK_TS" != "null" ]; then
        LAST_TICK_DT=$(python3 -c "from datetime import datetime, timezone; print(datetime.fromtimestamp($LAST_TICK_TS, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))" 2>/dev/null || echo "unknown")
        NOW_TS=$(date +%s)
        AGE_SECONDS=$(python3 -c "print(int($NOW_TS - $LAST_TICK_TS))" 2>/dev/null || echo "0")
        AGE_MINUTES=$(python3 -c "print($AGE_SECONDS / 60)" 2>/dev/null || echo "0")
        
        echo "  Время последнего тика: $LAST_TICK_DT"
        echo "  Возраст тика: ${AGE_SECONDS} секунд (${AGE_MINUTES%.1f} минут)"
        
        if [ "$AGE_SECONDS" -lt 360 ]; then  # Меньше 6 минут
            echo -e "  ${GREEN}✅ Тик свежий (ожидается каждые 5 минут)${NC}"
        elif [ "$AGE_SECONDS" -lt 600 ]; then  # Меньше 10 минут
            echo -e "  ${YELLOW}⚠️  Тик немного задержался${NC}"
        else
            echo -e "  ${RED}❌ Тик очень старый!${NC}"
        fi
        
        NEXT_TICK_IN=$(python3 -c "print(max(0, int(300 - $AGE_SECONDS)))" 2>/dev/null || echo "unknown")
        echo "  Следующий тик ожидается через: ${NEXT_TICK_IN} секунд"
    else
        echo -e "  ${YELLOW}⚠️  Не удалось получить время последнего тика${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠️  Prometheus недоступен${NC}"
fi

echo ""

# ============================================================================
# 4. HEARTBEAT
# ============================================================================
echo "4. HEARTBEAT"
echo "----------------------------------------------------------------------------------"

HEARTBEAT_JSON=$(curl -s "http://localhost:9090/api/v1/query?query=scheduler_heartbeat_seconds" 2>/dev/null || echo "")
if [ -n "$HEARTBEAT_JSON" ]; then
    HEARTBEAT_TS=$(echo "$HEARTBEAT_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '')" 2>/dev/null || echo "")
    
    if [ -n "$HEARTBEAT_TS" ] && [ "$HEARTBEAT_TS" != "null" ]; then
        HEARTBEAT_DT=$(python3 -c "from datetime import datetime, timezone; print(datetime.fromtimestamp($HEARTBEAT_TS, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))" 2>/dev/null || echo "unknown")
        NOW_TS=$(date +%s)
        AGE_SECONDS=$(python3 -c "print(int($NOW_TS - $HEARTBEAT_TS))" 2>/dev/null || echo "0")
        
        echo "  Последний heartbeat: $HEARTBEAT_DT"
        echo "  Возраст heartbeat: ${AGE_SECONDS} секунд"
        
        if [ "$AGE_SECONDS" -lt 60 ]; then  # Меньше 1 минуты
            echo -e "  ${GREEN}✅ Heartbeat свежий (обновляется каждые 30 секунд)${NC}"
        elif [ "$AGE_SECONDS" -lt 120 ]; then  # Меньше 2 минут
            echo -e "  ${YELLOW}⚠️  Heartbeat немного задержался${NC}"
        else
            echo -e "  ${RED}❌ Heartbeat очень старый!${NC}"
        fi
    else
        echo -e "  ${YELLOW}⚠️  Heartbeat метрика недоступна${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠️  Prometheus недоступен${NC}"
fi

echo ""

# ============================================================================
# 5. ПОСЛЕДНИЕ ЛОГИ
# ============================================================================
echo "5. ПОСЛЕДНИЕ ЛОГИ"
echo "----------------------------------------------------------------------------------"

RECENT_LOGS=$(docker compose logs telethon-ingest --since 5m 2>&1 | grep -iE "(Tick completed|Scheduler tick|run_forever)" | tail -3)
if [ -n "$RECENT_LOGS" ]; then
    echo "$RECENT_LOGS" | sed 's/^/  /'
else
    echo -e "  ${YELLOW}⚠️  Нет недавних логов о тиках${NC}"
fi

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""

# Определяем общий статус
STATUS_OK=true

if [ "$STATUS" != "running" ]; then
    STATUS_OK=false
fi

if [ -n "$LAST_TICK_TS" ] && [ "$LAST_TICK_TS" != "null" ]; then
    if [ "$AGE_SECONDS" -gt 600 ]; then
        STATUS_OK=false
    fi
fi

if [ "$STATUS_OK" = true ]; then
    echo -e "${GREEN}✅ SCHEDULER РАБОТАЕТ КОРРЕКТНО${NC}"
else
    echo -e "${RED}❌ SCHEDULER ИМЕЕТ ПРОБЛЕМЫ${NC}"
fi

echo ""
echo "Рекомендации (Context7):"
echo "  - Проверьте логи: docker compose logs telethon-ingest --since 10m -f"
echo "  - Проверьте метрики: http://localhost:9090/graph?g0.expr=scheduler_last_tick_ts_seconds"
echo "  - Проверьте Grafana: System Overview dashboard"

