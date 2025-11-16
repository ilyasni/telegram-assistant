---
title: Алгоритм поиска и выявления трендов
date: 2025-01-15
context7: true
---

## Обзор

Система обнаружения трендов использует **реактивный (reactive) пайплайн** для выявления emerging трендов в реальном времени и **стабильный (stable) пайплайн** для подтверждения и агрегации устойчивых трендов. Алгоритм основан на семантической кластеризации через векторные embeddings, временных окнах агрегации в Redis и динамических метриках для оценки значимости трендов.

---

## Архитектура пайплайна

### 1. Реактивный слой (Reactive / Emerging)

**Компонент**: `worker/trends_worker.py::TrendDetectionWorker`

**Триггер**: События `posts.indexed` из Redis Streams

**Поток обработки**:

```
posts.indexed → PostSnapshot → Embedding → Qdrant ANN Search → Cluster Match/Create
    ↓
Redis Time-Series (freq_short, freq_long, freq_baseline)
    ↓
Source Diversity Tracking
    ↓
Burst Detection & Metrics Calculation
    ↓
LLM Card Enrichment (опционально)
    ↓
PostgreSQL (trend_clusters, trend_metrics, trend_cluster_posts)
    ↓
trends.emerging Event (если пороги превышены)
```

---

## Детальный алгоритм обработки поста

### Этап 1: Загрузка и обогащение данных поста

**Метод**: `_fetch_post_snapshot(post_id: str) -> PostSnapshot`

**Источники данных**:
- `posts` — основной контент, метаданные, engagement метрики
- `post_enrichment` (kind='classify') — keywords, topics, metadata_topics
- `channels` — название канала

**Извлечение сущностей**:
- Хэштеги: `#[\w\d_]+`
- Именованные сущности: паттерн `[A-ZА-ЯЁ][\w\-]+(?:\s+[A-ZА-ЯЁ][\w\-]+){0,2}`

**Результат**: `PostSnapshot` с полями:
- `post_id`, `channel_id`, `channel_title`, `posted_at`
- `content` (текст поста)
- `keywords` (из enrichment или fallback)
- `topics` (объединение `topics` + `metadata_topics`)
- `entities` (извлечённые из контента)
- `engagements` (views, reactions, forwards, replies, score)

---

### Этап 2: Генерация embedding

**Метод**: `_generate_embedding(snapshot: PostSnapshot) -> List[float]`

**Комбинирование текста для embedding**:
```python
text_chunks = [
    snapshot.content.strip(),
    " ".join(snapshot.entities[:10]),
    " ".join(snapshot.keywords[:10]),
    " ".join(snapshot.topics[:10]),
]
combined_text = " ".join(chunk for chunk in text_chunks if chunk).strip()
```

**Сервис**: `EmbeddingService` (через `ai_providers/embedding_service.py`)
- **Primary**: GigaChat Embeddings API (`/v1/embeddings`)
- **Fallback**: Zero-vector при ошибке
- **Размерность**: 1536 (настраивается через `EMBED_DIM`)

**Результат**: Вектор размерности 1536

---

### Этап 3: Семантическая кластеризация (ANN Search)

**Метод**: `_match_cluster(embedding, snapshot) -> (cluster_id, cluster_key, similarity)`

**Алгоритм**:
1. **Поиск в Qdrant**: ANN (Approximate Nearest Neighbors) поиск в коллекции `trends_hot`
   - Запрос: `query_vector = embedding`
   - Лимит: 3 ближайших вектора
   - Метрика: Cosine similarity

2. **Проверка порога**: `similarity >= TREND_COHERENCE_THRESHOLD` (по умолчанию 0.55)

3. **Результат**:
   - Если найден кластер: возвращается `(cluster_id, cluster_key, similarity)`
   - Если не найден: `(None, None, None)` → создаётся новый кластер

**Создание нового кластера**:
- `cluster_id = uuid.uuid4()`
- `cluster_key = _build_cluster_key(snapshot)` (SHA1 хэш от нормализованных entities/topics/keywords)

