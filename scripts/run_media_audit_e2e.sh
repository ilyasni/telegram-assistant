#!/bin/bash
# Context7: Запуск E2E тестов для новых компонентов Media Audit
# Использует существующую инфраструктуру тестирования

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REPORT_DIR="artifacts/e2e_reports"
mkdir -p "$REPORT_DIR"

echo "🧪 Запуск E2E тестов Media Audit - $(date)"
echo "=" | tee -a "$REPORT_DIR/media_audit_e2e_${TIMESTAMP}.log"

# Новые тесты для Media Audit
TESTS=(
    "tests/e2e/test_media_groups.py"
    "tests/e2e/test_retagging.py"
)

# Функция запуска тестов
run_tests() {
    local run_number=$1
    local delay_minutes=${2:-0}
    
    if [ $delay_minutes -gt 0 ]; then
        echo "⏰ Ожидание $delay_minutes минут перед запуском (попытка $run_number)..."
        sleep $((delay_minutes * 60))
    fi
    
    local report_file="$REPORT_DIR/media_audit_e2e_${TIMESTAMP}_attempt_${run_number}.txt"
    
    echo "" | tee -a "$report_file"
    echo "📋 Попытка $run_number - $(date)" | tee -a "$report_file"
    echo "=" | tee -a "$report_file"
    
    # Проверка доступности сервисов через существующий скрипт
    echo "🔍 Проверка доступности сервисов..." | tee -a "$report_file"
    
    if bash "$PROJECT_ROOT/scripts/check_services.sh" 2>&1 | tee -a "$report_file"; then
        echo "✅ Все сервисы доступны" | tee -a "$report_file"
    else
        echo "❌ Некоторые сервисы недоступны!" | tee -a "$report_file"
        return 1
    fi
    
    # Запуск тестов через docker compose exec
    echo "" | tee -a "$report_file"
    echo "🧪 Запуск тестов..." | tee -a "$report_file"
    
    local test_result=0
    for test_file in "${TESTS[@]}"; do
        if [ ! -f "$test_file" ]; then
            echo "⚠️  Тест не найден: $test_file" | tee -a "$report_file"
            continue
        fi
        
        echo "" | tee -a "$report_file"
        echo "📝 Тест: $test_file" | tee -a "$report_file"
        echo "-" | tee -a "$report_file"
        
        # Запуск через docker compose exec worker
        if docker compose exec -T worker python3 -m pytest "$test_file" -v --tb=short 2>&1 | tee -a "$report_file"; then
            echo "✅ $test_file - пройден" | tee -a "$report_file"
        else
            echo "❌ $test_file - провален" | tee -a "$report_file"
            test_result=1
        fi
    done
    
    echo "" | tee -a "$report_file"
    echo "=" | tee -a "$report_file"
    
    if [ $test_result -eq 0 ]; then
        echo "✅ Все тесты пройдены (попытка $run_number)" | tee -a "$report_file"
    else
        echo "❌ Некоторые тесты провалены (попытка $run_number)" | tee -a "$report_file"
    fi
    
    return $test_result
}

# Главная логика
if [ "${1:-}" == "--retry" ]; then
    RUN_NUMBER=${2:-2}
    DELAY_MINUTES=${3:-0}
    echo "🔄 Повторный запуск тестов (попытка $RUN_NUMBER, задержка $DELAY_MINUTES мин)"
    run_tests $RUN_NUMBER $DELAY_MINUTES
else
    # Первый запуск
    echo "🚀 Первый запуск тестов"
    run_tests 1 0
    
    # Планирование повторных запусков через фоновые процессы
    if [ "${1:-}" != "--no-retry" ]; then
        echo ""
        echo "⏰ Планирование повторных запусков..."
        
        # Запуск через 20 минут
        (
            sleep $((20 * 60))
            bash "$PROJECT_ROOT/scripts/run_media_audit_e2e.sh" --retry 2 0
        ) &
        PID_20=$!
        echo "✅ Фоновый процесс для 20-минутного запуска (PID: $PID_20)"
        
        # Запуск через 40 минут
        (
            sleep $((40 * 60))
            bash "$PROJECT_ROOT/scripts/run_media_audit_e2e.sh" --retry 3 0
        ) &
        PID_40=$!
        echo "✅ Фоновый процесс для 40-минутного запуска (PID: $PID_40)"
        
        echo ""
        echo "📊 Отслеживание процессов:"
        echo "   ps aux | grep run_media_audit_e2e"
        echo "   kill $PID_20 $PID_40  # для отмены"
    fi
    
    echo ""
    echo "📊 Отчеты сохраняются в: $REPORT_DIR"
    echo "📝 Последний отчет: $REPORT_DIR/media_audit_e2e_${TIMESTAMP}_attempt_1.txt"
fi

