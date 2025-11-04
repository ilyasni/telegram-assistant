#!/bin/bash
# Финальная проверка исправлений ownership_mismatch

echo "=== Финальная проверка исправлений ==="
echo ""

echo "1. Проверка кода в контейнерах..."
echo "   API:"
docker compose exec api grep -A 2 "QR start request received" /app/routers/tg_auth.py | head -3
echo "   telethon-ingest:"
docker compose exec telethon-ingest grep -A 2 "Checking expected_telegram_id" /app/services/qr_auth.py | head -3
echo ""

echo "2. Очистка старых сессий..."
docker compose exec redis redis-cli --scan --pattern "t:*:qr:session" | while read key; do
  docker compose exec redis redis-cli DEL "$key" > /dev/null 2>&1
done
echo "   ✓ Очищено"
echo ""

echo "3. Мониторинг новых запросов (10 секунд)..."
echo "   Сейчас откройте MiniApp и начните QR авторизацию"
echo ""
timeout 10 docker compose logs -f api telethon-ingest 2>&1 | grep -E "QR start request|Processing init_data|Extracted telegram_user_id|Creating QR session.*telegram_user_id|Checking expected_telegram_id|Found telegram_user_id|Ownership mismatch" || true
echo ""

echo "4. Проверка результата в Redis..."
KEYS=$(docker compose exec redis redis-cli --scan --pattern "t:*:qr:session" 2>/dev/null || echo "")
if [ -z "$KEYS" ]; then
  echo "   ⚠ Нет активных сессий"
else
  echo "$KEYS" | while read key; do
    echo "   === $key ==="
    TG_ID=$(docker compose exec redis redis-cli HGET "$key" "telegram_user_id" 2>/dev/null)
    TENANT_ID=$(docker compose exec redis redis-cli HGET "$key" "tenant_id" 2>/dev/null)
    STATUS=$(docker compose exec redis redis-cli HGET "$key" "status" 2>/dev/null)
    echo "     tenant_id: $TENANT_ID"
    echo "     telegram_user_id: ${TG_ID:-'<НЕ НАЙДЕН - ПРОБЛЕМА!>'} ⚠️"
    echo "     status: $STATUS"
    if [ -z "$TG_ID" ]; then
      echo "     ❌ КРИТИЧЕСКАЯ ПРОБЛЕМА: telegram_user_id не сохранен!"
      echo "     Проверьте логи API: docker compose logs api | grep -E 'init_data|telegram_user_id'"
    else
      echo "     ✓ telegram_user_id сохранен: $TG_ID"
    fi
  done
fi
echo ""

echo "=== Проверка завершена ==="
echo ""
echo "Для подробной диагностики:"
echo "  docker compose logs --tail=100 api | grep -E 'QR start request|init_data|telegram_user_id'"
echo "  docker compose logs --tail=100 telethon-ingest | grep -E 'Checking expected|Found telegram_user_id|Ownership'"

