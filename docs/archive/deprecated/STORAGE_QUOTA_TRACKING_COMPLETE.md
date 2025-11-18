# Storage Quota Tracking: Реализация завершена

**Дата**: 2025-01-22  
**Context7**: Полная реализация tenant storage usage tracking с интеграцией в систему

## ✅ Выполненные задачи

### 1. Миграция для tenant_storage_usage таблицы

**Файл**: `api/alembic/versions/20250122_add_tenant_storage_usage.py`

**Реализация**:
- ✅ Создание таблицы `tenant_storage_usage` для отслеживания использования S3 storage по tenant
- ✅ Поля: `tenant_id`, `content_type`, `total_bytes`, `total_gb`, `objects_count`, `last_updated`, `created_at`
- ✅ Constraints для валидации данных (положительные значения, валидные content_type)
- ✅ Индексы для быстрого поиска по `tenant_id`, `content_type`, композитный индекс
- ✅ Unique constraint на `(tenant_id, content_type)` для идемпотентности

**Применение миграции**:
```bash
docker compose exec api alembic upgrade 20250122_tenant_storage
```

**Статус**: ✅ Миграция применена успешно

### 2. Методы в StorageQuotaService (worker версия)

**Файл**: `api/worker/services/storage_quota.py`

#### 2.1. `update_tenant_usage()`

**Реализация**:
- ✅ Обновление использования storage для tenant через UPSERT (ON CONFLICT)
- ✅ Идемпотентная операция: инкрементальное добавление `total_bytes` и `objects_count`
- ✅ Автоматическое вычисление `total_gb` из `total_bytes`
- ✅ Обновление Prometheus метрик `tenant_storage_usage_gb`

**Использование**:
```python
await quota_service.update_tenant_usage(
    tenant_id="tenant-uuid",
    content_type="media",
    size_bytes=1024 * 1024,  # 1 MB
    objects_count=1
)
```

#### 2.2. `get_tenant_usage()`

**Реализация**:
- ✅ Получение использования storage для tenant из БД
- ✅ Поддержка фильтрации по `content_type` (опционально)
- ✅ Возврат агрегированных данных по всем типам контента
- ✅ Обработка ошибок с возвратом пустых значений

**Использование**:
```python
# Получение использования для конкретного типа
usage = await quota_service.get_tenant_usage(tenant_id, "media")

# Получение использования для всех типов
usage = await quota_service.get_tenant_usage(tenant_id)
```

#### 2.3. `calculate_and_update_tenant_usage()`

**Реализация**:
- ✅ Расчет использования storage из S3 bucket для tenant
- ✅ Сканирование S3 по префиксу `{content_type}/t{tenant_id}/`
- ✅ Обновление БД с результатами расчета
- ✅ Обновление Prometheus метрик

**Использование**:
```python
result = await quota_service.calculate_and_update_tenant_usage(
    tenant_id="tenant-uuid",
    content_type="media"
)
```

### 3. Интеграция с check_quota_before_upload

**Файл**: `api/worker/services/storage_quota.py`

**Реализация**:
- ✅ Добавлена проверка tenant квоты через БД в `check_quota_before_upload()`
- ✅ Использование `get_tenant_usage()` для получения текущего использования
- ✅ Проверка `per_tenant_max_gb` лимита (по умолчанию 2.0 GB)
- ✅ Детальное логирование блокировок с метриками
- ✅ Fail-open: при ошибке проверки tenant квоты продолжаем с другими проверками

**Логика**:
```python
# Проверка 2: Tenant квота через БД
tenant_usage_result = await self.get_tenant_usage(tenant_id, content_type)
tenant_usage_gb = tenant_usage_result.get("total_gb", 0.0)
per_tenant_limit = self.limits.get("per_tenant_max_gb", 2.0)

if tenant_usage_gb + size_gb > per_tenant_limit:
    # Блокируем загрузку
    return QuotaCheckResult(allowed=False, reason="tenant_limit", ...)
```

### 4. Prometheus метрики

**Файл**: `api/worker/services/storage_quota.py`

**Реализация**:
- ✅ Добавлена метрика `tenant_storage_usage_gb` с labels `[tenant_id, content_type]`
- ✅ Обновление метрик при `update_tenant_usage()` и `calculate_and_update_tenant_usage()`
- ✅ Использование `Gauge` для хранения текущего значения

**Метрика**:
```python
tenant_storage_usage_gb = Gauge(
    'tenant_storage_usage_gb',
    'Storage usage per tenant by content type',
    ['tenant_id', 'content_type'],
    namespace='worker'
)
```

