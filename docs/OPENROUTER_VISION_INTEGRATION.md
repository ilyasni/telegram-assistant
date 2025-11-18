# Интеграция OpenRouter Vision для OCR Fallback

**Дата**: 2025-11-06  
**Статус**: ✅ Реализовано

## Context

Интеграция OpenRouter Vision API с моделью `qwen/qwen2.5-vl-32b-instruct:free` для замены локальных OCR библиотек (Tesseract/RapidOCR) в OCRFallbackService.

## Plan

1. ✅ Создан адаптер `OpenRouterVisionAdapter` для Vision анализа
2. ✅ Обновлен `OCRFallbackService` для использования OpenRouter Vision
3. ✅ Обновлен `VisionAnalysisTask` для поддержки нового формата
4. ✅ Исправлена проблема с модулем `shared.python`
5. ✅ Обновлена конфигурация

## Patch

### 1. Создан OpenRouter Vision Adapter

**Файл**: `worker/ai_adapters/openrouter_vision.py`

Адаптер для анализа изображений через OpenRouter Vision API:
- Использует модель `qwen/qwen2.5-vl-32b-instruct:free`
- Совместимый интерфейс с `GigaChatVisionAdapter`
- Поддержка retry логики и rate limiting
- Метрики Prometheus для мониторинга

**Ключевые особенности**:
- Base64 кодирование изображений для передачи в API
- Парсинг JSON ответов с fallback на regex extraction
- Нормализация результатов в формат совместимый с GigaChat

### 2. Обновлен OCRFallbackService

**Файл**: `worker/services/ocr_fallback.py`

Изменения:
- Использует `OpenRouterVisionAdapter` вместо локальных OCR библиотек
- Новый метод `analyze_image()` для полноценного Vision анализа
- Сохранена обратная совместимость с `extract_text()` и `classify_content_type()`

**Преимущества**:
- Полноценный Vision анализ (OCR + классификация + описание)
- Не требует локальных библиотек или GPU
- Бесплатная модель для fallback использования

### 3. Обновлен VisionAnalysisTask

**Файл**: `worker/tasks/vision_analysis_task.py`

Изменения:
- Метод `_process_with_ocr()` использует `analyze_image()` если доступен
- Поддержка нового формата результатов от OpenRouter Vision
- Исправлен импорт модуля `shared.schemas.enrichment_validation`

### 4. Исправлена проблема с модулем shared.python

**Проблема**: `ModuleNotFoundError: No module named 'shared.python'`

**Решение**: Исправлен неправильный импорт:
```python
# Было:
from shared.python.shared.schemas.enrichment_validation import validate_vision_enrichment

# Стало:
from shared.schemas.enrichment_validation import validate_vision_enrichment
```

### 5. Обновлена конфигурация

**Файлы**:
- `worker/config/vision_policy.yml` - добавлена поддержка OpenRouter
- `worker/config/enrichment_policy.yml` - включен OCR fallback

## Checks

### Проверка интеграции

```bash
# Проверить импорт OpenRouter Vision Adapter
docker compose exec worker python -c "
from ai_adapters.openrouter_vision import OpenRouterVisionAdapter
print('✅ OpenRouterVisionAdapter импортирован успешно')
"

# Проверить импорт OCRFallbackService
docker compose exec worker python -c "
from services.ocr_fallback import OCRFallbackService
print('✅ OCRFallbackService импортирован успешно')
"

# Проверить импорт shared модулей
docker compose exec worker python -c "
from shared.schemas.enrichment_validation import validate_vision_enrichment
print('✅ shared.schemas.enrichment_validation импортирован успешно')
"
```

### Проверка конфигурации

```bash
# Проверить переменные окружения для OpenRouter
docker compose exec worker env | grep OPENROUTER

# Должны быть установлены:
# OPENROUTER_API_KEY=...
# OPENROUTER_MODEL=qwen/qwen2.5-vl-32b-instruct:free (опционально)
```

### Тестирование Vision анализа

