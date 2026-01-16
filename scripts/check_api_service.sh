#!/bin/bash
# Комплексная проверка контейнера и сервиса API
# Context7: Полная диагностика состояния API сервиса

set -euo pipefail

echo "=================================================================================="
echo "API SERVICE COMPREHENSIVE CHECK"
echo "Context7: Проверка контейнера и сервиса API"
echo "=================================================================================="
echo ""

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

# ============================================================================
# 1. ПРОВЕРКА КОНТЕЙНЕРА
# ============================================================================
echo "1. ПРОВЕРКА КОНТЕЙНЕРА"
echo "----------------------------------------------------------------------------------"

CONTAINER_STATUS=$(docker compose ps api --format json 2>/dev/null | jq -r '.[0].State' 2>/dev/null || echo "unknown")
HEALTH_STATUS=$(docker compose ps api --format json 2>/dev/null | jq -r '.[0].Health' 2>/dev/null || echo "unknown")
CONTAINER_UPTIME=$(docker compose ps api --format json 2>/dev/null | jq -r '.[0].Status' 2>/dev/null || echo "unknown")

echo "  Статус контейнера: $CONTAINER_STATUS"
echo "  Health статус: $HEALTH_STATUS"
echo "  Uptime: $CONTAINER_UPTIME"

if [ "$CONTAINER_STATUS" = "running" ]; then
    if [ "$HEALTH_STATUS" = "healthy" ]; then
        echo -e "${GREEN}✅ Контейнер работает и здоров${NC}"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        echo -e "${YELLOW}⚠️  Контейнер работает, но health check не пройден${NC}"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
else
    echo -e "${RED}❌ Контейнер не работает${NC}"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 2. ПРОВЕРКА HEALTH ENDPOINT
# ============================================================================
echo "2. ПРОВЕРКА HEALTH ENDPOINT"
echo "----------------------------------------------------------------------------------"

