#!/bin/bash
# Диагностика критического алерта ParsingNoActivity
# Context7: Комплексная проверка состояния парсинга

set -e

echo "🔍 Диагностика ParsingNoActivity"
echo "=================================="
echo ""

# 1. Проверка логов telethon-ingest
echo "1️⃣ Проверка логов telethon-ingest (последние 100 строк)..."
echo "---"
docker logs telegram-assistant-telethon-ingest-1 --tail 100 2>&1 | grep -E "(parse|scheduler|TelegramClientManager|no_client|error|ERROR)" | tail -20 || echo "   ⚠️  Нет релевантных логов"
echo ""

# 2. Проверка состояния scheduler
echo "2️⃣ Проверка состояния scheduler..."
echo "---"
# Проверка lock
LOCK=$(docker exec telegram-assistant-redis-1 redis-cli GET "parse_all_channels:lock" 2>&1 || echo "none")
if [ "$LOCK" != "none" ] && [ -n "$LOCK" ]; then
    echo "   ✅ Lock активен: $LOCK"
    TTL=$(docker exec telegram-assistant-redis-1 redis-cli TTL "parse_all_channels:lock" 2>&1 || echo "0")
    echo "   TTL: ${TTL} секунд"
else
    echo "   ❌ Lock не найден или истек"
fi
echo ""

# 3. Проверка метрик Prometheus
echo "3️⃣ Проверка метрик Prometheus..."
echo "---"
METRICS_URL="http://localhost:8001/metrics"
if curl -s "$METRICS_URL" > /dev/null 2>&1; then
    echo "   ✅ Метрики доступны"
    # Проверка parser_runs_total
    PARSER_RUNS=$(curl -s "$METRICS_URL" | grep "^parser_runs_total" | head -5 || echo "")
    if [ -n "$PARSER_RUNS" ]; then
        echo "   parser_runs_total:"
        echo "$PARSER_RUNS" | sed 's/^/      /'
    else
        echo "   ⚠️  parser_runs_total не найден"
    fi
    
    # Проверка posts_parsed_total
    POSTS_PARSED=$(curl -s "$METRICS_URL" | grep "^posts_parsed_total" | head -5 || echo "")
    if [ -n "$POSTS_PARSED" ]; then
        echo "   posts_parsed_total:"
        echo "$POSTS_PARSED" | sed 's/^/      /'
    else
        echo "   ⚠️  posts_parsed_total не найден"
    fi
    
    # Проверка scheduler_heartbeat
    HEARTBEAT=$(curl -s "$METRICS_URL" | grep "^scheduler_heartbeat_seconds" || echo "")
    if [ -n "$HEARTBEAT" ]; then
        echo "   scheduler_heartbeat_seconds: $HEARTBEAT"
        # Вычисляем возраст heartbeat
        HB_VALUE=$(echo "$HEARTBEAT" | grep -oP '\d+\.\d+' | head -1)
        if [ -n "$HB_VALUE" ]; then
            NOW=$(date +%s)
            AGE=$((NOW - ${HB_VALUE%.*}))
            echo "   Возраст heartbeat: ${AGE} секунд ($(($AGE / 60)) минут)"
            if [ $AGE -gt 120 ]; then
                echo "   ⚠️  Heartbeat устарел (> 2 минут)"
            fi
        fi
    else
        echo "   ⚠️  scheduler_heartbeat_seconds не найден"
    fi
else
    echo "   ❌ Метрики недоступны ($METRICS_URL)"
fi
echo ""

# 4. Проверка последних парсингов
echo "4️⃣ Проверка последних парсингов (последние 20 записей)..."
echo "---"
docker logs telegram-assistant-telethon-ingest-1 --tail 500 2>&1 | \
    grep -E "(CHANNEL_PARSE_START|CHANNEL_PARSE_END|messages_processed|status)" | \
    tail -20 || echo "   ⚠️  Нет логов парсинга"
echo ""

# 5. Проверка TelegramClientManager
echo "5️⃣ Проверка TelegramClientManager..."
echo "---"
docker logs telegram-assistant-telethon-ingest-1 --tail 500 2>&1 | \
    grep -E "(TelegramClientManager|Client connected|Client not authorized|Failed to connect|no_client)" | \
    tail -10 || echo "   ⚠️  Нет логов TelegramClientManager"
echo ""

# 6. Проверка Redis streams
echo "6️⃣ Проверка Redis streams..."
echo "---"
STREAM_LEN=$(docker exec telegram-assistant-redis-1 redis-cli XLEN "stream:posts:parsed" 2>&1 || echo "0")
echo "   stream:posts:parsed: $STREAM_LEN записей"
if [ "$STREAM_LEN" -gt 0 ]; then
    LAST_ID=$(docker exec telegram-assistant-redis-1 redis-cli XREVRANGE "stream:posts:parsed" + - 1 2>&1 | head -1 || echo "")
    if [ -n "$LAST_ID" ]; then
        echo "   Последний ID: $LAST_ID"
    fi
fi
echo ""

# 7. Проверка статуса контейнера
echo "7️⃣ Проверка статуса контейнера..."
echo "---"
STATUS=$(docker inspect telegram-assistant-telethon-ingest-1 --format='{{.State.Status}}' 2>&1 || echo "unknown")
HEALTH=$(docker inspect telegram-assistant-telethon-ingest-1 --format='{{.State.Health.Status}}' 2>&1 || echo "unknown")
echo "   Статус: $STATUS"
echo "   Health: $HEALTH"
echo ""

# 8. Рекомендации
echo "💡 Рекомендации:"
echo "---"
if [ "$LOCK" = "none" ] || [ -z "$LOCK" ]; then
    echo "   ⚠️  Scheduler lock отсутствует - scheduler может быть не запущен"
fi
if [ -z "$PARSER_RUNS" ]; then
    echo "   ⚠️  Метрики parser_runs_total не найдены - проверьте экспорт метрик"
fi
if [ -z "$POSTS_PARSED" ]; then
    echo "   ⚠️  Метрики posts_parsed_total не найдены - проверьте экспорт метрик"
fi
echo ""

echo "✅ Диагностика завершена"