**Метод `_build_cluster_key`**:
```python
tokens = filtered_entities + filtered_topics + filtered_keywords
normalized = [t.lower().strip() for t in tokens if t]
signature = "|".join(sorted(set(normalized)))
cluster_key = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:32]
```

---

### Этап 4: Обновление временных окон (Redis Time-Series)

**Метод**: `_increment_window(cluster_key, window: TrendWindow) -> int`

**Окна агрегации**:
- `SHORT_5M` (5 минут) — для burst detection
- `MID_1H` (1 час) — для window_mentions
- `LONG_24H` (24 часа) — для baseline

**Реализация**:
```python
key = f"trend:{cluster_key}:freq:{window.value}"
value = await redis.incr(key)
await redis.expire(key, window.seconds)
return value
```

**Метрики**:
- `freq_short` = частота за 5 минут
- `freq_long` = частота за 1 час
- `freq_baseline` = частота за 24 часа

---

### Этап 5: Source Diversity Tracking

**Метод**: `_update_source_diversity(cluster_key, channel_id) -> int`

**Реализация**:
```python
key = f"trend:{cluster_key}:sources"
await redis.sadd(key, channel_id)
await redis.expire(key, TrendWindow.LONG_24H.seconds)
return await redis.scard(key)
```

**Результат**: Количество уникальных каналов, упомянувших кластер за последние 24 часа

---

### Этап 6: Вычисление динамических метрик

#### 6.1. Burst Score

**Метод**: `_compute_burst(observed: int, expected: float) -> float`

**Формула**:
```python
baseline = max(1.0, expected)
burst_score = observed / baseline
```

**Ожидаемое значение (expected)**:
```python
def _expected_baseline(freq_baseline: int, window_seconds: int) -> float:
    long_window = 24 * 60 * 60  # 24 часа в секундах
    buckets = max(1, long_window // window_seconds)
    if freq_baseline <= 0:
        return 1.0
    return freq_baseline / buckets
```

**Интерпретация**:
- `burst_score = 1.0` — нормальная частота
- `burst_score > 3.0` — значительный всплеск (порог по умолчанию)

#### 6.2. Coherence Score

**Определение**: Семантическая схожесть поста с кластером
- Если пост найден в существующем кластере: `coherence = similarity` (из Qdrant)
- Если создан новый кластер: `coherence = 0.6` (базовое значение)

#### 6.3. Novelty Score

**Формула**: `novelty = max(0.0, 1.0 - coherence)`

**Интерпретация**:
- `novelty ≈ 1.0` — новый тренд (низкая схожесть с существующими)
- `novelty ≈ 0.0` — продолжение существующего тренда

#### 6.4. Rate of Change

**Формула**: `rate_of_change = freq_short - max(freq_long, 1)`

**Интерпретация**: Скорость изменения частоты упоминаний

---

### Этап 7: Фильтрация и построение primary label

**Метод**: `_build_primary_label(entities, topics, keywords, content) -> str`

**Приоритеты**:
1. **Многословные сущности/хэштеги**: `entities` с пробелами или начинающиеся с `#`
2. **Составные темы**: объединение 2+ тем через " — "
3. **Keyphrases из контента**: биграммы/триграммы (метод `_extract_keyphrases_from_content`)
4. **Объединение ключевых слов**: склейка 2–3 keywords через " — "
5. **Fallback**: "Тренд"

**Фильтрация generic labels**:
- Исключаются стоп-слова (`DEFAULT_TREND_STOPWORDS` + `EXPANDED_STOPWORDS`)
- Минимальная длина: 4 символа
- Одиночные слова без хэштега считаются generic

**Метод `_filter_terms`**:
- Удаление пунктуации по краям
- Исключение цифр, `@` упоминаний
- Дедупликация (case-insensitive)
- Минимальная длина: 3 символа

---

### Этап 8: Сохранение sample posts

**Метод**: `_record_cluster_sample(cluster_id, snapshot)`

