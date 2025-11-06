# Исправление пропадания OCR и params_hash в post_enrichment

**Дата**: 2025-02-01  
**Context7**: Диагностика и исправление проблемы с потерей OCR данных и params_hash

---

## Проблема

В таблице `post_enrichment` в столбце `data` пропадали значения OCR, также не сохранялся `params_hash`.

**Симптомы**:
- OCR данные отсутствуют в `data->'ocr'->>'text'`
- `params_hash` равен NULL для новых записей
- Данные не появляются при повторной обработке

---

## Причины

### 1. Потеря params_hash при ON CONFLICT

**Проблема**: При обновлении существующей записи через `ON CONFLICT DO UPDATE SET`, если `params_hash` был `None`, он перезаписывал существующий hash на `NULL`.

**Решение**: Использование `COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash)` для сохранения существующего hash, если новый равен NULL.

```sql
ON CONFLICT (post_id, kind) DO UPDATE SET
    params_hash = COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash),
    ...
```

### 2. Отсутствие диагностики

**Проблема**: Не было логирования для диагностики потери данных при сериализации и сохранении.

**Решение**: Добавлено детальное логирование на всех этапах:
- Перед сериализацией JSON
- После сериализации (проверка сохранения OCR)
- После сохранения в БД (верификация данных)

---

## Исправления

### 1. EnrichmentRepository.upsert_enrichment()

**Файл**: `shared/python/shared/repositories/enrichment_repository.py`

#### 1.1. Логирование перед сериализацией

```python
# Context7: Логируем данные перед сериализацией для диагностики OCR и params_hash
has_ocr = bool(data.get("ocr") and (isinstance(data.get("ocr"), dict) and data["ocr"].get("text")))
ocr_text_length = len(data.get("ocr", {}).get("text", "")) if isinstance(data.get("ocr"), dict) else 0
logger.debug(
    "Upserting enrichment to DB",
    post_id=post_id,
    kind=kind,
    provider=provider,
    params_hash=params_hash,
    has_ocr=has_ocr,
    ocr_text_length=ocr_text_length,
    data_keys=list(data.keys()) if isinstance(data, dict) else [],
    trace_id=trace_id
)
```

#### 1.2. Проверка после сериализации

```python
# Context7: Сериализация JSON с сохранением None значений
# ВАЖНО: json.dumps сохраняет None как null в JSON, что корректно для PostgreSQL JSONB
data_jsonb = json.dumps(data, ensure_ascii=False, default=str)

# Context7: Проверяем, что OCR сохранился после сериализации
data_parsed = json.loads(data_jsonb)
has_ocr_after = bool(data_parsed.get("ocr") and (isinstance(data_parsed.get("ocr"), dict) and data_parsed["ocr"].get("text")))
if has_ocr != has_ocr_after:
    logger.warning(
        "OCR data lost during JSON serialization",
        post_id=post_id,
        kind=kind,
        has_ocr_before=has_ocr,
        has_ocr_after=has_ocr_after,
        trace_id=trace_id
    )
```

#### 1.3. Исправление ON CONFLICT для params_hash

**AsyncPG версия**:
```sql
ON CONFLICT (post_id, kind) DO UPDATE SET
    provider = EXCLUDED.provider,
    params_hash = COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash),
    data = EXCLUDED.data,
    ...
    -- Context7: params_hash использует COALESCE чтобы не перезаписывать существующий hash на NULL
```

**SQLAlchemy версия**:
```sql
ON CONFLICT (post_id, kind) DO UPDATE SET
    provider = EXCLUDED.provider,
    params_hash = COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash),
    data = EXCLUDED.data,
    ...
```

#### 1.4. Верификация после сохранения

```python
# Context7: Проверяем, что данные сохранились правильно после upsert
check_row = await conn.fetchrow("""
    SELECT data, params_hash 
    FROM post_enrichment 
    WHERE post_id = $1 AND kind = 'vision'
""", post_id)

if check_row:
    saved_data = check_row['data']
    saved_params_hash = check_row['params_hash']
    saved_has_ocr = bool(saved_data and saved_data.get("ocr") and isinstance(saved_data.get("ocr"), dict) and saved_data["ocr"].get("text"))
    logger.debug(
        "Enrichment saved to DB - verification",
        post_id=post_id,
        kind=kind,
        params_hash_saved=bool(saved_params_hash),
        params_hash_value=saved_params_hash[:16] + "..." if saved_params_hash else None,
        has_ocr_saved=saved_has_ocr,
        ocr_text_length=len(saved_data.get("ocr", {}).get("text", "")) if saved_data and saved_data.get("ocr") else 0,
        trace_id=trace_id
    )
```

