#!/bin/bash
# Получение chat_id группы через бота
# Использование: отправьте любое сообщение боту в группе, затем запустите этот скрипт

set -euo pipefail

echo "=== Получение chat_id группы через бота ==="
echo ""
echo "Инструкция:"
echo "1. Откройте группу @testgroupassistant в Telegram"
echo "2. Отправьте любое сообщение боту (например: /start)"
echo "3. Запустите этот скрипт для получения chat_id"
echo ""
read -p "Нажмите Enter после отправки сообщения боту..."

# Проверяем наличие .env файла
if [ ! -f .env ]; then
    echo "❌ Файл .env не найден"
    echo "   Создайте .env на основе env.example"
    exit 1
fi

# Загружаем переменные окружения
source .env

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN не установлен в .env"
    exit 1
fi

echo ""
echo "🔍 Получение последних обновлений от бота..."
echo ""

# Используем API Telegram для получения обновлений
UPDATES=$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" 2>/dev/null)

if [ $? -ne 0 ]; then
    echo "❌ Ошибка при обращении к Telegram API"
    exit 1
fi

# Парсим chat_id из обновлений
CHAT_IDS=$(echo "$UPDATES" | jq -r '.result[] | select(.message.chat.type == "group" or .message.chat.type == "supergroup") | .message.chat.id' 2>/dev/null | sort -u)

if [ -z "$CHAT_IDS" ]; then
    echo "⚠️  Не найдено сообщений из групп"
    echo ""
    echo "Попробуйте:"
    echo "1. Убедитесь, что бот добавлен в группу"
    echo "2. Отправьте сообщение боту в группе"
    echo "3. Запустите скрипт снова"
    exit 1
fi

echo "✅ Найдены chat_id групп:"
echo ""
for CHAT_ID in $CHAT_IDS; do
    # Получаем информацию о чате
    CHAT_INFO=$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getChat?chat_id=${CHAT_ID}" 2>/dev/null)
    CHAT_TITLE=$(echo "$CHAT_INFO" | jq -r '.result.title // .result.username // "Unknown"' 2>/dev/null)
    CHAT_TYPE=$(echo "$CHAT_INFO" | jq -r '.result.type // "unknown"' 2>/dev/null)
    
    echo "  Chat ID: ${CHAT_ID}"
    echo "  Название: ${CHAT_TITLE}"
    echo "  Тип: ${CHAT_TYPE}"
    echo ""
done

# Если найден только один chat_id, предлагаем использовать его
if [ $(echo "$CHAT_IDS" | wc -l) -eq 1 ]; then
    CHAT_ID=$(echo "$CHAT_IDS" | head -1)
    echo "📋 Рекомендуемый chat_id: ${CHAT_ID}"
    echo ""
    echo "Добавьте в .env файл:"
    echo "  ALERT_TELEGRAM_CHAT_ID=${CHAT_ID}"
    echo ""
    read -p "Добавить автоматически? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Проверяем, есть ли уже ALERT_TELEGRAM_CHAT_ID
        if grep -q "^ALERT_TELEGRAM_CHAT_ID=" .env 2>/dev/null; then
            # Заменяем существующее значение
            sed -i "s|^ALERT_TELEGRAM_CHAT_ID=.*|ALERT_TELEGRAM_CHAT_ID=${CHAT_ID}|" .env
            echo "✅ Обновлено ALERT_TELEGRAM_CHAT_ID=${CHAT_ID} в .env"
        else
            # Добавляем новую строку
            echo "ALERT_TELEGRAM_CHAT_ID=${CHAT_ID}" >> .env
            echo "✅ Добавлено ALERT_TELEGRAM_CHAT_ID=${CHAT_ID} в .env"
        fi
    fi
fi

echo ""
echo "💡 Альтернативный способ: используйте username напрямую"
echo "   ALERT_TELEGRAM_CHAT_ID=@testgroupassistant"
echo "   (код автоматически разрешит username в chat_id)"

