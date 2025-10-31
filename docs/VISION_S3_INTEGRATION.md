# GigaChat Vision + S3 Integration

**Статус**: ✅ Реализовано | **Версия**: 1.0 | **Дата**: 2025-01-28

## Контекст

Интеграция GigaChat Vision API с S3 хранилищем Cloud.ru для анализа медиа из Telegram каналов. Полный пайплайн включает:

- Telegram → S3 (MediaProcessor)
- S3 → Vision Analysis (GigaChat API + OCR fallback)
- Vision → Neo4j (ImageContent nodes)
- Crawl4ai → S3 → Neo4j (Article nodes)

## Критические ограничения

⚠️ **STORAGE LIMIT: 15 GB** — требует активного quota management

## Архитектура

```
Telegram Message
    ↓
MediaProcessor (SHA256 hash, quota check)
    ↓
S3 Storage (content-addressed: media/{tenant}/{sha256[:2]}/{sha256}.{ext})
    ↓
VisionUploadedEventV1
    ↓
Vision Analysis Worker
    ├─ VisionPolicyEngine (sampling, routing)
    ├─ BudgetGateService (token quotas)
    ├─ GigaChat Vision API / OCR Fallback
    └─ S3 Cache (vision/{tenant}/{sha256}_{model}_v{schema}.json)
    ↓
PostEnrichment (БД) + Neo4j ImageContent
    ↓
VisionAnalyzedEventV1
```

## Компоненты

### Storage Layer

- **`api/services/s3_storage.py`**: S3 сервис с content-addressed storage, gzip compression, presigned URLs
- **`worker/services/storage_quota.py`**: Quota management для 15 GB лимита
- **`api/services/url_canonicalizer.py`**: URL нормализация для дедупликации

### Vision Analysis

- **`worker/ai_adapters/gigachat_vision.py`**: GigaChat Vision API адаптер
- **`worker/services/vision_policy_engine.py`**: Policy engine (sampling, routing, fallback)
- **`worker/services/budget_gate.py`**: Token quota tracking и enforcement
- **`worker/services/ocr_fallback.py`**: OCR fallback для quota exhausted
- **`worker/tasks/vision_analysis_task.py`**: Vision worker с идемпотентностью

### Event Schemas

- **`worker/events/schemas/posts_vision_v1.py`**: Vision events (VisionUploadedEventV1, VisionAnalyzedEventV1)
- **`worker/events/schemas/dlq_v1.py`**: DLQ events

### Workers

- **`worker/services/retry_policy.py`**: Retry logic с exponential backoff
- **`telethon-ingest/services/media_processor.py`**: Telegram → S3 интеграция

### Crawl4ai

- **`crawl4ai/enrichment_engine.py`**: S3 кэширование HTML/metadata
- **`crawl4ai/crawl4ai_service.py`**: Neo4j sync для Article nodes

### Neo4j

- **`worker/integrations/neo4j_client.py`**: Расширен методами create_image_content_node, create_article_node

### API

- **`api/routers/storage.py`**: Storage quota endpoints
- **`api/config.py`**: S3 конфигурация

## Database Schema

### Миграция: `20250128_add_media_registry_vision.py`

- **`media_objects`**: Content-addressed registry (file_sha256 PK)
- **`post_media_map`**: Many-to-many связи постов и медиа
- **`post_enrichment`**: Расширено vision полями (CHECK constraints, GIN indexes)

## Configuration

### Environment Variables

```bash
# S3 Storage (Cloud.ru)
S3_ENDPOINT_URL=https://s3.cloud.ru
S3_BUCKET_NAME=test-467940
S3_REGION=ru-central-1
S3_ACCESS_KEY_ID=your_key
S3_SECRET_ACCESS_KEY=your_secret
S3_DEFAULT_TENANT_ID=877193ef-be80-4977-aaeb-8009c3d772ee

# Storage Limits (15 GB)
S3_TOTAL_LIMIT_GB=15.0
S3_EMERGENCY_THRESHOLD_GB=14.0
S3_PER_TENANT_LIMIT_GB=2.0

# GigaChat Vision
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_CREDENTIALS=your_credentials
GIGACHAT_VISION_MODEL=GigaChat-Pro
```

## API Endpoints

### Storage Quota Monitoring

