#!/usr/bin/env bash
# Context7 best practice: Снимок состояния Redis перед очисткой
set -euo pipefail

STREAM="stream:posts:parsed"
OUTPUT="redis_state_snapshot_$(date +%Y%m%d_%H%M%S).txt"

echo "=== Redis State Snapshot $(date) ===" > "$OUTPUT"
echo "Stream: $STREAM" >> "$OUTPUT"
echo "" >> "$OUTPUT"

echo "=== XINFO STREAM ===" >> "$OUTPUT"
docker compose exec -T redis redis-cli XINFO STREAM "$STREAM" >> "$OUTPUT" 2>&1 || echo "Stream not found" >> "$OUTPUT"

echo "" >> "$OUTPUT"
echo "=== XLEN ===" >> "$OUTPUT"
docker compose exec -T redis redis-cli XLEN "$STREAM" >> "$OUTPUT" 2>&1 || echo "0" >> "$OUTPUT"

echo "" >> "$OUTPUT"
echo "=== XINFO GROUPS ===" >> "$OUTPUT"
docker compose exec -T redis redis-cli XINFO GROUPS "$STREAM" >> "$OUTPUT" 2>&1 || echo "No groups" >> "$OUTPUT"

echo "" >> "$OUTPUT"
echo "=== XPENDING enrichment_workers ===" >> "$OUTPUT"
docker compose exec -T redis redis-cli XPENDING "$STREAM" enrichment_workers >> "$OUTPUT" 2>&1 || echo "No pending" >> "$OUTPUT"

echo "" >> "$OUTPUT"
echo "=== First 5 entries ===" >> "$OUTPUT"
docker compose exec -T redis redis-cli XRANGE "$STREAM" - + COUNT 5 >> "$OUTPUT" 2>&1 || echo "No entries" >> "$OUTPUT"

echo "Snapshot saved to: $OUTPUT"
echo "Content preview:"
head -20 "$OUTPUT"