**Запрос Prometheus**:
```promql
# Использование storage по tenant
tenant_storage_usage_gb{tenant_id="...", content_type="media"}

# Общее использование всех tenant по типам
sum(tenant_storage_usage_gb) by (content_type)
```

### 5. Периодическая задача для расчета использования

**Файл**: `api/tasks/scheduler_tasks.py`

**Реализация**:
- ✅ Добавлена функция `calculate_tenant_storage_usage_task()`
- ✅ Периодическое выполнение каждые 6 часов через APScheduler
- ✅ Итерация по всем tenant из таблицы `tenants`
- ✅ Расчет использования для всех типов контента (media, vision, crawl)
- ✅ Использование worker версии `StorageQuotaService` для async методов
- ✅ Создание asyncpg pool для доступа к БД
- ✅ Детальное логирование результатов расчета

**Расписание**:
```python
scheduler.add_job(
    calculate_tenant_storage_usage_task,
    trigger=CronTrigger(hour="*/6"),  # Каждые 6 часов
    id="calculate_tenant_storage_usage",
    name="Calculate tenant storage usage from S3",
    replace_existing=True
)
```

**Логика**:
1. Получение списка всех tenant из БД
2. Для каждого tenant и типа контента:
   - Сканирование S3 bucket по префиксу `{content_type}/t{tenant_id}/`
   - Расчет общего размера и количества объектов
   - Обновление БД через `calculate_and_update_tenant_usage()`
   - Обновление Prometheus метрик
3. Логирование результатов (успешно обработано, ошибки)

## 📋 Структура таблицы

```sql
CREATE TABLE tenant_storage_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    content_type VARCHAR(20) NOT NULL,  -- media|vision|crawl
    total_bytes BIGINT NOT NULL DEFAULT 0,
    total_gb REAL NOT NULL DEFAULT 0.0,
    objects_count INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_tenant_storage_tenant_content UNIQUE (tenant_id, content_type),
    CONSTRAINT chk_tenant_storage_bytes_positive CHECK (total_bytes >= 0),
    CONSTRAINT chk_tenant_storage_gb_positive CHECK (total_gb >= 0.0),
    CONSTRAINT chk_tenant_storage_objects_positive CHECK (objects_count >= 0),
    CONSTRAINT chk_tenant_storage_content_type CHECK (content_type IN ('media', 'vision', 'crawl'))
);

CREATE INDEX idx_tenant_storage_tenant_id ON tenant_storage_usage (tenant_id);
CREATE INDEX idx_tenant_storage_content_type ON tenant_storage_usage (content_type);
CREATE INDEX idx_tenant_storage_last_updated ON tenant_storage_usage (last_updated);
CREATE INDEX idx_tenant_storage_tenant_content ON tenant_storage_usage (tenant_id, content_type);
```

## 🎯 Использование

### 1. Применение миграции

```bash
docker compose exec api alembic upgrade 20250122_tenant_storage
```

### 2. Проверка таблицы

```sql
SELECT * FROM tenant_storage_usage ORDER BY last_updated DESC LIMIT 10;
```

### 3. Мониторинг через Prometheus

```promql
# Использование storage по tenant и типу контента
tenant_storage_usage_gb{tenant_id="...", content_type="media"}

# Общее использование всех tenant
sum(tenant_storage_usage_gb)

# Использование по типам контента
sum(tenant_storage_usage_gb) by (content_type)

# Top 10 tenant по использованию storage
topk(10, tenant_storage_usage_gb)
```

### 4. Использование в коде

**Обновление использования при загрузке файла**:
```python
# После успешной загрузки файла в S3
await storage_quota_service.update_tenant_usage(
    tenant_id=tenant_id,
    content_type="media",
    size_bytes=file_size,
    objects_count=1
)
```

**Проверка квоты перед загрузкой**:
```python
# Автоматически проверяет tenant квоту через get_tenant_usage()
result = await storage_quota_service.check_quota_before_upload(
    tenant_id=tenant_id,
    size_bytes=file_size,
    content_type="media"
)

if not result.allowed:
    logger.warning("Upload blocked", reason=result.reason, tenant_usage_gb=result.tenant_usage_gb)
```

**Получение использования для tenant**:
```python
# Получение использования для конкретного типа
usage = await storage_quota_service.get_tenant_usage(tenant_id, "media")
print(f"Media usage: {usage['total_gb']:.2f} GB ({usage['objects_count']} objects)")

# Получение использования для всех типов
usage = await storage_quota_service.get_tenant_usage(tenant_id)
print(f"Total usage: {usage['total_gb']:.2f} GB")
for content_type, data in usage['by_type'].items():
    print(f"  {content_type}: {data['total_gb']:.2f} GB ({data['objects_count']} objects)")
```

