"""
Worker Metrics Module
====================

Prometheus метрики для Worker сервиса.
"""

from prometheus_client import Counter, Gauge, Histogram

# Task processing metrics
worker_tasks_processed_total = Counter(
    'worker_tasks_processed_total',
    'Total tasks processed by worker',
    ['task_type', 'status']
)

# Posts processing metrics (for Grafana dashboards)
posts_processed_total = Counter(
    'posts_processed_total',
    'Total posts processed',
    ['stage', 'success']
)

# [C7-ID: WORKER-METRICS-002] - Метрики глубины стримов
stream_depth = Gauge(
    'stream_depth',
    'Current depth of Redis streams',
    ['stream']
)

# posts_in_queue_total определено в event_bus.py (используется там)

worker_queue_size = Gauge(
    'worker_queue_size',
    'Current queue size',
    ['queue_name']
)

worker_task_duration_seconds = Histogram(
    'worker_task_duration_seconds',
    'Task processing duration',
    ['task_type']
)

worker_errors_total = Counter(
    'worker_errors_total',
    'Total worker errors',
    ['error_type', 'task_type']
)

# Neo4j integration metrics
neo4j_operations_total = Counter(
    'neo4j_operations_total',
    'Total Neo4j operations',
    ['operation_type', 'status']
)

neo4j_operation_duration_seconds = Histogram(
    'neo4j_operation_duration_seconds',
    'Neo4j operation duration',
    ['operation_type']
)

# RAG processing metrics
rag_queries_total = Counter(
    'rag_queries_total',
    'Total RAG queries processed',
    ['query_type', 'status']
)

rag_processing_duration_seconds = Histogram(
    'rag_processing_duration_seconds',
    'RAG processing duration',
    ['query_type']
)

# AI provider metrics
ai_requests_total = Counter(
    'ai_requests_total',
    'Total AI provider requests',
    ['provider', 'model', 'status']
)

# Tagging metrics (imported from gigachain_adapter)
from ai_providers.gigachain_adapter import tagging_requests_total, tagging_latency_seconds

ai_request_duration_seconds = Histogram(
    'ai_request_duration_seconds',
    'AI request duration',
    ['provider', 'model']
)

# Enrichment metrics
enrichment_requests_total = Counter(
    'enrichment_requests_total',
    'Total enrichment requests',
    ['provider', 'operation', 'success']
)

enrichment_latency_seconds = Histogram(
    'enrichment_latency_seconds',
    'Enrichment processing latency',
    ['status']
)

enrichment_skipped_total = Counter(
    'enrichment_skipped_total',
    'Total enrichment requests skipped',
    ['reason']
)

# Context7: CRAWL4AI enrichment metrics
enrichment_triggers_total = Counter(
    'enrichment_triggers_total',
    'Total enrichment triggers checked',
    ['type', 'decision']  # type: tag|url|wordcount, decision: hit|miss
)

enrichment_crawl_requests_total = Counter(
    'enrichment_crawl_requests_total',
    'Total crawl requests',
    ['domain', 'status']  # status: ok|timeout|blocked|ssrf_denied
)

enrichment_crawl_duration_seconds = Histogram(
    'enrichment_crawl_duration_seconds',
    'Crawl request duration',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0]
)

enrichment_budget_checks_total = Counter(
    'enrichment_budget_checks_total',
    'Total budget checks',
    ['type', 'result']  # type: tenant|domain, result: allowed|denied
)

# Embedding metrics (imported from embedding_service)
from ai_providers.embedding_service import embedding_requests_total, embedding_latency_seconds

# Memory and resource metrics
worker_memory_usage_bytes = Gauge(
    'worker_memory_usage_bytes',
    'Worker memory usage in bytes'
)

worker_cpu_usage_percent = Gauge(
    'worker_cpu_usage_percent',
    'Worker CPU usage percentage'
)