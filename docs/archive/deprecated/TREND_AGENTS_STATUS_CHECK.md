# Проверка статуса Trend Agents

**Дата**: 2025-01-22  
**Context7**: Инструкции по проверке работы Trend Agents

---

## Context

Проверка статуса работы Trend Agents для ответа на вопрос: "Добавляются или анализируются ли новые тренды сейчас?"

---

## Быстрая проверка

### 1. Проверка статуса контейнеров

```bash
docker compose ps | grep -E "worker|api"
```

**Ожидаемый результат**: Контейнеры должны быть в статусе `Up` и `healthy`.

---

### 2. Проверка событий в Redis Streams

```bash
# Количество событий posts.indexed (должно быть > 0)
docker compose exec redis redis-cli XLEN stream:posts:indexed

# Количество событий trends.emerging (должно быть > 0, если есть новые тренды)
docker compose exec redis redis-cli XLEN stream:trends:emerging
```

**Ожидаемый результат**:
- `stream:posts:indexed` должен содержать события (например, 6989)
- `stream:trends:emerging` должен содержать события, если есть новые тренды (например, 14)

---

### 3. Проверка метрик Prometheus

```bash
# Метрики обработки событий TrendDetectionWorker
curl -s http://localhost:8001/metrics | grep trend_events_processed_total

# Метрики публикации emerging трендов
curl -s http://localhost:8001/metrics | grep trend_emerging_events_total
```

**Ожидаемые метрики**:
- `trend_events_processed_total{status="processed"}` - количество обработанных постов
- `trend_events_processed_total{status="album_duplicate"}` - пропущенные дубликаты альбомов
- `trend_events_processed_total{status="missing_post"}` - посты не найдены
- `trend_events_processed_total{status="invalid"}` - невалидные события
- `trend_emerging_events_total{status="published"}` - опубликованные emerging тренды

---

### 4. Проверка логов TrendDetectionWorker

```bash
# Логи инициализации и обработки
docker compose logs worker --tail 100 | grep -i "trend.*worker\|TrendDetectionWorker"
```

**Ожидаемые логи**:
- `TrendDetectionWorker initialized` - инициализация worker
- `TrendDetectionWorker initialization completed` - завершение инициализации
- `trend_worker_processing_error` - ошибки обработки (если есть)

---

### 5. Проверка Consumer Groups в Redis

```bash
# Проверка consumer groups для stream:posts:indexed
docker compose exec redis redis-cli XINFO GROUPS stream:posts:indexed
```

**Ожидаемый результат**: Должна быть consumer group для `trend_workers` с активными consumers.

---

### 6. Проверка трендов в БД

```bash
# Количество кластеров и последняя активность
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total_clusters,
    COUNT(*) FILTER (WHERE status = 'active') as active_clusters,
    MAX(last_activity_at) as last_activity
FROM trend_clusters;
"
```

**Ожидаемый результат**:
- `total_clusters` > 0 - есть кластеры трендов
- `active_clusters` > 0 - есть активные кластеры
- `last_activity` - время последней активности (должно быть недавно)

---

### 7. Проверка новых трендов за последний час

```bash
# Количество новых emerging трендов за последний час
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as new_trends_last_hour
FROM trend_clusters
WHERE last_activity_at >= NOW() - INTERVAL '1 hour'
  AND status = 'active';
"
```

**Ожидаемый результат**: `new_trends_last_hour` > 0, если есть новые тренды.

---

## Детальная диагностика

### Проверка регистрации TrendDetectionWorker

**Файл**: `api/worker/run_all_tasks.py`

```python
supervisor.register_task(TaskConfig(
    name="trend_worker",
    task_func=create_trend_worker_task,
    max_retries=5,
    initial_backoff=1.0,
    max_backoff=60.0,
    backoff_multiplier=2.0
))
```

**Проверка**:
```bash
docker compose logs worker | grep -i "trend_worker.*registered\|trend_worker.*started"
```

---

### Проверка обработки событий posts.indexed

**Метод**: `TrendDetectionWorker._handle_message`

**Проверка**:
```bash
# Логи обработки событий
docker compose logs worker | grep -i "trend_worker.*processed\|trend_worker.*album_duplicate"
```

---

### Проверка публикации emerging трендов

**Метод**: `TrendDetectionWorker._maybe_emit_emerging`

**Проверка**:
```bash
# Логи публикации emerging трендов
docker compose logs worker | grep -i "trend.*emerging.*published"
```

---

## Возможные проблемы

### 1. TrendDetectionWorker не запущен

**Симптомы**:
- Нет логов `TrendDetectionWorker initialized`
- Метрики `trend_events_processed_total` не обновляются
- Consumer group `trend_workers` отсутствует

**Решение**:
```bash
# Перезапуск worker
docker compose restart worker

# Проверка логов
docker compose logs worker --tail 50
```

---

### 2. Нет событий posts.indexed

**Симптомы**:
- `XLEN stream:posts:indexed` = 0
- Нет новых постов для обработки

**Решение**:
- Проверить парсинг каналов
- Проверить публикацию событий `posts.indexed` в `IndexingTask`

---

### 3. События не обрабатываются

**Симптомы**:
- `XLEN stream:posts:indexed` > 0, но метрики не обновляются
- Consumer group показывает pending сообщения

**Решение**:
```bash
# Проверка pending сообщений
docker compose exec redis redis-cli XPENDING stream:posts:indexed trend_workers

# Очистка pending сообщений (если нужно)
docker compose exec redis redis-cli XAUTOCLAIM stream:posts:indexed trend_workers trend_worker_1 0 0
```

---

### 4. Нет новых трендов

**Симптомы**:
- События обрабатываются, но `trend_emerging_events_total` = 0
- Нет новых кластеров в БД

**Причины**:
- Пороги слишком высокие (`TREND_FREQ_RATIO_THRESHOLD`, `TREND_MIN_SOURCE_DIVERSITY`)
- Недостаточно постов для формирования трендов
- Низкий `coherence_score` (похожесть постов)

**Решение**:
- Проверить настройки порогов в `.env`
- Проверить метрики `trend_worker_latency_seconds` и `trend_events_processed_total`
- Проверить логи на наличие ошибок

---

## Выводы

**Тренды добавляются и анализируются, если**:
1. ✅ Контейнеры `worker` и `api` запущены и healthy
2. ✅ `stream:posts:indexed` содержит события (> 0)
3. ✅ Метрики `trend_events_processed_total{status="processed"}` обновляются
4. ✅ Consumer group `trend_workers` активна
5. ✅ В БД есть активные кластеры с недавней активностью
6. ✅ Метрики `trend_emerging_events_total{status="published"}` > 0 (если есть emerging тренды)

**Тренды НЕ добавляются, если**:
1. ❌ TrendDetectionWorker не запущен
2. ❌ Нет событий `posts.indexed`
3. ❌ События не обрабатываются (pending в consumer group)
4. ❌ Пороги слишком высокие (нет emerging трендов)
5. ❌ Ошибки в логах

---

## Рекомендации

1. ✅ Регулярно проверять метрики Prometheus для мониторинга активности
2. ✅ Настроить алерты на отсутствие обработки событий
3. ✅ Мониторить логи на наличие ошибок
4. ✅ Проверять consumer groups на наличие pending сообщений
5. ✅ Настроить дашборды Grafana для визуализации метрик

