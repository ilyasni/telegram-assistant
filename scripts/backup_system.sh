#!/bin/bash

# ============================================================================
# SYSTEM BACKUP SCRIPT
# ============================================================================
# Создание полного бэкапа системы перед аудитом и очисткой данных
# Включает: PostgreSQL, Redis, Qdrant, Neo4j, конфигурации

set -euo pipefail

# Конфигурация
BACKUP_DIR="/opt/telegram-assistant/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="system_backup_${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

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

# Создание директории для бэкапа
create_backup_dir() {
    log_info "Создание директории для бэкапа: ${BACKUP_PATH}"
    mkdir -p "${BACKUP_PATH}"
    mkdir -p "${BACKUP_PATH}/postgresql"
    mkdir -p "${BACKUP_PATH}/redis"
    mkdir -p "${BACKUP_PATH}/qdrant"
    mkdir -p "${BACKUP_PATH}/neo4j"
    mkdir -p "${BACKUP_PATH}/configs"
    mkdir -p "${BACKUP_PATH}/logs"
}

# Бэкап PostgreSQL
backup_postgresql() {
    log_info "Создание бэкапа PostgreSQL..."
    
    # Получение переменных окружения
    source /opt/telegram-assistant/.env 2>/dev/null || {
        log_warning "Не удалось загрузить .env файл, используем значения по умолчанию"
        POSTGRES_PASSWORD="postgres"
        POSTGRES_DB="postgres"
    }
    
    # Создание полного дампа
    docker exec telegram-assistant-supabase-db-1 pg_dump \
        -U postgres \
        -d "${POSTGRES_DB:-postgres}" \
        --verbose \
        --clean \
        --if-exists \
        --create \
        --format=custom \
        --file="/tmp/postgres_backup_${TIMESTAMP}.dump"
    
    # Копирование дампа из контейнера
    docker cp telegram-assistant-supabase-db-1:/tmp/postgres_backup_${TIMESTAMP}.dump \
        "${BACKUP_PATH}/postgresql/postgres_backup_${TIMESTAMP}.dump"
    
    # Создание SQL дампа для читаемости
    docker exec telegram-assistant-supabase-db-1 pg_dump \
        -U postgres \
        -d "${POSTGRES_DB:-postgres}" \
        --verbose \
        --clean \
        --if-exists \
        --create \
        --format=plain \
        --file="/tmp/postgres_backup_${TIMESTAMP}.sql"
    
    docker cp telegram-assistant-supabase-db-1:/tmp/postgres_backup_${TIMESTAMP}.sql \
        "${BACKUP_PATH}/postgresql/postgres_backup_${TIMESTAMP}.sql"
    
    # Очистка временных файлов в контейнере
    docker exec telegram-assistant-supabase-db-1 rm -f /tmp/postgres_backup_${TIMESTAMP}.dump /tmp/postgres_backup_${TIMESTAMP}.sql
    
    log_success "PostgreSQL бэкап создан: ${BACKUP_PATH}/postgresql/"
}

# Бэкап Redis
backup_redis() {
    log_info "Создание бэкапа Redis..."
    
    # Создание RDB дампа
    docker exec telegram-assistant-redis-1 redis-cli BGSAVE
    
    # Ожидание завершения сохранения
    while [ "$(docker exec telegram-assistant-redis-1 redis-cli LASTSAVE)" = "$(docker exec telegram-assistant-redis-1 redis-cli LASTSAVE)" ]; do
        sleep 1
    done
    
    # Копирование RDB файла
    docker cp telegram-assistant-redis-1:/data/dump.rdb \
        "${BACKUP_PATH}/redis/dump_${TIMESTAMP}.rdb"
    
    # Экспорт всех ключей в текстовом формате
    docker exec telegram-assistant-redis-1 redis-cli --rdb /tmp/redis_backup_${TIMESTAMP}.rdb
    docker cp telegram-assistant-redis-1:/tmp/redis_backup_${TIMESTAMP}.rdb \
        "${BACKUP_PATH}/redis/redis_backup_${TIMESTAMP}.rdb"
    
    # Экспорт конфигурации
    docker exec telegram-assistant-redis-1 redis-cli CONFIG GET "*" > "${BACKUP_PATH}/redis/redis_config_${TIMESTAMP}.txt"
    
    # Очистка временных файлов
    docker exec telegram-assistant-redis-1 rm -f /tmp/redis_backup_${TIMESTAMP}.rdb
    
    log_success "Redis бэкап создан: ${BACKUP_PATH}/redis/"
}

# Бэкап Qdrant
backup_qdrant() {
    log_info "Создание бэкапа Qdrant..."
    
    # Получение списка коллекций
    docker exec telegram-assistant-qdrant-1 curl -s "http://localhost:6333/collections" > "${BACKUP_PATH}/qdrant/collections_${TIMESTAMP}.json"
    
    # Создание снапшота для каждой коллекции
    COLLECTIONS=$(docker exec telegram-assistant-qdrant-1 curl -s "http://localhost:6333/collections" | jq -r '.result.collections[].name' 2>/dev/null || echo "")
    
    if [ -n "$COLLECTIONS" ]; then
        for collection in $COLLECTIONS; do
            log_info "Создание снапшота коллекции: $collection"
            
            # Создание снапшота
            SNAPSHOT_RESPONSE=$(docker exec telegram-assistant-qdrant-1 curl -s -X POST "http://localhost:6333/collections/${collection}/snapshots")
            SNAPSHOT_NAME=$(echo "$SNAPSHOT_RESPONSE" | jq -r '.result.name' 2>/dev/null || echo "")
            
            if [ -n "$SNAPSHOT_NAME" ] && [ "$SNAPSHOT_NAME" != "null" ]; then
                # Копирование снапшота
                docker cp "telegram-assistant-qdrant-1:/qdrant/snapshots/${collection}/${SNAPSHOT_NAME}" \
                    "${BACKUP_PATH}/qdrant/${collection}_${SNAPSHOT_NAME}"
                log_info "Снапшот создан: ${collection}_${SNAPSHOT_NAME}"
            else
                log_warning "Не удалось создать снапшот для коллекции: $collection"
            fi
        done
    else
        log_warning "Коллекции Qdrant не найдены или пусты"
    fi
    
    # Экспорт информации о коллекциях
    docker exec telegram-assistant-qdrant-1 curl -s "http://localhost:6333/collections" > "${BACKUP_PATH}/qdrant/collections_info_${TIMESTAMP}.json"
    
    log_success "Qdrant бэкап создан: ${BACKUP_PATH}/qdrant/"
}

# Бэкап Neo4j
backup_neo4j() {
    log_info "Создание бэкапа Neo4j..."
    
    # Получение переменных окружения
    source /opt/telegram-assistant/.env 2>/dev/null || {
        log_warning "Не удалось загрузить .env файл, используем значения по умолчанию"
        NEO4J_PASSWORD="changeme"
    }
    
    # Создание дампа базы данных
    docker exec telegram-assistant-neo4j-1 cypher-shell \
        -u neo4j \
        -p "${NEO4J_PASSWORD:-changeme}" \
        "CALL apoc.export.cypher.all('/tmp/neo4j_backup_${TIMESTAMP}.cypher', {format: 'cypher-shell'})" \
        > "${BACKUP_PATH}/neo4j/neo4j_export_${TIMESTAMP}.log" 2>&1 || {
        log_warning "APOC не доступен, создаем простой дамп"
        
        # Простой дамп без APOC
        docker exec telegram-assistant-neo4j-1 cypher-shell \
            -u neo4j \
            -p "${NEO4J_PASSWORD:-changeme}" \
            "MATCH (n) RETURN n LIMIT 1000" \
            > "${BACKUP_PATH}/neo4j/neo4j_sample_${TIMESTAMP}.cypher" 2>&1 || true
    }
    
    # Копирование дампа из контейнера (если создан)
    if docker exec telegram-assistant-neo4j-1 test -f "/tmp/neo4j_backup_${TIMESTAMP}.cypher"; then
        docker cp telegram-assistant-neo4j-1:/tmp/neo4j_backup_${TIMESTAMP}.cypher \
            "${BACKUP_PATH}/neo4j/neo4j_backup_${TIMESTAMP}.cypher"
        docker exec telegram-assistant-neo4j-1 rm -f /tmp/neo4j_backup_${TIMESTAMP}.cypher
    fi
    
    # Экспорт статистики базы данных
    docker exec telegram-assistant-neo4j-1 cypher-shell \
        -u neo4j \
        -p "${NEO4J_PASSWORD:-changeme}" \
        "CALL db.stats.retrieve('GRAPH COUNTS')" \
        > "${BACKUP_PATH}/neo4j/neo4j_stats_${TIMESTAMP}.json" 2>&1 || true
    
    # Экспорт схемы
    docker exec telegram-assistant-neo4j-1 cypher-shell \
        -u neo4j \
        -p "${NEO4J_PASSWORD:-changeme}" \
        "CALL db.schema.visualization()" \
        > "${BACKUP_PATH}/neo4j/neo4j_schema_${TIMESTAMP}.json" 2>&1 || true
    
    log_success "Neo4j бэкап создан: ${BACKUP_PATH}/neo4j/"
}

# Бэкап конфигураций
backup_configs() {
    log_info "Создание бэкапа конфигураций..."
    
    # Копирование конфигурационных файлов
    cp /opt/telegram-assistant/.env "${BACKUP_PATH}/configs/.env" 2>/dev/null || log_warning ".env файл не найден"
    cp /opt/telegram-assistant/docker-compose.yml "${BACKUP_PATH}/configs/docker-compose.yml"
    cp /opt/telegram-assistant/Caddyfile "${BACKUP_PATH}/configs/Caddyfile" 2>/dev/null || log_warning "Caddyfile не найден"
    cp /opt/telegram-assistant/Makefile "${BACKUP_PATH}/configs/Makefile"
    
    # Копирование конфигураций Prometheus и Grafana
    cp -r /opt/telegram-assistant/prometheus "${BACKUP_PATH}/configs/" 2>/dev/null || log_warning "Prometheus конфиг не найден"
    cp -r /opt/telegram-assistant/grafana "${BACKUP_PATH}/configs/" 2>/dev/null || log_warning "Grafana конфиг не найден"
    
    # Копирование SQL миграций
    cp -r /opt/telegram-assistant/supabase "${BACKUP_PATH}/configs/" 2>/dev/null || log_warning "Supabase конфиг не найден"
    
    log_success "Конфигурации сохранены: ${BACKUP_PATH}/configs/"
}

# Бэкап логов
backup_logs() {
    log_info "Создание бэкапа логов..."
    
    # Создание архива логов за последние 24 часа
    docker compose logs --since=24h > "${BACKUP_PATH}/logs/docker_compose_logs_${TIMESTAMP}.log" 2>&1 || true
    
    # Логи отдельных сервисов
    docker compose logs --since=24h api > "${BACKUP_PATH}/logs/api_logs_${TIMESTAMP}.log" 2>&1 || true
    docker compose logs --since=24h worker > "${BACKUP_PATH}/logs/worker_logs_${TIMESTAMP}.log" 2>&1 || true
    docker compose logs --since=24h telethon-ingest > "${BACKUP_PATH}/logs/telethon_logs_${TIMESTAMP}.log" 2>&1 || true
    
    log_success "Логи сохранены: ${BACKUP_PATH}/logs/"
}

# Создание метаданных бэкапа
create_backup_metadata() {
    log_info "Создание метаданных бэкапа..."
    
    cat > "${BACKUP_PATH}/backup_metadata.json" << EOF
{
    "backup_name": "${BACKUP_NAME}",
    "timestamp": "${TIMESTAMP}",
    "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "system_info": {
        "hostname": "$(hostname)",
        "docker_version": "$(docker --version)",
        "docker_compose_version": "$(docker compose version)"
    },
    "services_status": $(docker compose ps --format json),
    "backup_components": [
        "postgresql",
        "redis", 
        "qdrant",
        "neo4j",
        "configs",
        "logs"
    ],
    "notes": "Полный бэкап системы перед аудитом и очисткой данных"
}
EOF
    
    log_success "Метаданные созданы: ${BACKUP_PATH}/backup_metadata.json"
}

