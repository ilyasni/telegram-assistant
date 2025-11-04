#!/bin/bash
# Скрипт для запуска ingestion и проверки пайплайна альбомов

set -e

echo "🚀 Запуск ingestion для тестирования пайплайна альбомов"
echo "============================================================"

# Проверка каналов
echo ""
echo "1️⃣ Проверка активных каналов..."
CHANNEL_INFO=$(docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -t -A -c "
SELECT username || '|' || id 
FROM channels 
WHERE is_active = true 
LIMIT 1;
" 2>&1 | grep -v WARNING | grep -v "^DETAIL:" | grep -v "^HINT:" | grep "|" | head -1)

if [ -z "$CHANNEL_INFO" ] || [ -z "$(echo "$CHANNEL_INFO" | grep '|')" ]; then
    echo "   ❌ Нет активных каналов"
    exit 1
fi

USERNAME=$(echo "$CHANNEL_INFO" | cut -d'|' -f1 | xargs)
CHANNEL_ID=$(echo "$CHANNEL_INFO" | cut -d'|' -f2 | xargs)

echo "   ✅ Найден канал: @$USERNAME (ID: $CHANNEL_ID)"

# Проверка текущего состояния
echo ""
echo "2️⃣ Проверка текущего состояния пайплайна..."

ALBUMS_COUNT=$(docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -t -A -c "
SELECT COUNT(*) FROM media_groups;
" 2>&1 | grep -v WARNING | grep -v "^DETAIL:" | grep -v "^HINT:" | grep -E "^[0-9]+" | xargs)

echo "   Альбомов в БД: $ALBUMS_COUNT"

ALBUMS_PARSED=$(docker exec telegram-assistant-redis-1 redis-cli XLEN stream:albums:parsed 2>&1)
echo "   Событий albums.parsed: $ALBUMS_PARSED"

ALBUMS_ASSEMBLED=$(docker exec telegram-assistant-redis-1 redis-cli XLEN stream:album:assembled 2>&1)
echo "   Событий album.assembled: $ALBUMS_ASSEMBLED"

# Проверка метрик worker
echo ""
echo "3️⃣ Проверка метрик worker..."
METRICS=$(docker exec telegram-assistant-worker-1 curl -s http://localhost:8001/metrics 2>/dev/null | grep -E "^albums_parsed_total|^albums_assembled_total" | head -5 || echo "")
if [ -n "$METRICS" ]; then
    echo "   Метрики:"
    echo "$METRICS" | sed 's/^/     /'
else
    echo "   ⚠️  Метрики не найдены или worker недоступен"
fi

# Запуск парсинга
echo ""
echo "4️⃣ Запуск парсинга канала @$USERNAME..."
echo "   (Это может занять несколько минут)"

docker exec telegram-assistant-telethon-ingest-1 python -m scripts.manual_parse_channel --username "$USERNAME" --mode incremental 2>&1 | tee /tmp/ingestion_test.log

echo ""
echo "5️⃣ Проверка результатов..."

# Проверка новых альбомов
NEW_ALBUMS_COUNT=$(docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -t -A -c "
SELECT COUNT(*) FROM media_groups;
" 2>&1 | grep -v WARNING | grep -v "^DETAIL:" | grep -v "^HINT:" | grep -E "^[0-9]+" | xargs)

echo "   Альбомов в БД (после парсинга): $NEW_ALBUMS_COUNT"

if [ "$NEW_ALBUMS_COUNT" -gt "$ALBUMS_COUNT" ]; then
    NEW_ALBUMS=$((NEW_ALBUMS_COUNT - ALBUMS_COUNT))
    echo "   ✅ Новых альбомов: $NEW_ALBUMS"
else
    echo "   ⚠️  Новых альбомов не добавлено"
fi

# Проверка событий
NEW_ALBUMS_PARSED=$(docker exec telegram-assistant-redis-1 redis-cli XLEN stream:albums:parsed 2>&1)
echo "   Событий albums.parsed (после парсинга): $NEW_ALBUMS_PARSED"

# Ждём обработки album_assembler_task (до 30 секунд)
echo ""
echo "6️⃣ Ожидание обработки album_assembler_task (до 30 секунд)..."
for i in {1..30}; do
    NEW_ALBUMS_ASSEMBLED=$(docker exec telegram-assistant-redis-1 redis-cli XLEN stream:album:assembled 2>&1)
    if [ "$NEW_ALBUMS_ASSEMBLED" -gt "$ALBUMS_ASSEMBLED" ]; then
        echo "   ✅ Альбом собран! Событий album.assembled: $NEW_ALBUMS_ASSEMBLED"
        break
    fi
    sleep 1
    if [ $((i % 5)) -eq 0 ]; then
        echo "   ... ожидание ($i/30 сек)"
    fi
done

# Финальная проверка
echo ""
echo "7️⃣ Финальная проверка..."
echo ""
echo "Последние альбомы в БД:"
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    id,
    grouped_id,
    items_count,
    LEFT(caption_text, 50) as caption_preview,
    CASE 
        WHEN meta->>'enrichment' IS NOT NULL THEN '✅' 
        ELSE '❌' 
    END as has_enrichment,
    created_at
FROM media_groups 
ORDER BY created_at DESC 
LIMIT 5;
" 2>&1 | grep -v WARNING

echo ""
echo "============================================================"
echo "✅ Тестирование завершено"
echo ""
echo "📊 Логи парсинга сохранены в: /tmp/ingestion_test.log"
echo "📚 Подробные команды проверки: scripts/check_album_pipeline_real_data.md"

