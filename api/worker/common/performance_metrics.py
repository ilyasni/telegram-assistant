"""
Performance Metrics - метрики производительности для Performance Guardrails.

Performance KPIs:
- P95 latency для Fast Path (`/ask`)
- Среднее `llm_calls_per_request` < 3 для Fast Path
- P95 `tokens_per_request` < 8k
- P95 `agent_steps_per_request` ≤ 4
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge

# Метрики производительности Fast Path
fast_path_latency_seconds = Histogram(
    'fast_path_latency_seconds',
    'Fast Path request latency (P95 target: < 5s)',
    ['endpoint', 'tenant_id'],
    buckets=[0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 30.0]
)

smart_path_latency_seconds = Histogram(
    'smart_path_latency_seconds',
    'Smart Path request latency (async background tasks)',
    ['task_type', 'tenant_id'],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0]
)

llm_calls_per_request = Histogram(
    'llm_calls_per_request',
    'Number of LLM calls per request (target: < 3 for Fast Path)',
    ['path_type', 'endpoint', 'tenant_id'],
    buckets=[0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 15, 20]
)

tokens_per_request = Histogram(
    'tokens_per_request',
    'Number of tokens per request (P95 target: < 8k)',
    ['path_type', 'endpoint', 'tenant_id'],
    buckets=[100, 500, 1000, 2000, 4000, 8000, 12000, 16000, 20000, 32000]
)

agent_steps_per_request = Histogram(
    'agent_steps_per_request',
    'Number of agent steps per request (P95 target: ≤ 4)',
    ['path_type', 'endpoint', 'tenant_id'],
    buckets=[0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 15, 20]
)

# Бюджеты на запрос
request_budget_exceeded_total = Counter(
    'request_budget_exceeded_total',
    'Total requests that exceeded budget limits',
    ['budget_type', 'path_type', 'endpoint', 'tenant_id']
)

# QoS уровни
qos_level_requests_total = Counter(
    'qos_level_requests_total',
    'Total requests by QoS level',
    ['qos_level', 'path_type', 'endpoint', 'tenant_id']
)

# Кэширование
cache_hits_total = Counter(
    'performance_cache_hits_total',
    'Cache hits for performance optimization',
    ['cache_type', 'path_type']
)

cache_misses_total = Counter(
    'performance_cache_misses_total',
    'Cache misses for performance optimization',
    ['cache_type', 'path_type']
)

# Метрики для мониторинга производительности
fast_path_p95_latency = Gauge(
    'fast_path_p95_latency_seconds',
    'P95 latency for Fast Path requests (target: < 5s)',
    ['endpoint']
)

avg_llm_calls_per_request = Gauge(
    'avg_llm_calls_per_request',
    'Average LLM calls per request (target: < 3 for Fast Path)',
    ['path_type', 'endpoint']
)

p95_tokens_per_request = Gauge(
    'p95_tokens_per_request',
    'P95 tokens per request (target: < 8k)',
    ['path_type', 'endpoint']
)

p95_agent_steps_per_request = Gauge(
    'p95_agent_steps_per_request',
    'P95 agent steps per request (target: ≤ 4)',
    ['path_type', 'endpoint']
)

