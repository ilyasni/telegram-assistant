# Руководство по развертыванию пайплайна альбомов

**Дата**: 2025-01-30  
**Версия**: 1.0

## Контекст

Пайплайн обработки Telegram альбомов полностью реализован по Context7 best practices. Все 4 фазы завершены и готовы к использованию.

---

## 📋 Pre-deployment Checklist

### База данных

- [ ] Применена миграция `004_add_album_fields.sql`
- [ ] Проверены новые поля в `media_groups` и `media_group_items`
- [ ] Проверено наличие `media_objects.id` (UUID)

**Команда для проверки:**
```bash
psql $DATABASE_URL -c "
SELECT 
    column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'media_groups' 
AND column_name IN ('caption_text', 'cover_media_id', 'posted_at', 'meta')
ORDER BY column_name;
"
```

### Redis Streams

- [ ] Проверено наличие стримов `stream:albums:parsed` и `stream:album:assembled`
- [ ] Созданы consumer groups (автоматически при первом запуске)

**Команда для проверки:**
```bash
redis-cli XINFO STREAM stream:albums:parsed
redis-cli XINFO STREAM stream:album:assembled
```

### Environment Variables

- [ ] `REDIS_URL` — настроен и доступен
- [ ] `DATABASE_URL` — настроен с asyncpg драйвером
- [ ] `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME` — для сохранения vision summary (опционально)
- [ ] `QDRANT_URL` — для индексации с album_id
- [ ] `NEO4J_URL` — для создания узлов альбомов (опционально)

---

## 🚀 Deployment Steps

### 1. Применение миграции БД

```bash
# Проверка текущего состояния
psql $DATABASE_URL -c "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;"

# Применение миграции
psql $DATABASE_URL -f telethon-ingest/migrations/004_add_album_fields.sql

# Проверка результата
psql $DATABASE_URL -c "\d media_groups"
psql $DATABASE_URL -c "\d media_group_items"
```

### 2. Обновление Worker

Worker автоматически запустит `album_assembler` task при старте через supervisor.

**Проверка логов:**
```bash
docker logs worker | grep -i "album"
# Должно быть: "AlbumAssemblerTask created and starting..."
```

### 3. Проверка метрик

```bash
# Проверка доступности метрик
curl http://localhost:8001/metrics | grep album

# Ожидаемые метрики:
# - albums_parsed_total
# - albums_assembled_total
# - album_assembly_lag_seconds
# - album_items_count_gauge
# - album_vision_summary_size_bytes
# - album_aggregation_duration_ms
```

### 4. Проверка Health Checks

```bash
# Проверка health check album_assembler_task
curl http://localhost:8000/health/detailed | jq '.tasks.album_assembler'

# Ожидаемый ответ:
# {
#   "status": "healthy",
#   "redis_connected": true,
#   "running": true,
#   "albums_in_progress": 0,
#   "backlog_size": 0
# }
```

### 5. Настройка Prometheus Alerts

Проверьте, что файл `prometheus/alerts.yml` содержит группу `album_pipeline`:

```bash
grep -A 5 "album_pipeline" prometheus/alerts.yml
```

**Перезагрузка конфигурации Prometheus:**
```bash
# Если используется reload endpoint
curl -X POST http://localhost:9090/-/reload

# Или перезапуск контейнера
docker compose restart prometheus
```

### 6. Импорт Grafana Dashboard

1. Откройте Grafana (обычно `http://localhost:3000`)
2. Перейдите в Dashboards → Import
3. Загрузите файл `grafana/dashboards/album_pipeline.json`
4. Выберите Prometheus datasource
5. Сохраните dashboard

---

## 🧪 Тестирование

### Unit тесты

```bash
# Тест схем событий
python3 -c "
from worker.events.schemas import AlbumParsedEventV1, AlbumAssembledEventV1
print('✅ Схемы событий импортируются корректно')
"
```

### Integration тесты

```bash
# Полный E2E тест
python3 scripts/test_album_pipeline_full.py

# Тест фильтрации Qdrant
python3 scripts/test_album_qdrant_filtering.py

# E2E тест (pytest)
pytest tests/e2e/test_album_pipeline_e2e.py -v
```

### Проверка на реальных данных

```bash
# Создание тестового альбома
python3 scripts/create_test_album.py

# Проверка в БД
psql $DATABASE_URL -c "
SELECT mg.id, mg.grouped_id, mg.items_count, mg.caption_text
FROM media_groups mg
ORDER BY mg.created_at DESC
LIMIT 5;
"
```

---

## 📊 Мониторинг после deployment

### 1. Метрики Prometheus

**Основные метрики для отслеживания:**

```promql
# Скорость обработки альбомов
rate(albums_parsed_total[5m])
rate(albums_assembled_total[5m])

# Задержка сборки (p95)
histogram_quantile(0.95, rate(album_assembly_lag_seconds_bucket[5m]))

# Активные альбомы в процессе
count(album_items_count_gauge{status="pending"})
```

### 2. Логи

**Проверка работы album_assembler_task:**
```bash
docker logs worker 2>&1 | grep -i "album" | tail -50
```