**Логика**:
- Вставка в `trend_cluster_posts` с `ON CONFLICT (cluster_id, post_id) DO UPDATE`
- Ограничение: `TREND_CLUSTER_SAMPLE_LIMIT` (по умолчанию 10)
- Очистка старых записей: `DELETE ... OFFSET limit`

**Метод `_fetch_cluster_samples`**:
- Использует `DISTINCT ON (channel_id)` для разнообразия источников
- Сортировка: `ORDER BY channel_id, posted_at DESC`
- Лимит: до 5 примеров

---

### Этап 9: LLM-обогащение карточки (опционально)

**Метод**: `_enhance_card_with_llm(...) -> Optional[Dict]`

**Условия активации**:
- `TREND_CARD_LLM_ENABLED = true`
- `len(sample_posts) >= 2` и `window_mentions >= 3`
- Cooldown: не чаще чем раз в `TREND_CARD_LLM_REFRESH_MINUTES` (по умолчанию 10 минут)

**Промпт**:
```json
{
  "primary_topic": "...",
  "keywords": [...],
  "topics": [...],
  "window_minutes": 60,
  "mentions": 15,
  "baseline": 3,
  "sources": 5,
  "sample_posts": [
    {
      "source": "channel_title",
      "snippet": "content_snippet",
      "posted_at": "ISO datetime"
    }
  ]
}
```

**System message**:
```
Ты — редактор трендов. Получаешь статистику по новости и формируешь краткую карточку.
Верни JSON с полями title, summary, why_important, topics (список до 5 кратких тегов).
Пиши по-русски, без Markdown.
```

**Модель**: `GigaChat` (через `gpt2giga-proxy:8090/v1/chat/completions`)

**Параметры**:
- `temperature = 0.2`
- `max_tokens = 400`

**Результат**: `{title, summary, why_important, topics}` или `None` при ошибке

**Fallback**:
- `summary`: `_summarize_samples(sample_posts)` — склейка первых 2 сниппетов
- `why_important`: `_build_why_important(window_mentions, window_baseline, ...)`

---

### Этап 10: Построение card payload

**Метод**: `_build_card_payload(...) -> Dict`

**Структура**:
```json
{
  "id": "cluster_key",
  "title": "primary_topic",
  "status": "emerging",
  "time_window": {
    "from": "ISO datetime",
    "to": "ISO datetime",
    "duration_minutes": 60
  },
  "stats": {
    "mentions": 15,
    "baseline": 3,
    "burst_score": 5.0,
    "sources": 5,
    "channels": 5,
    "coherence": 0.75
  },
  "summary": "...",
  "why_important": "...",
  "keywords": [...],
  "topics": [...],
  "example_posts": [
    {
      "post_id": "...",
      "channel_id": "...",
      "channel_title": "...",
      "posted_at": "ISO datetime",
      "content_snippet": "..."
    }
  ]
}
```

---

### Этап 11: Сохранение в PostgreSQL

**Метод**: `_upsert_cluster(...) -> cluster_id`

**Таблица `trend_clusters`**:
- `INSERT ... ON CONFLICT (cluster_key) DO UPDATE`
- Обновляются: `last_activity_at`, `summary`, `keywords`, `primary_topic`, `novelty_score`, `coherence_score`, `source_diversity`, `trend_embedding`, `window_start`, `window_end`, `window_mentions`, `freq_baseline`, `burst_score`, `sources_count`, `channels_count`, `why_important`, `topics`, `card_payload`

**Таблица `trend_metrics`**:
- `INSERT ... ON CONFLICT (cluster_id, metrics_at) DO UPDATE`
- Поля: `freq_short`, `freq_long`, `freq_baseline`, `rate_of_change`, `burst_score`, `source_diversity`, `coherence_score`
- `metrics_at`: округлённое до минуты (`replace(second=0, microsecond=0)`)

