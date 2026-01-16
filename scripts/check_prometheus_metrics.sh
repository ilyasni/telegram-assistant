#!/bin/bash
# Скрипт проверки новых метрик Prometheus

echo "=== Проверка новых метрик Prometheus ==="
echo ""

# Проверка PostgreSQL метрик
echo "📊 PostgreSQL метрики:"
curl -s http://localhost:8000/metrics | grep -E "^postgres_operations_total|^postgres_operation_duration_seconds|^postgres_connections" | head -3
echo ""

# Проверка Qdrant метрик
echo "📊 Qdrant метрики:"
curl -s http://localhost:8001/metrics | grep -E "^qdrant_operations_total|^qdrant_operation_duration_seconds|^qdrant_collection" | head -3
echo ""

# Проверка Post Persistence метрик
echo "📊 Post Persistence метрики:"
curl -s http://localhost:8001/metrics | grep -E "^post_persistence_processed_total|^post_persistence_latency_seconds|^post_persistence_pel_size" | head -3
echo ""

# Проверка Graph Writer метрик
echo "📊 Graph Writer метрики:"
curl -s http://localhost:8001/metrics | grep -E "^graph_writer_processed_total|^graph_writer_pel_size" | head -3
echo ""

# Проверка API Endpoint метрик
echo "📊 API Endpoint метрики:"
curl -s http://localhost:8000/metrics | grep -E "^api_endpoint_requests_total|^api_endpoint_latency_seconds" | head -3
echo ""

echo "✅ Проверка завершена"
