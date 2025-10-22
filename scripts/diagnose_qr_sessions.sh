#!/bin/bash
# scripts/diagnose_qr_sessions.sh

echo "=== QR Sessions Diagnostic ==="

# 1. Проверка Redis
echo -e "\n[1/4] Redis connectivity..."
docker exec telegram-assistant-redis-1 redis-cli ping || exit 1

# 2. Активные QR-сессии
echo -e "\n[2/4] Active QR sessions..."
docker exec telegram-assistant-redis-1 redis-cli --scan --pattern "tg:qr:session:*" | while read key; do
    echo "  Key: $key"
    docker exec telegram-assistant-redis-1 redis-cli HGETALL "$key"
done

# 3. Логи telethon-ingest (последние ошибки QR)
echo -e "\n[3/4] Recent QR errors..."
docker logs telegram-assistant-telethon-ingest-1 2>&1 | grep -i "qr" | grep -i "error" | tail -10

# 4. Health check
echo -e "\n[4/4] Health status..."
curl -s http://localhost:8010/health/auth | jq .