**Qdrant**:
- `upsert_vector(collection_name="trends_hot", vector_id=cluster_key, vector=embedding, payload={cluster_id, cluster_key, primary_topic, channel_id})`

---

### Этап 12: Публикация emerging event (если пороги превышены)

**Метод**: `_maybe_emit_emerging(...)`

**Условия публикации**:
```python
ratio = burst_score (freq_short / expected_short_baseline)
should_emit = (
    ratio >= TREND_FREQ_RATIO_THRESHOLD (3.0) and
    source_diversity >= TREND_MIN_SOURCE_DIVERSITY (3) and
    coherence >= TREND_COHERENCE_THRESHOLD (0.55)
)
```

**Cooldown механизм**:
- Redis key: `trend:{cluster_key}:emitted`
- TTL: `TREND_EMERGING_COOLDOWN_SEC` (900 секунд = 15 минут)
- `SETNX` — публикация только если ключ не существует

**Event payload** (`TrendEmergingEventV1`):
```python
{
    "idempotency_key": f"trend:{cluster_id}:{post_id}",
    "cluster_id": "...",
    "cluster_key": "...",
    "post_id": "...",
    "channel_id": "...",
    "channel_title": "...",
    "primary_topic": "...",
    "keywords": [...],
    "freq_short": 10,
    "freq_baseline": 2,
    "source_diversity": 5,
    "burst_score": 5.0,
    "coherence": 0.75,
    "detected_at": "ISO datetime"
}
```

**Stream**: `stream:trends.emerging`

---

## Стабильный пайплайн (Stable / Confirmation)

**Компонент**: `api/tasks/scheduler_tasks.py::trends_stable_task`

**Триггер**: Ежечасный cron (APScheduler)

**Алгоритм**:
1. Загрузка `trend_clusters` со статусом `"emerging"` и `last_activity_at >= cutoff` (по умолчанию 3 часа)
2. Для каждого кластера:
   - Загрузка последних метрик из `trend_metrics`
   - Проверка порогов:
     - `freq_long >= min_freq` (по умолчанию 5)
     - `source_diversity >= min_sources` (по умолчанию 3)
     - `burst_score >= min_burst` (по умолчанию 1.5)
3. Если все пороги превышены:
   - Обновление статуса: `status = "stable"`
   - Создание записи `TrendDetection` (если `resolved_trend_id` отсутствует)
   - Связывание: `cluster.resolved_trend_id = trend.id`

---

## Персонализация трендов

**Фильтрация по пользователю**: `api/routers/trends.py`

**Алгоритм**:
1. Загрузка каналов пользователя: `SELECT channel_id FROM user_channels WHERE user_id = ? AND is_active = true`
2. Фильтрация `trend_clusters`:
   - Если `user_id` передан, выбираются только кластеры, у которых есть посты из каналов пользователя
3. Фильтрация `example_posts`:
   - `SELECT ... FROM trend_cluster_posts WHERE cluster_id = ? AND channel_id IN (user_channels)`
4. Пересчёт метрик:
   - `mentions` = количество постов пользователя в кластере
   - `sources` = количество уникальных каналов пользователя
   - `channels` = `sources`

**Endpoints с персонализацией**:
- `GET /api/trends/emerging?user_id={uuid}`
- `GET /api/trends/clusters?user_id={uuid}`
- `GET /api/trends/clusters/{cluster_id}?user_id={uuid}`

---

## Метрики и наблюдаемость

### Prometheus метрики

**Worker**:
- `trend_events_processed_total{status}` — обработанные события `posts.indexed`
- `trend_emerging_events_total{status}` — опубликованные `trends.emerging` события
- `trend_worker_latency_seconds{outcome}` — латентность обработки поста
- `trend_card_llm_requests_total{outcome}` — запросы к LLM для обогащения карточек
- `trend_cluster_sample_posts` — гистограмма количества sample posts

**API**:
- `trends_personal_requests_total{endpoint, outcome}` — персонализированные запросы

---

## Конфигурация

### Environment Variables

