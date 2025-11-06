# Диагностика сохранения OCR текста

**Дата**: 2025-01-31  
**Проблема**: Не находятся новые посты с OCR-текстом

## Context

OCR-текст сохраняется в таблице `post_enrichment` в поле `data->'ocr'->>'text'` (JSONB). Legacy поле `vision_ocr_text` синхронизируется автоматически.

## Проблема

Пользователь не находит новых постов с OCR-текстом, хотя анализ изображений выполняется.

## Возможные причины

### 1. GigaChat не возвращает OCR в ответе
- Промпт явно запрашивает OCR, но модель может игнорировать запрос
- Изображение может не содержать текста (фото без надписей)

### 2. Парсинг ответа не извлекает OCR
- JSON ответ может быть неполным или поврежденным
- OCR может быть в неожиданном формате

### 3. Валидация блокирует сохранение OCR
- **ИСПРАВЛЕНО**: Пустые OCR объекты теперь конвертируются в `None`
- Валидация не должна блокировать сохранение (есть fallback)

### 4. OCR текст пустой и фильтруется
- Текст может быть только пробелами
- Минимальная длина текста не проверяется

## Решение

### Исправление валидации OCR

Добавлен валидатор `validate_ocr()` в `VisionEnrichmentData`, который:
1. Конвертирует пустые OCR объекты в `None`
2. Проверяет наличие валидного текста перед созданием `OCRData`
3. Логирует проблемы для диагностики

```python
@field_validator('ocr', mode='before')
@classmethod
def validate_ocr(cls, v: Any) -> Optional[OCRData]:
    """
    Context7: Валидация OCR данных перед созданием OCRData объекта.
    Конвертирует пустые OCR объекты в None для корректной обработки.
    """
    if v is None:
        return None
    
    # Если это словарь, проверяем наличие валидного текста
    if isinstance(v, dict):
        ocr_text = v.get("text")
        # Если текст пустой или отсутствует, возвращаем None
        if not ocr_text or not str(ocr_text).strip():
            logger.debug("OCR text is empty, converting to None", ocr_dict=v)
            return None
        # Если текст валидный, создаем OCRData объект
        try:
            return OCRData(**v)
        except Exception as e:
            logger.warning("Failed to create OCRData, converting to None", error=str(e), ocr_dict=v)
            return None
```

## Диагностика

### Скрипт проверки

Создан скрипт `/opt/telegram-assistant/scripts/check_ocr_saving.py` для диагностики:

```bash
python scripts/check_ocr_saving.py
```

Скрипт проверяет:
1. **Статистику OCR в БД**:
   - Всего Vision записей
   - С OCR текстом (новый формат)
   - С OCR текстом (legacy формат)
   - Процент покрытия OCR

2. **Последнее сохранение**:
   - Когда было последнее сохранение с OCR
   - Когда было последнее сохранение Vision вообще

3. **Статистика по провайдерам**:
   - GigaChat vs OCR Fallback
   - Процент OCR по каждому провайдеру

4. **Проблемы с парсингом**:
   - Посты где OCR может отсутствовать (document, screenshot)
   - Посты с пустым OCR объектом

5. **Структура OCR данных**:
   - Примеры структуры OCR в БД
   - Проверка типов данных

### SQL запросы для диагностики