```bash
# Общая квота
GET /api/v1/storage/quota

# Использование по tenant
GET /api/v1/storage/usage/{tenant_id}

# Проверка перед загрузкой
POST /api/v1/storage/quota/check
{
  "tenant_id": "uuid",
  "size_bytes": 1024000,
  "content_type": "media"
}

# Ручная очистка
POST /api/v1/storage/cleanup
{
  "force": false,
  "target_free_gb": 12.0
}

# Детальная статистика
GET /api/v1/storage/stats
```

## Diagnostic CLI

### Vision Diagnostics

```bash
# Проверка Vision обогащения
python worker/scripts/diag_vision.py check --post-id <uuid>

# Статистика Vision событий
python worker/scripts/diag_vision.py stats --tenant-id <uuid>

# Тест Vision Policy Engine
python worker/scripts/diag_vision.py test --media-file image.jpg
```

### S3 Diagnostics

```bash
# Проверка квот
python worker/scripts/diag_s3.py quota --tenant-id <uuid>

# Использование bucket
python worker/scripts/diag_s3.py usage

# Список объектов
python worker/scripts/diag_s3.py list --prefix media/ --limit 20

# Проверка объекта
python worker/scripts/diag_s3.py check-key media/tenant/file.jpg
```

### Emergency Cleanup

```bash
# Автоматическая очистка
python worker/scripts/emergency_s3_cleanup.py auto --target-gb 10 --dry-run

# Очистка crawl кэша
python worker/scripts/emergency_s3_cleanup.py crawl --max-age-days 3

# Очистка vision кэша
python worker/scripts/emergency_s3_cleanup.py vision --max-age-days 7

# Очистка orphaned multipart uploads
python worker/scripts/emergency_s3_cleanup.py multipart
```

## Storage Quota Management

### Лимиты

- **Total Bucket**: 15 GB
- **Emergency Threshold**: 14 GB (триггер cleanup)
- **Per-Tenant**: 2 GB
- **By Type**:
  - Media: 10 GB
  - Vision: 2 GB
  - Crawl: 2 GB

### Lifecycle Policies

- **Media**: 30 days TTL
- **Vision**: 14 days TTL
- **Crawl**: 7 days TTL
- **Abort incomplete uploads**: 1 day

### Emergency Cleanup Strategy

1. Crawl cache > 3 days
2. Vision results > 7 days
3. LRU media с `refs_count=0`
4. Orphaned multipart uploads

## Мониторинг

### Prometheus Metrics

```python
# Storage
storage_bucket_usage_gb{content_type}
storage_quota_violations_total{tenant_id, reason}
storage_emergency_cleanups_total

# Vision
vision_analysis_requests_total{status, provider, tenant}
vision_tokens_used_total{provider, tenant}
vision_analysis_duration_seconds

# S3
s3_upload_duration_seconds{content_type, size_bucket}
s3_operations_total{operation, result, content_type}
```

## Troubleshooting

### Quota Violations

```bash
# Проверить текущее использование
curl http://localhost:8000/api/v1/storage/quota

# Запустить emergency cleanup
python worker/scripts/emergency_s3_cleanup.py auto --target-gb 12

# Проверить метрики
curl http://localhost:9090/metrics | grep storage_quota_violations
```

### Vision Analysis Issues

```bash
# Проверить обогащение поста
python worker/scripts/diag_vision.py check --post-id <uuid>

# Проверить Vision события
python worker/scripts/diag_vision.py stats

# Проверить S3 vision кэш
python worker/scripts/diag_s3.py list --prefix vision/
```

## Best Practices

1. **Idempotency**: Все операции через SHA256 content-addressed keys
2. **Quota First**: Всегда проверять квоту перед загрузкой
3. **Compression**: Обязательный gzip для JSON/HTML
4. **TTL-based Cleanup**: Агрессивная очистка старых данных
5. **Trace Propagation**: trace_id через все события и логи
6. **Graceful Degradation**: OCR fallback при quota exhausted

## Следующие шаги

1. Запустить миграцию БД: `cd api && alembic upgrade head`
2. Запустить services: `docker-compose up -d`
3. Протестировать с реальными credentials
4. Настроить Grafana dashboards
5. Определить SLO и alerting rules

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28

