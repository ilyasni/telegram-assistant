#!/bin/bash
# Проверка Scheduler Freshness - диагностика застрявшего scheduler
# Context7: Комплексная диагностика состояния scheduler

set -euo pipefail

echo "=================================================================================="
echo "SCHEDULER FRESHNESS DIAGNOSTIC"
echo "Context7: Проверка состояния scheduler и метрики freshness"
echo "=================================================================================="
echo ""

TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ============================================================================
# 1. ПРОВЕРКА СЕРВИСА TELETHON-INGEST
# ============================================================================
echo "1. ПРОВЕРКА СЕРВИСА TELETHON-INGEST"
echo "----------------------------------------------------------------------------------"

SERVICE_STATUS=$(docker compose ps telethon-ingest --format json 2>/dev/null | jq -r '.[0].State' 2>/dev/null || echo "unknown")
HEALTH_STATUS=$(docker compose ps telethon-ingest --format json 2>/dev/null | jq -r '.[0].Health' 2>/dev/null || echo "unknown")

echo "  Статус: $SERVICE_STATUS"
echo "  Health: $HEALTH_STATUS"

if [ "$SERVICE_STATUS" = "running" ]; then
    if [ "$HEALTH_STATUS" = "healthy" ]; then
        echo -e "${GREEN}✅ Сервис работает и здоров${NC}"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        echo -e "${YELLOW}⚠️  Сервис работает, но health check не пройден${NC}"
        WARNING_CHECKS=$((WARNING_CHECKS + 1))
    fi
else
    echo -e "${RED}❌ Сервис не работает${NC}"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 2. ПРОВЕРКА ПОСЛЕДНЕГО TICK
# ============================================================================
echo "2. ПРОВЕРКА ПОСЛЕДНЕГО SCHEDULER TICK"
echo "----------------------------------------------------------------------------------"

LAST_TICK_LOG=$(docker compose logs telethon-ingest --since 15m 2>&1 | grep -i "Scheduler tick completed" | tail -1)
if [ -n "$LAST_TICK_LOG" ]; then
    # Извлекаем timestamp из лога
    TICK_TIMESTAMP=$(echo "$LAST_TICK_LOG" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}' | head -1)
    echo "  Последний tick: $TICK_TIMESTAMP"
    
    if [ -n "$TICK_TIMESTAMP" ]; then
        # Вычисляем разницу во времени
        NOW_EPOCH=$(date -u +%s)
        TICK_EPOCH=$(date -u -d "$TICK_TIMESTAMP" +%s 2>/dev/null || echo "0")
        
        if [ "$TICK_EPOCH" != "0" ]; then
            DIFF_SECONDS=$((NOW_EPOCH - TICK_EPOCH))
            DIFF_MINUTES=$((DIFF_SECONDS / 60))
            
            echo "  Прошло секунд: $DIFF_SECONDS ($DIFF_MINUTES минут)"
            
            EXPECTED_INTERVAL=300  # 5 минут по умолчанию
            if [ "$DIFF_SECONDS" -lt "$EXPECTED_INTERVAL" ]; then
                echo -e "${GREEN}✅ Свежесть в норме (ожидается интервал $EXPECTED_INTERVAL секунд)${NC}"
                PASSED_CHECKS=$((PASSED_CHECKS + 1))
            elif [ "$DIFF_SECONDS" -lt $((EXPECTED_INTERVAL * 2)) ]; then
                echo -e "${YELLOW}⚠️  Свежесть на грани (прошло $DIFF_SECONDS секунд)${NC}"
                WARNING_CHECKS=$((WARNING_CHECKS + 1))
            else
                echo -e "${RED}❌ Свежесть превысила ожидаемый интервал ($DIFF_SECONDS секунд)${NC}"
                FAILED_CHECKS=$((FAILED_CHECKS + 1))
            fi
            TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
        fi
    fi
else
    echo -e "${RED}❌ Не найдено завершенных ticks за последние 15 минут${NC}"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
fi

echo ""

# ============================================================================
# 3. ПРОВЕРКА LOCK
# ============================================================================
echo "3. ПРОВЕРКА SCHEDULER LOCK"
echo "----------------------------------------------------------------------------------"

LOCK_VALUE=$(docker compose exec -T redis redis-cli GET "parse_all_channels:lock" 2>&1 | grep -v "^$" || echo "")
LOCK_TTL=$(docker compose exec -T redis redis-cli TTL "parse_all_channels:lock" 2>&1 | grep -v "^$" || echo "")

