#!/bin/bash
# Скрипт проверки доступности сервисов для E2E тестирования
# Возвращает ненулевой код если хотя бы один сервис недоступен

echo "=========================================="
echo "Проверка доступности сервисов для E2E"
echo "=========================================="

# Цвета для вывода
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция проверки
check_service() {
    local name=$1
    local check_cmd=$2
    
    if eval "$check_cmd" > /dev/null 2>&1; then
        echo -e "${GREEN}✅${NC} $name доступен"
        return 0
    else
        echo -e "${RED}❌${NC} $name недоступен"
        return 1
    fi
}

# Проверка переменных окружения
echo ""
echo "📋 Переменные окружения:"
export DATABASE_URL=${DATABASE_URL:-"postgresql://postgres:postgres@supabase-db:5432/telegram_assistant"}
export REDIS_URL=${REDIS_URL:-"redis://redis:6379"}
export QDRANT_URL=${QDRANT_URL:-"http://qdrant:6333"}
export NEO4J_URI=${NEO4J_URI:-"neo4j://neo4j:7687"}

echo "  DATABASE_URL=${DATABASE_URL}"
echo "  REDIS_URL=${REDIS_URL}"
echo "  QDRANT_URL=${QDRANT_URL}"
echo "  NEO4J_URI=${NEO4J_URI}"

# Проверка PostgreSQL
echo ""
echo "🔍 Проверка сервисов:"

FAILED=0

# PostgreSQL (через docker exec если в контейнере, или напрямую)
DB_CHECKED=false
if command -v psql &> /dev/null; then
    DB_HOST=$(echo $DATABASE_URL | sed -n 's/.*@\([^:]*\):.*/\1/p')
    DB_PORT=$(echo $DATABASE_URL | sed -n 's/.*:\([0-9]*\)\/.*/\1/p' | head -1)
    if [ -z "$DB_PORT" ]; then DB_PORT=5432; fi
    
    if check_service "PostgreSQL" "timeout 2 psql '$DATABASE_URL' -c 'SELECT 1'" 2>/dev/null || \
       check_service "PostgreSQL" "timeout 2 nc -z $DB_HOST $DB_PORT"; then
        DB_CHECKED=true
    else
        FAILED=$((FAILED + 1))
    fi
else
    echo -e "${YELLOW}⚠️${NC} psql не установлен, пропускаем проверку PostgreSQL"
fi

# Redis
REDIS_HOST=$(echo $REDIS_URL | sed -n 's/.*:\/\/\([^:]*\):.*/\1/p')
REDIS_PORT=$(echo $REDIS_URL | sed -n 's/.*:\([0-9]*\)$/\1/p' | tail -1)
if [ -z "$REDIS_PORT" ]; then REDIS_PORT=6379; fi

if command -v redis-cli &> /dev/null; then
    if ! check_service "Redis" "timeout 2 redis-cli -u '$REDIS_URL' ping" 2>/dev/null && \
       ! check_service "Redis" "timeout 2 nc -z $REDIS_HOST $REDIS_PORT"; then
        FAILED=$((FAILED + 1))
    fi
else
    if ! check_service "Redis" "timeout 2 nc -z $REDIS_HOST $REDIS_PORT"; then
        FAILED=$((FAILED + 1))
        echo -e "${YELLOW}⚠️${NC} redis-cli не установлен, проверяем порт..."
    fi
fi

# Qdrant
QDRANT_HOST=$(echo $QDRANT_URL | sed -n 's/.*:\/\/\([^:]*\):.*/\1/p')
QDRANT_PORT=$(echo $QDRANT_URL | sed -n 's/.*:\([0-9]*\)$/\1/p' | tail -1)
if [ -z "$QDRANT_PORT" ]; then QDRANT_PORT=6333; fi

if ! check_service "Qdrant" "timeout 2 curl -sf '$QDRANT_URL/health' > /dev/null" && \
   ! check_service "Qdrant" "timeout 2 nc -z $QDRANT_HOST $QDRANT_PORT"; then
    FAILED=$((FAILED + 1))
fi

# Neo4j
NEO4J_HOST=$(echo $NEO4J_URI | sed -n 's/.*:\/\/\([^:]*\):.*/\1/p')
NEO4J_PORT=$(echo $NEO4J_URI | sed -n 's/.*:\([0-9]*\)$/\1/p' | tail -1)
if [ -z "$NEO4J_PORT" ]; then NEO4J_PORT=7687; fi

if ! check_service "Neo4j" "timeout 2 nc -z $NEO4J_HOST $NEO4J_PORT"; then
    FAILED=$((FAILED + 1))
fi

echo ""
echo "=========================================="
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ Все сервисы доступны${NC}"
    echo "=========================================="
    echo ""
    echo "💡 Следующие шаги:"
    echo ""
    echo "1. Установите зависимости:"
    echo "   pip install -r scripts/requirements.txt"
    echo ""
    echo "2. Запустите E2E тест:"
    echo "   make test-smoke"
    exit 0
else
    echo -e "${RED}❌ $FAILED сервис(ов) недоступен${NC}"
    echo "=========================================="
    echo ""
    echo "💡 Проверьте:"
    echo "   docker compose ps"
    echo "   docker compose logs <service>"
    exit $FAILED
fi

