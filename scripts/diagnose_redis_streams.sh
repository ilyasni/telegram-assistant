#!/usr/bin/env bash
# Context7 best practice: кроссплатформенная диагностика Redis Streams
set -euo pipefail

STREAM="stream:posts:parsed"
GROUP="tagging_workers"

echo "# Stream info"
docker compose exec -T redis redis-cli XINFO STREAM "$STREAM" || { echo "Stream not found"; exit 0; }

echo ""
echo "# Length:"
docker compose exec -T redis redis-cli XLEN "$STREAM"

echo ""
echo "# Groups:"
docker compose exec -T redis redis-cli XINFO GROUPS "$STREAM" || echo "No groups"

echo ""
echo "# Pending (group=$GROUP):"
docker compose exec -T redis redis-cli XPENDING "$STREAM" "$GROUP" || echo "No pending"

# 24h boundary (GNU date vs BSD date compatibility)
if date -v -24H +"%s" >/dev/null 2>&1; then
  # macOS/BSD
  START_MS=$(date -v -24H +"%s")000
else
  # GNU Linux
  START_MS=$(date -d '24 hours ago' +%s)000
fi
START_ID="${START_MS}-0"

echo ""
echo "24h boundary ID: $START_ID"
echo ""
echo "# Sample from last 24h:"
docker compose exec -T redis redis-cli XRANGE "$STREAM" "$START_ID" + COUNT 10 || true

echo ""
echo "# First entry timestamp:"
FIRST=$(docker compose exec -T redis redis-cli XRANGE "$STREAM" - + COUNT 1 | head -2 | tail -1)
if [[ -n "$FIRST" && "$FIRST" =~ ^[0-9]+-[0-9]+$ ]]; then
  FIRST_MS=$(echo "$FIRST" | cut -d'-' -f1)
  FIRST_DATE=$(date -d "@$((FIRST_MS / 1000))" 2>/dev/null || date -r "$((FIRST_MS / 1000))" 2>/dev/null || echo "unknown")
  echo "First entry: $FIRST (timestamp: $FIRST_DATE)"
fi

echo ""
echo "# Redis version:"
docker compose exec -T redis redis-cli INFO SERVER | grep redis_version | head -1
