#!/bin/bash
# Скрипт для проверки статуса синхронизации тем
# Context7: Мониторинг прогресса синхронизации всех 369 тем

echo "=== Sync Status Check ==="
echo ""

# Проверка количества тем в БД
echo "Themes in database:"
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total_themes,
    SUM(channels_count) as total_channels,
    MAX(indexed_at) as last_sync
FROM themes 
WHERE slug != 'test-theme';
" 2>&1 | grep -A 2 "total_themes"

echo ""
echo "Recent synced themes (last 10):"
docker compose exec -T supabase-db psql -U postgres -d postgres -c "
SELECT slug, name, channels_count, indexed_at 
FROM themes 
WHERE slug != 'test-theme' 
ORDER BY indexed_at DESC 
LIMIT 10;
" 2>&1 | grep -A 11 "slug"

echo ""
echo "Sync processes:"
docker compose exec tgstat-parser ps aux | grep -E "python3.*sync|sync_all_themes" | grep -v grep || echo "No sync processes running"

echo ""
echo "Recent sync logs:"
docker compose logs --tail=50 tgstat-parser 2>&1 | grep -E "(Sync progress|Theme saved|Starting sync|themes_count)" | tail -10 || echo "No recent sync logs"