# Создание архива
create_archive() {
    log_info "Создание архива бэкапа..."
    
    cd "${BACKUP_DIR}"
    tar -czf "${BACKUP_NAME}.tar.gz" "${BACKUP_NAME}/"
    
    # Удаление временной директории
    rm -rf "${BACKUP_PATH}"
    
    log_success "Архив создан: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
    
    # Показать размер архива
    ARCHIVE_SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" | cut -f1)
    log_info "Размер архива: ${ARCHIVE_SIZE}"
}

# Основная функция
main() {
    log_info "Начало создания бэкапа системы..."
    log_info "Время: $(date)"
    log_info "Директория бэкапа: ${BACKUP_PATH}"
    
    # Проверка доступности Docker
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker недоступен. Проверьте, что Docker запущен."
        exit 1
    fi
    
    # Проверка доступности docker-compose
    if ! docker compose ps >/dev/null 2>&1; then
        log_error "Docker Compose недоступен. Проверьте конфигурацию."
        exit 1
    fi
    
    # Создание бэкапа
    create_backup_dir
    backup_postgresql
    backup_redis
    backup_qdrant
    backup_neo4j
    backup_configs
    backup_logs
    create_backup_metadata
    create_archive
    
    log_success "Бэкап системы успешно создан!"
    log_info "Архив: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
    log_info "Для восстановления: tar -xzf ${BACKUP_NAME}.tar.gz"
}

# Запуск скрипта
main "$@"
