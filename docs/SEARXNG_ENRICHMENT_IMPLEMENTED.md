# SearXNG Enrichment - Реализация обогащения ответов

**Дата**: 2025-11-06  
**Статус**: ✅ Реализовано и протестировано

## Обзор

Реализована логика обогащения ответов через SearXNG при низком качестве результатов из каналов пользователя. Обогащение используется не только как fallback, но и для улучшения ответов при низкой уверенности, малом количестве результатов или низких scores.

## Реализованные изменения

### 1. Конфигурационные параметры (`api/config.py`)

Добавлены настраиваемые параметры для обогащения:

```python
# Context7: SearXNG Enrichment Configuration
searxng_enrichment_enabled: bool = True  # Включить/выключить обогащение
searxng_enrichment_confidence_threshold: float = 0.5  # Порог уверенности для обогащения
searxng_enrichment_min_results_threshold: int = 3  # Минимальное количество результатов
searxng_enrichment_score_threshold: float = 0.6  # Порог среднего score
searxng_enrichment_max_external_results: int = 2  # Максимум внешних результатов
```

### 2. Методы обогащения (`api/services/rag_service.py`)

#### `_should_enrich_with_searxng()`
Проверяет условия для обогащения:
- Низкая уверенность (confidence < threshold)
- Мало результатов (< minimum_results_threshold)
- Низкие scores результатов (средний score < score_threshold)

#### `_enrich_with_searxng()`
Выполняет обогащение ответа внешними источниками:
- Параллельный запрос к SearXNG (не блокирует основной flow)
- Добавление внешних источников с пометкой "external"
- Confidence boost на основе качества внешних источников (до 0.15)
- Graceful degradation при ошибках

### 3. Интеграция в основной flow

Обогащение интегрировано в метод `query()` после получения результатов из каналов:

```python
# 4. Context7: Обогащение ответа через SearXNG (если нужно)
if await self._should_enrich_with_searxng(search_results, confidence, query):
    # Обогащаем внешними источниками
    enriched_sources, confidence_boost = await self._enrich_with_searxng(...)
    
    # Обновляем источники и confidence
    sources = enriched_sources
    confidence = min(1.0, confidence + confidence_boost)
```

## Логика работы

### Условия для обогащения

1. **Низкая уверенность** (confidence < 0.5 по умолчанию):
   - Intent classifier вернул низкую уверенность
   - Ответ может быть неточным
   - Внешние источники дополняют ответ

2. **Мало результатов** (< 3 по умолчанию):
   - Найдено недостаточно релевантных постов
   - Внешние источники расширяют контекст

3. **Низкие scores** (средний score < 0.6 по умолчанию):
   - Результаты имеют низкую релевантность
   - Внешние источники повышают качество ответа

### Процесс обогащения

1. Проверка условий через `_should_enrich_with_searxng()`
2. Если условия выполнены → параллельный запрос к SearXNG
3. Добавление внешних источников к существующим
4. Корректировка confidence (boost до 0.15)
5. Генерация ответа с обогащенными источниками

### Graceful Degradation

- Ошибки SearXNG не влияют на основной ответ
- Если обогащение не удалось → используется только внутренние источники
- Логирование всех ошибок для мониторинга

## Best Practices (Context7)

1. **Graceful Degradation**: Обогащение не блокирует основной ответ
2. **Configurable Thresholds**: Все пороги настраиваемые через config
3. **Parallel Execution**: Запрос к SearXNG выполняется параллельно
4. **Source Attribution**: Четкое разделение внутренних и внешних источников
5. **Confidence Adjustment**: Confidence корректируется на основе качества источников

## Результаты тестирования

### Тест 1: Низкая уверенность
```
Should enrich (low confidence): True
Confidence: 0.3 < Threshold: 0.5
✅ Работает корректно
```

### Тест 2: Мало результатов
```
Should enrich (few results): True
Results count: 2 < Threshold: 3
✅ Работает корректно
```

### Тест 3: Низкие scores
```
Should enrich (low scores): True
Avg score: 0.45 < Threshold: 0.6
✅ Работает корректно
```

