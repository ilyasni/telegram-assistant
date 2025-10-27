#!/usr/bin/env bash
set -euo pipefail
# 1) ping ядра
redis-cli -u "${REDIS_URL}" PING | grep -q PONG
curl -sk "${QDRANT_URL}/readyz" >/dev/null
# 2) supabase внешка
for p in rest auth storage; do
  code=$(curl -sk -o /dev/null -w "%{http_code}" "https://${SUPABASE_HOST}/${p}/v1/")
  [[ "$code" =~ ^(200|401|404)$ ]] || { echo "BAD ${p}: $code"; exit 1; }
done
# 3) вставка фейкового поста + outbox
docker compose exec -T supabase-db psql -U postgres -d "$POSTGRES_DB" -c \
"INSERT INTO posts(id, tenant_id, channel_native_id, message_id, text, posted_at)  VALUES (gen_random_uuid(), default-tenant,@test, 1, hello, now())  ON CONFLICT DO NOTHING;"
echo "SMOKE OK"
