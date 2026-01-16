#!/bin/bash
# Диагностика алерта CrawlTriggerQueueDepthHigh
# Проверяет метрики, состояние очереди в Redis и логи для выявления причин высокой глубины очереди

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
STREAM_IN="stream:posts:tagged"
CONSUMER_GROUP="crawl_triggers"

echo -e "${BLUE}=== Диагностика алерта CrawlTriggerQueueDepthHigh ===${NC}\n"

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

# Функция для проверки метрики глубины очереди
check_queue_metrics() {
    echo -e "${BLUE}2. Проверка метрики глубины очереди...${NC}"
    
    local query="crawl_trigger_queue_depth_current"
    local response=$(curl -s "${PROMETHEUS_URL}/api/v1/query?query=${query}" 2>/dev/null || echo "")
    
    if [ -z "$response" ]; then
        echo -e "${RED}✗ Не удалось получить метрику из Prometheus${NC}\n"
        return 1
    fi
    
    local queue_depth=$(echo "$response" | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "0")
    
    echo -e "  Текущая глубина очереди: ${YELLOW}${queue_depth}${NC}"
    
    if [ "$queue_depth" -gt 500 ]; then
        echo -e "  ${RED}⚠ Глубина очереди превышает порог (500) - это вызывает алерт${NC}"
    elif [ "$queue_depth" -gt 1000 ]; then
        echo -e "  ${RED}⚠ Критически высокая глубина очереди (>1000)${NC}"
    else
        echo -e "  ${GREEN}✓ Глубина очереди в норме${NC}"
    fi
    
    # Проверяем скорость обработки
    local processing_rate=$(curl -s "${PROMETHEUS_URL}/api/v1/query?query=rate(crawl_triggers_total[5m])" 2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "0")
    echo -e "  Скорость обработки (triggers/sec): ${GREEN}${processing_rate}${NC}"
    
    # Проверяем latency
    local p95_latency=$(curl -s "${PROMETHEUS_URL}/api/v1/query?query=histogram_quantile(0.95,rate(crawl_trigger_processing_latency_seconds_bucket[5m]))" 2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "0")
    echo -e "  p95 latency (сек): ${YELLOW}${p95_latency}${NC}"
    
    if (( $(echo "$p95_latency > 2.0" | bc -l 2>/dev/null || echo 0) )); then
        echo -e "  ${RED}⚠ Высокая latency (>2s) - это может замедлять обработку${NC}"
    fi
    
    echo ""
}

# Функция для проверки состояния очереди в Redis
check_redis_queue() {
    echo -e "${BLUE}3. Проверка состояния очереди в Redis...${NC}"
    
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
    
    # Проверяем длину стрима
    local stream_length=$(redis-cli -h "$redis_host" -p "$redis_port" XLEN "$STREAM_IN" 2>/dev/null || echo "0")
    echo -e "  Длина стрима ${STREAM_IN}: ${YELLOW}${stream_length}${NC}"
    
    if [ "$stream_length" -gt 500 ]; then
        echo -e "  ${RED}⚠ Высокая длина стрима (>500)${NC}"
    fi
    
    # Проверяем pending сообщения
    local pending_info=$(redis-cli -h "$redis_host" -p "$redis_port" XPENDING "$STREAM_IN" "$CONSUMER_GROUP" 2>/dev/null || echo "")
    if [ -n "$pending_info" ]; then
        local pending_count=$(echo "$pending_info" | awk '{print $1}')
        echo -e "  Pending сообщений: ${YELLOW}${pending_count}${NC}"
        
        if [ "$pending_count" -gt 100 ]; then
            echo -e "  ${RED}⚠ Высокое количество pending сообщений (>100)${NC}"
        fi
    else
        echo -e "  ${YELLOW}⚠ Не удалось получить информацию о pending сообщениях${NC}"
    fi
    
    # Проверяем consumer'ы
    local consumers=$(redis-cli -h "$redis_host" -p "$redis_port" XINFO GROUPS "$STREAM_IN" 2>/dev/null | grep -A 10 "name $CONSUMER_GROUP" | grep "consumers" | awk '{print $2}' || echo "0")
    echo -e "  Активных consumer'ов: ${GREEN}${consumers}${NC}"
    
    if [ "$consumers" -eq "0" ]; then
        echo -e "  ${RED}⚠ Нет активных consumer'ов - это критическая проблема!${NC}"
    elif [ "$consumers" -lt "2" ]; then
        echo -e "  ${YELLOW}⚠ Мало consumer'ов (<2) - может быть недостаточно для обработки нагрузки${NC}"
    fi
    
    # Показываем последние сообщения
    echo -e "\n  ${BLUE}Последние 3 сообщения в очереди:${NC}"
    redis-cli -h "$redis_host" -p "$redis_port" XREVRANGE "$STREAM_IN" + - COUNT 3 2>/dev/null | head -20 || echo "    Не удалось получить сообщения"
    
    echo ""
}

