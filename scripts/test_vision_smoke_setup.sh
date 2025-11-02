#!/bin/bash
# Базовая подготовка для smoke-тестов Vision Analysis Task
# Context7: Очистка стримов, проверка/создание consumer groups

set -euo pipefail

# Конфигурация
STREAM_IN="${STREAM_IN:-stream:posts:vision}"
STREAM_ANALYZED="${STREAM_ANALYZED:-stream:posts:vision:analyzed}"
STREAM_SKIPPED="${STREAM_SKIPPED:-stream:posts:vision:skipped}"  # Для логики, но эмиссия в analyzed
STREAM_DLQ="${STREAM_DLQ:-stream:posts:vision:dlq}"
GROUP="${GROUP:-vision_workers}"
CONSUMER="${CONSUMER:-tester}"
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"

# S3 объект для тестов
S3_BUCKET="${S3_BUCKET:-bucket-467940}"
S3_KEY="${S3_KEY:-media/877193ef-be80-4977-aaeb-8009c3d772ee/4e/4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b..png}"
SHA256="${SHA256:-4e5b10df0bc2faa503f911a4db7ae01730ab7b549af1d86003970ac1b001415b}"

echo "=== Vision Analysis Smoke Tests Setup ==="
echo "STREAM_IN: $STREAM_IN"
echo "STREAM_ANALYZED: $STREAM_ANALYZED"
echo "STREAM_DLQ: $STREAM_DLQ"
echo "GROUP: $GROUP"
echo "S3_BUCKET: $S3_BUCKET"
echo "S3_KEY: $S3_KEY"
echo "SHA256: $SHA256"
echo ""

# Проверка redis-cli
if ! command -v redis-cli &> /dev/null; then
    echo "ERROR: redis-cli not found. Please install redis-cli or run inside Docker container."
    exit 1
fi

# Извлечение host и port из REDIS_URL
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
if [[ "$REDIS_URL" =~ redis://([^:]+):([0-9]+) ]]; then
    REDIS_HOST="${BASH_REMATCH[1]}"
    REDIS_PORT="${BASH_REMATCH[2]}"
fi

REDIS_CLI_CMD="redis-cli -h $REDIS_HOST -p $REDIS_PORT"

echo "1. Очистка стримов..."
$REDIS_CLI_CMD XTRIM "$STREAM_IN" MAXLEN 0 || echo "  Stream $STREAM_IN does not exist (OK)"
$REDIS_CLI_CMD XTRIM "$STREAM_ANALYZED" MAXLEN 0 || echo "  Stream $STREAM_ANALYZED does not exist (OK)"
$REDIS_CLI_CMD XTRIM "$STREAM_DLQ" MAXLEN 0 || echo "  Stream $STREAM_DLQ does not exist (OK)"
echo "  ✓ Стримы очищены"

echo ""
echo "2. Проверка/создание consumer group..."
$REDIS_CLI_CMD XGROUP CREATE "$STREAM_IN" "$GROUP" 0 MKSTREAM || {
    if [[ $? -eq 1 ]]; then
        echo "  Consumer group $GROUP already exists (OK)"
    else
        echo "  ERROR: Failed to create consumer group"
        exit 1
    fi
}

echo ""
echo "3. Верификация consumer group..."
$REDIS_CLI_CMD XINFO GROUPS "$STREAM_IN"
echo "  ✓ Consumer group verified"

echo ""
echo "=== Setup completed successfully ==="
echo ""
echo "Next steps:"
echo "  - Run smoke test 1: python scripts/test_vision_smoke_1_success.py"
echo "  - Or run all tests: ./scripts/run_all_vision_smoke_tests.sh"

