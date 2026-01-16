#!/bin/bash
# Диагностика алертов AlbumItemsCountMismatch
# Проверяет метрики, состояние в Redis и логи для выявления причин проблем с альбомами

set -euo pipefail

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Конфигурация
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
WORKER_CONTAINER="${WORKER_CONTAINER:-telegram-assistant-worker-1}"

echo -e "${BLUE}=== Диагностика алертов AlbumItemsCountMismatch ===${NC}\n"

# Функция для проверки доступности Prometheus
check_prometheus() {
    echo -e "${BLUE}1. Проверка доступности Prometheus...${NC}"
    if curl -s -f "${PROMETHEUS_URL}/api/v1/status/config" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Prometheus доступен${NC}\n"
        return 0
    else
        echo -e "${RED}✗ Prometheus недоступен по адресу ${PROMETHEUS_URL}${NC}\n"
        return 1
    fi
}

# Функция для проверки метрик альбомов
check_album_metrics() {
    echo -e "${BLUE}2. Проверка метрик альбомов в Prometheus...${NC}"
    
    # Получаем текущие значения метрики album_items_count_gauge
    local query="album_items_count_gauge"
    local response=$(curl -s "${PROMETHEUS_URL}/api/v1/query?query=${query}" 2>/dev/null || echo "")
    
    if [ -z "$response" ]; then
        echo -e "${RED}✗ Не удалось получить метрики из Prometheus${NC}\n"
        return 1
    fi
    
    # Парсим JSON ответ
    local total_count=$(echo "$response" | jq -r '.data.result[] | select(.metric.status=="total") | .value[1]' 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
    local analyzed_count=$(echo "$response" | jq -r '.data.result[] | select(.metric.status=="analyzed") | .value[1]' 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
    local pending_count=$(echo "$response" | jq -r '.data.result[] | select(.metric.status=="pending") | .value[1]' 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
    
    echo -e "  Всего элементов: ${GREEN}${total_count}${NC}"
    echo -e "  Проанализировано: ${YELLOW}${analyzed_count}${NC}"
    echo -e "  Ожидают анализа: ${RED}${pending_count}${NC}"
    
    if [ "$total_count" -gt 0 ]; then
        local coverage=$(echo "scale=2; ${analyzed_count} * 100 / ${total_count}" | bc 2>/dev/null || echo "0")
        echo -e "  Покрытие: ${coverage}%"
        
        if (( $(echo "$coverage < 90" | bc -l 2>/dev/null || echo 1) )); then
            echo -e "  ${RED}⚠ Покрытие ниже 90% - это может вызывать алерты${NC}"
        fi
    fi
    
    # Проверяем конкретные альбомы с проблемами
    echo -e "\n  ${BLUE}Альбомы с несоответствием (analyzed < 90% от total):${NC}"
    echo "$response" | jq -r '.data.result[] | select(.metric.status=="total") | "\(.metric.album_id) \(.value[1])"' 2>/dev/null | while read album_id total; do
        analyzed=$(echo "$response" | jq -r ".data.result[] | select(.metric.album_id==\"$album_id\" and .metric.status==\"analyzed\") | .value[1]" 2>/dev/null || echo "0")
        if [ -n "$analyzed" ] && [ "$analyzed" != "0" ]; then
            coverage=$(echo "scale=2; $analyzed * 100 / $total" | bc 2>/dev/null || echo "0")
            if (( $(echo "$coverage < 90" | bc -l 2>/dev/null || echo 1) )); then
                echo -e "    ${RED}Album ${album_id}: ${analyzed}/${total} (${coverage}%)${NC}"
            fi
        fi
    done
    
    echo ""
}

# Функция для проверки состояния в Redis
check_redis_state() {
    echo -e "${BLUE}3. Проверка состояния альбомов в Redis...${NC}"
    
    # Проверяем наличие Redis CLI
    if ! command -v redis-cli &> /dev/null; then
        echo -e "${YELLOW}⚠ redis-cli не найден, пропускаем проверку Redis${NC}\n"
        return 0
    fi
    
    # Извлекаем host и port из REDIS_URL
    local redis_host="localhost"
    local redis_port="6379"
    if [[ "$REDIS_URL" =~ redis://([^:]+):([0-9]+) ]]; then
        redis_host="${BASH_REMATCH[1]}"
        redis_port="${BASH_REMATCH[2]}"
    fi
    
    # Подсчитываем ключи album:state:*
    local state_keys=$(redis-cli -h "$redis_host" -p "$redis_port" --scan --pattern "album:state:*" 2>/dev/null | wc -l)
    echo -e "  Найдено состояний альбомов: ${GREEN}${state_keys}${NC}"
    
    if [ "$state_keys" -gt 50 ]; then
        echo -e "  ${YELLOW}⚠ Высокий backlog состояний (>50) - это может указывать на проблемы${NC}"
    fi
    
    # Показываем примеры состояний
    echo -e "\n  ${BLUE}Примеры состояний альбомов:${NC}"
    redis-cli -h "$redis_host" -p "$redis_port" --scan --pattern "album:state:*" 2>/dev/null | head -5 | while read key; do
        state=$(redis-cli -h "$redis_host" -p "$redis_port" GET "$key" 2>/dev/null)
        if [ -n "$state" ]; then
            album_id=$(echo "$state" | jq -r '.album_id' 2>/dev/null || echo "unknown")
            items_count=$(echo "$state" | jq -r '.items_count' 2>/dev/null || echo "0")
            items_analyzed=$(echo "$state" | jq -r '.items_analyzed | length' 2>/dev/null || echo "0")
            echo -e "    Album ${album_id}: ${items_analyzed}/${items_count} проанализировано"
        fi
    done
    
    echo ""
}

# Функция для проверки логов worker
check_worker_logs() {
    echo -e "${BLUE}4. Проверка логов worker (album_assembler_task)...${NC}"
    
    if ! docker ps | grep -q "$WORKER_CONTAINER"; then
        echo -e "${YELLOW}⚠ Контейнер worker не найден, пропускаем проверку логов${NC}\n"
        return 0
    fi
    
    # Проверяем последние ошибки
    local error_count=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "album.*error\|error.*album" | wc -l)
    echo -e "  Найдено ошибок, связанных с альбомами: ${RED}${error_count}${NC}"
    
    if [ "$error_count" -gt 0 ]; then
        echo -e "\n  ${BLUE}Последние ошибки:${NC}"
        docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "album.*error\|error.*album" | tail -5 | while read line; do
            echo -e "    ${RED}${line}${NC}"
        done
    fi
    
    # Проверяем успешные сборки
    local assembled_count=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "album.*assembled\|assembled.*album" | wc -l)
    echo -e "\n  Успешных сборок (последние 1000 строк): ${GREEN}${assembled_count}${NC}"
    
    # Проверяем предупреждения о несоответствии
    local mismatch_count=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "items_count mismatch\|mismatch.*items" | wc -l)
    if [ "$mismatch_count" -gt 0 ]; then
        echo -e "  ${YELLOW}⚠ Найдено предупреждений о несоответствии items_count: ${mismatch_count}${NC}"
    fi
    
    echo ""
}

# Функция для проверки vision_analysis_task
check_vision_analysis() {
    echo -e "${BLUE}5. Проверка работы vision_analysis_task...${NC}"
    
    if ! docker ps | grep -q "$WORKER_CONTAINER"; then
        echo -e "${YELLOW}⚠ Контейнер worker не найден${NC}\n"
        return 0
    fi
    
    # Проверяем логи vision анализа для альбомов
    local vision_count=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "vision.*analyzed\|vision.*album" | wc -l)
    echo -e "  Событий vision анализа (последние 1000 строк): ${GREEN}${vision_count}${NC}"
    
    # Проверяем ошибки vision анализа
    local vision_errors=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "vision.*error\|error.*vision" | wc -l)
    if [ "$vision_errors" -gt 0 ]; then
        echo -e "  ${RED}⚠ Найдено ошибок vision анализа: ${vision_errors}${NC}"
    fi
    
    echo ""
}

