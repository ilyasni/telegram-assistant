#!/bin/bash
# Комплексная проверка сервиса telethon-ingest
# Context7: Best practices для диагностики

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=================================================================================="
echo "ПРОВЕРКА СЕРВИСА TELETHON-INGEST"
echo "Context7: Комплексная диагностика"
echo "=================================================================================="
echo ""

# ============================================================================
# 1. СТАТУС КОНТЕЙНЕРА
# ============================================================================
echo "1. СТАТУС КОНТЕЙНЕРА"
echo "----------------------------------------------------------------------------------"

CONTAINER_INFO=$(docker compose ps telethon-ingest --format json 2>/dev/null)
if [ -z "$CONTAINER_INFO" ]; then
    echo -e "  ${RED}❌ Контейнер не найден${NC}"
    exit 1
fi

STATE=$(echo "$CONTAINER_INFO" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('State', 'unknown'))" 2>/dev/null || echo "unknown")
HEALTH=$(echo "$CONTAINER_INFO" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('Health', 'unknown'))" 2>/dev/null || echo "unknown")
UPTIME=$(echo "$CONTAINER_INFO" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('Status', ''))" 2>/dev/null || echo "")

if [ "$STATE" = "running" ]; then
    echo -e "  ${GREEN}✅ Статус: Running${NC}"
else
    echo -e "  ${RED}❌ Статус: $STATE${NC}"
fi

if [ "$HEALTH" = "healthy" ]; then
    echo -e "  ${GREEN}✅ Health check: Healthy${NC}"
elif [ "$HEALTH" = "starting" ]; then
    echo -e "  ${YELLOW}⚠️  Health check: Starting${NC}"
elif [ "$HEALTH" = "unhealthy" ]; then
    echo -e "  ${RED}❌ Health check: Unhealthy${NC}"
else
    echo -e "  ${YELLOW}⚠️  Health check: $HEALTH${NC}"
fi

if [ -n "$UPTIME" ]; then
    echo "  Uptime: $UPTIME"
fi

echo ""

# ============================================================================
# 2. РЕСУРСЫ
# ============================================================================
echo "2. ИСПОЛЬЗОВАНИЕ РЕСУРСОВ"
echo "----------------------------------------------------------------------------------"

STATS=$(docker stats telegram-assistant-telethon-ingest-1 --no-stream --format "{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}" 2>/dev/null || echo "")
if [ -n "$STATS" ]; then
    CPU=$(echo "$STATS" | cut -f1)
    MEM_USAGE=$(echo "$STATS" | cut -f2)
    MEM_PERC=$(echo "$STATS" | cut -f3)
    
    echo "  CPU: $CPU"
    echo "  Память: $MEM_USAGE ($MEM_PERC)"
    
    # Проверка на высокое использование
    CPU_NUM=$(echo "$CPU" | sed 's/%//' | cut -d'.' -f1)
    MEM_NUM=$(echo "$MEM_PERC" | sed 's/%//' | cut -d'.' -f1)
    
    if [ "$CPU_NUM" -gt 80 ]; then
        echo -e "  ${YELLOW}⚠️  Высокое использование CPU${NC}"
    fi
    
    if [ "$MEM_NUM" -gt 80 ]; then
        echo -e "  ${YELLOW}⚠️  Высокое использование памяти${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠️  Не удалось получить статистику${NC}"
fi

echo ""

# ============================================================================
# 3. HEALTH ENDPOINT
# ============================================================================
echo "3. HEALTH ENDPOINT"
echo "----------------------------------------------------------------------------------"

