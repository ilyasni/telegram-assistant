# Опциональные улучшения: Реализация завершена

**Дата**: 2025-01-21  
**Context7**: Реализация опциональных улучшений с использованием best practices

## ✅ Выполненные задачи

### 1. Оптимизации в vision_analysis_task.py

**Файл**: `api/worker/tasks/vision_analysis_task.py`

#### 1.1. Получение channel_username из БД

**Реализация**:
- ✅ Объединение запроса для получения `tenant_id` и `channel_username` из БД в одном SQL запросе
- ✅ Использование `JOIN channels c ON c.id = p.channel_id` для получения `c.username`
- ✅ Передача `channel_username` в `policy_engine.evaluate_media_for_vision()`

**Детали**:
```python
# Context7: Получение tenant_id и channel_username из БД для оптимизации
channel_info_result = await self.db.execute(
    text("""
        SELECT 
            COALESCE(...) as tenant_id,
            c.username as channel_username
        FROM posts p
        JOIN channels c ON c.id = p.channel_id
        ...
    """),
    {"post_id": post_id}
)

policy_result = self.policy_engine.evaluate_media_for_vision(
    media_file={...},
    channel_username=channel_username,  # Context7: получено из БД выше
    quota_exhausted=quota_exhausted
)
```

**Преимущества**:
- Снижение количества SQL запросов (один запрос вместо двух)
- Использование `channel_username` в политике Vision для более точной фильтрации
- Оптимизация производительности за счет JOIN вместо отдельных запросов

#### 1.2. Проверка quota_exhausted через budget_gate

**Реализация**:
- ✅ Проверка `budget_gate.check_budget()` один раз для оптимизации (вместо двух раз)
- ✅ Сохранение результата `budget_check` для повторного использования
- ✅ Передача `quota_exhausted` в `policy_engine.evaluate_media_for_vision()`

**Детали**:
```python
# Context7: Проверяем budget gate для определения quota_exhausted (один раз для оптимизации)
quota_exhausted = False
budget_check = None
if self.budget_gate:
    budget_check = await self.budget_gate.check_budget(
        tenant_id=tenant_id,
        estimated_tokens=1792
    )
    quota_exhausted = not budget_check.allowed

# Позже используем уже проверенный budget_check
if self.budget_gate:
    if budget_check is None:
        budget_check = await self.budget_gate.check_budget(...)
    if not budget_check.allowed:
        ...
```

**Преимущества**:
- Снижение количества вызовов `budget_gate.check_budget()` (один раз вместо двух)
- Использование кэшированного результата для проверки quota
- Оптимизация производительности за счет избежания повторных проверок

#### 1.3. Агрегация результатов от нескольких медиа

**Статус**: ✅ Уже реализовано в `_save_to_db()` (строки 1920-1950)

**Функционал**:
- ✅ Агрегация результатов от нескольких медиа в одном посте (`grouped_id`/album)
- ✅ Объединение `s3_keys` от всех медиа в `s3_keys_dict` и `s3_keys_list`
- ✅ Использование первого результата для формирования `vision_data`

**Детали**:
```python
# Context7: Агрегация результатов от нескольких медиа
first_result = analysis_results[0]["analysis"]
s3_keys_dict = {}
s3_keys_list = []
for r in analysis_results:
    sha256 = r.get("sha256")
    s3_key = r.get("s3_key")
    if sha256 and s3_key:
        s3_keys_dict["image"] = s3_key  # Основное изображение
        s3_keys_list.append({
            "sha256": sha256,
            "s3_key": s3_key,
            "analyzed_at": analyzed_at.isoformat()
        })

vision_data = {
    ...
    "s3_keys": s3_keys_dict,
    "s3_keys_list": s3_keys_list
}
```

**Преимущества**:
- Поддержка альбомов Telegram (несколько медиа в одном посте)
- Сохранение всех результатов анализа в одной записи `post_enrichment`
- Обратная совместимость через `s3_keys_list` для legacy потребителей

### 2. Storage quota tracking

**Файл**: `api/alembic/versions/20250122_add_tenant_storage_usage.py`

#### 2.1. Создание таблицы tenant_storage_usage

