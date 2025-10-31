# E2E Тестирование Vision + S3 Пайплайна

**Версия**: 1.0 | **Дата**: 2025-01-28

## Контекст

Руководство по полному end-to-end тестированию Vision анализа медиа через GigaChat API с интеграцией S3, Budget Gate, и Neo4j.

## Предварительные требования

1. **Environment Variables**:
   ```bash
   FEATURE_VISION_ENABLED=true
   GIGACHAT_CLIENT_ID=...
   GIGACHAT_CLIENT_SECRET=...
   S3_ACCESS_KEY_ID=...
   S3_SECRET_ACCESS_KEY=...
   ```

2. **Запущенные сервисы**:
   - Worker (с Vision task через supervisor)
   - API
   - Redis
   - PostgreSQL
   - S3 (Cloud.ru)

3. **Пост с медиа в БД** (опционально, можно использовать тестовый UUID)

---

## Быстрый E2E тест

### Автоматический скрипт

```bash
# Используя реальный пост из БД
docker exec worker python3 /opt/telegram-assistant/scripts/test_vision_e2e.py

# С указанным post_id
docker exec worker python3 /opt/telegram-assistant/scripts/test_vision_e2e.py --post-id <uuid>
```

Скрипт выполняет:
1. ✅ Проверку S3 квот
2. ✅ Создание VisionUploadedEventV1
3. ✅ Публикацию в stream:posts:vision:uploaded
4. ✅ Ожидание обработки (90 секунд)
5. ✅ Проверку результатов в БД (post_enrichment)
6. ✅ Проверку через Vision API endpoint
7. ✅ Проверку Redis Streams состояния

---

## Ручное тестирование

### Шаг 1: Проверка Vision Worker

```bash
# Проверка, что Vision task зарегистрирован в supervisor
docker compose logs worker | grep -E "(vision|supervisor|Registered task)"

# Должна быть строка:
# "Vision Analysis task registered with supervisor"
```

### Шаг 2: Проверка Feature Flag

```bash
# Внутри worker контейнера
docker exec worker python3 << 'EOF'
from feature_flags import feature_flags
print(f"Vision enabled: {feature_flags.vision_enabled}")
EOF
```

### Шаг 3: Создание тестового события

```python
# Внутри worker контейнера
import asyncio
from worker.event_bus import EventPublisher
from worker.events.schemas import VisionUploadedEventV1, MediaFile
from datetime import datetime, timezone
import uuid
import hashlib
import os

async def test():
    post_id = "your-post-uuid"
    tenant_id = os.getenv("S3_DEFAULT_TENANT_ID")
    trace_id = f"test_{uuid.uuid4().hex[:16]}"
    
    sha256 = hashlib.sha256(f"test_{post_id}".encode()).hexdigest()
    media = MediaFile(
        sha256=sha256,
        s3_key=f"media/{tenant_id}/{sha256[:2]}/{sha256}.jpg",
        mime_type="image/jpeg",
        size_bytes=512000,
        telegram_file_id="test_file_id"
    )
    
    event = VisionUploadedEventV1(
        schema_version="v1",
        trace_id=trace_id,
        idempotency_key=f"{tenant_id}:{post_id}:{sha256}",
        tenant_id=tenant_id,
        post_id=post_id,
        channel_id="channel-uuid",
        media_files=[media],
        uploaded_at=datetime.now(timezone.utc)
    )
    
    redis_client = await redis.from_url("redis://redis:6379", decode_responses=False)
    publisher = EventPublisher(redis_client)
    message_id = await publisher.publish_event("posts.vision.uploaded", event)
    print(f"Event published: {message_id}")

asyncio.run(test())
```

### Шаг 4: Мониторинг обработки

```bash
# Логи worker с фильтром по trace_id
docker compose logs -f worker | grep "e2e_test_xxxxx"

# Метрики Prometheus
curl http://localhost:8001/metrics | grep vision

# Redis Stream состояние
docker exec redis redis-cli XLEN stream:posts:vision:uploaded
docker exec redis redis-cli XINFO GROUPS stream:posts:vision:uploaded
```

### Шаг 5: Проверка результатов

#### Через БД

```sql
SELECT 
    pe.vision_analyzed_at,
    pe.vision_provider,
    pe.vision_model,
    pe.vision_is_meme,
    pe.vision_classification,
    pe.vision_tokens_used,
    pe.s3_vision_keys,
    pe.s3_media_keys
FROM post_enrichment pe
WHERE pe.post_id = 'your-post-uuid';
```

#### Через API

```bash
curl -H "X-Trace-ID: test_xxxxx" \
     http://localhost:8000/api/v1/vision/posts/{post_id} | jq
```

#### Через Diagnostic CLI

```bash
docker exec worker python3 -m worker.scripts.diag_vision --post-id {post_id}
```

---

## Проверка компонентов

### 1. S3 Storage