HEALTH_RESPONSE=$(docker compose exec -T api curl -s -w "\n%{http_code}" http://localhost:8000/health 2>&1 | tail -1)
HEALTH_BODY=$(docker compose exec -T api curl -s http://localhost:8000/health 2>&1)

echo "  HTTP Status: $HEALTH_RESPONSE"

if [ "$HEALTH_RESPONSE" = "200" ]; then
    echo -e "${GREEN}✅ Health endpoint доступен${NC}"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
    
    # Парсим JSON ответ
    DB_STATUS=$(echo "$HEALTH_BODY" | jq -r '.checks.database // "unknown"' 2>/dev/null || echo "unknown")
    REDIS_STATUS=$(echo "$HEALTH_BODY" | jq -r '.checks.redis // "unknown"' 2>/dev/null || echo "unknown")
    SCHEDULER_RUNNING=$(echo "$HEALTH_BODY" | jq -r '.checks.scheduler.running // false' 2>/dev/null || echo "false")
    SCHEDULER_JOBS=$(echo "$HEALTH_BODY" | jq -r '.checks.scheduler.jobs_count // 0' 2>/dev/null || echo "0")
    
    echo "  Database: $DB_STATUS"
    echo "  Redis: $REDIS_STATUS"
    echo "  Scheduler: running=$SCHEDULER_RUNNING, jobs=$SCHEDULER_JOBS"
    
    if [ "$DB_STATUS" = "healthy" ] && [ "$REDIS_STATUS" = "healthy" ]; then
        echo -e "${GREEN}✅ Все зависимости здоровы${NC}"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        echo -e "${YELLOW}⚠️  Есть проблемы с зависимостями${NC}"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    
    if [ "$SCHEDULER_RUNNING" = "true" ] && [ "$SCHEDULER_JOBS" -gt 0 ]; then
        echo -e "${GREEN}✅ Scheduler работает с $SCHEDULER_JOBS заданиями${NC}"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        echo -e "${YELLOW}⚠️  Scheduler не работает или нет заданий${NC}"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
else
    echo -e "${RED}❌ Health endpoint недоступен (HTTP $HEALTH_RESPONSE)${NC}"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 3. ПРОВЕРКА METRICS ENDPOINT
# ============================================================================
echo "3. ПРОВЕРКА METRICS ENDPOINT"
echo "----------------------------------------------------------------------------------"

METRICS_STATUS=$(docker compose exec -T api curl -s -w "%{http_code}" -o /dev/null http://localhost:8000/metrics 2>&1 | tail -1)

echo "  HTTP Status: $METRICS_STATUS"

if [ "$METRICS_STATUS" = "200" ]; then
    echo -e "${GREEN}✅ Metrics endpoint доступен${NC}"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
    
    # Проверяем наличие ключевых метрик
    METRICS_CONTENT=$(docker compose exec -T api curl -s http://localhost:8000/metrics 2>&1)
    
    if echo "$METRICS_CONTENT" | grep -q "scheduler_running"; then
        echo "  ✅ Метрика scheduler_running найдена"
    fi
    
    if echo "$METRICS_CONTENT" | grep -q "health_check_duration_seconds"; then
        echo "  ✅ Метрика health_check_duration_seconds найдена"
    fi
    
    if echo "$METRICS_CONTENT" | grep -q "api_http_request_duration_seconds"; then
        echo "  ✅ Метрика api_http_request_duration_seconds найдена"
    fi
else
    echo -e "${RED}❌ Metrics endpoint недоступен (HTTP $METRICS_STATUS)${NC}"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 4. ПРОВЕРКА ЛОГОВ НА ОШИБКИ
# ============================================================================
echo "4. ПРОВЕРКА ЛОГОВ НА ОШИБКИ"
echo "----------------------------------------------------------------------------------"

ERROR_COUNT=$(docker compose logs api --since 1h 2>&1 | grep -iE "(error|exception|traceback|failed|critical)" | wc -l || echo "0")
echo "  Ошибок за последний час: $ERROR_COUNT"

if [ "$ERROR_COUNT" -eq 0 ]; then
    echo -e "${GREEN}✅ Ошибок в логах не обнаружено${NC}"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
elif [ "$ERROR_COUNT" -lt 10 ]; then
    echo -e "${YELLOW}⚠️  Обнаружено несколько ошибок ($ERROR_COUNT)${NC}"
    echo "  Последние ошибки:"
    docker compose logs api --since 1h 2>&1 | grep -iE "(error|exception|traceback)" | tail -3
    WARNING_CHECKS=$((WARNING_CHECKS + 1))
else
    echo -e "${RED}❌ Много ошибок в логах ($ERROR_COUNT)${NC}"
    echo "  Последние ошибки:"
    docker compose logs api --since 1h 2>&1 | grep -iE "(error|exception|traceback)" | tail -5
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 5. ПРОВЕРКА РЕСУРСОВ
# ============================================================================
echo "5. ПРОВЕРКА РЕСУРСОВ"
echo "----------------------------------------------------------------------------------"

CONTAINER_STATS=$(docker stats telegram-assistant-api-1 --no-stream --format "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}" 2>/dev/null || echo "N/A|N/A|N/A")
CPU_USAGE=$(echo "$CONTAINER_STATS" | cut -d'|' -f1)
MEM_USAGE=$(echo "$CONTAINER_STATS" | cut -d'|' -f2)
MEM_PERC=$(echo "$CONTAINER_STATS" | cut -d'|' -f3)

echo "  CPU: $CPU_USAGE"
echo "  Memory: $MEM_USAGE ($MEM_PERC)"

if [ "$MEM_PERC" != "N/A" ]; then
    MEM_PERC_NUM=$(echo "$MEM_PERC" | tr -d '%' | cut -d'.' -f1)
    if [ "$MEM_PERC_NUM" -lt 80 ]; then
        echo -e "${GREEN}✅ Использование памяти в норме${NC}"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    elif [ "$MEM_PERC_NUM" -lt 90 ]; then
        echo -e "${YELLOW}⚠️  Высокое использование памяти${NC}"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    else
        echo -e "${RED}❌ Критическое использование памяти${NC}"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
fi

echo ""

# ============================================================================
# 6. ПРОВЕРКА ENDPOINTS
# ============================================================================
echo "6. ПРОВЕРКА ENDPOINTS"
echo "----------------------------------------------------------------------------------"

ENDPOINTS=(
    "/health"
    "/metrics"
    "/api/health"
    "/api/pipeline/health"
    "/api/monitoring/containers"
)

for endpoint in "${ENDPOINTS[@]}"; do
    STATUS=$(docker compose exec -T api curl -s -w "%{http_code}" -o /dev/null "http://localhost:8000$endpoint" 2>&1 | tail -1)
    if [ "$STATUS" = "200" ] || [ "$STATUS" = "404" ]; then
        echo "  ✅ $endpoint: HTTP $STATUS"
    else
        echo -e "  ${YELLOW}⚠️  $endpoint: HTTP $STATUS${NC}"
    fi
done

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo "Total checks: $TOTAL_CHECKS"
echo -e "${GREEN}Passed: $PASSED_CHECKS${NC}"
echo -e "${YELLOW}Warnings: $WARNING_CHECKS${NC}"
echo -e "${RED}Failed: $FAILED_CHECKS${NC}"
echo ""

if [ "$FAILED_CHECKS" -eq 0 ] && [ "$WARNING_CHECKS" -eq 0 ]; then
    OVERALL_STATUS="healthy"
    echo -e "${GREEN}Overall Status: HEALTHY ✅${NC}"
    exit 0
elif [ "$FAILED_CHECKS" -eq 0 ]; then
    OVERALL_STATUS="degraded"
    echo -e "${YELLOW}Overall Status: DEGRADED ⚠️${NC}"
    exit 1
else
    OVERALL_STATUS="unhealthy"
    echo -e "${RED}Overall Status: UNHEALTHY ❌${NC}"
    exit 2
fi