### Тест 4: Обогащение источников
```
Original sources: 1
Enriched sources: 3
Confidence boost: 0.10
External sources added: 2
✅ Работает корректно
```

### Тест 5: Полный flow
```
Enrichment triggered: low confidence confidence=0.2 threshold=0.5
Enriching answer with external sources
✅ Обогащение сработало в реальном запросе
```

## Конфигурация

### Переменные окружения

```bash
# Включение/выключение обогащения
SEARXNG_ENRICHMENT_ENABLED=true

# Пороги для обогащения
SEARXNG_ENRICHMENT_CONFIDENCE_THRESHOLD=0.5
SEARXNG_ENRICHMENT_MIN_RESULTS_THRESHOLD=3
SEARXNG_ENRICHMENT_SCORE_THRESHOLD=0.6
SEARXNG_ENRICHMENT_MAX_EXTERNAL_RESULTS=2
```

### Настройка порогов

Для более агрессивного обогащения (чаще использовать):
```bash
SEARXNG_ENRICHMENT_CONFIDENCE_THRESHOLD=0.6  # Выше порог = чаще обогащение
SEARXNG_ENRICHMENT_MIN_RESULTS_THRESHOLD=5   # Больше минимум = чаще обогащение
SEARXNG_ENRICHMENT_SCORE_THRESHOLD=0.7       # Выше порог = чаще обогащение
```

Для менее агрессивного обогащения (реже использовать):
```bash
SEARXNG_ENRICHMENT_CONFIDENCE_THRESHOLD=0.3  # Ниже порог = реже обогащение
SEARXNG_ENRICHMENT_MIN_RESULTS_THRESHOLD=2   # Меньше минимум = реже обогащение
SEARXNG_ENRICHMENT_SCORE_THRESHOLD=0.5       # Ниже порог = реже обогащение
```

## Мониторинг

### Логирование

Обогащение логируется на разных уровнях:

- **DEBUG**: Условия для обогащения
  ```
  Enrichment triggered: low confidence confidence=0.2 threshold=0.5
  ```

- **INFO**: Успешное обогащение
  ```
  Enriching answer with external sources
  Enrichment completed external_results=2 confidence_boost=0.10
  ```

- **WARNING**: Ошибки обогащения (graceful degradation)
  ```
  Enrichment failed, continuing without external sources
  ```

### Метрики

В логах RAG query теперь включается:
- `enrichment_applied: true/false` - было ли применено обогащение
- `confidence` - финальная уверенность после обогащения
- `sources_count` - общее количество источников (внутренние + внешние)

## Сравнение: До и После

### До улучшения

- SearXNG использовался только как fallback
- Если результаты есть → обогащение не применялось
- Низкая уверенность не компенсировалась внешними источниками

### После улучшения

- SearXNG используется как fallback И обогащение
- Если результаты есть, но качество низкое → обогащение применяется
- Низкая уверенность компенсируется внешними источниками
- Confidence корректируется на основе качества источников

## Примеры использования

### Сценарий 1: Низкая уверенность

**Запрос:** "необычный запрос который может иметь низкую уверенность"

**Результат:**
- Intent confidence: 0.2 (< 0.5)
- Обогащение: ✅ Применено
- External sources: 2
- Confidence boost: +0.10
- Final confidence: 0.30

### Сценарий 2: Мало результатов

**Запрос:** "специфичный технический термин"

**Результат:**
- Results count: 2 (< 3)
- Обогащение: ✅ Применено
- External sources: 2
- Total sources: 4 (2 внутренних + 2 внешних)

### Сценарий 3: Низкие scores

**Запрос:** "общий запрос с низкой релевантностью"

**Результат:**
- Avg score: 0.45 (< 0.6)
- Обогащение: ✅ Применено
- External sources: 2
- Confidence boost: +0.10

## Ссылки

- [RAG Service](../api/services/rag_service.py)
- [SearXNG Service](../api/services/searxng_service.py)
- [Config](../api/config.py)
- [SearXNG Usage Scopes](./SEARXNG_USAGE_SCOPES.md)
- [SearXNG Setup](./SEARXNG_SETUP.md)