```bash
# Проверка квот
curl http://localhost:8000/api/v1/storage/quota | jq

# Статистика
curl http://localhost:8000/api/v1/storage/stats | jq
```

### 2. Vision Policy Engine

```bash
# Проверка конфигурации
docker exec worker cat /app/config/vision_policy.yml
```

### 3. Budget Gate

```bash
# Метрики budget
curl http://localhost:8001/metrics | grep budget
```

### 4. Neo4j Integration

```bash
# Проверка ImageContent nodes
docker exec neo4j cypher-shell -u neo4j -p changeme \
  "MATCH (img:ImageContent) RETURN img LIMIT 5"
```

---

## Диагностика проблем

### Vision Worker не запущен

**Симптомы:**
- Нет логов "Vision Analysis task started"
- Stream length растёт, но события не обрабатываются

**Решение:**
1. Проверить `FEATURE_VISION_ENABLED=true` в .env
2. Проверить наличие GigaChat credentials
3. Проверить логи: `docker compose logs worker | grep -i vision`

### Ошибки Budget Gate

**Симптомы:**
- Ошибка "Budget exhausted" в логах
- `vision_budget_gate_blocks_total` увеличивается

**Решение:**
1. Проверить `VISION_MAX_DAILY_TOKENS`
2. Сбросить бюджет: `redis-cli DEL budget:tenant:{tenant_id}:*`

### Ошибки S3 Quota

**Симптомы:**
- Ошибка "Quota exceeded" при загрузке
- `storage_quota_violations_total` увеличивается

**Решение:**
1. Проверить использование: `curl /api/v1/storage/quota`
2. Запустить cleanup: `curl -X POST /api/v1/storage/cleanup`

### Vision Analysis не сохраняется в БД

**Симптомы:**
- Событие обработано, но `post_enrichment.vision_analyzed_at` = NULL

**Решение:**
1. Проверить логи worker на ошибки БД
2. Проверить наличие записи в `post_enrichment` для post_id
3. Проверить права доступа к БД

---

## Метрики для мониторинга

### Prometheus Metrics

```promql
# Request rate
rate(vision_analysis_requests_total[5m])

# Latency p95
histogram_quantile(0.95, rate(vision_analysis_duration_seconds_bucket[5m]))

# Error rate
rate(vision_analysis_requests_total{status="error"}[5m]) /
rate(vision_analysis_requests_total[5m])

# Budget usage
vision_budget_usage_gauge{tenant_id="..."}

# Storage usage
storage_bucket_usage_gb{content_type="vision"}
```

### Redis Stream Metrics

```bash
# Pending events
redis-cli XPENDING stream:posts:vision:uploaded vision_workers

# Stream length
redis-cli XLEN stream:posts:vision:uploaded
```

---

## Checklist E2E теста

- [ ] Vision worker запущен (supervisor logs)
- [ ] Feature flag `vision_enabled = true`
- [ ] GigaChat credentials настроены
- [ ] S3 credentials настроены и квота доступна
- [ ] Событие опубликовано в stream
- [ ] Vision worker обработал событие (логи)
- [ ] Результаты сохранены в `post_enrichment`
- [ ] Vision API endpoint возвращает данные
- [ ] Метрики Prometheus обновлены
- [ ] Neo4j ImageContent node создан (если включен Neo4j)
- [ ] Trace ID корректно пропагируется во всех логах

---

## Пример успешного E2E теста

```bash
$ docker exec worker python3 /opt/telegram-assistant/scripts/test_vision_e2e.py

======================================================================
🧪 E2E ТЕСТ VISION + S3 ПАЙПЛАЙНА
======================================================================

📋 Параметры теста:
  Post ID: 123e4567-e89b-12d3-a456-426614174000
  Trace ID: e2e_test_abc123

📊 Шаг 1: Проверка S3 квот...
  ✅ Квота доступна (2.34 GB / 2.00 GB)

📤 Шаг 2: Создание VisionUploadedEventV1...
  Media SHA256: def456...
  S3 Key: media/tenant/def/def456....jpg

🚀 Шаг 3: Публикация в stream:posts:vision:uploaded...
  ✅ Событие опубликовано: 1727654321000-0
  Stream length: 1 messages

⏳ Шаг 4: Ожидание обработки Vision worker (90 секунд)...
  ... ожидание (15 секунд)
  ... ожидание (30 секунд)
  ... ожидание (45 секунд)

🔍 Шаг 5: Проверка результатов в БД...
  ✅ Vision анализ выполнен!
    Analyzed At: 2025-01-28 12:34:56+00:00
    Provider: gigachat
    Model: GigaChat-Pro
    Is Meme: true
    Tokens Used: 1320

🔍 Шаг 6: Проверка через Vision API...
  ✅ API endpoint работает
    Provider: gigachat
    Is Meme: true

✅ E2E ТЕСТ ЗАВЕРШЁН
```

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28

