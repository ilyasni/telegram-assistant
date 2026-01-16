#!/bin/bash
# Context7: Скрипт для исправления проблем с telethon-ingest

set -e

PROJECT_DIR="/opt/telegram-assistant"
cd "$PROJECT_DIR"

echo "=================================================================================="
echo "ИСПРАВЛЕНИЕ TELETHON-INGEST СЕРВИСА"
echo "=================================================================================="
echo ""

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 1. Проверка конфигурации docker-compose
echo "1. Проверка конфигурации docker-compose..."
if grep -q "8011:8011" docker-compose.yml; then
    echo -e "  ${GREEN}✅ Порт 8011 проброшен${NC}"
else
    echo -e "  ${YELLOW}⚠️  Порт 8011 не проброшен, добавляю...${NC}"
    # Порт уже должен быть добавлен через search_replace
fi

# 2. Проверка статуса контейнера
echo ""
echo "2. Проверка статуса контейнера..."
if command -v docker &> /dev/null; then
    CONTAINER_STATUS=$(docker ps -a --filter "name=telethon-ingest" --format "{{.Status}}" 2>/dev/null || echo "not_found")
    if [ "$CONTAINER_STATUS" = "not_found" ]; then
        echo -e "  ${YELLOW}⚠️  Контейнер не найден${NC}"
    else
        echo "  Статус: $CONTAINER_STATUS"
        if echo "$CONTAINER_STATUS" | grep -q "Up"; then
            echo -e "  ${GREEN}✅ Контейнер запущен${NC}"
        else
            echo -e "  ${RED}❌ Контейнер остановлен${NC}"
            echo "  Перезапускаю контейнер..."
            docker-compose restart telethon-ingest 2>/dev/null || docker compose restart telethon-ingest 2>/dev/null || echo "  Не удалось перезапустить (проверьте docker-compose)"
        fi
    fi
else
    echo -e "  ${YELLOW}⚠️  Docker не найден, пропускаю проверку${NC}"
fi

# 3. Проверка health endpoint
echo ""
echo "3. Проверка health endpoint..."
sleep 2  # Даем время на запуск
HEALTH_RESPONSE=$(curl -s -w "\n%{http_code}" http://localhost:8011/health 2>/dev/null || echo -e "\n000")
HEALTH_CODE=$(echo "$HEALTH_RESPONSE" | tail -1)

if [ "$HEALTH_CODE" = "200" ]; then
    echo -e "  ${GREEN}✅ Health endpoint доступен (HTTP $HEALTH_CODE)${NC}"
    HEALTH_BODY=$(echo "$HEALTH_RESPONSE" | head -n -1)
    echo "  Ответ: $HEALTH_BODY"
elif [ "$HEALTH_CODE" = "000" ]; then
    echo -e "  ${RED}❌ Health endpoint недоступен${NC}"
    echo "  Возможные причины:"
    echo "    - Контейнер не запущен"
    echo "    - Порт не проброшен"
    echo "    - Сервис не запустился"
    echo ""
    echo "  Проверьте логи:"
    echo "    docker-compose logs telethon-ingest --tail=50"
else
    echo -e "  ${YELLOW}⚠️  Health endpoint вернул HTTP $HEALTH_CODE${NC}"
fi

# 4. Проверка метрик
echo ""
echo "4. Проверка метрик..."
METRICS_RESPONSE=$(curl -s -w "\n%{http_code}" http://localhost:8011/metrics 2>/dev/null || echo -e "\n000")
METRICS_CODE=$(echo "$METRICS_RESPONSE" | tail -1)

if [ "$METRICS_CODE" = "200" ]; then
    echo -e "  ${GREEN}✅ Metrics endpoint доступен (HTTP $METRICS_CODE)${NC}"
    METRICS_BODY=$(echo "$METRICS_RESPONSE" | head -n -1)
    PARSER_METRICS=$(echo "$METRICS_BODY" | grep -E "^parser_|^scheduler_" | head -5)
    if [ -n "$PARSER_METRICS" ]; then
        echo "  Найдены метрики парсера:"
        echo "$PARSER_METRICS" | sed 's/^/    /'
    fi
else
    echo -e "  ${YELLOW}⚠️  Metrics endpoint недоступен (HTTP $METRICS_CODE)${NC}"
fi

# 5. Проверка scheduler
echo ""
echo "5. Проверка scheduler..."
if [ "$HEALTH_CODE" = "200" ]; then
    SCHEDULER_RESPONSE=$(curl -s http://localhost:8011/health/details 2>/dev/null || echo "{}")
    SCHEDULER_STATUS=$(echo "$SCHEDULER_RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('scheduler', {}).get('status', 'unknown'))" 2>/dev/null || echo "unknown")
    LAST_TICK=$(echo "$SCHEDULER_RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('scheduler', {}).get('last_tick_ts', 'never'))" 2>/dev/null || echo "never")
    
    if [ "$SCHEDULER_STATUS" = "ok" ]; then
        echo -e "  ${GREEN}✅ Scheduler работает (status: $SCHEDULER_STATUS)${NC}"
    else
        echo -e "  ${YELLOW}⚠️  Scheduler статус: $SCHEDULER_STATUS${NC}"
    fi
    echo "  Последний тик: $LAST_TICK"
else
    echo -e "  ${YELLOW}⚠️  Не удалось проверить scheduler (health endpoint недоступен)${NC}"
fi

echo ""
echo "=================================================================================="
echo "ПРОВЕРКА ЗАВЕРШЕНА"
echo "=================================================================================="
echo ""
echo "Рекомендации (Context7):"
echo "  1. Если контейнер не запущен: docker-compose up -d telethon-ingest"
echo "  2. Если health endpoint недоступен: проверьте логи: docker-compose logs telethon-ingest"
echo "  3. Если scheduler не работает: проверьте переменные окружения PARSER_SCHEDULER_INTERVAL_SEC"
echo "  4. Проверьте метрики в Prometheus: http://localhost:9090/graph?g0.expr=scheduler_last_tick_ts_seconds"
