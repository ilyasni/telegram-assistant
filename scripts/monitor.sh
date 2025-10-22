#!/bin/bash
# Скрипт мониторинга логов Telegram Assistant
# Показывает логи всех сервисов в реальном времени

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Функция для логирования
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

# Функция для вывода с цветом
color_log() {
    local service="$1"
    local line="$2"
    
    case "$service" in
        "api")
            echo -e "${GREEN}[API]${NC} $line"
            ;;
        "telethon-ingest")
            echo -e "${YELLOW}[TELETHON]${NC} $line"
            ;;
        "redis")
            echo -e "${RED}[REDIS]${NC} $line"
            ;;
        "supabase-db")
            echo -e "${PURPLE}[POSTGRES]${NC} $line"
            ;;
        "qdrant")
            echo -e "${CYAN}[QDRANT]${NC} $line"
            ;;
        "caddy")
            echo -e "${BLUE}[CADDY]${NC} $line"
            ;;
        "grafana")
            echo -e "${PURPLE}[GRAFANA]${NC} $line"
            ;;
        *)
            echo -e "[$service] $line"
            ;;
    esac
}

# Проверка аргументов
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Использование: $0 [опции]"
    echo
    echo "Опции:"
    echo "  --help, -h          Показать эту справку"
    echo "  --services <list>   Показать логи только указанных сервисов"
    echo "  --tail <number>     Количество последних строк (по умолчанию: 100)"
    echo "  --follow, -f        Следить за логами в реальном времени (по умолчанию)"
    echo "  --no-follow         Показать только последние строки и выйти"
    echo
    echo "Примеры:"
    echo "  $0                                    # Все сервисы, следить за логами"
    echo "  $0 --services api,telethon-ingest    # Только API и Telethon"
    echo "  $0 --tail 50 --no-follow             # Последние 50 строк без слежения"
    echo
    echo "Доступные сервисы:"
    echo "  api, telethon-ingest, redis, supabase-db, qdrant, caddy, grafana"
    exit 0
fi

# Параметры по умолчанию
SERVICES="api,telethon-ingest,redis,supabase-db,qdrant,caddy"
TAIL_LINES=100
FOLLOW=true

# Парсинг аргументов
while [[ $# -gt 0 ]]; do
    case $1 in
        --services)
            SERVICES="$2"
            shift 2
            ;;
        --tail)
            TAIL_LINES="$2"
            shift 2
            ;;
        --follow|-f)
            FOLLOW=true
            shift
            ;;
        --no-follow)
            FOLLOW=false
            shift
            ;;
        *)
            echo "Неизвестный аргумент: $1"
            echo "Используйте --help для справки"
            exit 1
            ;;
    esac
done

# Проверка, что Docker Compose запущен
if ! docker compose ps >/dev/null 2>&1; then
    echo -e "${RED}Ошибка: Docker Compose не запущен или не найден${NC}"
    echo "Убедитесь, что вы находитесь в директории проекта и Docker Compose запущен"
    exit 1
fi

log "Запуск мониторинга логов Telegram Assistant"
echo -e "Сервисы: ${YELLOW}$SERVICES${NC}"
echo -e "Строк: ${YELLOW}$TAIL_LINES${NC}"
echo -e "Слежение: ${YELLOW}$FOLLOW${NC}"
echo

# Проверка доступности сервисов
log "Проверка доступности сервисов..."
IFS=',' read -ra SERVICE_ARRAY <<< "$SERVICES"
AVAILABLE_SERVICES=()

for service in "${SERVICE_ARRAY[@]}"; do
    service=$(echo "$service" | xargs) # убираем пробелы
    
    if docker compose ps "$service" | grep -q "Up"; then
        echo -e "  ${GREEN}✓${NC} $service запущен"
        AVAILABLE_SERVICES+=("$service")
    else
        echo -e "  ${RED}✗${NC} $service не запущен"
    fi
done

if [ ${#AVAILABLE_SERVICES[@]} -eq 0 ]; then
    echo -e "${RED}Нет доступных сервисов для мониторинга${NC}"
    exit 1
fi

echo
log "Начинаем мониторинг логов..."

# Функция для обработки логов
process_logs() {
    while IFS= read -r line; do
        # Парсим строку лога Docker Compose
        if [[ $line =~ ^([a-zA-Z0-9_-]+)\|(.*)$ ]]; then
            service="${BASH_REMATCH[1]}"
            log_line="${BASH_REMATCH[2]}"
            color_log "$service" "$log_line"
        else
            echo "$line"
        fi
    done
}

# Запуск мониторинга
if [ "$FOLLOW" = true ]; then
    # Следим за логами в реальном времени
    docker compose logs --tail="$TAIL_LINES" -f "${AVAILABLE_SERVICES[@]}" | process_logs
else
    # Показываем только последние строки
    docker compose logs --tail="$TAIL_LINES" "${AVAILABLE_SERVICES[@]}" | process_logs
fi

# Обработка сигналов для корректного завершения
trap 'echo -e "\n${YELLOW}Мониторинг остановлен${NC}"; exit 0' INT TERM
