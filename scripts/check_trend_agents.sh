#!/bin/bash
# Context7: Скрипт для проверки работы агентов трендов

set -euo pipefail

echo "🔍 Проверка работы агентов трендов..."

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

echo ""
echo "1️⃣  Проверка логов Trend Editor Agent..."
$DOCKER_CMD logs worker 2>&1 | grep -i "trend_editor" | tail -10 || echo "   ℹ️  Логи не найдены (возможно, агент еще не запускался)"

echo ""
echo "2️⃣  Проверка метрик Prometheus..."
if $DOCKER_CMD ps | grep -q "prometheus"; then
    echo "   📊 Метрики доступны на http://localhost:9090"
    echo "   Проверьте метрики:"
    echo "   - trend_editor_requests_total"
    echo "   - trend_editor_quality_score"
    echo "   - trend_qa_filtered_total"
    echo "   - trend_qa_latency_seconds"
else
    echo "   ⚠️  Prometheus не запущен"
fi

echo ""
echo "3️⃣  Проверка таблиц в БД..."
$DOCKER_CMD exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    'user_trend_profiles' as table_name,
    COUNT(*) as row_count
FROM user_trend_profiles
UNION ALL
SELECT 
    'trend_interactions' as table_name,
    COUNT(*) as row_count
FROM trend_interactions
UNION ALL
SELECT 
    'trend_threshold_suggestions' as table_name,
    COUNT(*) as row_count
FROM trend_threshold_suggestions;
" 2>/dev/null || echo "   ⚠️  Не удалось подключиться к БД"

echo ""
echo "4️⃣  Проверка API endpoints..."
if $DOCKER_CMD ps | grep -q "api"; then
    API_URL="http://localhost:8000"
    echo "   Проверка /api/trends/interactions..."
    curl -s -o /dev/null -w "   HTTP Status: %{http_code}\n" "$API_URL/api/trends/interactions" || echo "   ⚠️  Endpoint недоступен"
else
    echo "   ⚠️  API контейнер не запущен"
fi

echo ""
echo "5️⃣  Проверка конфигурации..."
if [ -f .env ]; then
    echo "   ✅ .env файл найден"
    grep -q "TREND_EDITOR_ENABLED" .env && echo "   ✅ TREND_EDITOR_ENABLED настроен" || echo "   ⚠️  TREND_EDITOR_ENABLED не найден"
    grep -q "TREND_QA_ENABLED" .env && echo "   ✅ TREND_QA_ENABLED настроен" || echo "   ⚠️  TREND_QA_ENABLED не найден"
else
    echo "   ⚠️  .env файл не найден"
fi

echo ""
echo "✅ Проверка завершена!"

