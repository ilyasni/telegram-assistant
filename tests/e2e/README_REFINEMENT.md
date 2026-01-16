# E2E тесты для пайплайна рефайнмента кластеров

## Описание

E2E тесты проверяют полный пайплайн рефайнмента кластеров трендов:

1. **Оценка метрик когерентности** — проверка вычисления и сохранения метрик
2. **Разделение низкокогерентных кластеров** — проверка split операции
3. **Слияние похожих кластеров** — проверка merge операции
4. **Полный пайплайн** — комплексная проверка всех этапов

## Структура тестов

### `test_refinement_evaluates_metrics`
Проверяет, что refinement service:
- Вычисляет метрики когерентности для кластеров
- Сохраняет метрики в БД (`coherence_score`, `silhouette_score`, `npmi_score`, `intra_cluster_similarity`)
- Обновляет `last_refinement_at`

### `test_refinement_splits_low_coherence_cluster`
Проверяет, что refinement service:
- Определяет кластеры с низкой когерентностью
- Разделяет их на подкластеры (если валидация пройдена)
- Создаёт иерархическую структуру (parent_cluster_id, cluster_level)

### `test_refinement_merges_similar_clusters`
Проверяет, что refinement service:
- Находит похожие кластеры (по keywords и embeddings)
- Объединяет их (если валидация пройдена)
- Перераспределяет посты между кластерами

### `test_refinement_full_pipeline`
Комплексный тест, проверяющий:
- Создание нескольких кластеров с разной когерентностью
- Запуск всех этапов рефайнмента
- Проверку результатов всех операций

## Запуск тестов

### В контейнере worker

```bash
docker compose exec worker bash -c "cd /tmp/tests && export PYTHONPATH=/app && python -m pytest e2e/test_trend_refinement_pipeline.py -v"
```

### Запуск конкретного теста

```bash
docker compose exec worker bash -c "cd /tmp/tests && export PYTHONPATH=/app && python -m pytest e2e/test_trend_refinement_pipeline.py::test_refinement_evaluates_metrics -v"
```

### Запуск с маркером e2e

```bash
docker compose exec worker bash -c "cd /tmp/tests && export PYTHONPATH=/app && python -m pytest -m e2e e2e/test_trend_refinement_pipeline.py -v"
```

## Требования

### Переменные окружения

- `DATABASE_URL` — URL базы данных PostgreSQL
- `QDRANT_URL` — URL Qdrant сервера

### Зависимости

- PostgreSQL с таблицами `trend_clusters`, `posts`, `channels`, `trend_cluster_posts`, `post_enrichment`
- Qdrant с коллекциями для embeddings
- Python модули: `pytest`, `sqlalchemy`, `asyncpg`, `numpy`, `structlog`

## Примечания

- Тесты создают тестовые данные в БД и Qdrant
- Тесты не удаляют данные автоматически (для отладки)
- Для production-окружения рекомендуется использовать отдельную тестовую БД
- Тесты могут пропускаться (`pytest.skip`), если зависимости недоступны

## Структура тестовых данных

### Кластеры
- Создаются с уникальными `cluster_key` (UUID-based)
- Имеют различные значения `coherence_score` для тестирования split/merge
- Связаны с постами через `trend_cluster_posts`

### Посты
- Создаются с уникальными `telegram_message_id`
- Связаны с каналами
- Имеют keywords в `post_enrichment` (kind='classify')

### Embeddings
- Хранятся в Qdrant в коллекции `t{tenant_id}_posts`
- Размерность: 384 (можно настроить)
- Используются для вычисления метрик когерентности и split/merge

## Отладка

### Просмотр созданных данных

```sql
-- В БД
SELECT id, cluster_key, coherence_score, last_refinement_at 
FROM trend_clusters 
WHERE cluster_key LIKE 'test_e2e%';

-- Подкластеры
SELECT id, cluster_key, parent_cluster_id, cluster_level 
FROM trend_clusters 
WHERE parent_cluster_id IS NOT NULL;
```

### Логи

Тесты используют `structlog` для логирования. Логи можно просмотреть в выводе pytest:

```bash
docker compose exec worker bash -c "cd /tmp/tests && export PYTHONPATH=/app && python -m pytest e2e/test_trend_refinement_pipeline.py -v -s"
```

## Известные ограничения

1. **LLM валидация**: Split и merge могут не выполняться, если LLM валидация отклоняет операции
2. **Время выполнения**: E2E тесты могут занимать несколько секунд из-за реальных операций с БД и Qdrant
3. **Зависимости**: Тесты требуют доступ к реальным сервисам (БД, Qdrant)

## Следующие шаги

- Добавить cleanup фикстуры для автоматической очистки тестовых данных
- Добавить тесты для edge cases (пустые кластеры, очень большие кластеры)
- Добавить тесты производительности (stress testing)

