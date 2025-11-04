#!/usr/bin/env bash
# Context7 best practice: Безопасная очистка всех Redis Streams стримов
# Очищает все стримы, DLQ и PEL (Pending Entry List)

set -euo pipefail

REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
DRY_RUN="${DRY_RUN:-false}"

# Все стримы из worker/event_bus.py
STREAMS=(
    "stream:posts:parsed"
    "stream:posts:tagged"
    "stream:posts:enriched"
    "stream:posts:indexed"
    "stream:posts:crawl"
    "stream:posts:deleted"
    "stream:posts:vision:uploaded"
    "stream:posts:vision:analyzed"
    "stream:albums:parsed"
    "stream:album:assembled"
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
echo "ОЧИСТКА REDIS STREAMS"
echo "=========================================="
echo "Host: ${REDIS_HOST}:${REDIS_PORT}"
echo "Режим: ${DRY_RUN}"
echo "Стримов: ${#STREAMS[@]}"
echo "=========================================="

for STREAM in "${STREAMS[@]}"; do
    echo ""
    echo "📊 Обработка стрима: ${STREAM}"
    
    # Проверяем существование и длину стрима
    LENGTH=$(docker compose exec -T "${REDIS_HOST}" redis-cli XLEN "${STREAM}" 2>/dev/null || echo "0")
    
    if [ "${LENGTH}" = "0" ] || [ -z "${LENGTH}" ]; then
        echo "  ℹ️  Стрим пуст или не существует, пропускаем"
        continue
    fi
    
    echo "  📈 Сообщений в стриме: ${LENGTH}"
    
    if [ "${DRY_RUN}" = "true" ]; then
        echo "  🔍 DRY-RUN: Пропускаем очистку"
        continue
    fi
    
    # Context7: Очистка PEL через XAUTOCLAIM для всех consumer groups
    echo "  🔧 Очистка PEL (Pending Entry List)..."
    
    # Получаем список групп для этого стрима
    GROUPS=$(docker compose exec -T "${REDIS_HOST}" redis-cli XINFO GROUPS "${STREAM}" 2>/dev/null | grep "^name" | awk '{print $2}' || true)
    
    if [ -n "${GROUPS}" ]; then
        echo "${GROUPS}" | while read -r GROUP; do
            if [ -n "${GROUP}" ]; then
                echo "    Обработка группы: ${GROUP}"
                
                # Context7: XAUTOCLAIM для очистки старых pending сообщений
                # Используем минимальный idle time (0) для очистки всех
                CLAIMED=$(docker compose exec -T "${REDIS_HOST}" redis-cli XAUTOCLAIM "${STREAM}" "${GROUP}" cleanup_worker 0 0-0 COUNT 100 2>/dev/null || echo "")
                
                if [ -n "${CLAIMED}" ] && [ "${CLAIMED}" != "(empty array)" ]; then
                    # Извлекаем ID сообщений из результата XAUTOCLAIM
                    # Формат ответа: [next_id, [msg1, msg2, ...], [deleted_ids]]
                    # Упрощённая обработка: ищем все ID в формате timestamp-counter
                    MSG_IDS=$(echo "${CLAIMED}" | grep -oE '[0-9]+-[0-9]+' || true)
                    
                    if [ -n "${MSG_IDS}" ]; then
                        echo "${MSG_IDS}" | while read -r MSG_ID; do
                            docker compose exec -T "${REDIS_HOST}" redis-cli XACK "${STREAM}" "${GROUP}" "${MSG_ID}" >/dev/null 2>&1 || true
                        done
                        echo "      ✅ Очищено pending сообщений в группе ${GROUP}"
                    fi
                fi
            fi
        done
    else
        echo "    ℹ️  Consumer groups не найдены"
    fi
    
    # Context7: XTRIM для полной очистки стрима
    echo "  🗑️  Удаление всех сообщений из стрима..."
    TRIMMED=$(docker compose exec -T "${REDIS_HOST}" redis-cli XTRIM "${STREAM}" MAXLEN 0 2>/dev/null || echo "0")
    
    # Проверяем результат
    FINAL_LENGTH=$(docker compose exec -T "${REDIS_HOST}" redis-cli XLEN "${STREAM}" 2>/dev/null || echo "0")
    
    if [ "${FINAL_LENGTH}" = "0" ]; then
        echo "  ✅ Стрим очищен полностью"
    else
        echo "  ⚠️  В стриме осталось сообщений: ${FINAL_LENGTH}"
    fi
done

echo ""
echo "=========================================="
echo "✅ Очистка Redis Streams завершена"
echo "=========================================="

