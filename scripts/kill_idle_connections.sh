#!/bin/bash

# ============================================================================
# KILL IDLE CONNECTIONS
# ============================================================================
# Context7 best practice: безопасное закрытие долгих idle соединений
# Закрывает только соединения в состоянии 'idle' или 'idle in transaction' старше указанного времени

set -euo pipefail

CONTAINER_NAME="telegram-assistant-supabase-db-1"
DB_NAME="postgres"
DB_USER="postgres"
IDLE_MINUTES=${1:-10}  # По умолчанию 10 минут

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка доступности
check_connection() {
    if ! docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1;" >/dev/null 2>&1; then
        log_error "Не удается подключиться к базе данных"
        exit 1
    fi
}

# Показать соединения, которые будут закрыты
show_connections_to_kill() {
    log_info "Соединения для закрытия (idle > ${IDLE_MINUTES} минут):"
    echo ""
    
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
        SELECT 
            pid,
            usename,
            COALESCE(application_name, 'NULL') as application,
            state,
            now() - state_change as idle_duration,
            client_addr
        FROM pg_stat_activity 
        WHERE datname = '${DB_NAME}'
        AND state IN ('idle', 'idle in transaction')
        AND now() - state_change > interval '${IDLE_MINUTES} minutes'
        AND pid != pg_backend_pid()
        ORDER BY state_change;
    " 2>&1 | grep -v "WARNING:" || true
    
    echo ""
}

# Закрыть idle соединения
kill_idle_connections() {
    log_warning "Закрытие idle соединений старше ${IDLE_MINUTES} минут..."
    
    local killed=$(docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -t -c "
        SELECT count(*) 
        FROM pg_stat_activity 
        WHERE datname = '${DB_NAME}'
        AND state IN ('idle', 'idle in transaction')
        AND now() - state_change > interval '${IDLE_MINUTES} minutes'
        AND pid != pg_backend_pid();
    " 2>&1 | grep -v "WARNING:" | tr -d ' ')
    
    if [ -z "$killed" ] || [ "$killed" = "0" ]; then
        log_info "Нет соединений для закрытия"
        return 0
    fi
    
    log_info "Закрываю $killed соединений..."
    
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity 
        WHERE datname = '${DB_NAME}'
        AND state IN ('idle', 'idle in transaction')
        AND now() - state_change > interval '${IDLE_MINUTES} minutes'
        AND pid != pg_backend_pid();
    " 2>&1 | grep -v "WARNING:" || true
    
    log_success "Закрыто соединений: $killed"
}

# Основная функция
main() {
    log_info "Закрытие долгих idle соединений"
    log_info "Минимальное время idle: ${IDLE_MINUTES} минут"
    log_info "Время: $(date)"
    echo ""
    
    check_connection
    show_connections_to_kill
    
    read -p "Продолжить с закрытием? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Прервано пользователем"
        exit 0
    fi
    
    kill_idle_connections
    
    log_success "Операция завершена"
}

# Запуск скрипта
main "$@"