**Ключевые события в логах:**
- `AlbumAssemblerTask initialized` — успешная инициализация
- `Album assembled and event emitted` — альбом собран
- `Album vision summary saved to S3` — summary сохранён в S3
- `Album enrichment saved to DB` — enrichment сохранён в БД

### 3. Redis Streams

**Проверка активности стримов:**
```bash
# Количество событий albums.parsed
redis-cli XLEN stream:albums:parsed

# Количество событий album.assembled
redis-cli XLEN stream:album:assembled

# Pending сообщения
redis-cli XPENDING stream:albums:parsed album_assemblers
```

---

## 🔧 Troubleshooting

### Проблема: Album Assembler Task не запускается

**Симптомы:**
- В логах нет сообщений о `AlbumAssemblerTask`
- Метрики `albums_parsed_total` отсутствуют

**Решение:**
1. Проверьте логи worker: `docker logs worker`
2. Проверьте импорты: `python3 -c "from worker.tasks.album_assembler_task import AlbumAssemblerTask"`
3. Проверьте Redis подключение: `redis-cli ping`
4. Проверьте DATABASE_URL (должен быть с `postgresql+asyncpg://`)

### Проблема: Альбомы не собираются

**Симптомы:**
- События `albums.parsed` поступают, но `album.assembled` нет
- Метрика `albums_assembled_total` не растёт

**Решение:**
1. Проверьте, что `vision_analysis_task` обрабатывает события `posts.vision.uploaded`
2. Проверьте Redis state: `redis-cli KEYS "album:state:*"`
3. Проверьте логи `album_assembler_task` на ошибки
4. Проверьте backlog: `redis-cli XPENDING stream:posts:vision:analyzed album_assemblers`

### Проблема: Vision summary не сохраняется в S3

**Симптомы:**
- В логах: `S3 service not available for album assembler`
- Отсутствуют файлы в S3 bucket

**Решение:**
1. Проверьте S3 credentials: `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`
2. Проверьте доступность S3: `curl $S3_ENDPOINT_URL`
3. Проверьте права доступа к bucket

### Проблема: Алерты не срабатывают

**Симптомы:**
- Метрики есть, но алерты не приходят

**Решение:**
1. Проверьте конфигурацию Prometheus: `prometheus/alerts.yml`
2. Проверьте, что алерты загружены: `curl http://localhost:9090/api/v1/alerts`
3. Проверьте правила: `curl http://localhost:9090/api/v1/rules`
4. Проверьте пороги алертов (возможно, они слишком высокие)

---

## 🔄 Rollback Plan

### Откат миграции БД

**Внимание:** Откат миграции может привести к потере данных. Используйте с осторожностью.

```sql
-- Удаление новых полей (если нужно)
ALTER TABLE media_groups 
    DROP COLUMN IF EXISTS caption_text,
    DROP COLUMN IF EXISTS cover_media_id,
    DROP COLUMN IF EXISTS posted_at,
    DROP COLUMN IF EXISTS meta;

ALTER TABLE media_group_items
    DROP COLUMN IF EXISTS media_object_id,
    DROP COLUMN IF EXISTS media_kind,
    DROP COLUMN IF EXISTS sha256,
    DROP COLUMN IF EXISTS meta;

-- Удаление UUID из media_objects (если был добавлен)
ALTER TABLE media_objects DROP COLUMN IF EXISTS id;
```

### Отключение Album Assembler Task

Если нужно временно отключить задачу:

```python
# В worker/run_all_tasks.py закомментировать:
# supervisor.register_task(TaskConfig(
#     name="album_assembler",
#     task_func=create_album_assembler_task,
#     ...
# ))
```

---

## 📈 Performance Tuning

### Redis State TTL

По умолчанию TTL для состояний альбомов: 24 часа (86400 секунд).

**Изменение TTL:**
```python
# В worker/tasks/album_assembler_task.py
self.state_ttl = 86400  # Изменить на нужное значение
```

### Batch Size для Vision Analysis

Если альбомы собираются медленно, проверьте скорость обработки vision analysis:

```bash
# Проверка rate vision analysis
curl http://localhost:8001/metrics | grep vision_analysis | grep rate
```

### Qdrant Filtering Performance

Для оптимизации фильтрации по `album_id` в Qdrant, создайте индекс:

```python
# В worker/tasks/indexing_task.py можно добавить создание индекса:
await qdrant_client.create_payload_index(
    collection_name=collection_name,
    field_name="album_id",
    field_schema=models.PayloadSchemaType.INTEGER
)
```

---

## 📚 Дополнительные ресурсы

- `docs/ALBUM_PIPELINE_ARCHITECTURE.md` — детальная архитектура
- `docs/ALBUM_PIPELINE_PHASES_SUMMARY.md` — сводка по фазам
- `docs/ALBUM_PIPELINE_INTEGRATION_COMPLETE.md` — документация интеграции
- `docs/examples/qdrant_album_filtering_example.py` — примеры использования

---

**Готово к развертыванию!** 🚀