# Функция для проверки логов crawl_trigger_task
check_crawl_trigger_logs() {
    echo -e "${BLUE}4. Проверка логов crawl_trigger_task...${NC}"
    
    if ! docker ps | grep -q "$WORKER_CONTAINER"; then
        echo -e "${YELLOW}⚠ Контейнер worker не найден, пропускаем проверку логов${NC}\n"
        return 0
    fi
    
    # Проверяем последние ошибки
    local error_count=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "crawl.*trigger.*error\|error.*crawl.*trigger" | wc -l)
    echo -e "  Найдено ошибок, связанных с crawl_trigger: ${RED}${error_count}${NC}"
    
    if [ "$error_count" -gt 0 ]; then
        echo -e "\n  ${BLUE}Последние ошибки:${NC}"
        docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "crawl.*trigger.*error\|error.*crawl.*trigger" | tail -5 | while read line; do
            echo -e "    ${RED}${line}${NC}"
        done
    fi
    
    # Проверяем скорость обработки в логах
    local processed_count=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "crawl.*trigger.*processed\|triggered.*crawl" | wc -l)
    echo -e "\n  Обработано событий (последние 1000 строк): ${GREEN}${processed_count}${NC}"
    
    # Проверяем предупреждения о высокой глубине очереди
    local queue_warnings=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "queue.*depth\|high.*queue" | wc -l)
    if [ "$queue_warnings" -gt 0 ]; then
        echo -e "  ${YELLOW}⚠ Найдено предупреждений о глубине очереди: ${queue_warnings}${NC}"
    fi
    
    # Проверяем блокировки или медленную обработку
    local slow_processing=$(docker logs "$WORKER_CONTAINER" --tail 1000 2>&1 | grep -i "slow.*processing\|processing.*slow\|timeout" | wc -l)
    if [ "$slow_processing" -gt 0 ]; then
        echo -e "  ${YELLOW}⚠ Найдено предупреждений о медленной обработке: ${slow_processing}${NC}"
    fi
    
    echo ""
}

# Функция для проверки скорости поступления сообщений
check_input_rate() {
    echo -e "${BLUE}5. Проверка скорости поступления сообщений в posts.tagged...${NC}"
    
    # Проверяем метрику rate поступления сообщений
    local input_rate=$(curl -s "${PROMETHEUS_URL}/api/v1/query?query=rate(stream_messages_total{stream=\"posts.tagged\"}[5m])" 2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "0")
    echo -e "  Скорость поступления (сообщений/сек): ${YELLOW}${input_rate}${NC}"
    
    # Проверяем общее количество сообщений
    local total_messages=$(curl -s "${PROMETHEUS_URL}/api/v1/query?query=stream_messages_total{stream=\"posts.tagged\"}" 2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "0")
    echo -e "  Всего сообщений в posts.tagged: ${GREEN}${total_messages}${NC}"
    
    echo ""
}

