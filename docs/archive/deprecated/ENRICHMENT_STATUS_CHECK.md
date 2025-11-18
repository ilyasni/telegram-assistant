# Проверка работы обогащения: Crawl4ai, Vision и OCR Fallback

**Дата**: 2025-01-22  
**Context7**: Проверка работы всех типов обогащения и диагностика проблем

---

## Context

Проверка работы обогащения через:
1. **Crawl4ai** - обогащение веб-страниц
2. **Vision** - анализ изображений через GigaChat Vision API
3. **OCR Fallback (OCP)** - OpenRouter Vision API для OCR и анализа изображений

---

## Статус обогащения

### 1. Crawl4ai ✅ Работает (с проблемой)

**Статус**: Обогащение работает, но есть проблема с `crawl_trigger`

**Детали**:
- Последнее обогащение: `2025-11-17 15:06:31`
- Всего обогащений в БД: `4969` (kind: `general`, provider: `enrichment_task`)
- Обогащение сохраняется в БД корректно
- Проблема: `crawl_trigger` перезапускается каждые 30 секунд

**Логи**:
```
2025-11-17 15:04:34,221 [WARNING] supervisor: Task crawl_trigger completed unexpectedly, will be restarted
2025-11-17 15:05:04,222 [WARNING] supervisor: Task crawl_trigger completed unexpectedly, will be restarted
2025-11-17 15:05:34,222 [WARNING] supervisor: Task crawl_trigger completed unexpectedly, will be restarted
```

**Проблема**: `CrawlTriggerTask` завершается неожиданно и перезапускается supervisor'ом.

**Рекомендация**: Нужна диагностика `CrawlTriggerTask` для выявления причины завершения.

---

### 2. Vision (GigaChat) ⚠️ Не активен

**Статус**: Обогащение работает, но нет новых событий

**Детали**:
- Последнее обогащение: `2025-11-15 11:17:17` (2 дня назад)
- Всего обогащений в БД: `405` (kind: `vision`, provider: `gigachat`)
- В логах нет активных Vision обогащений
- Метрики Vision пустые: `vision_analysis_requests_total` = 0

**Возможные причины**:
1. Нет новых постов с медиа для анализа
2. Vision события не публикуются в `posts.vision`
3. `VisionAnalysisTask` не обрабатывает события

**Рекомендация**: Проверить:
- Публикуются ли события в `posts.vision`
- Обрабатывает ли `VisionAnalysisTask` события
- Есть ли новые посты с медиа

---

### 3. OCR Fallback (OCP) ⚠️ Не активен

**Статус**: Обогащение работает, но нет новых событий

**Детали**:
- Последнее обогащение: `2025-11-15 11:17:17` (2 дня назад)
- Всего обогащений в БД: `356` (kind: `vision`, provider: `ocr_fallback`)
- В логах нет активных OCR Fallback обогащений

**Конфигурация**:
- `ocr_fallback_enabled: true` в `enrichment_policy.yml`
- Использует OpenRouter Vision API с моделью `qwen/qwen2.5-vl-32b-instruct:free`
- Fallback на OCR при ошибках GigaChat Vision

**Возможные причины**:
1. Нет новых постов с медиа для анализа
2. GigaChat Vision работает без ошибок (нет необходимости в fallback)
3. OCR Fallback не вызывается

**Рекомендация**: Проверить:
- Вызывается ли OCR Fallback при ошибках GigaChat Vision
- Есть ли новые посты с медиа для анализа

---

## Статистика обогащений в БД

```sql
SELECT kind, provider, COUNT(*) as count, MAX(created_at) as last_enrichment 
FROM post_enrichment 
GROUP BY kind, provider 
ORDER BY last_enrichment DESC;
```

**Результаты**:
- `general` (enrichment_task): 4969 обогащений, последнее: 2025-11-17 12:06:31
- `tags` (gigachat): 5194 обогащений, последнее: 2025-11-17 12:06:31
- `vision` (paddleocr): 2247 обогащений, последнее: 2025-11-16 21:04:17
- `vision` (ocr_fallback): 356 обогащений, последнее: 2025-11-15 11:17:17
- `vision` (gigachat): 405 обогащений, последнее: 2025-11-15 11:17:17

