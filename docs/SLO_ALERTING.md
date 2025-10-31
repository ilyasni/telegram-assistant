# SLO и Alerting Rules для Vision + S3 Integration

**Версия**: 1.0 | **Дата**: 2025-01-28

## Context7 Best Practices

- **SLO Definition**: Явные цели производительности
- **Alerting Rules**: Прагматичные пороги (не слишком чувствительные)
- **Multi-tenancy**: Метрики с `tenant_id` labels
- **Trace Correlation**: `trace_id` во всех логах и метриках

---

## Service Level Objectives (SLO)

### Vision Analysis

| Метрика | Target | Measurement Window | Alert Threshold |
|---------|--------|-------------------|----------------|
| **Latency p95** | ≤ 3-5s | 5 минут | > 5s (warning), > 10s (critical) |
| **Error Rate** | ≤ 2% | 5 минут | > 2% (warning), > 5% (critical) |
| **Availability** | ≥ 95% | 10 минут | < 95% (warning), < 90% (critical) |
| **Success Rate** | ≥ 98% | 5 минут | < 98% (warning) |

**Метрики:**
- `vision_analysis_requests_total{status, provider, tenant_id}`
- `vision_analysis_duration_seconds{provider, tenant_id, status}`
- `vision_tokens_used_total{provider, tenant_id, model}`

---

### S3 Storage Operations

| Метрика | Target | Measurement Window | Alert Threshold |
|---------|--------|-------------------|----------------|
| **Upload Latency p95** | ≤ 10s | 5 минут | > 10s (warning) |
| **Error Rate** | ≤ 1% | 5 минут | > 1% (warning) |
| **Quota Usage** | < 85% | 15 GB limit | > 85% (warning), > 93% (critical) |
| **Quota Violations** | < 10/hour | 10 минут | > 10/hour (warning) |

**Метрики:**
- `storage_bucket_usage_gb{content_type}`
- `storage_quota_violations_total{tenant_id, reason}`
- `s3_operations_total{operation, result, content_type, tenant_id}`
- `s3_upload_duration_seconds{content_type, size_bucket, tenant_id}`

---

### Crawl4ai

| Метрика | Target | Measurement Window | Alert Threshold |
|---------|--------|-------------------|----------------|
| **Latency p95** | ≤ 30s | 5 минут | > 30s (warning) |
| **Success Rate** | ≥ 90% | 10 минут | < 90% (warning) |
| **Cache Hit Rate** | ≥ 60% | 1 час | < 60% (warning) |

**Метрики:**
- `crawl_latency_seconds{host, status, tenant_id}`
- `crawl_s3_cache_hits_total{tenant_id}`
- `crawl_content_extraction_duration_seconds{extractor, tenant_id}`

---

### Budget Gate

| Метрика | Target | Measurement Window | Alert Threshold |
|---------|--------|-------------------|----------------|
| **Daily Budget Usage** | < 95% | 1 час | > 95% (warning) |
| **Block Rate** | < 5/hour | 10 минут | > 5/hour (warning) |

**Метрики:**
- `vision_budget_usage_gauge{tenant_id}`
- `vision_budget_gate_blocks_total{tenant_id, reason}`
- `vision_tokens_used_total{provider, tenant_id}`

---

### DLQ (Dead Letter Queue)

| Метрика | Target | Measurement Window | Alert Threshold |
|---------|--------|-------------------|----------------|
| **Backlog Size** | < 100 | 10 минут | > 100 (warning), > 500 (critical) |
| **Events Rate** | < 10/hour | 1 час | > 10/hour (warning) |

**Метрики:**
- `events_dlq_total{base_event, error_code}`
- `redis_xpending{stream}` (для PEL)
- `stream_pending_size{stream}`

---

## Prometheus Alert Rules

### Файл: `prometheus/alerts/vision_s3_alerts.yml`

Содержит alerting rules для:
- Vision Analysis SLO (latency, error rate, availability)
- S3 Storage Quota (usage, violations, emergency cleanup)
- Budget Gate (exhaustion, blocks)
- DLQ (backlog, events)
- Crawl4ai SLO (latency, success rate)
- S3 Operations (error rate, upload latency)
- Worker Health (processing, failure rate)

---

## Grafana Dashboards (TODO)

### Vision Analysis Dashboard

**Panels:**
- Request rate (by status, provider, tenant)
- Latency (p50, p95, p99) histogram
- Error rate (%)
- Token usage (total, average)
- Budget usage gauge
- Provider distribution (gigachat vs OCR)

**Variables:**
- `tenant_id`
- `provider` (gigachat, ocr, cached)
- `time_range` (1h, 6h, 24h, 7d)

---

### S3 Storage Dashboard

**Panels:**
- Bucket usage (GB) - gauge с 15 GB limit
- Usage by type (media, vision, crawl) - stacked area
- Quota violations counter
- Emergency cleanups counter
- LRU evictions histogram
- S3 operations rate (by operation, result)
- Upload latency histogram

**Variables:**
- `tenant_id`
- `content_type` (media, vision, crawl)

**Alerts:**
- Usage > 85% → yellow
- Usage > 93% → red
- Violations > 10/hour → warning

---

### Crawl4ai Dashboard

**Panels:**
- Crawl latency (p50, p95)
- Success rate (%)
- S3 cache hit rate (%)
- Content extraction duration
- URLs processed rate

---

### DLQ Dashboard

**Panels:**
- DLQ events counter (by base_event, error_code)
- PEL backlog size (by stream)
- Retry attempts histogram
- DLQ events rate (events/hour)

---

## Alert Routing

### Severity Levels

- **critical**: Немедленная реакция (pager, SMS)
  - Storage quota > 93%
  - Vision availability < 90%
  - Worker not processing

- **warning**: Мониторинг, плановая реакция
  - Storage quota > 85%
  - Vision latency > 5s
  - Budget usage > 95%

### Notification Channels

- **Critical**: PagerDuty / Telegram Bot / Email
- **Warning**: Slack / Telegram / Email

---

## SLO Calculation Examples

### Vision Analysis Latency p95

```promql
histogram_quantile(
  0.95,
  rate(vision_analysis_duration_seconds_bucket[5m])
)
```

### Vision Error Rate

```promql
(
  sum(rate(vision_analysis_requests_total{status="error"}[5m])) by (tenant_id)
  /
  sum(rate(vision_analysis_requests_total[5m])) by (tenant_id)
) * 100
```

### Storage Usage Percentage

```promql
(storage_bucket_usage_gb / 15) * 100
```

---

## Best Practices

1. **Alert Fatigue Prevention**: Используй `for` для снижения ложных срабатываний
2. **Multi-tenancy**: Все alert rules с `tenant_id` labels для изоляции
3. **Trace Correlation**: Добавляй `trace_id` в alert annotations
4. **Progressive Alerts**: Warning → Critical эскалация
5. **Runbook Links**: Добавляй ссылки на runbooks в annotations

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28

