# Trend Refinement Configuration

Документация по настройке системы автоматического улучшения качества кластеров трендов.

## Переменные окружения

### Основные настройки

```bash
# Включение/отключение рефайнмента
TREND_REFINEMENT_ENABLED=true  # По умолчанию: true

# Интервал запуска рефайнмента (в часах)
TREND_REFINEMENT_INTERVAL_HOURS=6  # По умолчанию: 6

# Минимальный размер кластера для обработки
TREND_REFINEMENT_MIN_CLUSTER_SIZE=3  # По умолчанию: 3

# Максимальное количество кластеров за один запуск
TREND_REFINEMENT_MAX_CLUSTERS_PER_RUN=50  # По умолчанию: 50
```

### Настройки Split Agent (разделение кластеров)

```bash
# Включение/отключение разделения
TREND_SPLIT_ENABLED=true  # По умолчанию: true

# Минимальная когерентность для разделения (ниже этого порога - кандидат на split)
TREND_MIN_COHERENCE_FOR_SPLIT=0.3  # По умолчанию: 0.3

# Минимальный размер кластера для разделения
TREND_MIN_CLUSTER_SIZE_FOR_SPLIT=5  # По умолчанию: 5

# Минимальное/максимальное количество подкластеров
TREND_MIN_SUBCLUSTERS=2  # По умолчанию: 2
TREND_MAX_SUBCLUSTERS=5  # По умолчанию: 5

# Включение LLM-валидации разделения
TREND_SPLIT_LLM_VALIDATION=true  # По умолчанию: true

# Модель LLM для валидации
TREND_SPLIT_LLM_MODEL=GigaChat  # По умолчанию: GigaChat
```

### Настройки Merge Agent (слияние кластеров)

```bash
# Включение/отключение слияния
TREND_MERGE_ENABLED=true  # По умолчанию: true

# Минимальное пересечение ключевых слов для слияния (Jaccard similarity)
TREND_MERGE_MIN_KEYWORD_OVERLAP=0.5  # По умолчанию: 0.5

# Минимальная близость центроидов для слияния (cosine similarity)
TREND_MERGE_MIN_CENTROID_SIMILARITY=0.85  # По умолчанию: 0.85

# Минимальный размер кластера для слияния (слияются мелкие кластеры)
TREND_MERGE_MIN_CLUSTER_SIZE=2  # По умолчанию: 2

# Максимальный размер кластера после слияния
TREND_MERGE_MAX_CLUSTER_SIZE=50  # По умолчанию: 50

# Включение LLM-валидации слияния
TREND_MERGE_LLM_VALIDATION=true  # По умолчанию: true

# Модель LLM для валидации
TREND_MERGE_LLM_MODEL=GigaChat  # По умолчанию: GigaChat
```

### Настройки Sub-clustering (двухуровневая кластеризация)

```bash
# Включение/отключение создания подтем
TREND_SUBCLUSTERING_ENABLED=true  # По умолчанию: true

# Минимальный размер кластера для создания подтем
TREND_SUBCLUSTER_MIN_SIZE=10  # По умолчанию: 10
```

### Настройки c-TF-IDF (взвешивание ключевых слов)

```bash
# Включение/отключение c-TF-IDF
TREND_CTFIDF_ENABLED=true  # По умолчанию: true
```

### Настройки Coherence Agent (валидация когерентности)

```bash
# Включение/отключение Coherence Agent
TREND_COHERENCE_AGENT_ENABLED=true  # По умолчанию: true

# Минимальная уверенность для принятия решения
TREND_COHERENCE_AGENT_MIN_CONFIDENCE=0.7  # По умолчанию: 0.7

# Модель LLM для Coherence Agent
TREND_COHERENCE_AGENT_LLM_MODEL=GigaChat  # По умолчанию: GigaChat

# Максимальное количество токенов для LLM
TREND_COHERENCE_AGENT_LLM_MAX_TOKENS=200  # По умолчанию: 200
```

### Настройки Graph Validation

```bash
# Включение/отключение Graph-RAG валидации
TREND_GRAPH_VALIDATION_ENABLED=true  # По умолчанию: true

# Минимальная similarity тем в графе
TREND_GRAPH_MIN_TOPIC_SIMILARITY=0.7  # По умолчанию: 0.7
```

### Настройки Drift Detector

```bash
# Включение/отключение Drift Detector
TREND_DRIFT_DETECTION_ENABLED=true  # По умолчанию: true

# Порог дрейфа центроида
TREND_DRIFT_THRESHOLD=0.05  # По умолчанию: 0.05
```

## Пример конфигурации

Минимальная конфигурация (всё включено, дефолтные значения):

```bash
TREND_REFINEMENT_ENABLED=true
TREND_REFINEMENT_INTERVAL_HOURS=6
```

Конфигурация с более агрессивным рефайнментом (чаще запуск, ниже пороги):

```bash
TREND_REFINEMENT_ENABLED=true
TREND_REFINEMENT_INTERVAL_HOURS=3
TREND_MIN_COHERENCE_FOR_SPLIT=0.4
TREND_MERGE_MIN_KEYWORD_OVERLAP=0.6
TREND_SUBCLUSTER_MIN_SIZE=8
```

Конфигурация для отладки (отключен LLM, включено больше логирования):

```bash
TREND_REFINEMENT_ENABLED=true
TREND_SPLIT_LLM_VALIDATION=false
TREND_MERGE_LLM_VALIDATION=false
TREND_REFINEMENT_MAX_CLUSTERS_PER_RUN=10
```

## Мониторинг

Метрики Prometheus:

- `trend_refinement_runs_total{status="success|error"}` - количество запусков рефайнмента
- `trend_refinement_clusters_split_total` - количество разделённых кластеров
- `trend_refinement_clusters_merged_total` - количество слитых кластеров
- `trend_refinement_duration_seconds` - длительность рефайнмента
- `trend_refinement_clusters_processed` - количество обработанных кластеров

## Логирование

Все события рефайнмента логируются через `structlog` с префиксами:
- `trend_refinement_*` - общие события рефайнмента
- `split_agent_*` - события разделения кластеров
- `merge_agent_*` - события слияния кластеров
- `refinement_metrics_*` - вычисление метрик

## Примечания

1. Рефайнмент запускается асинхронно в фоне и не блокирует основную обработку постов
2. LLM-валидация требует доступности GigaChat API
3. Для работы split agent требуется установленная библиотека `hdbscan`
4. Метрики когерентности вычисляются на основе embeddings постов кластера
5. Подтемы создаются только для крупных кластеров (по умолчанию >10 постов)