```sql
-- Последнее сохранение с OCR
SELECT 
    post_id,
    updated_at,
    provider,
    data->'ocr'->>'text' as ocr_text,
    LENGTH(data->'ocr'->>'text') as ocr_length,
    data->>'model' as model
FROM post_enrichment 
WHERE kind = 'vision' 
  AND data->'ocr'->>'text' IS NOT NULL 
  AND LENGTH(data->'ocr'->>'text') > 0
ORDER BY updated_at DESC 
LIMIT 1;

-- Статистика за последние 24 часа
SELECT 
    COUNT(*) FILTER (WHERE data->'ocr'->>'text' IS NOT NULL AND LENGTH(data->'ocr'->>'text') > 0) as with_ocr,
    COUNT(*) FILTER (WHERE data->'ocr'->>'text' IS NULL OR LENGTH(data->'ocr'->>'text') = 0) as without_ocr
FROM post_enrichment 
WHERE kind = 'vision' 
  AND updated_at > NOW() - INTERVAL '24 hours';

-- Посты где OCR может отсутствовать (document, screenshot)
SELECT 
    post_id,
    updated_at,
    provider,
    data->>'classification' as classification,
    LENGTH(data->>'description') as desc_length
FROM post_enrichment 
WHERE kind = 'vision' 
  AND updated_at > NOW() - INTERVAL '7 days'
  AND (data->'ocr' IS NULL OR data->'ocr' = 'null'::jsonb)
  AND data->>'classification' IN ('document', 'screenshot', 'infographic')
ORDER BY updated_at DESC 
LIMIT 10;
```

## Проверка логов

### Логи worker для диагностики OCR

```bash
# Проверить логи парсинга OCR
docker compose logs worker | grep -i "ocr\|vision.*parsed"

# Проверить логи сохранения OCR
docker compose logs worker | grep -i "ocr.*saved\|vision.*saved"

# Проверить ошибки валидации
docker compose logs worker | grep -i "validation.*failed\|ocr.*validation"
```

### Ключевые логи для проверки

1. **Парсинг ответа GigaChat**:
   ```
   "Vision analysis result parsed"
   "has_ocr": true/false
   "ocr_text_length": <number>
   ```

2. **Сохранение в БД**:
   ```
   "OCR value set for vision_data"
   "Saving vision enrichment to DB"
   "has_ocr": true/false
   ```

3. **Валидация**:
   ```
   "OCR text is empty, converting to None"
   "Vision enrichment validation failed"
   ```

## Best Practices (Context7)

### 1. Валидация OCR
- ✅ Пустые OCR объекты конвертируются в `None`
- ✅ Валидация не блокирует сохранение (есть fallback)
- ✅ Логирование проблем для диагностики

### 2. Парсинг ответов GigaChat
- ✅ Множественные методы парсинга (direct, bracket extractor, partial JSON)
- ✅ Извлечение OCR из content если отсутствует в JSON
- ✅ Логирование сырого ответа для диагностики

### 3. Сохранение в БД
- ✅ OCR сохраняется в JSONB поле `data->'ocr'`
- ✅ Legacy поле `vision_ocr_text` синхронизируется автоматически
- ✅ Идемпотентность через `ON CONFLICT`

## Действия для диагностики

1. **Запустить диагностический скрипт**:
   ```bash
   python scripts/check_ocr_saving.py
   ```

2. **Проверить логи worker**:
   ```bash
   docker compose logs worker --tail=1000 | grep -i ocr
   ```

3. **Проверить последние сохранения**:
   ```sql
   SELECT * FROM post_enrichment 
   WHERE kind = 'vision' 
   ORDER BY updated_at DESC 
   LIMIT 10;
   ```

4. **Проверить ответы GigaChat**:
   - Логи содержат `content_preview` с сырым ответом
   - Проверить, возвращает ли GigaChat OCR в ответе

## Impact / Rollback

### Изменения
- ✅ Исправлена валидация OCR (пустые объекты → None)
- ✅ Добавлен диагностический скрипт
- ✅ Улучшено логирование

### Rollback
- Валидация не критична (есть fallback)
- Изменения не влияют на существующие данные
- Можно откатить изменения в валидации если нужно

## Выводы

1. **Валидация исправлена**: Пустые OCR объекты теперь корректно обрабатываются
2. **Диагностика**: Создан скрипт для проверки сохранения OCR
3. **Логирование**: Улучшено для диагностики проблем с OCR

**Следующие шаги**:
1. Запустить диагностический скрипт
2. Проверить логи worker на ошибки парсинга
3. Проверить ответы GigaChat API на наличие OCR

