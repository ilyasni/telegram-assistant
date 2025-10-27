#!/bin/bash

# Quick Truth Detector
# ====================
# Быстрое включение детектора правды для диагностики database_save_failed

echo "🕵️ Быстрое включение детектора правды"
echo "===================================="

# Проверяем .env файл
if [ ! -f ".env" ]; then
    echo "📝 Создание .env файла..."
    cp env.example .env
fi

# Добавляем feature flags
echo ""
echo "🔧 Настройка feature flags для детектора правды..."

# Очищаем старые настройки
sed -i '/AUTH_FINALIZE_DB_BYPASS/d' .env
sed -i '/AUTH_DETAILED_DIAGNOSTICS/d' .env
sed -i '/AUTH_RETRY_OPERATIONAL_ERRORS/d' .env
sed -i '/AUTH_SOFT_DEGRADATION/d' .env
sed -i '/AUTH_LOG_SQL_STATEMENTS/d' .env

# Добавляем новые
echo "" >> .env
echo "# Truth Detector Feature Flags" >> .env
echo "AUTH_FINALIZE_DB_BYPASS=on" >> .env
echo "AUTH_DETAILED_DIAGNOSTICS=on" >> .env
echo "AUTH_RETRY_OPERATIONAL_ERRORS=on" >> .env
echo "AUTH_SOFT_DEGRADATION=on" >> .env
echo "AUTH_LOG_SQL_STATEMENTS=on" >> .env

echo "✅ Feature flags добавлены в .env"

# Проверяем содержимое
echo ""
echo "📋 Текущие feature flags:"
grep "AUTH_" .env

echo ""
echo "🎯 Инструкции для диагностики:"
echo "1. Перезапустите API: docker restart telegram-assistant-api-1"
echo "2. Попробуйте авторизацию через QR код"
echo "3. Проверьте логи: docker logs telegram-assistant-api-1 | grep -E '(Auth finalize|correlation_id|DB_|NON_DB)'"
echo "4. Проверьте метрики: curl http://localhost:8000/metrics | grep auth"
echo ""
echo "🔍 Ключевые строки для поиска в логах:"
echo "   - 'DB_INTEGRITY during session upsert'"
echo "   - 'DB_OPERATIONAL during session upsert'"
echo "   - 'DB_GENERIC during session upsert'"
echo "   - 'NON_DB during session upsert'"
echo "   - 'correlation_id' + 'user_id' + 'session_length'"
echo ""
echo "📊 После диагностики отключите feature flags:"
echo "   sed -i 's/AUTH_FINALIZE_DB_BYPASS=on/AUTH_FINALIZE_DB_BYPASS=off/' .env"
echo "   sed -i 's/AUTH_DETAILED_DIAGNOSTICS=on/AUTH_DETAILED_DIAGNOSTICS=off/' .env"
echo "   sed -i 's/AUTH_LOG_SQL_STATEMENTS=on/AUTH_LOG_SQL_STATEMENTS=off/' .env"
