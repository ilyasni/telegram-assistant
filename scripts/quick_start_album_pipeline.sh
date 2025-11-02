#!/bin/bash
# Быстрый старт пайплайна альбомов
# Context7: автоматизация проверки и запуска

set -e

echo "🚀 Быстрый старт пайплайна альбомов"
echo "===================================="

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Проверка окружения
echo ""
echo "1️⃣ Проверка окружения..."

if [ -z "$DATABASE_URL" ]; then
    echo -e "${YELLOW}⚠️  DATABASE_URL не установлен${NC}"
    echo "   Установите: export DATABASE_URL=postgresql+asyncpg://..."
else
    echo -e "${GREEN}✅ DATABASE_URL установлен${NC}"
fi

if [ -z "$REDIS_URL" ]; then
    echo -e "${YELLOW}⚠️  REDIS_URL не установлен${NC}"
    echo "   Установите: export REDIS_URL=redis://..."
else
    echo -e "${GREEN}✅ REDIS_URL установлен${NC}"
fi

# Проверка готовности
echo ""
echo "2️⃣ Проверка готовности компонентов..."

if [ -f "scripts/check_album_pipeline_ready.py" ]; then
    python3 scripts/check_album_pipeline_ready.py
    CHECK_RESULT=$?
    if [ $CHECK_RESULT -ne 0 ]; then
        echo -e "${RED}❌ Проверка готовности не пройдена${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}⚠️  Скрипт проверки не найден${NC}"
fi

# Применение миграции
echo ""
echo "3️⃣ Применение миграции БД..."

if [ -z "$DATABASE_URL" ]; then
    echo -e "${YELLOW}⚠️  DATABASE_URL не установлен, пропускаем миграцию${NC}"
    echo "   Примените вручную: psql \$DATABASE_URL -f telethon-ingest/migrations/004_add_album_fields.sql"
else
    if psql "$DATABASE_URL" -f telethon-ingest/migrations/004_add_album_fields.sql 2>/dev/null; then
        echo -e "${GREEN}✅ Миграция применена${NC}"
    else
        echo -e "${YELLOW}⚠️  Ошибка применения миграции (возможно, уже применена)${NC}"
    fi
fi

# Проверка worker
echo ""
echo "4️⃣ Проверка worker..."

if docker ps | grep -q worker; then
    echo -e "${GREEN}✅ Worker контейнер запущен${NC}"
    echo "   Проверка логов album_assembler:"
    docker logs worker 2>&1 | grep -i "album" | tail -5 || echo "   (нет логов album в последних записях)"
else
    echo -e "${YELLOW}⚠️  Worker контейнер не запущен${NC}"
    echo "   Запустите: docker compose up -d worker"
fi

# Проверка метрик
echo ""
echo "5️⃣ Проверка метрик..."

if curl -s http://localhost:8001/metrics | grep -q "albums_parsed_total"; then
    echo -e "${GREEN}✅ Метрики album доступны${NC}"
    echo "   URL: http://localhost:8001/metrics"
else
    echo -e "${YELLOW}⚠️  Метрики album не найдены${NC}"
    echo "   Убедитесь, что worker запущен и метрики доступны"
fi

# Итоги
echo ""
echo "===================================="
echo "✅ Быстрый старт завершён"
echo ""
echo "📚 Документация:"
echo "   - docs/ALBUM_PIPELINE_READY.md"
echo "   - docs/ALBUM_PIPELINE_DEPLOYMENT.md"
echo ""
echo "🧪 Тестирование:"
echo "   python3 scripts/test_album_pipeline_full.py"
echo "   python3 scripts/create_test_album.py"
echo ""
echo "📊 Мониторинг:"
echo "   curl http://localhost:8001/metrics | grep album"
echo "   curl http://localhost:8000/health/detailed | jq '.tasks.album_assembler'"
echo ""

