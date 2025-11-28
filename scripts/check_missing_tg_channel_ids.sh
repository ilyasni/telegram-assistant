#!/bin/bash
# Проверка каналов без tg_channel_id
# Использование: ./check_missing_tg_channel_ids.sh

set -e

echo "🔍 Проверка каналов без tg_channel_id..."

# Получаем количество каналов без tg_channel_id
COUNT=$(docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -t -A -c "
SELECT COUNT(*) 
FROM channels 
WHERE tg_channel_id IS NULL 
  AND is_active = true;
" 2>/dev/null || echo "0")

if [ -z "$COUNT" ] || [ "$COUNT" = "0" ]; then
    echo "✅ Все активные каналы имеют tg_channel_id"
    exit 0
fi

echo ""
echo "⚠️  Найдено $COUNT активных каналов без tg_channel_id"
echo ""
echo "Детальная информация:"
echo ""

# Показываем детальную информацию
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    id, 
    username, 
    title, 
    created_at,
    (SELECT COUNT(*) FROM user_channel WHERE channel_id = channels.id AND is_active = true) as subscribers
FROM channels 
WHERE tg_channel_id IS NULL 
  AND is_active = true
ORDER BY created_at DESC
LIMIT 20;
"

echo ""
echo "💡 Рекомендации:"
echo "  1. Заполнить tg_channel_id через скрипт:"
echo "     python telethon-ingest/scripts/fetch_tg_channel_ids.py"
echo ""
echo "  2. Или вручную для конкретных каналов:"
echo "     ./scripts/update_beer_channels_manual.sh <id1> <id2> <id3>"
echo ""
echo "  3. Проверить наличие сессий в Redis:"
echo "     docker exec telegram-assistant-redis-1 redis-cli KEYS '*session*'"

exit 1

