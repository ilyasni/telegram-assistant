#!/bin/bash
set -euo pipefail

echo "🚀 Retagging all posts..."

# Сначала получаем список post_id для ретега
IDS=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -A -c "
  SELECT p.id::text
  FROM posts p
  LEFT JOIN post_enrichment e ON e.post_id = p.id
  WHERE char_length(COALESCE(p.content,'')) >= 20
    AND (
      e.post_id IS NULL
      OR e.tags = '[]'::jsonb
      OR e.tags::text = '["финансоваяаналитика", "инвестиции", "экономика"]'
    )
  ORDER BY p.created_at ASC
")

if [ -z "${IDS}" ]; then
  echo "✅ Nothing to retag"
  exit 0
fi

PUSHED_TOTAL=0
BATCH_SIZE=${BATCH_SIZE:-100}
COUNT=0

echo "Found candidates: $(echo "${IDS}" | wc -l)"

echo "Pushing events..."
echo "---"
echo "(batch size ${BATCH_SIZE}, concurrency limited by worker/proxy)"

for pid in ${IDS}; do
  [ -z "${pid}" ] && continue
  # Собираем JSON для события строго по post_id
  JSON=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -A -c "
    SELECT json_build_object(
      'post_id', p.id::text,
      'channel_id', p.channel_id::text,
      'content', COALESCE(p.content, ''),
      'urls', COALESCE(p.media_urls, '[]'::jsonb),
      'posted_at', COALESCE(p.posted_at::text, ''),
      'telegram_post_url', COALESCE(p.telegram_post_url, ''),
      'has_media', p.has_media,
      'is_edited', p.is_edited
    )::text
    FROM posts p WHERE p.id='"${pid}"' LIMIT 1
  ")
  [ -z "${JSON}" ] && continue
  docker compose exec -T redis redis-cli XADD stream:posts:parsed '*' data "${JSON}" > /dev/null || true
  COUNT=$((COUNT+1))
  PUSHED_TOTAL=$((PUSHED_TOTAL+1))
  if (( COUNT >= BATCH_SIZE )); then
    echo "Pushed ${PUSHED_TOTAL} so far..."
    COUNT=0
    sleep 1
  fi
done

echo "✅ Done. Pushed total: ${PUSHED_TOTAL}"
echo "🎯 Worker will process them sequentially (1 concurrent request to GigaChat)."