**Worker**:
- `TREND_FREQ_RATIO_THRESHOLD` (default: 3.0) — минимальный burst ratio для emerging
- `TREND_MIN_SOURCE_DIVERSITY` (default: 3) — минимальное количество источников
- `TREND_COHERENCE_THRESHOLD` (default: 0.55) — минимальная similarity для кластеризации
- `TREND_EMERGING_COOLDOWN_SEC` (default: 900) — cooldown между публикациями emerging events
- `TREND_CARD_WINDOW_SECONDS` (default: 3600) — окно для карточки (1 час)
- `TREND_CARD_LLM_ENABLED` (default: true) — включить LLM-обогащение
- `TREND_CARD_LLM_MODEL` (default: "GigaChat") — модель для LLM
- `TREND_CARD_LLM_MAX_TOKENS` (default: 400) — максимальное количество токенов
- `TREND_CARD_LLM_REFRESH_MINUTES` (default: 10) — минимальный интервал между обновлениями
- `TREND_CLUSTER_SAMPLE_LIMIT` (default: 10) — максимальное количество sample posts
- `TREND_STOPWORDS` — дополнительные стоп-слова (через запятую)
- `TRENDS_HOT_COLLECTION` (default: "trends_hot") — имя коллекции в Qdrant

**API**:
- `TREND_CARD_LLM_MODEL` (default: "GigaChat") — модель для LLM в API

---

## Качество трендов

### Правила фильтрации

**Запрещённые тренды**:
1. Одиночные слова без хэштега (generic labels)
2. Стоп-слова (местоимения, предлоги, междометия)
3. Одиночные упоминания без burst (`burst_score < 1.0`)
4. Отсутствие summary или sources

### Санитизация заголовков

**Метод**: `_sanitize_card_title` в `api/routers/trends.py`

**Алгоритм**:
1. Проверка generic title: длина < 4, стоп-слова, одиночное слово без `#`
2. Если generic:
   - Попытка вывести из `topics` (объединение 2+ тем)
   - Попытка вывести из `keywords` (объединение 2+ keywords)
   - Попытка вывести из `example_posts` (первые 3–5 слов из сниппета)
3. Fallback: "Тренд"

---

## Производительность

### Оптимизации

1. **Redis Time-Series**: TTL автоматически очищает старые данные
2. **Qdrant ANN**: Быстрый поиск ближайших векторов (O(log N))
3. **Batch processing**: Consumer group обрабатывает события батчами
4. **LLM Cooldown**: Ограничение частоты запросов к LLM
5. **Sample posts limit**: Ограничение количества примеров в карточке

### Латентность

- **Обработка поста**: ~100–500ms (зависит от LLM и Qdrant)
- **LLM enrichment**: ~1–3s (если активировано)
- **Qdrant search**: ~10–50ms

---

## Ограничения и улучшения

### Текущие ограничения

1. **Embedding размерность**: Фиксированная (1536), должна совпадать с Qdrant collection
2. **Окна агрегации**: Фиксированные (5m, 1h, 24h)
3. **Burst detection**: Простая формула (observed / expected), можно улучшить через CUSUM
4. **Персонализация**: Только фильтрация по каналам, нет учёта интересов пользователя

### Возможные улучшения

1. **Динамические окна**: Адаптивные окна на основе паттернов
2. **Graph-based clustering**: Использование Neo4j для связей между трендами
3. **Multi-modal embeddings**: Учёт изображений и видео
4. **Sentiment analysis**: Учёт тональности постов
5. **Temporal patterns**: Учёт времени суток, дня недели
6. **A/B testing**: Тестирование различных порогов и алгоритмов

---

## Заключение

Алгоритм обнаружения трендов использует комбинацию семантической кластеризации (embeddings + Qdrant ANN), временных окон агрегации (Redis), динамических метрик (burst, coherence, novelty) и LLM-обогащения для генерации человекочитаемых карточек трендов. Система работает в реальном времени (reactive) и подтверждает устойчивые тренды через стабильный пайплайн (stable).