if [ -z "$LOCK_VALUE" ] || [ "$LOCK_VALUE" = "(nil)" ]; then
    echo "  Lock: не установлен"
    echo -e "${GREEN}✅ Lock свободен${NC}"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    echo "  Lock: установлен"
    echo "  Значение: $LOCK_VALUE"
    echo "  TTL: $LOCK_TTL секунд"
    
    if [ -n "$LOCK_TTL" ] && [ "$LOCK_TTL" != "-2" ]; then
        if [ "$LOCK_TTL" -gt 0 ]; then
            echo -e "${YELLOW}⚠️  Lock установлен (TTL: $LOCK_TTL секунд)${NC}"
            WARNING_CHECKS=$((WARNING_CHECKS + 1))
        else
            echo -e "${GREEN}✅ Lock истекает${NC}"
            PASSED_CHECKS=$((PASSED_CHECKS + 1))
        fi
    fi
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 4. ПРОВЕРКА ОШИБОК В ЛОГАХ
# ============================================================================
echo "4. ПРОВЕРКА ОШИБОК В ЛОГАХ"
echo "----------------------------------------------------------------------------------"

ERROR_COUNT=$(docker compose logs telethon-ingest --since 10m 2>&1 | grep -iE "(ERROR|CRITICAL|Exception|Traceback)" | wc -l || echo "0")
TIMEOUT_COUNT=$(docker compose logs telethon-ingest --since 10m 2>&1 | grep -iE "(timeout|TIMEOUT)" | wc -l || echo "0")

echo "  Ошибок за последние 10 минут: $ERROR_COUNT"
echo "  Таймаутов за последние 10 минут: $TIMEOUT_COUNT"

if [ "$ERROR_COUNT" -eq 0 ] && [ "$TIMEOUT_COUNT" -eq 0 ]; then
    echo -e "${GREEN}✅ Ошибок не обнаружено${NC}"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
elif [ "$ERROR_COUNT" -lt 5 ] && [ "$TIMEOUT_COUNT" -lt 10 ]; then
    echo -e "${YELLOW}⚠️  Обнаружено несколько ошибок/таймаутов${NC}"
    echo "  Последние ошибки:"
    docker compose logs telethon-ingest --since 10m 2>&1 | grep -iE "(ERROR|CRITICAL)" | tail -3
    WARNING_CHECKS=$((WARNING_CHECKS + 1))
else
    echo -e "${RED}❌ Много ошибок в логах${NC}"
    echo "  Последние ошибки:"
    docker compose logs telethon-ingest --since 10m 2>&1 | grep -iE "(ERROR|CRITICAL)" | tail -5
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

echo ""

# ============================================================================
# 5. ПРОВЕРКА АКТИВНОСТИ SCHEDULER
# ============================================================================
echo "5. ПРОВЕРКА АКТИВНОСТИ SCHEDULER"
echo "----------------------------------------------------------------------------------"

TICK_ATTEMPTS=$(docker compose logs telethon-ingest --since 15m 2>&1 | grep -iE "(Running scheduler tick|lock acquired|Lock held)" | wc -l || echo "0")
TICK_COMPLETED=$(docker compose logs telethon-ingest --since 15m 2>&1 | grep -iE "Scheduler tick completed" | wc -l || echo "0")

echo "  Попыток запустить tick за последние 15 минут: $TICK_ATTEMPTS"
echo "  Завершенных ticks за последние 15 минут: $TICK_COMPLETED"

if [ "$TICK_COMPLETED" -gt 0 ]; then
    echo -e "${GREEN}✅ Scheduler активен${NC}"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
elif [ "$TICK_ATTEMPTS" -gt 0 ]; then
    echo -e "${YELLOW}⚠️  Scheduler пытается работать, но ticks не завершаются${NC}"
    WARNING_CHECKS=$((WARNING_CHECKS + 1))
else
    echo -e "${RED}❌ Scheduler неактивен${NC}"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

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
    echo -e "${GREEN}Overall Status: SCHEDULER WORKING ✅${NC}"
    exit 0
elif [ "$FAILED_CHECKS" -eq 0 ]; then
    echo -e "${YELLOW}Overall Status: SCHEDULER DEGRADED ⚠️${NC}"
    exit 1
else
    echo -e "${RED}Overall Status: SCHEDULER STUCK ❌${NC}"
    exit 2
fi