```python
# Пример использования OpenRouter Vision Adapter
from ai_adapters.openrouter_vision import OpenRouterVisionAdapter

adapter = OpenRouterVisionAdapter(
    model="qwen/qwen2.5-vl-32b-instruct:free"
)

result = await adapter.analyze_media(
    sha256="test",
    file_content=image_bytes,
    mime_type="image/jpeg",
    tenant_id="test",
    trace_id="test"
)

print(f"OCR текст: {result.get('ocr', {}).get('text')}")
print(f"Классификация: {result.get('classification')}")
print(f"Описание: {result.get('description')}")
```

## Impact / Rollback

### Преимущества OpenRouter Vision

1. **Полноценный Vision анализ**: OCR + классификация + описание в одном запросе
2. **Не требует локальных библиотек**: Не нужны Tesseract или RapidOCR
3. **Бесплатная модель**: `qwen/qwen2.5-vl-32b-instruct:free` для fallback
4. **Лучшее качество**: Vision модели лучше локальных OCR библиотек

### Обратная совместимость

- ✅ Сохранен интерфейс `OCRFallbackService.extract_text()`
- ✅ Сохранен интерфейс `OCRFallbackService.classify_content_type()`
- ✅ Новый метод `analyze_image()` опционален
- ✅ Старые локальные OCR библиотеки могут использоваться как fallback

### Rollback

Если нужно откатить на локальные OCR библиотеки:

1. Установить `ocr_fallback_enabled: false` в `enrichment_policy.yml`
2. Или изменить `ocr_engine: "tesseract"` в `vision_policy.yml`
3. Установить зависимости: `pytesseract` или `rapidocr_onnxruntime`

## Best Practices (Context7)

### 1. Использование OpenRouter Vision

- ✅ Используется как fallback при недоступности GigaChat
- ✅ Бесплатная модель для экономии затрат
- ✅ Полноценный Vision анализ вместо простого OCR

### 2. Обработка ошибок

- ✅ Retry логика с exponential backoff и jitter
- ✅ Обработка rate limits (HTTP 429) с использованием заголовков `Retry-After` и `X-RateLimit-Reset`
- ✅ Обработка quota exhausted для free моделей (не повторяем запросы)
- ✅ Circuit breaker для защиты от каскадных сбоев
- ✅ Graceful degradation при недоступности API

### 3. Метрики и мониторинг

- ✅ Prometheus метрики для запросов и latency
- ✅ Логирование с trace_id для корреляции
- ✅ Отслеживание использования токенов

## Конфигурация

### Переменные окружения

```bash
# OpenRouter API ключ (обязательно)
OPENROUTER_API_KEY=your_api_key_here

# Модель для Vision анализа (опционально, по умолчанию qwen/qwen2.5-vl-32b-instruct:free)
OPENROUTER_MODEL=qwen/qwen2.5-vl-32b-instruct:free

# Базовый URL (опционально)
OPENROUTER_API_BASE=https://openrouter.ai/api/v1

# HTTP Referer для OpenRouter (опционально)
OPENROUTER_HTTP_REFERER=https://github.com/telegram-assistant

# Circuit Breaker настройки (опционально)
OPENROUTER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5  # Количество сбоев для открытия
OPENROUTER_CIRCUIT_BREAKER_RECOVERY_TIMEOUT=60  # Время восстановления в секундах
```

### Конфигурационные файлы

**`worker/config/enrichment_policy.yml`**:
```yaml
enrichment:
  ocr_fallback_enabled: true  # Включить OCR fallback с OpenRouter
```

**`worker/config/vision_policy.yml`**:
```yaml
fallback:
  ocr_engine: "openrouter"  # Использовать OpenRouter Vision
  openrouter_model: "qwen/qwen2.5-vl-32b-instruct:free"
```

## Выводы

1. ✅ **OpenRouter Vision интегрирован**: Адаптер создан и интегрирован
2. ✅ **OCRFallbackService обновлен**: Использует OpenRouter Vision вместо локальных библиотек
3. ✅ **Проблема с модулем исправлена**: Импорт `shared.schemas` работает корректно
4. ✅ **Конфигурация обновлена**: Поддержка OpenRouter в конфигах

## Следующие шаги

1. Установить `OPENROUTER_API_KEY` в переменные окружения
2. Перезапустить worker для применения изменений
3. Проверить логи на успешную инициализацию OpenRouter Vision
4. Мониторить метрики Prometheus для отслеживания использования