---

## Проблемы и рекомендации

### 1. Crawl4ai: crawl_trigger перезапускается

**Проблема**: `CrawlTriggerTask` завершается неожиданно каждые 30 секунд.

**Диагностика**:
```bash
# Проверить логи crawl_trigger
docker compose logs --tail=2000 worker | grep -B 10 "crawl_trigger completed unexpectedly"

# Проверить код CrawlTriggerTask
cat worker/tasks/crawl_trigger_task.py
```

**Рекомендация**: 
1. Проверить обработку исключений в `CrawlTriggerTask`
2. Добавить детальное логирование для диагностики
3. Проверить подключение к Redis и доступность streams

### 2. Vision: Нет новых обогащений

**Проблема**: Vision обогащения не активны (последнее 2 дня назад).

**Диагностика**:
```bash
# Проверить события posts.vision
docker compose exec -T redis redis-cli XINFO STREAM posts.vision

# Проверить логи VisionAnalysisTask
docker compose logs --tail=2000 worker | grep -iE "vision|gigachat.*vision"
```

**Рекомендация**:
1. Проверить, публикуются ли события в `posts.vision`
2. Проверить, обрабатывает ли `VisionAnalysisTask` события
3. Проверить, есть ли новые посты с медиа для анализа

### 3. OCR Fallback: Нет новых обогащений

**Проблема**: OCR Fallback не активен (последнее 2 дня назад).

**Диагностика**:
```bash
# Проверить логи OCR Fallback
docker compose logs --tail=2000 worker | grep -iE "ocr.*fallback|openrouter.*vision"
```

**Рекомендация**:
1. Проверить, вызывается ли OCR Fallback при ошибках GigaChat Vision
2. Проверить конфигурацию `ocr_fallback_enabled`
3. Проверить доступность OpenRouter Vision API

---

## Context7 Best Practices

### 1. Мониторинг обогащения

**Рекомендации**:
- Добавить метрики для каждого типа обогащения
- Мониторить время последнего обогащения
- Алерты на отсутствие обогащений > 24 часов

### 2. Обработка ошибок

**Рекомендации**:
- Детальное логирование ошибок
- Retry политики для временных ошибок
- DLQ для критичных ошибок

### 3. Диагностика

**Рекомендации**:
- Проверять статус streams (posts.vision, posts.crawl)
- Проверять метрики Prometheus
- Проверять логи на наличие ошибок

---

## Checks

### 1. Проверка Crawl4ai
```bash
# Проверить последние обогащения
docker compose logs --tail=1000 worker | grep -iE "crawl|enrichment.*crawl"

# Проверить crawl_trigger
docker compose logs --tail=2000 worker | grep -B 10 "crawl_trigger completed unexpectedly"
```

### 2. Проверка Vision
```bash
# Проверить события posts.vision
docker compose exec -T redis redis-cli XINFO STREAM posts.vision

# Проверить метрики Vision
curl -s "http://localhost:9090/api/v1/query?query=vision_analysis_requests_total"
```

### 3. Проверка OCR Fallback
```bash
# Проверить логи OCR Fallback
docker compose logs --tail=2000 worker | grep -iE "ocr.*fallback|openrouter.*vision"

# Проверить конфигурацию
cat api/worker/config/enrichment_policy.yml | grep -A 5 "ocr_fallback"
```

---

## Вывод

**Статус**:
- ✅ **Crawl4ai**: Работает, но `crawl_trigger` перезапускается (нужна диагностика)
- ⚠️ **Vision**: Не активен (нет новых обогащений 2 дня)
- ⚠️ **OCR Fallback**: Не активен (нет новых обогащений 2 дня)

**Рекомендации**:
1. Диагностировать проблему с `crawl_trigger`
2. Проверить, почему Vision обогащения не активны
3. Проверить, почему OCR Fallback не вызывается

