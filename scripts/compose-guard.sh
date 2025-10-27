#!/usr/bin/env bash
set -euo pipefail
# [C7-ID: ENV-SEC-003] Не трогать базовый compose в дев — используем override

docker compose config -q

required=(supabase-db kong rest auth storage redis qdrant api worker telethon-ingest)
for s in "${required[@]}"; do
  docker compose config --services | grep -qx "$s" || { echo "ERROR: отсутствует сервис $s"; exit 1; }
done

echo "compose OK"