**Реализация**:
- ✅ Создание миграции для таблицы `tenant_storage_usage`
- ✅ Поля: `tenant_id`, `content_type`, `total_bytes`, `total_gb`, `objects_count`, `last_updated`, `created_at`
- ✅ Constraints для валидации данных (положительные значения, валидные content_type)
- ✅ Индексы для быстрого поиска по `tenant_id`, `content_type`, и композитный индекс

**Структура таблицы**:
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
```

**Индексы**:
- `idx_tenant_storage_tenant_id`: для поиска по tenant_id
- `idx_tenant_storage_content_type`: для фильтрации по content_type
- `idx_tenant_storage_last_updated`: для сортировки по времени обновления
- `idx_tenant_storage_tenant_content`: композитный индекс для быстрого поиска

#### 2.2. Мониторинг использования S3

**Статус**: ⏳ Требует реализации в StorageQuotaService

**Рекомендации**:
- Реализовать метод `update_tenant_usage()` в `StorageQuotaService` для обновления `tenant_storage_usage`
- Добавить периодическую задачу в `scheduler_tasks.py` для расчета использования
- Добавить Prometheus метрики для мониторинга использования S3 по tenant
- Интегрировать с `budget_gate` для контроля квот

**Пример реализации** (рекомендуется):
```python
async def update_tenant_usage(
    self,
    tenant_id: str,
    content_type: str,
    size_bytes: int,
    objects_count: int = 1
) -> None:
    """Обновление использования storage для tenant."""
    from sqlalchemy import text
    
    async with self.db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tenant_storage_usage (tenant_id, content_type, total_bytes, total_gb, objects_count)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (tenant_id, content_type)
            DO UPDATE SET
                total_bytes = tenant_storage_usage.total_bytes + $3,
                total_gb = (tenant_storage_usage.total_bytes + $3) / (1024.0 ^ 3),
                objects_count = tenant_storage_usage.objects_count + $5,
                last_updated = now()
            """,
            tenant_id, content_type, size_bytes, size_bytes / (1024 ** 3), objects_count
        )
```

## 📋 Next Steps (требуют реализации)

### 1. Storage quota tracking - полная реализация

**Требуется**:
- Добавить метод `update_tenant_usage()` в `StorageQuotaService` (worker версия)
- Добавить метод `get_tenant_usage()` для получения использования по tenant
- Создать периодическую задачу для расчета использования из S3
- Добавить Prometheus метрики `tenant_storage_usage_gb` с labels `[tenant_id, content_type]`
- Интегрировать с `budget_gate` для контроля квот перед загрузкой

**Рекомендации**:
- Использовать `asyncpg` pool для эффективных запросов
- Кэшировать результаты расчета на 15 минут (как в `get_bucket_usage()`)
- Периодическая задача каждые 6 часов для пересчета использования
- Использовать `UPSERT` (ON CONFLICT) для идемпотентности

### 2. Дополнительные оптимизации

**Рекомендации**:
- Добавить мониторинг использования S3 через Cloud.ru S3 API (если доступно)
- Реализовать алерты при приближении к лимитам
- Добавить dashboard в Grafana для визуализации использования storage
- Интегрировать с billing системой для учета использования

## 🎯 Статус

- ✅ **Оптимизации vision_analysis_task.py**: Все выполнены
- ⏳ **Storage quota tracking**: Миграция создана, требуется полная реализация

## ✅ Context7 Best Practices

- ✅ Оптимизация SQL запросов (JOIN вместо отдельных запросов)
- ✅ Кэширование результатов проверок (budget_gate)
- ✅ Агрегация результатов от нескольких медиа
- ✅ Использование Constraints для валидации данных
- ✅ Индексы для оптимизации запросов
- ✅ Идемпотентность через ON CONFLICT

## 📝 Использование

### Применение миграции

```bash
docker-compose exec api alembic upgrade head
```

### Проверка таблицы

```sql
SELECT * FROM tenant_storage_usage ORDER BY last_updated DESC LIMIT 10;
```

### Мониторинг через Prometheus

```promql
# Использование storage по tenant (после реализации метрик)
tenant_storage_usage_gb{tenant_id="...", content_type="media"}
```