HEALTH_RESPONSE=$(curl -s -w "\n%{http_code}" http://telethon-ingest:8011/health 2>/dev/null || echo -e "\n000")
HTTP_CODE=$(echo "$HEALTH_RESPONSE" | tail -1)
HEALTH_BODY=$(echo "$HEALTH_RESPONSE" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "  ${GREEN}✅ Health endpoint доступен (HTTP $HTTP_CODE)${NC}"
    if [ -n "$HEALTH_BODY" ]; then
        echo "  Ответ:"
        echo "$HEALTH_BODY" | python3 -m json.tool 2>/dev/null | sed 's/^/    /' || echo "    $HEALTH_BODY"
    fi
elif [ "$HTTP_CODE" = "000" ]; then
    echo -e "  ${RED}❌ Health endpoint недоступен${NC}"
else
    echo -e "  ${YELLOW}⚠️  Health endpoint вернул HTTP $HTTP_CODE${NC}"
    if [ -n "$HEALTH_BODY" ]; then
        echo "  Ответ: $HEALTH_BODY"
    fi
fi

echo ""

# ============================================================================
# 4. METRICS ENDPOINT
# ============================================================================
echo "4. METRICS ENDPOINT"
echo "----------------------------------------------------------------------------------"

METRICS_RESPONSE=$(curl -s -w "\n%{http_code}" http://telethon-ingest:8011/metrics 2>/dev/null || echo -e "\n000")
METRICS_CODE=$(echo "$METRICS_RESPONSE" | tail -1)

if [ "$METRICS_CODE" = "200" ]; then
    echo -e "  ${GREEN}✅ Metrics endpoint доступен (HTTP $METRICS_CODE)${NC}"
    
    METRICS_BODY=$(echo "$METRICS_RESPONSE" | head -n -1)
    SCHEDULER_METRICS=$(echo "$METRICS_BODY" | grep -E "^scheduler_|^posts_parsed|^parser_runs" | head -10)
    
    if [ -n "$SCHEDULER_METRICS" ]; then
        echo "  Ключевые метрики:"
        echo "$SCHEDULER_METRICS" | sed 's/^/    /'
    fi
else
    echo -e "  ${YELLOW}⚠️  Metrics endpoint недоступен (HTTP $METRICS_CODE)${NC}"
fi

echo ""

# ============================================================================
# 5. КОНФИГУРАЦИЯ
# ============================================================================
echo "5. КОНФИГУРАЦИЯ"
echo "----------------------------------------------------------------------------------"

CONFIG_VARS=$(docker compose exec -T telethon-ingest printenv 2>/dev/null | grep -E "LOG_LEVEL|ENVIRONMENT|PARSER_|FEATURE_|INGEST_HEALTH_PORT" | sort || echo "")
if [ -n "$CONFIG_VARS" ]; then
    echo "$CONFIG_VARS" | sed 's/^/  /'
else
    echo -e "  ${YELLOW}⚠️  Не удалось получить конфигурацию${NC}"
fi

echo ""

# ============================================================================
# 6. ПОСЛЕДНИЕ ОШИБКИ
# ============================================================================
echo "6. ПОСЛЕДНИЕ ОШИБКИ И ПРЕДУПРЕЖДЕНИЯ"
echo "----------------------------------------------------------------------------------"

ERRORS=$(docker compose logs telethon-ingest --since 10m 2>&1 | grep -iE "(ERROR|WARNING|CRITICAL|Exception|Traceback|failed|failed to)" | tail -10)
if [ -n "$ERRORS" ]; then
    ERROR_COUNT=$(echo "$ERRORS" | wc -l)
    echo -e "  ${YELLOW}⚠️  Найдено ошибок/предупреждений: $ERROR_COUNT${NC}"
    echo ""
    echo "  Последние ошибки:"
    echo "$ERRORS" | sed 's/^/    /'
else
    echo -e "  ${GREEN}✅ Ошибок не обнаружено${NC}"
fi

echo ""

# ============================================================================
# 7. SCHEDULER СТАТУС
# ============================================================================
echo "7. SCHEDULER СТАТУС"
echo "----------------------------------------------------------------------------------"

# Проверка последнего тика через Prometheus
LAST_TICK_JSON=$(curl -s "http://localhost:9090/api/v1/query?query=scheduler_last_tick_ts_seconds" 2>/dev/null || echo "")
if [ -n "$LAST_TICK_JSON" ]; then
    LAST_TICK_TS=$(echo "$LAST_TICK_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '')" 2>/dev/null || echo "")
    
    if [ -n "$LAST_TICK_TS" ] && [ "$LAST_TICK_TS" != "null" ]; then
        LAST_TICK_DT=$(python3 -c "from datetime import datetime, timezone; print(datetime.fromtimestamp($LAST_TICK_TS, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))" 2>/dev/null || echo "unknown")
        NOW_TS=$(date +%s)
        AGE_SECONDS=$(python3 -c "print(int($NOW_TS - $LAST_TICK_TS))" 2>/dev/null || echo "0")
        
        echo "  Последний тик: $LAST_TICK_DT"
        echo "  Возраст: ${AGE_SECONDS} секунд ($(python3 -c "print('%.1f' % ($AGE_SECONDS / 60))") минут)"
        
        if [ "$AGE_SECONDS" -lt 360 ]; then
            echo -e "  ${GREEN}✅ Scheduler активен${NC}"
        elif [ "$AGE_SECONDS" -lt 600 ]; then
            echo -e "  ${YELLOW}⚠️  Тик немного задержался${NC}"
        else
            echo -e "  ${RED}❌ Тик очень старый!${NC}"
        fi
    else
        echo -e "  ${YELLOW}⚠️  Не удалось получить время последнего тика${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠️  Prometheus недоступен${NC}"
fi

# Проверка heartbeat
HEARTBEAT_JSON=$(curl -s "http://localhost:9090/api/v1/query?query=scheduler_heartbeat_seconds" 2>/dev/null || echo "")
if [ -n "$HEARTBEAT_JSON" ]; then
    HEARTBEAT_TS=$(echo "$HEARTBEAT_JSON" | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '')" 2>/dev/null || echo "")
    
    if [ -n "$HEARTBEAT_TS" ] && [ "$HEARTBEAT_TS" != "null" ]; then
        NOW_TS=$(date +%s)
        AGE_SECONDS=$(python3 -c "print(int($NOW_TS - $HEARTBEAT_TS))" 2>/dev/null || echo "0")
        
        if [ "$AGE_SECONDS" -lt 60 ]; then
            echo -e "  ${GREEN}✅ Heartbeat активен (${AGE_SECONDS} сек назад)${NC}"
        else
            echo -e "  ${YELLOW}⚠️  Heartbeat задержался (${AGE_SECONDS} сек назад)${NC}"
        fi
    fi
fi

echo ""

# ============================================================================
# 8. ПОСЛЕДНИЕ СОБЫТИЯ
# ============================================================================
echo "8. ПОСЛЕДНИЕ СОБЫТИЯ (последние 5 минут)"
echo "----------------------------------------------------------------------------------"

RECENT_EVENTS=$(docker compose logs telethon-ingest --since 5m 2>&1 | grep -iE "(Tick completed|Scheduler tick|Started|Started parsing|CHANNEL_PARSE)" | tail -5)
if [ -n "$RECENT_EVENTS" ]; then
    echo "$RECENT_EVENTS" | sed 's/^/  /'
else
    echo -e "  ${YELLOW}⚠️  Нет недавних событий${NC}"
fi

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""

STATUS_OK=true
ISSUES=()

if [ "$STATE" != "running" ]; then
    STATUS_OK=false
    ISSUES+=("Контейнер не запущен")
fi

if [ "$HEALTH" != "healthy" ]; then
    STATUS_OK=false
    ISSUES+=("Health check: $HEALTH")
fi

if [ "$HTTP_CODE" != "200" ]; then
    STATUS_OK=false
    ISSUES+=("Health endpoint недоступен")
fi

if [ "$STATUS_OK" = true ]; then
    echo -e "${GREEN}✅ TELETHON-INGEST СЕРВИС РАБОТАЕТ КОРРЕКТНО${NC}"
else
    echo -e "${RED}❌ TELETHON-INGEST СЕРВИС ИМЕЕТ ПРОБЛЕМЫ:${NC}"
    for issue in "${ISSUES[@]}"; do
        echo "  - $issue"
    done
fi

echo ""
echo "Рекомендации (Context7):"
echo "  - Проверьте логи: docker compose logs telethon-ingest --since 10m -f"
echo "  - Проверьте метрики: http://localhost:9090/graph?g0.expr=scheduler_last_tick_ts_seconds"
echo "  - Проверьте Grafana: System Overview dashboard"
echo "  - Health endpoint: http://telethon-ingest:8011/health"
echo "  - Metrics endpoint: http://telethon-ingest:8011/metrics"