# Функция для проверки производительности Redis
check_redis_performance() {
    echo -e "${BLUE}6. Проверка производительности Redis...${NC}"
    
    if ! command -v redis-cli &> /dev/null; then
        echo -e "${YELLOW}⚠ redis-cli не найден, пропускаем проверку Redis${NC}\n"
        return 0
    fi
    
    local redis_host="localhost"
    local redis_port="6379"
    if [[ "$REDIS_URL" =~ redis://([^:]+):([0-9]+) ]]; then
        redis_host="${BASH_REMATCH[1]}"
        redis_port="${BASH_REMATCH[2]}"
    fi
    
    # Проверяем latency Redis
    local redis_latency=$(redis-cli -h "$redis_host" -p "$redis_port" --latency -i 1 -c 1 2>/dev/null | tail -1 | awk '{print $3}' || echo "0")
    echo -e "  Redis latency (мс): ${YELLOW}${redis_latency}${NC}"
    
    if (( $(echo "$redis_latency > 10" | bc -l 2>/dev/null || echo 0) )); then
        echo -e "  ${RED}⚠ Высокая latency Redis (>10ms) - это может замедлять обработку${NC}"
    fi
    
    # Проверяем использование памяти
    local memory_used=$(redis-cli -h "$redis_host" -p "$redis_port" INFO memory 2>/dev/null | grep "used_memory_human" | cut -d: -f2 | tr -d '\r' || echo "unknown")
    echo -e "  Использование памяти: ${GREEN}${memory_used}${NC}"
    
    echo ""
}

# Функция для вывода рекомендаций
print_recommendations() {
    echo -e "${BLUE}=== Рекомендации ===${NC}\n"
    
    echo -e "${YELLOW}1. Если высокая глубина очереди (>500):${NC}"
    echo -e "   - Увеличить количество consumer'ов в consumer group"
    echo -e "   - Проверить производительность обработки сообщений"
    echo -e "   - Убедиться, что нет блокировок или ошибок в обработке"
    echo ""
    
    echo -e "${YELLOW}2. Если высокая latency обработки (>2s):${NC}"
    echo -e "   - Проверить производительность Redis"
    echo -e "   - Проверить логи на наличие медленных операций"
    echo -e "   - Оптимизировать логику обработки сообщений"
    echo ""
    
    echo -e "${YELLOW}3. Если нет активных consumer'ов:${NC}"
    echo -e "   - Проверить, что crawl_trigger_task запущен"
    echo -e "   - Проверить логи worker на наличие ошибок"
    echo -e "   - Убедиться, что consumer group создан корректно"
    echo ""
    
    echo -e "${YELLOW}4. Если высокая скорость поступления сообщений:${NC}"
    echo -e "   - Увеличить количество consumer'ов"
    echo -e "   - Оптимизировать batch processing"
    echo -e "   - Рассмотреть возможность горизонтального масштабирования"
    echo ""
    
    echo -e "${YELLOW}5. Если высокая latency Redis:${NC}"
    echo -e "   - Проверить нагрузку на Redis"
    echo -e "   - Проверить использование памяти"
    echo -e "   - Рассмотреть возможность оптимизации запросов"
    echo ""
    
    echo -e "${YELLOW}6. Общие проверки:${NC}"
    echo -e "   - Проверить метрики в Grafana: http://localhost:3000"
    echo -e "   - Проверить активные алерты в Prometheus: ${PROMETHEUS_URL}/alerts"
    echo -e "   - Проверить логи worker: docker logs ${WORKER_CONTAINER} --tail 100"
    echo ""
}

# Главная функция
main() {
    check_prometheus || exit 1
    check_queue_metrics
    check_redis_queue
    check_crawl_trigger_logs
    check_input_rate
    check_redis_performance
    print_recommendations
    
    echo -e "${GREEN}=== Диагностика завершена ===${NC}"
}

# Запуск
main

