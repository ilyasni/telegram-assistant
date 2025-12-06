#!/bin/bash
# Тестовая отправка уведомления в Telegram через AlertManager webhook

set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
WEBHOOK_URL="${API_URL}/api/monitoring/alertmanager/webhook"

echo "=== Тестовая отправка уведомления в Telegram ==="
echo ""

# Создаем тестовый алерт в формате AlertManager
TEST_ALERT=$(cat <<EOF
{
  "version": "4",
  "groupKey": "test_alert",
  "status": "firing",
  "receiver": "telegram-webhook",
  "groupLabels": {
    "alertname": "TestAlert"
  },
  "commonLabels": {
    "alertname": "TestAlert",
    "severity": "critical"
  },
  "commonAnnotations": {
    "summary": "Тестовое уведомление от AlertManager",
    "description": "Это тестовое сообщение для проверки настройки Telegram уведомлений"
  },
  "externalURL": "http://localhost:9093",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "TestAlert",
        "severity": "critical",
        "component": "test"
      },
      "annotations": {
        "summary": "Тестовое уведомление",
        "description": "Проверка работы Telegram уведомлений"
      },
      "startsAt": "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://localhost:9090"
    }
  ]
}
EOF
)

echo "📤 Отправка тестового алерта..."
echo "   URL: ${WEBHOOK_URL}"
echo ""

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  -H "Content-Type: application/json" \
  -d "${TEST_ALERT}" \
  "${WEBHOOK_URL}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

echo "📥 Ответ сервера:"
echo "   HTTP Code: ${HTTP_CODE}"
echo "   Body: ${BODY}"
echo ""

if [ "$HTTP_CODE" -eq 200 ]; then
    echo "✅ Запрос успешно отправлен"
    echo ""
    echo "💡 Проверьте группу @testgroupassistant в Telegram"
    echo "   Должно прийти сообщение с тестовым алертом"
    echo ""
    echo "📋 Если сообщение не пришло, проверьте:"
    echo "   1. Логи API: docker logs telegram-assistant-api-1 --tail 50 | grep -i alert"
    echo "   2. Что бот добавлен в группу и имеет права администратора"
    echo "   3. Что ALERT_TELEGRAM_CHAT_ID установлен в .env"
else
    echo "❌ Ошибка при отправке запроса"
    echo ""
    echo "📋 Проверьте:"
    echo "   1. Что API запущен: docker ps | grep api"
    echo "   2. Логи API: docker logs telegram-assistant-api-1 --tail 50"
fi

