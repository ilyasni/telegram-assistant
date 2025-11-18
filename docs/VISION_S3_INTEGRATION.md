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
- **`worker/services/experiment_manager.py`**: Пер-tenant эксперименты (Wave A/B/C, Context7 A/B контроль)

#### Token Estimation (Context7)

- `worker/ai_adapters/gigachat_vision.py` вызывает `/tokens/count` перед основным запросом Vision, чтобы прогнозировать расход токенов и логировать метрику `vision_tokens_estimated_total`.
- Результат оценки сохраняется в `analysis.context.tokens_estimated`, что позволяет сравнивать предсказанные и фактические значения (`response.usage.total_tokens`).
- При ошибке `tokens_count` пайплайн деградирует корректно (лог + метрика без инкремента), анализ продолжается.

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

### Storage Quota Management API

**Base URL**: `/api/storage`

Все endpoints требуют JWT токен в заголовке `Authorization: Bearer <token>`.

#### GET /api/storage/quota

Получение текущего использования storage и квот.

**Response 200**:
```json
{
  "total_gb": 12.5,
  "limit_gb": 15.0,
  "usage_percent": 83.33,
  "by_type": {
    "media": 8.2,
    "vision": 2.1,
    "crawl": 2.2
  },
  "emergency_threshold_gb": 14.0,
  "last_updated": "2025-01-28T12:00:00Z"
}
```

**Example**:
```bash
curl -X GET "http://localhost:8000/api/storage/quota" \
  -H "Authorization: Bearer <token>"
```

#### GET /api/storage/usage/{tenant_id}

Получение использования storage для конкретного tenant.

**Path Parameters**:
- `tenant_id` (string) - UUID tenant

**Response 200**:
```json
{
  "tenant_id": "877193ef-be80-4977-aaeb-8009c3d772ee",
  "usage_gb": 1.5,
  "limit_gb": 2.0,
  "usage_percent": 75.0,
  "last_updated": "2025-01-28T12:00:00Z"
}
```

#### POST /api/storage/quota/check

Проверка квоты перед загрузкой файла.

**Request Body**:
```json
{
  "tenant_id": "877193ef-be80-4977-aaeb-8009c3d772ee",
  "size_bytes": 1024000,
  "content_type": "media"
}
```

**Response 200**:
```json
{
  "allowed": true,
  "reason": "quota_available",
  "current_usage_gb": 1.5,
  "tenant_limit_gb": 2.0,
  "bucket_usage_gb": 12.5
}
```

**Response 200 (quota exceeded)**:
```json
{
  "allowed": false,
  "reason": "tenant_limit_exceeded",
  "current_usage_gb": 2.0,
  "tenant_limit_gb": 2.0,
  "bucket_usage_gb": 12.5
}
```

#### POST /api/storage/cleanup

Запуск emergency cleanup для освобождения места.

**Request Body**:
```json
{
  "force": false,
  "target_free_gb": 12.0
}
```

**Response 200**:
```json
{
  "cleanup_started": true,
  "target_free_gb": 12.0,
  "estimated_duration_seconds": 300,
  "message": "Cleanup task queued"
}
```

#### GET /api/storage/stats

Получение детальной статистики storage: метрики, история, тренды.

**Response 200**:
```json
{
  "current": {
    "total_gb": 12.5,
    "limit_gb": 15.0,
    "usage_percent": 83.33,
    "by_type": {
      "media": 8.2,
      "vision": 2.1,
      "crawl": 2.2
    }
  },
  "limits": {
    "total_bucket_gb": 15.0,
    "emergency_threshold_gb": 14.0,
    "per_tenant_max_gb": 2.0,
    "media_max_gb": 10.0,
    "vision_max_gb": 2.0,
    "crawl_max_gb": 2.0
  },
  "policies": {
    "media_ttl_days": 30,
    "vision_ttl_days": 14,
    "crawl_ttl_days": 7,
    "compression_required": true
  },
  "last_updated": "2025-01-28T12:00:00Z"
}
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

