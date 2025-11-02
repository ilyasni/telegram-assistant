#!/bin/bash

# ============================================================================
# FIX POSTGRES COLLATION VERSION MISMATCH
# ============================================================================
# Исправление предупреждения о collation version mismatch в PostgreSQL
# Context7 best practice: безопасное обновление collation с проверками
#
# Примечание: REINDEX DATABASE CONCURRENTLY поддерживается в PostgreSQL 12+
# Это минимизирует блокировки, но всё равно требует окно низкой нагрузки

set -euo pipefail

# Конфигурация
CONTAINER_NAME="telegram-assistant-supabase-db-1"
DB_NAME="postgres"
DB_USER="postgres"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функции логирования
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

# Проверка доступности контейнера
check_container() {
    log_info "Проверка доступности контейнера ${CONTAINER_NAME}..."
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_error "Контейнер ${CONTAINER_NAME} не найден или не запущен"
        exit 1
    fi
    log_success "Контейнер доступен"
}

# Проверка текущей версии collation
check_collation() {
    log_info "Проверка текущей версии collation..."
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
        SELECT 
            datname, 
            datcollate, 
            datctype, 
            datcollversion 
        FROM pg_database 
        WHERE datname = '${DB_NAME}';
    " 2>&1 | grep -v "WARNING:" || true
}

# Создание бэкапа перед изменениями (опционально, но рекомендуется)
create_backup() {
    log_warning "ВНИМАНИЕ: Перед исправлением collation рекомендуется создать бэкап"
    log_info "Запустите скрипт бэкапа: ./scripts/backup_system.sh"
    read -p "Продолжить без бэкапа? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Прервано пользователем. Создайте бэкап и повторите."
        exit 0
    fi
}

# Обновление версии collation
refresh_collation() {
    log_info "Обновление метаданных collation..."
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
        ALTER DATABASE ${DB_NAME} REFRESH COLLATION VERSION;
    " 2>&1 | grep -v "WARNING:" || true
    
    log_success "Collation version обновлён"
}

# Переиндексация базы данных (CONCURRENTLY для минимизации блокировок)
reindex_database() {
    log_warning "Начинается переиндексация базы данных (может занять время)..."
    log_info "REINDEX DATABASE CONCURRENTLY минимизирует блокировки, но требует времени"
    
    # Проверка версии PostgreSQL (CONCURRENTLY поддерживается с версии 12)
    PG_VERSION=$(docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -t -c "SELECT version();" | grep -oE "PostgreSQL [0-9]+" | grep -oE "[0-9]+" | head -1)
    
    if [ -z "$PG_VERSION" ] || [ "$PG_VERSION" -lt 12 ]; then
        log_warning "PostgreSQL версия $PG_VERSION < 12, CONCURRENTLY не поддерживается"
        log_warning "Используется обычный REINDEX (с блокировками)"
        docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
            REINDEX DATABASE ${DB_NAME};
        " 2>&1 | grep -v "WARNING:" || true
    else
        log_info "PostgreSQL версия $PG_VERSION, используем REINDEX DATABASE CONCURRENTLY"
        docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "
            REINDEX DATABASE CONCURRENTLY ${DB_NAME};
        " 2>&1 | grep -v "WARNING:" || true
    fi
    
    log_success "Переиндексация завершена"
}

# Проверка зависимых баз данных
check_other_databases() {
    log_info "Проверка других баз данных в кластере..."
    OTHER_DBS=$(docker exec "${CONTAINER_NAME}" psql -U "${DB_USER}" -t -c "
        SELECT datname 
        FROM pg_database 
        WHERE datistemplate = false 
        AND datname != 'postgres' 
        AND datname != 'template0' 
        AND datname != 'template1';
    " 2>&1 | grep -v "WARNING:" | tr -d ' ' || true)
    
    if [ -n "$OTHER_DBS" ]; then
        log_warning "Найдены другие базы данных:"
        echo "$OTHER_DBS"
        log_info "Для них также нужно выполнить REFRESH COLLATION VERSION и REINDEX"
        log_info "Выполните команды вручную для каждой базы:"
        echo "  docker exec ${CONTAINER_NAME} psql -U ${DB_USER} -d <db_name> -c 'ALTER DATABASE <db_name> REFRESH COLLATION VERSION;'"
        echo "  docker exec ${CONTAINER_NAME} psql -U ${DB_USER} -d <db_name> -c 'REINDEX DATABASE CONCURRENTLY <db_name>;'"
    else
        log_info "Других баз данных не найдено"
    fi
}

# Основная функция
main() {
    log_info "Исправление collation version mismatch в PostgreSQL"
    log_info "Время: $(date)"
    
    check_container
    check_collation
    create_backup
    refresh_collation
    reindex_database
    check_other_databases
    
    log_success "Исправление collation завершено успешно!"
    log_info "Проверьте логи на наличие предупреждений:"
    echo "  docker logs --tail 50 ${CONTAINER_NAME}"
}

# Запуск скрипта
main "$@"

