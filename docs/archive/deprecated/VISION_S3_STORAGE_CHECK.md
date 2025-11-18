# Vision & S3 Storage: Результаты проверки

**Дата**: 2025-01-22  
**Context7**: Проверка интеграции Vision Analysis Task с S3 Storage и tenant storage tracking

## ✅ Проверки выполнены

### 1. Инициализация StorageQuotaService

**Статус**: ✅ Настроено корректно

**Проверка**:
- ✅ `StorageQuotaService` инициализируется в `create_vision_analysis_task()` с `db_pool` для async методов
- ✅ Используется worker версия `StorageQuotaService` (поддерживает `update_tenant_usage()`)
- ✅ Передается в `VisionAnalysisTask` через `__init__`

**Файл**: `api/worker/tasks/vision_analysis_task.py` (строки 3170-3185)

**Код**:
```python
# Context7: StorageQuotaService с db_pool для tenant usage tracking
storage_quota = StorageQuotaService(**init_params)
```

### 2. Оптимизации Vision Analysis Task

**Статус**: ✅ Реализованы корректно

#### 2.1. Получение channel_username из БД

**Проверка**:
- ✅ Объединенный SQL запрос для получения `tenant_id` и `channel_username` из БД (строки 493-519)
- ✅ Использование `JOIN channels c ON c.id = p.channel_id` для получения `c.username`
- ✅ Передача `channel_username` в `policy_engine.evaluate_media_for_vision()` (строка 650)

**Преимущества**:
- Снижение количества SQL запросов (один вместо двух)
- Использование `channel_username` в политике Vision для более точной фильтрации

#### 2.2. Проверка quota_exhausted через budget_gate

**Проверка**:
- ✅ Проверка `budget_gate.check_budget()` один раз для оптимизации (строки 636-643)
- ✅ Сохранение результата `budget_check` для повторного использования
- ✅ Передача `quota_exhausted` в `policy_engine.evaluate_media_for_vision()` (строка 651)

**Преимущества**:
- Снижение количества вызовов `budget_gate.check_budget()` (один раз вместо двух)
- Использование кэшированного результата для проверки quota

#### 2.3. Агрегация результатов от нескольких медиа

**Статус**: ✅ Уже реализовано в `_save_to_db()` (строки 1929-1959)

**Проверка**:
- ✅ Агрегация результатов от нескольких медиа в одном посте (`grouped_id`/album)
- ✅ Объединение `s3_keys` от всех медиа в `s3_keys_dict` и `s3_keys_list`

### 3. Сохранение Vision результатов в S3

**Статус**: ⚠️ Частично реализовано

**Проверка**:

#### 3.1. Сохранение в S3 через GigaChatVisionAdapter

**Файл**: `api/worker/ai_adapters/gigachat_vision.py` (строки 593-623)

**Проверка**:
- ✅ Vision результаты сохраняются в S3 через `s3_service.put_json()` (строка 596)
- ✅ Используется `build_vision_key()` для генерации S3 ключа (строка 285, предположительно)
- ✅ Префикс `vision/` используется для vision результатов
- ✅ Сжатие JSON включается через `compress=True`

**Код**:
```python
# Context7: Сохранение в S3 кэш (включая OCR данные)
if self.s3_service and cache_key:
    try:
        size_bytes = await self.s3_service.put_json(
            data={
                **analysis_result,
                "usage": usage_payload,
            },
            s3_key=cache_key,
            compress=True,
        )
        logger.debug("Vision result saved to S3 cache", ...)
    except Exception as e:
        logger.warning("Failed to save vision result to S3 cache", ...)
```

#### 3.2. Обновление tenant usage после сохранения в S3

**Статус**: ❌ НЕ РЕАЛИЗОВАНО

**Проблема**:
- ❌ После сохранения vision результатов в S3 через `s3_service.put_json()` **НЕ вызывается** `storage_quota.update_tenant_usage()`
- ❌ Tenant usage не обновляется для vision контента после сохранения в S3

**Место для исправления**:
- `api/worker/ai_adapters/gigachat_vision.py` (после строки 603, где вызывается `put_json()`)

