#!/bin/bash
# Скрипт для извлечения x-amz-request-id из логов Docker контейнеров
# Использование: ./scripts/extract_s3_request_ids.sh [service_name] [--follow]

SERVICE=${1:-worker}
FOLLOW_FLAG=${2:-}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 ИЗВЛЕЧЕНИЕ x-amz-request-id ИЗ ЛОГОВ ($SERVICE)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 Команды для получения request IDs:"
echo ""

if [ "$FOLLOW_FLAG" == "--follow" ]; then
    echo "# Следить за логами в реальном времени:"
    echo "docker compose logs -f $SERVICE | grep -E '(x-amz-request-id|x_amz_request_id|request_id)'"
    echo ""
    docker compose logs -f $SERVICE | grep -E '(x-amz-request-id|x_amz_request_id|request_id)'
else
    echo "# Показать последние ошибки S3 с request IDs:"
    echo "docker compose logs --tail=100 $SERVICE | grep -B5 -A5 -E '(x-amz-request-id|x_amz_request_id|request_id|InternalError|S3.*error)'"
    echo ""
    docker compose logs --tail=200 $SERVICE | grep -B5 -A5 -E '(x-amz-request-id|x_amz_request_id|request_id|InternalError|S3.*error)' || echo "Ошибки S3 не найдены в последних логах"
    echo ""
    echo "# Показать только request IDs:"
    echo "docker compose logs --tail=500 $SERVICE | grep -oE '(x_amz_request_id|request_id|x-amz-request-id)="[^"]*"' | sort | uniq"
    echo ""
    docker compose logs --tail=500 $SERVICE | grep -oE '(x_amz_request_id|request_id|x-amz-request-id)="[^"]*"' | sort | uniq || echo "Request IDs не найдены"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
