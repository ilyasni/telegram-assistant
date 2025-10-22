#!/bin/bash
# Диагностический скрипт для Telegram Assistant
# Проверяет состояние всех сервисов и зависимостей

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Счетчики
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0

# Функция для логирования
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

# Функция для проверки
check() {
    local name="$1"
    local command="$2"
    local expected="$3"
    
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    
    log "Проверка: $name"
    
    if eval "$command" >/dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} $name"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        return 0
    else
        echo -e "  ${RED}✗${NC} $name"
        if [ -n "$expected" ]; then
            echo -e "    ${YELLOW}Ожидалось: $expected${NC}"
        fi
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
        return 1
    fi
}

# Функция для проверки HTTP endpoint
check_http() {
    local name="$1"
    local url="$2"
    local expected_status="$3"
    
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    
    log "Проверка HTTP: $name"
    
    local status_code
    status_code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    
    if [ "$status_code" = "$expected_status" ]; then
        echo -e "  ${GREEN}✓${NC} $name (HTTP $status_code)"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        return 0
    else
        echo -e "  ${RED}✗${NC} $name (HTTP $status_code, ожидался $expected_status)"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
        return 1
    fi
}

# Функция для проверки Docker контейнера
check_container() {
    local name="$1"
    local container_name="$2"
    
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    
    log "Проверка контейнера: $name"
    
    if docker compose ps "$container_name" | grep -q "Up"; then
        echo -e "  ${GREEN}✓${NC} $name (контейнер запущен)"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        return 0
    else
        echo -e "  ${RED}✗${NC} $name (контейнер не запущен)"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
        return 1
    fi
}

echo -e "${BLUE}=== Диагностика Telegram Assistant ===${NC}"
echo

# Проверка Docker
log "Проверка Docker..."
check "Docker установлен" "docker --version" "Docker version"
check "Docker Compose установлен" "docker compose version" "Docker Compose version"

# Проверка .env файла
log "Проверка конфигурации..."
if [ -f ".env" ]; then
    echo -e "  ${GREEN}✓${NC} .env файл найден"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    echo -e "  ${RED}✗${NC} .env файл не найден"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# Проверка обязательных переменных
log "Проверка переменных окружения..."
required_vars=("TELEGRAM_API_ID" "TELEGRAM_API_HASH" "SESSION_CRYPTO_KEY" "REDIS_URL" "POSTGRES_URL")
for var in "${required_vars[@]}"; do
    if grep -q "^${var}=" .env 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $var установлена"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        echo -e "  ${RED}✗${NC} $var не установлена"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
done

# Проверка Docker Compose файла
log "Проверка Docker Compose..."
check "docker-compose.yml найден" "[ -f docker-compose.yml ]" "docker-compose.yml файл"

# Проверка контейнеров
log "Проверка контейнеров..."
check_container "API сервис" "api"
check_container "Telethon Ingest" "telethon-ingest"
check_container "Redis" "redis"
check_container "PostgreSQL" "supabase-db"
check_container "Qdrant" "qdrant"
check_container "Caddy" "caddy"

# Проверка health endpoints
log "Проверка health endpoints..."
check_http "API Health" "http://localhost:8000/health" "200"
check_http "Telethon Health" "http://localhost:8011/health" "200"

# Проверка Redis
log "Проверка Redis..."
if docker compose exec redis redis-cli ping >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} Redis отвечает на PING"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    echo -e "  ${RED}✗${NC} Redis не отвечает на PING"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# Проверка PostgreSQL
log "Проверка PostgreSQL..."
if docker compose exec supabase-db psql -U postgres -d telegram_assistant -c '\l' >/dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} PostgreSQL подключение успешно"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    echo -e "  ${RED}✗${NC} PostgreSQL подключение не удалось"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

# Проверка Qdrant
log "Проверка Qdrant..."
check_http "Qdrant Health" "http://localhost:6333/health" "200"

# Проверка DNS резолва (если настроен домен)
if [ -n "$CADDY_DOMAINS" ]; then
    log "Проверка DNS резолва..."
    IFS=',' read -ra DOMAINS <<< "$CADDY_DOMAINS"
    for domain in "${DOMAINS[@]}"; do
        domain=$(echo "$domain" | xargs) # убираем пробелы
        if nslookup "$domain" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✓${NC} DNS резолв для $domain"
            PASSED_CHECKS=$((PASSED_CHECKS + 1))
        else
            echo -e "  ${RED}✗${NC} DNS резолв для $domain не удался"
            FAILED_CHECKS=$((FAILED_CHECKS + 1))
        fi
        TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    done
fi

# Проверка volumes
log "Проверка volumes..."
volumes=("telethon-ingest/sessions" "qdrant/storage" "redis/data")
for volume in "${volumes[@]}"; do
    if [ -d "$volume" ]; then
        echo -e "  ${GREEN}✓${NC} Volume $volume существует"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        echo -e "  ${YELLOW}!${NC} Volume $volume не найден (будет создан автоматически)"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    fi
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
done

# Проверка портов
log "Проверка портов..."
ports=("80" "443" "8000" "8011" "6379" "5432" "6333")
for port in "${ports[@]}"; do
    if netstat -tuln 2>/dev/null | grep -q ":$port "; then
        echo -e "  ${GREEN}✓${NC} Порт $port открыт"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        echo -e "  ${RED}✗${NC} Порт $port не открыт"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
done

# Итоговая сводка
echo
echo -e "${BLUE}=== Итоговая сводка ===${NC}"
echo -e "Всего проверок: ${TOTAL_CHECKS}"
echo -e "Успешно: ${GREEN}${PASSED_CHECKS}${NC}"
echo -e "Неудачно: ${RED}${FAILED_CHECKS}${NC}"

if [ $FAILED_CHECKS -eq 0 ]; then
    echo -e "${GREEN}✓ Все проверки пройдены успешно!${NC}"
    exit 0
else
    echo -e "${RED}✗ Обнаружены проблемы. Проверьте логи выше.${NC}"
    echo
    echo -e "${YELLOW}Рекомендации:${NC}"
    echo "1. Убедитесь, что все контейнеры запущены: docker compose ps"
    echo "2. Проверьте логи: docker compose logs"
    echo "3. Проверьте .env файл на наличие всех обязательных переменных"
    echo "4. Убедитесь, что порты не заняты другими процессами"
    exit 1
fi
