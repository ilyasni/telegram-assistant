#!/bin/bash
# Простой скрипт для очистки старых DLQ сообщений
# Использует Redis CLI для безопасной очистки

set -euo pipefail

DRY_RUN="${DRY_RUN:-true}"
MIN_AGE_DAYS="${MIN_AGE_DAYS:-60}"

# Вычисляем timestamp 60 дней назад в миллисекундах
if date -v -${MIN_AGE_DAYS}d +"%s" >/dev/null 2>&1; then
    # macOS
    CUTOFF_TS=$(($(date -v -${MIN_AGE_DAYS}d +"%s") * 1000))
else
    # Linux
    CUTOFF_TS=$(($(date -d "${MIN_AGE_DAYS} days ago" +%s) * 1000))
fi

DLQ_STREAMS=(
    "stream:posts:parsed:dlq"
    "stream:posts:tagged:dlq"
    "stream:posts:enriched:dlq"
    "stream:posts:indexed:dlq"
    "stream:posts:crawl:dlq"
    "stream:posts:deleted:dlq"
    "stream:posts:vision:analyzed:dlq"
    "stream:albums:parsed:dlq"
    "stream:album:assembled:dlq"
)

echo "=========================================="
echo "ОЧИСТКА СТАРЫХ DLQ СООБЩЕНИЙ"
echo "=========================================="
echo "Режим: ${DRY_RUN}"
echo "Минимальный возраст: ${MIN_AGE_DAYS} дней"
echo "Cutoff timestamp: ${CUTOFF_TS}"
echo "=========================================="
echo ""

TOTAL_DELETED=0

for STREAM in "${DLQ_STREAMS[@]}"; do
    LEN=$(docker compose exec -T redis redis-cli XLEN "$STREAM" 2>/dev/null || echo "0")
    
    if [ "$LEN" = "0" ] || [ -z "$LEN" ]; then
        echo "📊 $STREAM: пусто"
        continue
    fi
    
    echo "📊 $STREAM: $LEN сообщений"
    
    if [ "${DRY_RUN}" = "true" ]; then
        echo "  🔍 DRY-RUN: Будет проверено сообщений (удаление отключено)"
        # В DRY-RUN просто показываем длину
        continue
    fi
    
    # Для реального удаления используем XTRIM с MINID
    # Удаляем все сообщения старше cutoff_timestamp
    echo "  🗑️  Удаление старых сообщений..."
    
    # Используем XTRIM MINID для удаления старых сообщений
    DELETED=$(docker compose exec -T redis redis-cli XTRIM "$STREAM" MINID "$CUTOFF_TS" 2>/dev/null || echo "0")
    
    NEW_LEN=$(docker compose exec -T redis redis-cli XLEN "$STREAM" 2>/dev/null || echo "0")
    ACTUAL_DELETED=$((LEN - NEW_LEN))
    
    if [ "$ACTUAL_DELETED" -gt 0 ]; then
        echo "  ✅ Удалено: $ACTUAL_DELETED (было: $LEN, стало: $NEW_LEN)"
        TOTAL_DELETED=$((TOTAL_DELETED + ACTUAL_DELETED))
    else
        echo "  ℹ️  Нет старых сообщений для удаления"
    fi
done

echo ""
echo "=========================================="
echo "ИТОГИ"
echo "=========================================="
if [ "${DRY_RUN}" = "true" ]; then
    echo "Режим: DRY-RUN (без удаления)"
    echo "Для реального удаления запустите:"
    echo "  DRY_RUN=false MIN_AGE_DAYS=60 $0"
else
    echo "Удалено сообщений: $TOTAL_DELETED"
fi
echo "=========================================="