### 5. Ручной запуск периодической задачи

```python
# В Python shell или скрипте
from api.tasks.scheduler_tasks import calculate_tenant_storage_usage_task
import asyncio

asyncio.run(calculate_tenant_storage_usage_task())
```

## ✅ Context7 Best Practices

- ✅ **Идемпотентность**: UPSERT операции через ON CONFLICT для предотвращения дублей
- ✅ **Fail-open**: При ошибке проверки tenant квоты продолжаем с другими проверками
- ✅ **Детальное логирование**: Все операции логируются с `tenant_id`, `content_type`, `size_bytes`
- ✅ **Prometheus метрики**: Мониторинг использования storage по tenant и типам контента
- ✅ **Периодическая синхронизация**: Автоматический пересчет использования из S3 каждые 6 часов
- ✅ **Индексы для производительности**: Оптимизация запросов по `tenant_id`, `content_type`, композитный индекс
- ✅ **Constraints для валидации**: Гарантия целостности данных (положительные значения, валидные типы)
- ✅ **Async/await**: Использование async методов для эффективной работы с БД и S3

## 📊 Метрики и мониторинг

### Prometheus метрики

1. **`tenant_storage_usage_gb`** (Gauge):
   - Labels: `tenant_id`, `content_type`
   - Описание: Использование storage по tenant и типу контента
   - Обновление: При `update_tenant_usage()` и `calculate_and_update_tenant_usage()`

2. **`storage_quota_violations_total`** (Counter):
   - Labels: `tenant_id`, `reason` (включая `tenant_limit`)
   - Описание: Попытки превышения квот (включая tenant квоту)

### Grafana Dashboard (рекомендуется)

**Дашборд для мониторинга storage usage**:
- График общего использования storage по типам контента
- Топ 10 tenant по использованию storage
- Алерты при приближении к лимитам (`per_tenant_max_gb`)
- История изменений использования по tenant

## 🔄 Интеграция с существующими сервисами

### 1. Vision Analysis Task

**Рекомендация**: Добавить вызов `update_tenant_usage()` после успешной загрузки медиа в S3:

```python
# В vision_analysis_task.py после успешной загрузки медиа
if self.storage_quota:
    await self.storage_quota.update_tenant_usage(
        tenant_id=tenant_id,
        content_type="vision",
        size_bytes=media_file.size_bytes,
        objects_count=1
    )
```

### 2. S3 Storage Service

**Рекомендация**: Интегрировать `update_tenant_usage()` в методы загрузки файлов:

```python
# В s3_storage.py после успешной загрузки
if self.storage_quota:
    await self.storage_quota.update_tenant_usage(
        tenant_id=tenant_id,
        content_type=content_type,
        size_bytes=file_size,
        objects_count=1
    )
```

### 3. Crawl Service

**Рекомендация**: Добавить отслеживание использования для crawl контента:

```python
# После успешной загрузки crawl результатов
await storage_quota_service.update_tenant_usage(
    tenant_id=tenant_id,
    content_type="crawl",
    size_bytes=crawl_result_size,
    objects_count=1
)
```

## 📝 Следующие шаги (опционально)

1. **Интеграция в существующие сервисы**:
   - Добавить вызовы `update_tenant_usage()` в Vision Analysis Task
   - Интегрировать в S3 Storage Service при загрузке файлов
   - Добавить отслеживание для Crawl Service

2. **Алерты и уведомления**:
   - Настройка алертов в Prometheus при приближении к лимитам
   - Уведомления tenant при превышении квоты
   - Автоматическая очистка старых файлов при превышении лимита

3. **Dashboard в Grafana**:
   - Визуализация использования storage по tenant
   - История изменений использования
   - Прогнозирование использования на основе трендов

4. **Оптимизация производительности**:
   - Кэширование результатов расчета использования
   - Batch обновления при массовых загрузках
   - Инкрементальное обновление вместо полного пересчета

## ✅ Итоги

- ✅ Миграция применена успешно
- ✅ Все методы реализованы в `StorageQuotaService` (worker версия)
- ✅ Интеграция с `check_quota_before_upload()` для контроля tenant квот
- ✅ Prometheus метрики добавлены и обновляются автоматически
- ✅ Периодическая задача добавлена в scheduler (каждые 6 часов)
- ✅ Детальное логирование всех операций
- ✅ Обработка ошибок с fail-open стратегией

**Все задачи выполнены согласно Context7 best practices.**

