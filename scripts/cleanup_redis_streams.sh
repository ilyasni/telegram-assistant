#!/usr/bin/env bash
# Context7 best practice: безопасная очистка старых событий и pending messages
set -euo pipefail

STREAM="stream:posts:parsed"
GROUP="tagging_workers"
CONSUMER="${CONSUMER:-maintenance_worker}"
IDLE_MS="${IDLE_MS:-3600000}" # 1 час для pending

echo "# Step 1: Reclaim and ACK pending messages via XAUTOCLAIM..."
CLAIMED_COUNT=0
while :; do
  # XAUTOCLAIM возвращает массив: [next_id, [msg1, msg2, ...]]
  OUT=$(docker compose exec -T redis redis-cli XAUTOCLAIM "$STREAM" "$GROUP" "$CONSUMER" "$IDLE_MS" 0-0 COUNT 200)
  
  # Простая эвристика: если пусто — выходим
  if [[ "$OUT" == *"(empty"* ]] || [[ "$OUT" == *"(nil)"* ]] || [[ -z "$OUT" ]]; then
    break
  fi
  
  # Извлекаем ID из вывода (формат: 1234567890-0)
  IDS=$(echo "$OUT" | grep -oE '[0-9]{10,}-[0-9]+' || true)
  
  if [[ -n "$IDS" ]]; then
    # ACK всех полученных (если это мусор или уже обработано)
    echo "Acknowledging $(echo "$IDS" | wc -w) messages..."
    docker compose exec -T redis redis-cli XACK "$STREAM" "$GROUP" $IDS >/dev/null
    CLAIMED_COUNT=$((CLAIMED_COUNT + $(echo "$IDS" | wc -w)))
  else
    break
  fi
  
  # Предохранитель от бесконечного цикла
  if [[ $CLAIMED_COUNT -gt 10000 ]]; then
    echo "Warning: Too many pending messages, stopping at $CLAIMED_COUNT"
    break
  fi
done

echo "Total claimed and ACKed: $CLAIMED_COUNT messages"

# Step 2: Trim до 24 часов (для Redis 7.0+)
echo ""
echo "# Step 2: XTRIM by MINID (approx trim to last 24h)..."

if date -v -24H +"%s" >/dev/null 2>&1; then
  START_MS=$(date -v -24H +"%s")000
else
  START_MS=$(date -d '24 hours ago' +%s)000
fi
MIN_ID="${START_MS}-0"

echo "MIN_ID for 24h: $MIN_ID"

# Проверка версии Redis
REDIS_VERSION=$(docker compose exec -T redis redis-cli INFO SERVER | grep redis_version | cut -d: -f2 | tr -d '\r' | cut -d. -f1)

if [[ "$REDIS_VERSION" -ge 7 ]]; then
  TRIMMED=$(docker compose exec -T redis redis-cli XTRIM "$STREAM" MINID ~ "$MIN_ID" 2>&1)
  if [[ "$TRIMMED" =~ ^[0-9]+$ ]]; then
    echo "Trimmed $TRIMMED entries"
  else
    echo "XTRIM error: $TRIMMED"
    echo "Trying alternative approach..."
    # Fallback: count entries after MIN_ID and trim by MAXLEN
    COUNT_AFTER_MINID=$(docker compose exec -T redis redis-cli XRANGE "$STREAM" "$MIN_ID" + | grep -c '^[0-9]' || echo 0)
    if [[ "$COUNT_AFTER_MINID" -gt 0 ]]; then
      docker compose exec -T redis redis-cli XTRIM "$STREAM" MAXLEN ~ "$COUNT_AFTER_MINID"
    fi
  fi
else
  echo "Warning: Redis < 7.0, MINID not supported. Use MAXLEN as fallback:"
  # Узнаём текущую длину после MINID
  CURRENT_LEN=$(docker compose exec -T redis redis-cli XLEN "$STREAM")
  COUNT_AFTER_MINID=$(docker compose exec -T redis redis-cli XRANGE "$STREAM" "$MIN_ID" + | grep -c '^[0-9]' || echo 0)
  
  if [[ "$COUNT_AFTER_MINID" -gt 0 ]]; then
    docker compose exec -T redis redis-cli XTRIM "$STREAM" MAXLEN ~ "$COUNT_AFTER_MINID"
  fi
fi

echo ""
echo "# Resulting length:"
docker compose exec -T redis redis-cli XLEN "$STREAM"

echo ""
echo "# Remaining pending:"
docker compose exec -T redis redis-cli XPENDING "$STREAM" "$GROUP" | head -5 || true
