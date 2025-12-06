#!/bin/bash

echo "=================================================================================="
echo "ПРОВЕРКА МЕТРИКИ Channel Processing Time"
echo "=================================================================================="
echo ""

echo "1. Проверка наличия метрики в Prometheus:"
echo "   Запрос: parser_channel_processing_seconds_bucket"
docker compose exec -T prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=parser_channel_processing_seconds_bucket' 2>&1 | python3 -m json.tool | grep -E '"status"|"result"' | head -2
echo ""

echo "2. Проверка запроса p50:"
docker compose exec -T prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.50,%20sum(rate(parser_channel_processing_seconds_bucket[5m]))%20by%20(le,%20mode,%20status))' 2>&1 | python3 -m json.tool | grep -A 3 '"value"'
echo ""

echo "3. Проверка запроса p95:"
docker compose exec -T prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,%20sum(rate(parser_channel_processing_seconds_bucket[5m]))%20by%20(le,%20mode,%20status))' 2>&1 | python3 -m json.tool | grep -A 3 '"value"'
echo ""

echo "4. Проверка запроса p99:"
docker compose exec -T prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,%20sum(rate(parser_channel_processing_seconds_bucket[5m]))%20by%20(le,%20mode,%20status))' 2>&1 | python3 -m json.tool | grep -A 3 '"value"'
echo ""

echo "5. Проверка количества buckets и labels:"
docker compose exec -T prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=parser_channel_processing_seconds_bucket' 2>&1 | python3 -m json.tool | grep -E '"mode"|"status"|"le"' | sort | uniq -c
echo ""

echo "6. Проверка последних значений за 1 час:"
docker compose exec -T prometheus wget -qO- 'http://localhost:9090/api/v1/query_range?query=parser_channel_processing_seconds_bucket&start='$(date -d '1 hour ago' +%s)'&end='$(date +%s)'&step=60s' 2>&1 | python3 -m json.tool | grep -c '"values"'
echo "   (показано количество временных рядов)"
echo ""

echo "7. Проверка максимального значения времени обработки:"
docker compose exec -T prometheus wget -qO- 'http://localhost:9090/api/v1/query?query=max_over_time(parser_channel_processing_seconds_bucket{le="+Inf"}[1h])' 2>&1 | python3 -m json.tool | grep -A 3 '"value"'
echo ""

echo "=================================================================================="
echo "ПРОВЕРКА ЗАВЕРШЕНА"
echo "=================================================================================="

