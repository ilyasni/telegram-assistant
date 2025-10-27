#!/bin/bash

# Скрипт для генерации событий posts.parsed для существующих постов
# Запускает пайплайн тегирования и обогащения

echo "🚀 Starting posts.parsed events generation..."

# Получаем необработанные посты из БД
while true; do
  echo "📊 Fetching unprocessed posts..."
  POSTS_DATA=$(docker compose exec -T supabase-db psql -U postgres -d postgres -t -c "
  SELECT 
    trim(p.id::text) || '|' || 
    trim(p.channel_id::text) || '|' || 
    trim(p.telegram_message_id::text) || '|' || 
    trim(COALESCE(p.content, '')) || '|' || 
    trim(COALESCE(p.media_urls::text, '[]')) || '|' || 
    trim(p.posted_at::text) || '|' || 
    trim(COALESCE(p.telegram_post_url, '')) || '|' || 
    trim(p.has_media::text) || '|' || 
    trim(p.is_edited::text) || '|' || 
    trim(COALESCE(c.tg_channel_id::text, '0')) || '|' || 
    'default-tenant'
  FROM posts p
  JOIN channels c ON p.channel_id = c.id
  WHERE p.is_processed = false
  ORDER BY p.created_at ASC
  LIMIT 50
  ")

  if [ -z "$POSTS_DATA" ]; then
      echo "✅ No unprocessed posts found"
      break
  fi

  echo "Found posts to process:"
  echo "$POSTS_DATA" | wc -l

  # Обрабатываем каждый пост
  echo "$POSTS_DATA" | while IFS='|' read -r post_id channel_id telegram_message_id content media_urls posted_at telegram_post_url has_media is_edited telegram_channel_id tenant_id; do
    # Пропускаем пустые строки
    if [ -z "$post_id" ]; then
      continue
    fi
    # Удаляем лишние пробелы
    post_id=$(echo "$post_id" | xargs)
    channel_id=$(echo "$channel_id" | xargs)
    telegram_message_id=$(echo "$telegram_message_id" | xargs)
    
    if [ -z "$post_id" ]; then
        continue
    fi
    
    echo "🔄 Processing post $post_id..."
    
    # Обрабатываем пустые значения
    if [ -z "$tenant_id" ]; then tenant_id="default-tenant"; fi
    if [ -z "$media_urls" ]; then media_urls="[]"; fi
    if [ -z "$posted_at" ]; then posted_at=""; fi
    if [ -z "$telegram_post_url" ]; then telegram_post_url=""; fi
    if [ -z "$has_media" ]; then has_media="false"; fi
    if [ -z "$is_edited" ]; then is_edited="false"; fi
    if [ -z "$telegram_channel_id" ]; then telegram_channel_id="0"; fi
    
    # Создаем событие posts.parsed
    EVENT_DATA=$(cat <<EOF
{
  "idempotency_key": "${post_id}:$(echo -n "$content" | sha256sum | cut -d' ' -f1)",
  "user_id": "$tenant_id",
  "channel_id": "$channel_id", 
  "post_id": "$post_id",
  "tenant_id": "$tenant_id",
  "text": "$content",
  "urls": $media_urls,
  "posted_at": "$posted_at",
  "content_hash": "$(echo -n "$content" | sha256sum | cut -d' ' -f1)",
  "link_count": $(echo "$media_urls" | jq '. | length'),
  "tg_message_id": $telegram_message_id,
  "telegram_message_id": $telegram_message_id,
  "tg_channel_id": $telegram_channel_id,
  "telegram_post_url": "$telegram_post_url",
  "has_media": $has_media,
  "is_edited": $is_edited,
  "schema_version": "v1",
  "trace_id": "$(openssl rand -hex 16)",
  "occurred_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
)
    
    # Публикуем событие в Redis Stream
    docker compose exec -T redis redis-cli XADD stream:posts:parsed '*' data "$EVENT_DATA" > /dev/null
    
    if [ $? -eq 0 ]; then
        echo "✅ Published event for post $post_id"
        # Отмечаем пост как опубликованный, чтобы не зациклиться на выборке
        docker compose exec -T supabase-db psql -U postgres -d postgres -c "UPDATE posts SET is_processed = true WHERE id = '$post_id';" >/dev/null 2>&1 || true
    else
        echo "❌ Failed to publish event for post $post_id"
    fi
  done
  # Небольшая пауза, чтобы воркер обработал пачку
  sleep 2
done

echo "🎯 Events published! Worker should now start processing..."
