#!/bin/bash

# Enable Truth Detector
# =====================
# Включает детектор правды для диагностики database_save_failed

echo "🕵️ Включение детектора правды для диагностики database_save_failed"
echo "=================================================================="

# Проверяем, что мы в правильной директории
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ Ошибка: запустите скрипт из корневой директории проекта"
    exit 1
fi

# Создаем .env файл с feature flags если его нет
if [ ! -f ".env" ]; then
    echo "📝 Создание .env файла с feature flags..."
    cp env.example .env
fi

# Добавляем feature flags в .env
echo ""
echo "🔧 Настройка feature flags..."

# AUTH_FINALIZE_DB_BYPASS для изоляции проблемы
if ! grep -q "AUTH_FINALIZE_DB_BYPASS" .env; then
    echo "AUTH_FINALIZE_DB_BYPASS=on" >> .env
    echo "✅ Добавлен AUTH_FINALIZE_DB_BYPASS=on"
else
    echo "⚠️  AUTH_FINALIZE_DB_BYPASS уже существует в .env"
fi

# AUTH_DETAILED_DIAGNOSTICS для детальной диагностики
if ! grep -q "AUTH_DETAILED_DIAGNOSTICS" .env; then
    echo "AUTH_DETAILED_DIAGNOSTICS=on" >> .env
    echo "✅ Добавлен AUTH_DETAILED_DIAGNOSTICS=on"
else
    echo "⚠️  AUTH_DETAILED_DIAGNOSTICS уже существует в .env"
fi

# AUTH_RETRY_OPERATIONAL_ERRORS для retry на сетевые ошибки
if ! grep -q "AUTH_RETRY_OPERATIONAL_ERRORS" .env; then
    echo "AUTH_RETRY_OPERATIONAL_ERRORS=on" >> .env
    echo "✅ Добавлен AUTH_RETRY_OPERATIONAL_ERRORS=on"
else
    echo "⚠️  AUTH_RETRY_OPERATIONAL_ERRORS уже существует в .env"
fi

# AUTH_SOFT_DEGRADATION для мягкой деградации
if ! grep -q "AUTH_SOFT_DEGRADATION" .env; then
    echo "AUTH_SOFT_DEGRADATION=on" >> .env
    echo "✅ Добавлен AUTH_SOFT_DEGRADATION=on"
else
    echo "⚠️  AUTH_SOFT_DEGRADATION уже существует в .env"
fi

# AUTH_LOG_SQL_STATEMENTS для логирования SQL
if ! grep -q "AUTH_LOG_SQL_STATEMENTS" .env; then
    echo "AUTH_LOG_SQL_STATEMENTS=on" >> .env
    echo "✅ Добавлен AUTH_LOG_SQL_STATEMENTS=on"
else
    echo "⚠️  AUTH_LOG_SQL_STATEMENTS уже существует в .env"
fi

echo ""
echo "🔄 Перезапуск сервисов с новыми feature flags..."

# Останавливаем API и telethon-ingest
echo "   Остановка API и telethon-ingest..."
docker-compose stop api telethon-ingest

# Запускаем с новыми переменными
echo "   Запуск с feature flags..."
docker-compose up -d api telethon-ingest

# Ждем запуска
echo "   Ожидание запуска сервисов..."
sleep 10

# Проверяем статус
echo ""
echo "📊 Проверка статуса сервисов..."
docker-compose ps api telethon-ingest

echo ""
echo "🧪 Запуск детектора правды..."
python3 scripts/auth_truth_detector.py

echo ""
echo "📋 Инструкции для диагностики:"
echo "1. Попробуйте авторизацию через QR код"
echo "2. Проверьте логи: docker-compose logs api telethon-ingest"
echo "3. Ищите строки с 'Auth finalize' и 'correlation_id'"
echo "4. Проверьте метрики: curl http://localhost:8000/metrics | grep auth"
echo ""
echo "🔍 Ключевые строки для поиска в логах:"
echo "   - 'DB_INTEGRITY during session upsert'"
echo "   - 'DB_OPERATIONAL during session upsert'"
echo "   - 'DB_GENERIC during session upsert'"
echo "   - 'NON_DB during session upsert'"
echo "   - 'correlation_id' + 'user_id' + 'session_length'"
echo ""
echo "🎯 После диагностики отключите feature flags:"
echo "   AUTH_FINALIZE_DB_BYPASS=off"
echo "   AUTH_DETAILED_DIAGNOSTICS=off"
echo "   AUTH_LOG_SQL_STATEMENTS=off"
