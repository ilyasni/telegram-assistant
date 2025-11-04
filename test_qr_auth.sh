#!/bin/bash
# Тестовый скрипт для проверки QR авторизации

set -e

echo "=== Тест QR авторизации ==="
echo ""

# Очистка старых сессий
echo "1. Очистка старых QR сессий..."
docker compose exec redis redis-cli --scan --pattern "t:*:qr:session" | while read key; do
  echo "  Удаление: $key"
  docker compose exec redis redis-cli DEL "$key" > /dev/null
done
echo "  ✓ Очищено"
echo ""

# Проверка логов API
echo "2. Проверка логов API (последние 20 строк с QR)..."
docker compose logs --tail=100 api 2>&1 | grep -i "qr\|telegram_user_id\|init_data" | tail -20
echo ""

# Проверка логов telethon-ingest
echo "3. Проверка логов telethon-ingest (последние 20 строк с QR)..."
docker compose logs --tail=100 telethon-ingest 2>&1 | grep -i "qr\|telegram_user_id\|ownership" | tail -20
echo ""

# Проверка Redis
echo "4. Проверка активных QR сессий в Redis..."
KEYS=$(docker compose exec redis redis-cli --scan --pattern "t:*:qr:session" 2>/dev/null || echo "")
if [ -z "$KEYS" ]; then
  echo "  ✓ Нет активных сессий (это нормально если не было запросов)"
else
  echo "  Найдены сессии:"
  echo "$KEYS" | while read key; do
    echo "  === $key ==="
    docker compose exec redis redis-cli HGETALL "$key"
    echo ""
  done
fi
echo ""

echo "=== Тест завершен ==="
echo ""
echo "Для проверки полного flow:"
echo "1. Откройте MiniApp в Telegram"
echo "2. Начните QR авторизацию"
echo "3. Проверьте логи: docker compose logs -f api telethon-ingest | grep -E 'telegram_user_id|ownership|QR'"