### 2. VisionAnalysisTask._save_to_db()

**Файл**: `worker/tasks/vision_analysis_task.py`

#### 2.1. Улучшенное логирование params_hash

```python
# Context7: Вычисляем params_hash для идемпотентности
repo = EnrichmentRepository(self.db)
model_name = first_result.get("model") or "unknown"
provider_name = first_result.get("provider") or "unknown"
params_hash = repo.compute_params_hash(
    model=model_name,
    version=None,  # TODO: добавить версию
    inputs={"provider": provider_name}
)

# Context7: Логируем вычисление params_hash для диагностики
logger.debug(
    "Computed params_hash for vision enrichment",
    post_id=post_id,
    model=model_name,
    provider=provider_name,
    params_hash=params_hash,
    trace_id=trace_id
)
```

#### 2.2. Явное сохранение OCR (даже если None)

```python
# Context7: OCR данные - ВАЖНО: сохраняем даже если None, чтобы поле было в JSON
"ocr": ocr_value,  # Может быть None, dict или отсутствовать - все варианты валидны
```

---

## Context7 Best Practices применены

### ✅ Структурированное логирование
- Детальное логирование на всех этапах с `trace_id`
- Логирование до и после критических операций
- Предупреждения при обнаружении проблем

### ✅ Валидация данных
- Проверка сохранения данных после сериализации
- Верификация данных после сохранения в БД
- Предупреждения при потере данных

### ✅ Идемпотентность
- Использование `COALESCE` для сохранения существующих значений
- Защита от перезаписи `params_hash` на NULL
- Корректная обработка `ON CONFLICT`

### ✅ Обработка None значений
- `json.dumps` сохраняет `None` как `null` в JSON (корректно для PostgreSQL JSONB)
- Явное сохранение поля `ocr` даже если значение `None`
- Правильная обработка опциональных полей

---

## Диагностика

### Скрипт проверки

Создан диагностический скрипт `scripts/check_ocr_params_hash_issue.py` для проверки:
- Статистика по OCR и params_hash
- Последние записи с проблемами
- Сравнение старых и новых записей

**Запуск**:
```bash
docker compose exec worker python scripts/check_ocr_params_hash_issue.py --limit 50
```

### Логи для мониторинга

**Ключевые логи**:
- `Upserting enrichment to DB` - данные перед сохранением
- `OCR data lost during JSON serialization` - предупреждение о потере OCR
- `Computed params_hash for vision enrichment` - вычисление params_hash
- `Enrichment saved to DB - verification` - верификация после сохранения

**Проверка логов**:
```bash
docker compose logs worker | grep -E "(Upserting enrichment|OCR data lost|Computed params_hash|Enrichment saved to DB)"
```

---

## Проверка исправлений

### 1. Проверка в БД

```sql
SELECT 
    post_id,
    params_hash,
    data->'ocr'->>'text' as ocr_text,
    data->'ocr'->>'engine' as ocr_engine,
    updated_at
FROM post_enrichment
WHERE kind = 'vision'
ORDER BY updated_at DESC
LIMIT 10;
```

### 2. Проверка метрик

Мониторинг метрик Prometheus:
- `post_enrichment_upsert_total` - общее количество upsert операций
- `post_enrichment_upsert_errors_total` - ошибки при upsert

### 3. Проверка логов

После обработки новых постов проверить логи на:
- Отсутствие предупреждений `OCR data lost during JSON serialization`
- Наличие `Enrichment saved to DB - verification` с корректными данными
- Наличие `params_hash_saved=true` в логах верификации

---

## Следующие шаги

1. ✅ Мониторить логи после деплоя
2. ✅ Запустить диагностический скрипт для проверки текущего состояния
3. ✅ Проверить, что новые записи сохраняют OCR и params_hash корректно
4. ⏳ Добавить метрики для отслеживания потери OCR данных
5. ⏳ Рассмотреть добавление алертов на потерю OCR данных

---

## Выводы

1. ✅ **Исправлена потеря params_hash** - использование `COALESCE` предотвращает перезапись существующего hash на NULL
2. ✅ **Добавлена диагностика** - детальное логирование на всех этапах для выявления проблем
3. ✅ **Верификация данных** - проверка сохранения данных после операций
4. ✅ **Соответствие Context7** - все изменения следуют best practices для логирования, валидации и идемпотентности

