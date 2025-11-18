#!/bin/bash
# Скрипт для мониторинга активности Trend Agents
# Использование: ./scripts/monitor_trend_activity.sh [интервал_в_секундах]

INTERVAL=${1:-60}  # По умолчанию 60 секунд

echo "🔍 Мониторинг активности Trend Agents (интервал: ${INTERVAL}с)"
echo "Нажмите Ctrl+C для остановки"
echo ""

# Функция для получения текущего времени
get_timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

# Функция для получения метрик
get_metrics() {
    docker compose exec -T worker curl -s http://localhost:8001/metrics 2>/dev/null | grep -E "trend_events_processed_total|trend_emerging_events_total" | head -10
}

# Функция для получения количества событий в стримах
get_stream_lengths() {
    echo "📊 События в Redis Streams:"
    echo "  - posts.indexed: $(docker compose exec -T redis redis-cli XLEN stream:posts:indexed 2>/dev/null || echo 'N/A')"
    echo "  - trends.emerging: $(docker compose exec -T redis redis-cli XLEN stream:trends:emerging 2>/dev/null || echo 'N/A')"
}

# Функция для получения последних трендов
get_recent_trends() {
    echo "📈 Последние тренды в БД:"
    docker compose exec -T supabase-db psql -U postgres -d postgres -c "
        SELECT 
            COUNT(*) as total_clusters,
            COUNT(*) FILTER (WHERE status = 'active') as active_clusters,
            COUNT(*) FILTER (WHERE last_activity_at >= NOW() - INTERVAL '1 hour') as trends_last_hour,
            MAX(last_activity_at) as last_activity
        FROM trend_clusters;
    " 2>/dev/null | grep -v "^-" | grep -v "row" | grep -v "^$" | head -2
}

# Инициализация переменных для отслеживания изменений
LAST_PROCESSED=0
LAST_EMERGING=0
LAST_STREAM_LENGTH=0

while true; do
    clear
    echo "═══════════════════════════════════════════════════════════════"
    echo "🕐 Время: $(get_timestamp)"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    
    # Получаем метрики
    echo "📊 Метрики Prometheus:"
    METRICS=$(get_metrics)
    echo "$METRICS" | while IFS= read -r line; do
        if [[ $line =~ trend_events_processed_total.*status=\"processed\" ]]; then
            CURRENT=$(echo "$line" | grep -oP '\d+\.\d+' | head -1)
            if [ ! -z "$CURRENT" ] && [ "$CURRENT" != "$LAST_PROCESSED" ]; then
                DIFF=$(echo "$CURRENT - $LAST_PROCESSED" | bc 2>/dev/null || echo "0")
                if [ "$DIFF" != "0" ]; then
                    echo "  ✅ $line (новых: +$DIFF)"
                else
                    echo "  $line"
                fi
                LAST_PROCESSED=$CURRENT
            else
                echo "  $line"
            fi
        elif [[ $line =~ trend_emerging_events_total.*status=\"published\" ]]; then
            CURRENT=$(echo "$line" | grep -oP '\d+\.\d+' | head -1)
            if [ ! -z "$CURRENT" ] && [ "$CURRENT" != "$LAST_EMERGING" ]; then
                DIFF=$(echo "$CURRENT - $LAST_EMERGING" | bc 2>/dev/null || echo "0")
                if [ "$DIFF" != "0" ]; then
                    echo "  🎯 $line (новых: +$DIFF)"
                else
                    echo "  $line"
                fi
                LAST_EMERGING=$CURRENT
            else
                echo "  $line"
            fi
        else
            echo "  $line"
        fi
    done
    
    echo ""
    get_stream_lengths
    echo ""
    get_recent_trends
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "⏳ Ожидание новых событий... (обновление каждые ${INTERVAL}с)"
    echo "   Нажмите Ctrl+C для остановки"
    
    sleep $INTERVAL
done

