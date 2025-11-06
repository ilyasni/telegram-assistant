#!/bin/bash
# Скрипт для проверки исправлений ownership_mismatch

echo "=== Проверка исправлений ownership_mismatch ==="
echo ""

echo "1. Проверка API: извлечение telegram_user_id из initData"
echo "   Ожидается: логи 'Processing init_data', 'Extracted telegram_user_id'"
docker compose logs --tail=50 api 2>&1 | grep -E "Processing init_data|Extracted telegram_user_id|WITHOUT telegram_user_id" | tail -5
echo ""

echo "2. Проверка API: сохранение telegram_user_id в Redis"
echo "   Ожидается: логи 'Creating QR session with telegram_user_id', 'saved_telegram_user_id'"
docker compose logs --tail=50 api 2>&1 | grep -E "Creating QR session.*telegram_user_id|saved_telegram_user_id|match=" | tail -5
echo ""

echo "3. Проверка Redis: наличие telegram_user_id в сессии"
KEYS=$(docker compose exec redis redis-cli --scan --pattern "t:*:qr:session" 2>/dev/null || echo "")
if [ -z "$KEYS" ]; then
  echo "   ⚠ Нет активных сессий (ожидается новая сессия после запроса)"
else
  echo "   Найдены сессии:"
  echo "$KEYS" | while read key; do
    TG_ID=$(docker compose exec redis redis-cli HGET "$key" "telegram_user_id" 2>/dev/null)
    TENANT_ID=$(docker compose exec redis redis-cli HGET "$key" "tenant_id" 2>/dev/null)
    STATUS=$(docker compose exec redis redis-cli HGET "$key" "status" 2>/dev/null)
    echo "   Key: $key"
    echo "     tenant_id: $TENANT_ID"
    echo "     telegram_user_id: ${TG_ID:-'<не найден>'} ⚠️"
    echo "     status: $STATUS"
    if [ -z "$TG_ID" ]; then
      echo "     ❌ ПРОБЛЕМА: telegram_user_id не сохранен!"
    else
      echo "     ✓ telegram_user_id сохранен"
    fi
  done
fi
echo ""

echo "4. Проверка telethon-ingest: использование telegram_user_id для проверки"
echo "   Ожидается: логи 'Checking expected_telegram_id', 'Found telegram_user_id in Redis'"
docker compose logs --tail=50 telethon-ingest 2>&1 | grep -E "Checking expected_telegram_id|Found telegram_user_id|Using tenant_id as telegram_user_id fallback" | tail -5
echo ""

echo "5. Проверка ошибок ownership_mismatch"
MISMATCH=$(docker compose logs --since 10m telethon-ingest 2>&1 | grep -c "Ownership mismatch" || echo "0")
if [ "$MISMATCH" -gt 0 ]; then
  echo "   ❌ Найдено ошибок ownership_mismatch: $MISMATCH"
  docker compose logs --since 10m telethon-ingest 2>&1 | grep -A 3 "Ownership mismatch" | tail -10
else
  echo "   ✓ Ошибок ownership_mismatch не найдено за последние 10 минут"
fi
echo ""

echo "=== Рекомендации ==="
echo "1. Откройте MiniApp и начните QR авторизацию"
echo "2. Проверьте логи: docker compose logs -f api telethon-ingest | grep -E 'telegram_user_id|ownership|QR'"
echo "3. Проверьте Redis: docker compose exec redis redis-cli HGETALL 't:{tenant_id}:qr:session'"
echo ""

