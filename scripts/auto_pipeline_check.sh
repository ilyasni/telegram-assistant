#!/bin/bash
# Автоматическая проверка пайплайна (Context7 Best Practices)
# Используется для cron или периодического мониторинга

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

REPORT_DIR="${REPORT_DIR:-reports}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT_FILE="${REPORT_DIR}/pipeline_auto_check_${TIMESTAMP}.md"

mkdir -p "$REPORT_DIR"

echo "=================================================================================="
echo "АВТОМАТИЧЕСКАЯ ПРОВЕРКА ПАЙПЛАЙНА"
echo "Context7: Комплексная проверка всех этапов пайплайна"
echo "=================================================================================="
echo ""

# Инициализация отчета
cat > "$REPORT_FILE" <<EOF
# Автоматическая проверка пайплайна

**Дата**: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
**Context7**: Автоматическая проверка всех этапов пайплайна

---

## Результаты проверки

EOF

ERRORS=0
WARNINGS=0

# ============================================================================
# 1. ПРОВЕРКА SCHEDULER
# ============================================================================
echo "1. ПРОВЕРКА SCHEDULER"
echo "----------------------------------------------------------------------------------"

SCHEDULER_RUNNING=$(curl -s http://localhost:9090/api/v1/query?query=scheduler_running 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '0')" 2>/dev/null || echo "0")

if [ "$SCHEDULER_RUNNING" = "1" ]; then
    echo -e "  ${GREEN}✅ Scheduler работает${NC}"
    echo "- ✅ Scheduler работает" >> "$REPORT_FILE"
else
    echo -e "  ${RED}❌ Scheduler не работает${NC}"
    echo "- ❌ Scheduler не работает" >> "$REPORT_FILE"
    ERRORS=$((ERRORS + 1))
fi

# Проверка последнего тика
LAST_TICK=$(curl -s http://localhost:9090/api/v1/query?query=scheduler_last_tick_ts_seconds 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '0')" 2>/dev/null || echo "0")
if [ "$LAST_TICK" != "0" ] && [ "$LAST_TICK" != "null" ]; then
    NOW=$(date +%s)
    AGE=$((NOW - ${LAST_TICK%.*}))
    if [ "$AGE" -lt 600 ]; then
        echo -e "  ${GREEN}✅ Последний тик свежий (${AGE}s назад)${NC}"
        echo "- ✅ Последний тик свежий (${AGE}s назад)" >> "$REPORT_FILE"
    else
        echo -e "  ${YELLOW}⚠️  Последний тик старый (${AGE}s назад)${NC}"
        echo "- ⚠️ Последний тик старый (${AGE}s назад)" >> "$REPORT_FILE"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

echo ""

# ============================================================================
# 2. ПРОВЕРКА ПАРСИНГА
# ============================================================================
echo "2. ПРОВЕРКА ПАРСИНГА"
echo "----------------------------------------------------------------------------------"

PARSING_RATE=$(curl -s http://localhost:9090/api/v1/query?query=rate\(parser_runs_total\[10m\]\) 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '0')" 2>/dev/null || echo "0")

if [ "$(echo "$PARSING_RATE > 0" | bc 2>/dev/null || echo "0")" = "1" ]; then
    echo -e "  ${GREEN}✅ Парсинг активен (rate: ${PARSING_RATE})${NC}"
    echo "- ✅ Парсинг активен (rate: ${PARSING_RATE})" >> "$REPORT_FILE"
else
    echo -e "  ${YELLOW}⚠️  Парсинг неактивен${NC}"
    echo "- ⚠️ Парсинг неактивен" >> "$REPORT_FILE"
    WARNINGS=$((WARNINGS + 1))
fi

POSTS_PARSED=$(curl -s http://localhost:9090/api/v1/query?query=rate\(posts_parsed_total\[1h\]\) 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); total=0; [total:=total+float(v['value'][1]) for v in r]; print(total)" 2>/dev/null || echo "0")
echo "  Постов обработано за час: ${POSTS_PARSED}"
echo "- Постов обработано за час: ${POSTS_PARSED}" >> "$REPORT_FILE"

echo ""

# ============================================================================
# 3. ПРОВЕРКА VISION АНАЛИЗА
# ============================================================================
echo "3. ПРОВЕРКА VISION АНАЛИЗА"
echo "----------------------------------------------------------------------------------"

VISION_RATE=$(curl -s http://localhost:9090/api/v1/query?query=rate\(vision_analysis_requests_total\[10m\]\) 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); total=0; [total:=total+float(v['value'][1]) for v in r]; print(total)" 2>/dev/null || echo "0")

if [ "$(echo "$VISION_RATE > 0" | bc 2>/dev/null || echo "0")" = "1" ]; then
    echo -e "  ${GREEN}✅ Vision анализ активен (rate: ${VISION_RATE})${NC}"
    echo "- ✅ Vision анализ активен (rate: ${VISION_RATE})" >> "$REPORT_FILE"
else
    echo -e "  ${YELLOW}⚠️  Vision анализ неактивен${NC}"
    echo "- ⚠️ Vision анализ неактивен" >> "$REPORT_FILE"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# ============================================================================
# 4. ПРОВЕРКА ТЕГИРОВАНИЯ
# ============================================================================
echo "4. ПРОВЕРКА ТЕГИРОВАНИЯ"
echo "----------------------------------------------------------------------------------"

TAGGING_RATE=$(curl -s http://localhost:9090/api/v1/query?query=rate\(tagging_processed_total\[10m\]\) 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); total=0; [total:=total+float(v['value'][1]) for v in r]; print(total)" 2>/dev/null || echo "0")

if [ "$(echo "$TAGGING_RATE > 0" | bc 2>/dev/null || echo "0")" = "1" ]; then
    echo -e "  ${GREEN}✅ Тегирование активно (rate: ${TAGGING_RATE})${NC}"
    echo "- ✅ Тегирование активно (rate: ${TAGGING_RATE})" >> "$REPORT_FILE"
else
    echo -e "  ${YELLOW}⚠️  Тегирование неактивно${NC}"
    echo "- ⚠️ Тегирование неактивно" >> "$REPORT_FILE"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# ============================================================================
# 5. ПРОВЕРКА REDIS STREAMS
# ============================================================================
echo "5. ПРОВЕРКА REDIS STREAMS"
echo "----------------------------------------------------------------------------------"

# Проверка pending сообщений
PENDING_PARSED=$(curl -s http://localhost:9090/api/v1/query?query=stream_pending_size\{stream=\"posts\\.parsed\"\} 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '0')" 2>/dev/null || echo "0")
PENDING_TAGGED=$(curl -s http://localhost:9090/api/v1/query?query=stream_pending_size\{stream=\"posts\\.tagged\"\} 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '0')" 2>/dev/null || echo "0")
PENDING_ENRICHED=$(curl -s http://localhost:9090/api/v1/query?query=stream_pending_size\{stream=\"posts\\.enriched\"\} 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(r[0]['value'][1] if r else '0')" 2>/dev/null || echo "0")

echo "  Pending parsed: ${PENDING_PARSED}"
echo "  Pending tagged: ${PENDING_TAGGED}"
echo "  Pending enriched: ${PENDING_ENRICHED}"
echo "- Pending parsed: ${PENDING_PARSED}" >> "$REPORT_FILE"
echo "- Pending tagged: ${PENDING_TAGGED}" >> "$REPORT_FILE"
echo "- Pending enriched: ${PENDING_ENRICHED}" >> "$REPORT_FILE"

if [ "$PENDING_PARSED" -gt 1000 ] || [ "$PENDING_TAGGED" -gt 1000 ] || [ "$PENDING_ENRICHED" -gt 1000 ]; then
    echo -e "  ${YELLOW}⚠️  Высокий backlog в Redis streams${NC}"
    echo "- ⚠️ Высокий backlog в Redis streams" >> "$REPORT_FILE"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo "=================================================================================="
echo "SUMMARY"
echo "=================================================================================="
echo ""

if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo -e "${GREEN}✅ Все проверки пройдены${NC}"
    STATUS="✅ Все проверки пройдены"
elif [ "$ERRORS" -eq 0 ]; then
    echo -e "${YELLOW}⚠️  Есть предупреждения (${WARNINGS})${NC}"
    STATUS="⚠️ Есть предупреждения (${WARNINGS})"
else
    echo -e "${RED}❌ Обнаружены ошибки (${ERRORS}) и предупреждения (${WARNINGS})${NC}"
    STATUS="❌ Обнаружены ошибки (${ERRORS}) и предупреждения (${WARNINGS})"
fi

cat >> "$REPORT_FILE" <<EOF

---

## Итоговый статус

${STATUS}

- Ошибок: ${ERRORS}
- Предупреждений: ${WARNINGS}

---

**Отчет сохранен**: ${REPORT_FILE}
EOF

echo ""
echo "📄 Отчет сохранен: ${REPORT_FILE}"

# Возвращаем код выхода
if [ "$ERRORS" -gt 0 ]; then
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    exit 2
else
    exit 0
fi