**Рекомендация**:
```python
# После сохранения в S3
size_bytes = await self.s3_service.put_json(...)

# Context7: Обновление tenant usage для vision контента
if self.storage_quota and hasattr(self.storage_quota, 'update_tenant_usage'):
    try:
        await self.storage_quota.update_tenant_usage(
            tenant_id=tenant_id,
            content_type="vision",
            size_bytes=size_bytes,
            objects_count=1
        )
    except Exception as e:
        logger.warning("Failed to update tenant usage for vision", error=str(e))
```

#### 3.3. Проверка квоты перед сохранением в S3

**Статус**: ❌ НЕ РЕАЛИЗОВАНО

**Проблема**:
- ❌ Перед сохранением vision результатов в S3 **НЕ вызывается** `storage_quota.check_quota_before_upload()`
- ❌ Нет проверки tenant квоты перед сохранением vision результатов

**Место для исправления**:
- `api/worker/ai_adapters/gigachat_vision.py` (перед строкой 596, где вызывается `put_json()`)

**Рекомендация**:
```python
# Перед сохранением в S3
if self.storage_quota and hasattr(self.storage_quota, 'check_quota_before_upload'):
    # Оцениваем размер JSON (приблизительно)
    estimated_json_size = len(json.dumps(analysis_result, default=str).encode('utf-8'))
    
    quota_check = await self.storage_quota.check_quota_before_upload(
        tenant_id=tenant_id,
        size_bytes=estimated_json_size,
        content_type="vision"
    )
    
    if not quota_check.allowed:
        logger.warning(
            "Quota check blocked vision result save to S3",
            tenant_id=tenant_id,
            reason=quota_check.reason,
            tenant_usage_gb=quota_check.tenant_usage_gb
        )
        # Продолжаем без сохранения в S3 (но результат все равно возвращается)
        return analysis_result

# Сохранение в S3
size_bytes = await self.s3_service.put_json(...)
```

### 4. Интеграция StorageQuotaService в GigaChatVisionAdapter

**Статус**: ⚠️ Частично реализовано

**Проверка**:
- ❌ `GigaChatVisionAdapter` **НЕ получает** `storage_quota` в `__init__`
- ❌ `GigaChatVisionAdapter` **НЕ может** вызывать `check_quota_before_upload()` и `update_tenant_usage()`

**Проблема**:
- `GigaChatVisionAdapter` сохраняет vision результаты в S3 (строка 596), но не может обновлять tenant usage
- Нет доступа к `StorageQuotaService` из `GigaChatVisionAdapter`

**Рекомендация**:
1. Добавить `storage_quota: Optional[StorageQuotaService] = None` в `__init__` `GigaChatVisionAdapter`
2. Передать `storage_quota` из `create_vision_analysis_task()` в `GigaChatVisionAdapter`
3. Добавить проверку квоты и обновление tenant usage в `analyze_media()` метод

### 5. Префикс vision/ для Vision результатов

**Статус**: ✅ Используется корректно

**Проверка**:
- ✅ Метод `build_vision_key()` в `S3StorageService` использует префикс `vision/` (строка 254)
- ✅ Формат ключа: `vision/{tenant_id}/{sha256}_{provider}_{model}_v{schema_version}.json`
- ✅ Используется в `GigaChatVisionAdapter.analyze_media()` (строка 285, предположительно)

**Файл**: `api/services/s3_storage.py` (строки 242-254)

## ❌ Найденные проблемы

### Проблема 1: Не обновляется tenant usage после сохранения vision результатов

**Описание**:
- После сохранения vision результатов в S3 через `s3_service.put_json()` не вызывается `storage_quota.update_tenant_usage()`
- Tenant usage не отслеживается для vision контента

**Влияние**:
- Неточный учет использования storage для tenant
- Периодическая задача `calculate_tenant_storage_usage_task` пересчитывает из S3, но это происходит только каждые 6 часов
- Реальное использование может быть выше, чем показано в `tenant_storage_usage` таблице

