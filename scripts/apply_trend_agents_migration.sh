#!/bin/bash
# Context7: Скрипт для применения миграции агентов трендов

set -euo pipefail

echo "🔧 Применение миграции для агентов трендов..."

# Проверяем наличие docker-compose
if ! command -v docker-compose &> /dev/null && ! command -v docker &> /dev/null; then
    echo "❌ Docker не найден. Установите Docker для применения миграции."
    exit 1
fi

# Определяем команду docker
if command -v docker-compose &> /dev/null; then
    DOCKER_CMD="docker-compose"
elif docker compose version &> /dev/null 2>&1; then
    DOCKER_CMD="docker compose"
else
    echo "❌ Не найдена команда docker-compose или 'docker compose'"
    exit 1
fi

cd "$(dirname "$0")/.."

# Проверяем, запущен ли контейнер API
if ! $DOCKER_CMD ps | grep -q "api"; then
    echo "⚠️  Контейнер API не запущен. Запускаем..."
    $DOCKER_CMD up -d api
    sleep 5
fi

echo "📦 Применение миграции через Alembic..."
# Применяем конкретную миграцию, так как может быть несколько head ревизий
$DOCKER_CMD exec api alembic upgrade 20251116_trend_agents || $DOCKER_CMD exec api alembic upgrade head

echo "✅ Миграция применена успешно!"

# Проверяем, что таблицы созданы
echo "🔍 Проверка созданных таблиц..."
$DOCKER_CMD exec supabase-db psql -U postgres -d postgres -c "
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('user_trend_profiles', 'trend_interactions', 'trend_threshold_suggestions')
ORDER BY table_name;
"

echo "✅ Проверка завершена!"

