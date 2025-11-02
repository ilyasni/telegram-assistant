#!/bin/bash

# ============================================================================
# MONITOR DATABASE CONNECTIONS
# ============================================================================
# Context7 best practice: мониторинг активных соединений PostgreSQL
# Помогает выявлять утечки соединений и переполнение пула

set -euo pipefail

CONTAINER_NAME="telegram-assistant-supabase-db-1"
DB_NAME="postgres"
DB_USER="postgres"

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

# Общая статистика соединений
show_connection_stats() {
    log_info "Статистика соединений PostgreSQL:"
    echo ""
    
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
        SELECT 
            setting::int as max_connections,
            (SELECT count(*) FROM pg_stat_activity) as current_connections,
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'active') as active,
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle') as idle,
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction') as idle_in_transaction,
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction (aborted)') as idle_aborted
        FROM pg_settings 
        WHERE name = 'max_connections';
    " 2>&1 | grep -v "WARNING:" || true
    
    echo ""
}

# Соединения по приложениям
show_connections_by_app() {
    log_info "Соединения по приложениям:"
    echo ""
    
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
        SELECT 
            COALESCE(application_name, 'NULL') as application,
            count(*) as connections,
            count(*) FILTER (WHERE state = 'active') as active,
            count(*) FILTER (WHERE state = 'idle') as idle,
            count(*) FILTER (WHERE state = 'idle in transaction') as idle_in_txn
        FROM pg_stat_activity 
        WHERE datname = '${DB_NAME}'
        GROUP BY application_name
        ORDER BY connections DESC;
    " 2>&1 | grep -v "WARNING:" || true
    
    echo ""
}

# Долгие idle соединения
show_idle_connections() {
    log_info "Idle соединения (> 5 минут):"
    echo ""
    
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
        SELECT 
            pid,
            usename,
            application_name,
            state,
            now() - state_change as idle_duration,
            client_addr
        FROM pg_stat_activity 
        WHERE datname = '${DB_NAME}'
        AND state IN ('idle', 'idle in transaction')
        AND now() - state_change > interval '5 minutes'
        ORDER BY state_change;
    " 2>&1 | grep -v "WARNING:" || true
    
    echo ""
}

# Предупреждение о переполнении
check_connection_limit() {
    local usage=$(docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -t -c "
        SELECT 
            ROUND((SELECT count(*)::numeric FROM pg_stat_activity)::numeric / 
                  (SELECT setting::numeric FROM pg_settings WHERE name = 'max_connections')::numeric * 100, 1);
    " 2>&1 | grep -v "WARNING:" | grep -E "^[0-9]+\.?[0-9]*$" | tr -d ' ' || echo "0")
    
    if [ -n "$usage" ] && [ -n "${usage//[0-9.]}" ] && (( $(awk "BEGIN {print ($usage > 80)}") )); then
        log_warning "Использование соединений: ${usage}% (критично!)"
        return 1
    elif [ -n "$usage" ] && (( $(awk "BEGIN {print ($usage > 60)}") )); then
        log_warning "Использование соединений: ${usage}% (высокое)"
        return 0
    else
        log_success "Использование соединений: ${usage}% (норма)"
        return 0
    fi
}

# Основная функция
main() {
    log_info "Мониторинг соединений PostgreSQL"
    log_info "Время: $(date)"
    echo ""
    
    check_connection
    show_connection_stats
    show_connections_by_app
    show_idle_connections
    
    if ! check_connection_limit; then
        log_warning "Рекомендуется:"
        log_warning "1. Проверить настройки pool_size в приложениях"
        log_warning "2. Настроить pgbouncer для connection pooling"
        log_warning "3. Закрыть idle соединения: ./scripts/kill_idle_connections.sh"
        exit 1
    fi
}

# Запуск скрипта
main "$@"