**Решение**:
1. Передать `storage_quota` в `GigaChatVisionAdapter.__init__`
2. Добавить вызов `update_tenant_usage()` после успешного сохранения в S3

### Проблема 2: Нет проверки квоты перед сохранением vision результатов

**Описание**:
- Перед сохранением vision результатов в S3 не вызывается `storage_quota.check_quota_before_upload()`
- Нет проверки tenant квоты перед сохранением vision результатов

**Влияние**:
- Tenant может превысить лимит `per_tenant_max_gb` для vision контента
- Результаты сохраняются в S3 даже при превышении квоты

**Решение**:
1. Добавить проверку квоты перед сохранением в S3
2. Логировать предупреждения при блокировке сохранения
3. Продолжать обработку (возвращать результат), но не сохранять в S3 при превышении квоты

### Проблема 3: GigaChatVisionAdapter не имеет доступа к StorageQuotaService

**Описание**:
- `GigaChatVisionAdapter` не получает `storage_quota` в `__init__`
- Нет возможности вызывать методы `StorageQuotaService` из `GigaChatVisionAdapter`

**Влияние**:
- Невозможно проверить квоту и обновить tenant usage из `GigaChatVisionAdapter`

**Решение**:
1. Добавить `storage_quota: Optional[StorageQuotaService] = None` в `GigaChatVisionAdapter.__init__`
2. Передать `storage_quota` из `create_vision_analysis_task()` в `GigaChatVisionAdapter`

## 📋 Рекомендации по исправлению

### 1. Передача StorageQuotaService в GigaChatVisionAdapter

**Файл**: `api/worker/tasks/vision_analysis_task.py` (строка 3196)

**Изменение**:
```python
# Vision Adapter
vision_adapter = GigaChatVisionAdapter(
    credentials=vision_config["credentials"],
    scope=vision_config.get("scope", "GIGACHAT_API_PERS"),
    model=vision_config.get("model", "GigaChat-Pro"),
    base_url=vision_config.get("base_url"),
    s3_service=s3_service,
    budget_gate=budget_gate,
    storage_quota=storage_quota,  # Context7: Добавить для tenant usage tracking
    verify_ssl=vision_config.get("verify_ssl", False),
    timeout=vision_config.get("timeout", 600)
)
```

### 2. Добавление storage_quota в GigaChatVisionAdapter.__init__

**Файл**: `api/worker/ai_adapters/gigachat_vision.py` (строки 92-130)

**Изменение**:
```python
def __init__(
    self,
    credentials: str,
    scope: str = "GIGACHAT_API_PERS",
    model: str = "GigaChat-Pro",
    base_url: Optional[str] = None,
    s3_service: Optional[S3StorageService] = None,
    budget_gate: Optional[BudgetGateService] = None,
    storage_quota: Optional[StorageQuotaService] = None,  # Context7: Добавить для tenant usage tracking
    verify_ssl: bool = True,
    timeout: int = 600,
    preprocess_enabled: bool = True,
    roi_crop_enabled: bool = False,
    max_output_tokens: int = 4096
):
    # ...
    self.storage_quota = storage_quota  # Context7: Сохранить для использования
```

### 3. Проверка квоты и обновление tenant usage в analyze_media

**Файл**: `api/worker/ai_adapters/gigachat_vision.py` (строки 593-623)

