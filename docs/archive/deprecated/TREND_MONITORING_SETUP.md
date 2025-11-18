# Настройка мониторинга Trend Agents

**Дата**: 2025-01-22  
**Context7**: Инструкции по мониторингу активности Trend Agents

---

## Context

Настроен мониторинг для отслеживания активности Trend Agents и ожидания новых постов.

---

## Быстрый старт

### Запуск мониторинга

```bash
# Запуск мониторинга с интервалом 60 секунд (по умолчанию)
./scripts/monitor_trend_activity.sh

# Запуск с кастомным интервалом (например, 30 секунд)
./scripts/monitor_trend_activity.sh 30
```

**Что показывает скрипт**:
- 📊 Метрики Prometheus (обработанные события, опубликованные тренды)
- 📊 Количество событий в Redis Streams
- 📈 Статистика трендов в БД
- ✅ Индикация новых событий (помечается "+N" при появлении новых)

---

## Ручная проверка

### 1. Проверка последних событий posts.indexed

```bash
# Последние 5 событий
docker compose exec redis redis-cli XREVRANGE stream:posts:indexed + - COUNT 5

# Последнее событие
docker compose exec redis redis-cli XREVRANGE stream:posts:indexed + - COUNT 1
```

**Ожидаемый результат**: Список событий с полями `post_id`, `tenant_id`, `timestamp`.

---

### 2. Проверка метрик в реальном времени

```bash
# Все метрики Trend Agents
docker compose exec worker curl -s http://localhost:8001/metrics | grep trend

# Только обработанные события
docker compose exec worker curl -s http://localhost:8001/metrics | grep trend_events_processed_total

# Только опубликованные тренды
docker compose exec worker curl -s http://localhost:8001/metrics | grep trend_emerging_events_total
```

---

### 3. Проверка новых постов в БД

```bash
# Количество новых постов за последний час
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as posts_last_hour 
FROM posts 
WHERE created_at >= NOW() - INTERVAL '1 hour';
"
```

---

### 4. Проверка новых трендов в БД

```bash
# Количество новых трендов за последний час
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as new_trends_last_hour,
    MAX(last_activity_at) as last_activity
FROM trend_clusters
WHERE last_activity_at >= NOW() - INTERVAL '1 hour'
  AND status = 'active';
"
```

---

### 5. Проверка логов на ошибки

```bash
# Ошибки Trend Agents
docker compose logs worker --tail 100 | grep -i "trend.*error\|trend.*warning"

# Обработанные события
docker compose logs worker --tail 100 | grep -i "trend.*processed"

# Публикация emerging трендов
docker compose logs worker --tail 100 | grep -i "trend.*emerging.*published"
```

---

## Автоматический мониторинг

### Настройка алертов (опционально)

Создайте файл `scripts/check_trend_health.sh`:

```bash
#!/bin/bash
# Проверка здоровья Trend Agents

PROCESSED=$(docker compose exec -T worker curl -s http://localhost:8001/metrics 2>/dev/null | grep 'trend_events_processed_total{status="processed"}' | grep -oP '\d+\.\d+' | head -1)
EMERGING=$(docker compose exec -T worker curl -s http://localhost:8001/metrics 2>/dev/null | grep 'trend_emerging_events_total{status="published"}' | grep -oP '\d+\.\d+' | head -1)

if [ -z "$PROCESSED" ] || [ "$PROCESSED" = "0" ]; then
    echo "⚠️  WARNING: TrendDetectionWorker не обрабатывает события"
    exit 1
fi

if [ -z "$EMERGING" ]; then
    echo "⚠️  WARNING: Нет метрик emerging трендов"
    exit 1
fi

echo "✅ Trend Agents работают нормально"
echo "   Обработано событий: $PROCESSED"
echo "   Опубликовано трендов: $EMERGING"
exit 0
```

---

## Что делать при отсутствии активности

### 1. Проверить парсинг каналов

```bash
# Проверить последний парсинг каналов
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    c.title,
    c.last_parsed_at,
    NOW() - c.last_parsed_at as time_since_parsing
FROM channels c
WHERE c.is_active = true
ORDER BY c.last_parsed_at DESC
LIMIT 10;
"
```

---

### 2. Проверить публикацию событий posts.indexed

```bash
# Проверить логи IndexingTask
docker compose logs worker --tail 100 | grep -i "indexing.*published\|posts.indexed"

# Проверить consumer groups
docker compose exec redis redis-cli XINFO GROUPS stream:posts:indexed
```

---

### 3. Проверить настройки порогов

```bash
# Проверить переменные окружения
docker compose exec worker env | grep TREND_

# Основные пороги:
# - TREND_FREQ_RATIO_THRESHOLD (по умолчанию 3.0)
# - TREND_MIN_SOURCE_DIVERSITY (по умолчанию 3)
# - TREND_COHERENCE_THRESHOLD (по умолчанию 0.55)
```

---

### 4. Временно снизить пороги для тестирования

Добавьте в `.env`:

```env
# Временные настройки для тестирования
TREND_FREQ_RATIO_THRESHOLD=2.0
TREND_MIN_SOURCE_DIVERSITY=2
TREND_COHERENCE_THRESHOLD=0.5
```

Затем перезапустите worker:

```bash
docker compose restart worker
```

---

## Ожидание новых постов

### Что происходит при появлении новых постов

1. **Парсинг канала** → публикация `posts.parsed`
2. **Tagging Task** → публикация `posts.tagged`
3. **Enrichment Task** → публикация `posts.enriched`
4. **Indexing Task** → публикация `posts.indexed`
5. **TrendDetectionWorker** → обработка `posts.indexed`:
   - Загрузка поста из БД
   - Генерация embedding
   - Кластеризация
   - Обновление метрик
   - Публикация `trends.emerging` (если пороги превышены)

---

### Как отслеживать появление новых постов

```bash
# Запустить мониторинг
./scripts/monitor_trend_activity.sh

# В другом терминале проверить последние события
watch -n 10 'docker compose exec redis redis-cli XREVRANGE stream:posts:indexed + - COUNT 1'
```

---

## Выводы

✅ **Мониторинг настроен**:
- Скрипт `scripts/monitor_trend_activity.sh` готов к использованию
- Инструкции по ручной проверке добавлены
- Рекомендации по диагностике проблем добавлены

✅ **Ожидание новых постов**:
- TrendDetectionWorker работает и готов обрабатывать новые события
- При появлении новых постов они будут автоматически обработаны
- Новые тренды будут опубликованы, если пороги превышены

---

## Следующие шаги

1. ✅ Запустить мониторинг: `./scripts/monitor_trend_activity.sh`
2. ✅ Дождаться новых постов (парсинг каналов происходит автоматически)
3. ✅ Проверить метрики на наличие новых обработанных событий
4. ✅ Проверить БД на наличие новых трендов

