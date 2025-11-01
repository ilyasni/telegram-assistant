#!/bin/bash
# Context7 best practice: Мониторинг пайплайна Telegram Assistant
# Проверка метрик, потоков и состояния системы

set -euo pipefail

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
echo "======================================================================"
echo "Pipeline Health Check - $TIMESTAMP"
echo "======================================================================"

# 1. Redis Streams health
echo ""
echo "📊 Redis Streams Status:"
echo "------------------------"
docker exec telegram-assistant-redis-1 redis-cli XINFO STREAM "stream:posts:parsed" 2>&1 | grep -E "length|groups" || true
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS "stream:posts:parsed" 2>&1 | grep -A 5 "^name:" || true

# 2. Container health
echo ""
echo "🏥 Container Health:"
echo "--------------------"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "(NAME|redis|worker|telethon|supabase)" || true

# 3. Database metrics
echo ""
echo "💾 Database Metrics:"
echo "--------------------"
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total_posts,
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as last_hour,
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '30 minutes') as last_30min,
    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '10 minutes') as last_10min,
    MAX(created_at) as newest_post,
    NOW() - MAX(created_at) as age
FROM posts;
" 2>&1 | grep -v WARNING || true

# 4. Enrichment status
echo ""
echo "🔍 Enrichment Status:"
echo "---------------------"
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    kind,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE status = 'ok') as ok,
    COUNT(*) FILTER (WHERE status = 'error') as errors
FROM post_enrichment
GROUP BY kind;
" 2>&1 | grep -v WARNING || true

# 5. Recent logs - errors
echo ""
echo "⚠️  Recent Errors (last 30 min):"
echo "----------------------------------"
docker logs telegram-assistant-telethon-ingest-1 --since 30m 2>&1 | grep -i "error\|failed\|exception" | grep -v DEBUG | head -20 || true

# 6. Worker activity
echo ""
echo "⚙️  Worker Activity:"
echo "-------------------"
docker logs telegram-assistant-worker-1 --since 30m 2>&1 | grep -E "Post tagged successfully|enrichment|indexing" | tail -10 || true

# 7. Pending messages
echo ""
echo "📬 Pending Messages:"
echo "-------------------"
docker exec telegram-assistant-redis-1 redis-cli XPENDING "stream:posts:parsed" "tagging_workers" 2>&1 | head -5 || true

echo ""
echo "======================================================================"