**Изменение**:
```python
# Context7: Сохранение в S3 кэш (включая OCR данные)
if self.s3_service and cache_key:
    try:
        # Context7: Оценка размера JSON перед проверкой квоты
        import json
        estimated_json_size = len(json.dumps(analysis_result, default=str).encode('utf-8'))
        
        # Context7: Проверка квоты перед сохранением в S3
        if self.storage_quota and hasattr(self.storage_quota, 'check_quota_before_upload'):
            quota_check = await self.storage_quota.check_quota_before_upload(
                tenant_id=tenant_id,
                size_bytes=estimated_json_size,
                content_type="vision"
            )
            
            if not quota_check.allowed:
                logger.warning(
                    "Quota check blocked vision result save to S3",
                    sha256=sha256,
                    tenant_id=tenant_id,
                    reason=quota_check.reason,
                    tenant_usage_gb=quota_check.tenant_usage_gb,
                    trace_id=trace_id
                )
                # Продолжаем без сохранения в S3 (но результат все равно возвращается)
                # Это не критично - результат уже проанализирован
            else:
                # Сохранение в S3
                size_bytes = await self.s3_service.put_json(
                    data={
                        **analysis_result,
                        "usage": usage_payload,
                    },
                    s3_key=cache_key,
                    compress=True,
                )
                
                # Context7: Обновление tenant usage после успешного сохранения
                if self.storage_quota and hasattr(self.storage_quota, 'update_tenant_usage'):
                    try:
                        await self.storage_quota.update_tenant_usage(
                            tenant_id=tenant_id,
                            content_type="vision",
                            size_bytes=size_bytes,
                            objects_count=1
                        )
                        logger.debug(
                            "Tenant usage updated for vision result",
                            sha256=sha256,
                            tenant_id=tenant_id,
                            size_bytes=size_bytes,
                            trace_id=trace_id
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to update tenant usage for vision result",
                            sha256=sha256,
                            tenant_id=tenant_id,
                            error=str(e),
                            trace_id=trace_id
                        )
                
                logger.debug(
                    "Vision result saved to S3 cache",
                    sha256=sha256,
                    cache_key=cache_key,
                    size_bytes=size_bytes,
                    has_ocr=bool(analysis_result.get("ocr") and analysis_result["ocr"].get("text")),
                    ocr_text_length=len(analysis_result.get("ocr", {}).get("text", "")) if analysis_result.get("ocr") else 0,
                    usage=usage_payload,
                    trace_id=trace_id
                )
        else:
            # Сохранение в S3 без проверки квоты (fallback)
            size_bytes = await self.s3_service.put_json(
                data={
                    **analysis_result,
                    "usage": usage_payload,
                },
                s3_key=cache_key,
                compress=True,
            )
            
            logger.debug("Vision result saved to S3 cache (without quota check)", ...)
            
    except Exception as e:
        # Не критичная ошибка - логируем но продолжаем
        logger.warning(
            "Failed to save vision result to S3 cache",
            sha256=sha256,
            cache_key=cache_key,
            error=str(e),
            trace_id=trace_id
        )
```

## ✅ Что работает корректно

1. ✅ **Инициализация StorageQuotaService**: настроена корректно с `db_pool`
2. ✅ **Оптимизации Vision Analysis Task**: все реализованы (channel_username из БД, budget_gate проверка, агрегация результатов)
3. ✅ **Сохранение в S3**: vision результаты сохраняются в S3 через `put_json()` с правильным префиксом `vision/`
4. ✅ **Агрегация результатов**: поддержка альбомов и нескольких медиа в одном посте

## ❌ Что требует исправления

1. ❌ **Обновление tenant usage**: не вызывается после сохранения vision результатов в S3
2. ❌ **Проверка квоты**: не выполняется перед сохранением vision результатов в S3
3. ❌ **Доступ к StorageQuotaService**: `GigaChatVisionAdapter` не получает `storage_quota` в `__init__`

## 📊 Статус

- ✅ **Оптимизации**: Все реализованы
- ⚠️ **Интеграция с StorageQuotaService**: Частично (требует доработки)
- ❌ **Tenant usage tracking для vision**: Не реализовано (критично)
- ❌ **Quota checks для vision**: Не реализовано (критично)

## 🎯 Приоритет исправлений

1. **Высокий**: Передача `storage_quota` в `GigaChatVisionAdapter` и обновление tenant usage
2. **Высокий**: Добавление проверки квоты перед сохранением vision результатов в S3
3. **Средний**: Улучшение логирования при блокировке сохранения из-за квоты

## 📝 Использование

После исправлений:
- Tenant usage будет обновляться в реальном времени при сохранении vision результатов
- Quota checks будут предотвращать превышение лимитов для vision контента
- Периодическая задача `calculate_tenant_storage_usage_task` будет только синхронизировать данные, а не быть единственным источником

