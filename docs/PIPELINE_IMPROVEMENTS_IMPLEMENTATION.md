# Реализация улучшений пайплайна

**Дата**: 2025-02-01  
**Context7**: Реализация рекомендаций из PIPELINE_COMPLETE_AUDIT.md

---

## Статус реализации

### ✅ 1. TTL для Qdrant и Neo4j (Высокий приоритет)

#### Текущее состояние

**Neo4j**: ✅ Реализовано
- Метод `cleanup_expired_posts()` в `Neo4jClient`
- Удаляет узлы где `expires_at < current_time`
- Используется в `CleanupTask.run_expired_cleanup()`

**Qdrant**: ⚠️ Частично реализовано
- Метод `sweep_expired_vectors()` существует
- Нужно проверить наличие `sweep_all_collections()`
- Нужно добавить периодический запуск

#### План улучшений

1. **Добавить `sweep_all_collections()` в QdrantClient**
   - Итерация по всем коллекциям
   - Вызов `sweep_expired_vectors()` для каждой
   - Метрики для мониторинга

2. **Добавить периодический запуск TTL cleanup**
   - Cron job или периодический task
   - Запуск `run_expired_cleanup()` каждые N часов
   - Логирование результатов

3. **Улучшить метрики**
   - `cleanup_expired_posts_total{source}` - количество удаленных постов
   - `cleanup_expired_vectors_total{collection}` - количество удаленных векторов
   - `cleanup_ttl_duration_seconds` - длительность TTL cleanup

---

### ⏳ 2. Circuit Breaker для внешних API (Средний приоритет)

#### Текущее состояние

- Circuit breaker упоминается в документации, но не реализован
- Нет защиты от каскадных сбоев для GigaChat и Crawl4AI

#### План реализации

1. **Создать универсальный CircuitBreaker класс**
   - Состояния: CLOSED, OPEN, HALF_OPEN
   - Настраиваемые пороги (failure_threshold, timeout)
   - Метрики Prometheus для мониторинга

2. **Интегрировать в GigaChatVisionAdapter**
   - Защита вызовов Vision API
   - Graceful degradation при OPEN состоянии
   - Логирование переходов состояний

3. **Интегрировать в Crawl4AIService**
   - Защита вызовов Crawl API
   - Graceful degradation при OPEN состоянии
   - Логирование переходов состояний

4. **Метрики**
   - `circuit_breaker_state{service, state}` - текущее состояние
   - `circuit_breaker_transitions_total{service, from_state, to_state}` - переходы
   - `circuit_breaker_failures_total{service}` - количество сбоев

---

### ⏳ 3. Валидация данных через Pydantic (Средний приоритет)

#### Текущее состояние

- Частичная валидация в `EnrichmentRepository` (valid_kinds, valid_statuses)
- Нет валидации для данных перед сохранением в Qdrant/Neo4j

#### План реализации

1. **Создать Pydantic модели для валидации**
   - `VisionEnrichmentData` - для Vision данных
   - `CrawlEnrichmentData` - для Crawl данных
   - `QdrantPayload` - для Qdrant payload
   - `Neo4jPostNode` - для Neo4j узлов

2. **Интегрировать валидацию в ключевые точки**
   - `VisionAnalysisTask._save_to_db()` - валидация перед сохранением
   - `EnrichmentTask._save_enrichment_data()` - валидация crawl данных
   - `IndexingTask._index_to_qdrant()` - валидация payload
   - `IndexingTask._index_to_neo4j()` - валидация узлов

3. **Обработка ошибок валидации**
   - Логирование с деталями ошибок
   - Метрики для отслеживания ошибок валидации
   - Graceful degradation (не прерывать обработку)

---

### ⏳ 4. Улучшение обработки больших альбомов (Низкий приоритет)

#### Текущее состояние

- Окно поиска сообщений: ±20 сообщений
- Возможен пропуск элементов в больших альбомах

#### План улучшений

1. **Увеличить окно поиска**
   - Изменить с ±20 на ±50 сообщений
   - Настраиваемый параметр через ENV

2. **Добавить проверку полноты альбома**
   - Сравнение количества элементов с `items_count` из БД
   - Предупреждение при несоответствии
   - Retry логика для пропущенных элементов

---

### ⏳ 5. Distributed Tracing через OpenTelemetry (Низкий приоритет)

#### Текущее состояние

- Используется `trace_id` для корреляции
- Нет distributed tracing через OpenTelemetry

#### План реализации

1. **Интеграция OpenTelemetry**
   - Установка `opentelemetry-api`, `opentelemetry-sdk`
   - Настройка экспортера (Jaeger/Zipkin)
   - Инструментация ключевых операций

2. **Добавить spans для ключевых операций**
   - Парсинг постов
   - Vision анализ
   - Тегирование
   - Обогащение
   - Индексация

3. **Корреляция с существующим trace_id**
   - Использование `trace_id` как OpenTelemetry trace ID
   - Пропагация через все этапы пайплайна

---

## Приоритеты реализации

1. **Высокий приоритет** (критично для production):
   - ✅ TTL для Qdrant и Neo4j - частично реализовано, нужно улучшить

2. **Средний приоритет** (важно для надежности):
   - ⏳ Circuit Breaker - не реализовано
   - ⏳ Валидация данных - частично реализовано

3. **Низкий приоритет** (улучшения):
   - ⏳ Обработка больших альбомов
   - ⏳ Distributed Tracing

---

## Следующие шаги

1. Проверить и улучшить TTL cleanup для Qdrant
2. Добавить периодический запуск TTL cleanup
3. Реализовать Circuit Breaker для GigaChat и Crawl4AI
4. Добавить Pydantic модели для валидации
5. Интегрировать валидацию в ключевые точки пайплайна