# Функция для вывода рекомендаций
print_recommendations() {
    echo -e "${BLUE}=== Рекомендации ===${NC}\n"
    
    echo -e "${YELLOW}1. Если покрытие < 90%:${NC}"
    echo -e "   - Проверить работу vision_analysis_task"
    echo -e "   - Убедиться, что все элементы альбома проходят vision анализ"
    echo -e "   - Проверить логи worker на наличие ошибок vision анализа"
    echo ""
    
    echo -e "${YELLOW}2. Если высокий backlog в Redis:${NC}"
    echo -e "   - Проверить TTL состояний альбомов (должен быть 24 часа)"
    echo -e "   - Убедиться, что album_assembler_task работает корректно"
    echo -e "   - Проверить производительность Redis"
    echo ""
    
    echo -e "${YELLOW}3. Если альбомы с >10 элементами:${NC}"
    echo -e "   - Telegram ограничивает альбомы до 10 элементов"
    echo -e "   - Альбомы с >10 элементами могут быть неполными"
    echo -e "   - Это нормальное поведение, не требует исправления"
    echo ""
    
    echo -e "${YELLOW}4. Если items_count не соответствует post_ids:${NC}"
    echo -e "   - Проверить логику парсинга альбомов в telethon-ingest"
    echo -e "   - Убедиться, что items_count корректно устанавливается при парсинге"
    echo ""
    
    echo -e "${YELLOW}5. Общие проверки:${NC}"
    echo -e "   - Проверить health check album_assembler_task: curl http://localhost:8000/health/detailed | jq '.tasks.album_assembler'"
    echo -e "   - Проверить метрики в Grafana: http://localhost:3000"
    echo -e "   - Проверить активные алерты в Prometheus: ${PROMETHEUS_URL}/alerts"
    echo ""
}

# Главная функция
main() {
    check_prometheus || exit 1
    check_album_metrics
    check_redis_state
    check_worker_logs
    check_vision_analysis
    print_recommendations
    
    echo -e "${GREEN}=== Диагностика завершена ===${NC}"
}

# Запуск
main

