# Исправление EnrichmentTask и Trend Agents Monitoring

**Дата**: 2025-01-22  
**Context7**: Исправление критической проблемы с EnrichmentTask и улучшение мониторинга Trend Agents

---

## Context

Обнаружена критическая проблема: EnrichmentTask читает события из `posts.tagged` (5145 событий), но публикует только 3 события в `posts.enriched`. Это блокирует всю цепочку обработки: `posts.indexed` не получает новые события, Trend Agents не обрабатывают новые посты.

---

## Обнаруженные проблемы

### ❌ ПРОБЛЕМА 1: EnrichmentTask не публикует события для всех постов

**Симптомы**:
- `posts.tagged`: 5145 событий
- `posts.enriched`: только 3 события в stream (!!!) 
- Consumer group `enrich_workers`: `entries-read=5145`, `lag=0` - все прочитано, но не обработано
- Consumer group `indexing_workers`: `entries-read=10081`, но stream показывает только 3 события

**Корневая причина**:
1. **Пост не найден в БД** - событие не публикуется (строка 262)
2. **Ошибка сериализации** - событие не публикуется (строка 457)
3. **Ошибка публикации** - событие не публикуется (строка 501)
4. **Ошибка обработки** - событие не публикуется (строка 535)

**Проблема**: Во всех этих случаях происходит `return` без публикации `posts.enriched`, что блокирует цепочку обработки.

---

## Исправления

### Исправление 1: Публикация skipped enrichment при отсутствии поста в БД

**Файл**: `api/worker/tasks/enrichment_task.py` (строка 258-264)

**Было**:
```python
if not post_context:
    logger.warning("Post not found in DB, skipping enrichment")
    posts_processed_total.labels(stage='enrichment', success='skip').inc()
    return  # ❌ Событие не публикуется!
```

**Стало**:
```python
if not post_context:
    logger.warning("Post not found in DB, skipping enrichment")
    posts_processed_total.labels(stage='enrichment', success='skip').inc()
    # Context7: Публикуем skipped enrichment для обеспечения цепочки обработки
    await self._publish_skipped_enrichment(event_data, "post_not_found")
    return
```

---

### Исправление 2: Публикация skipped enrichment при ошибке сериализации

**Файл**: `api/worker/tasks/enrichment_task.py` (строка 449-464)

**Было**:
```python
except Exception as e:
    logger.exception("enrich_serialize_fail")
    await self.publisher.to_dlq("posts.tagged", event_data, reason="serialize_error")
    posts_processed_total.labels(stage='enrichment', success='error').inc()
    return  # ❌ Событие не публикуется!
```

**Стало**:
```python
except Exception as e:
    logger.exception("enrich_serialize_fail")
    await self.publisher.to_dlq("posts.tagged", event_data, reason="serialize_error")
    posts_processed_total.labels(stage='enrichment', success='error').inc()
    # Context7: Публикуем skipped enrichment для обеспечения цепочки обработки
    try:
        await self._publish_skipped_enrichment(event_data, f"serialize_error: {str(e)[:50]}")
    except Exception as skip_error:
        logger.error("Failed to publish skipped enrichment after serialize error")
    return
```

---

### Исправление 3: Публикация skipped enrichment при ошибке публикации

**Файл**: `api/worker/tasks/enrichment_task.py` (строка 499-512)

**Было**:
```python
except Exception as e:
    logger.exception("enrich_publish_fail")
    await self.publisher.to_dlq("posts.tagged", {"data": body}, reason="publish_fail")
    posts_processed_total.labels(stage='enrichment', success='error').inc()
    return  # ❌ Событие не публикуется!
```

**Стало**:
```python
except Exception as e:
    logger.exception("enrich_publish_fail")
    await self.publisher.to_dlq("posts.tagged", {"data": body}, reason="publish_fail")
    posts_processed_total.labels(stage='enrichment', success='error').inc()
    # Context7: Пытаемся опубликовать skipped enrichment как fallback
    try:
        await self._publish_skipped_enrichment(event_data, f"publish_fail: {str(e)[:50]}")
    except Exception as skip_error:
        logger.error("Failed to publish skipped enrichment after publish error")
    return
```

---

### Исправление 4: Публикация skipped enrichment при ошибке обработки

**Файл**: `api/worker/tasks/enrichment_task.py` (строка 524-543)

**Было**:
```python
except Exception as e:
    logger.error("Error processing post")
    enrichment_requests_total.labels(provider='enrichment_task', operation='enrich', success=False).inc()
    posts_processed_total.labels(stage='enrichment', success='error').inc()
    await self.publisher.to_dlq("posts.tagged", event_data, reason="processing_error")
    # ❌ Событие не публикуется!
```

**Стало**:
```python
except Exception as e:
    logger.error("Error processing post")
    enrichment_requests_total.labels(provider='enrichment_task', operation='enrich', success=False).inc()
    posts_processed_total.labels(stage='enrichment', success='error').inc()
    await self.publisher.to_dlq("posts.tagged", event_data, reason="processing_error")
    # Context7: Публикуем skipped enrichment для обеспечения цепочки обработки
    try:
        await self._publish_skipped_enrichment(event_data, f"processing_error: {str(e)[:50]}")
    except Exception as skip_error:
        logger.error("Failed to publish skipped enrichment after processing error")
```

---

### Исправление 5: Добавление панелей с общим количеством событий в Grafana

**Проблема**: Метрики `rate()` показывают 0, когда нет новых событий за последние 5 минут, что не позволяет видеть общую активность.

**Исправление**: Добавлены панели с общим количеством событий (не rate) в `grafana/dashboards/trend_agents.json`:
- `Trend Events Processed - Total Count` - общее количество обработанных событий
- `Trend Emerging Events - Total Count` - общее количество опубликованных трендов
- `Trend Editor Requests - Total Count` - общее количество запросов к Editor Agent
- `Trend QA Filtered - Total Count` - общее количество отфильтрованных трендов

**Файл**: `grafana/dashboards/trend_agents.json` (строки 279-406)

**Запросы**:
```promql
# Общее количество обработанных событий
sum(trend_events_processed_total) by (status) or vector(0)

# Общее количество опубликованных трендов
sum(trend_emerging_events_total) by (status) or vector(0)

# Общее количество запросов к Editor Agent
sum(trend_editor_requests_total) by (outcome) or vector(0)

# Общее количество отфильтрованных трендов
sum(trend_qa_filtered_total) by (reason) or vector(0)
```

---

## Проверка результатов

### 1. Проверка публикации событий

```bash
# Проверка количества событий в streams
docker compose exec redis redis-cli XLEN stream:posts:enriched
docker compose exec redis redis-cli XLEN stream:posts:indexed

# Проверка последних событий
docker compose exec redis redis-cli XREVRANGE stream:posts:enriched + - COUNT 5
```

**Ожидаемый результат**: Количество событий в `posts.enriched` должно увеличиваться при обработке новых постов.

### 2. Проверка метрик в Grafana

1. Откройте дашборд: `Trend Agents Monitoring`
2. Проверьте панели с общим количеством событий (не rate)
3. Убедитесь, что данные обновляются каждые 30 секунд

### 3. Проверка цепочки обработки

```bash
# Проверка consumer groups
docker compose exec redis redis-cli XINFO GROUPS stream:posts:tagged
docker compose exec redis redis-cli XINFO GROUPS stream:posts:enriched
docker compose exec redis redis-cli XINFO GROUPS stream:posts:indexed
```

**Ожидаемый результат**: 
- `enrich_workers`: `lag=0` (все события обработаны)
- `indexing_workers`: `lag` должен уменьшаться при обработке новых событий
- `trend_workers`: `lag` должен уменьшаться при обработке новых событий

---

## Impact / Rollback

**Impact**:
- Исправления гарантируют, что все посты публикуют события `posts.enriched`, даже при ошибках
- Цепочка обработки не прерывается при ошибках в EnrichmentTask
- Мониторинг улучшен за счет панелей с общим количеством событий

**Rollback**:
- Все изменения обратно совместимы
- При необходимости можно откатить изменения по отдельности
- Метрики не влияют на функциональность, только на мониторинг

---

## Следующие шаги

1. Мониторинг публикации событий `posts.enriched`
2. Проверка появления новых событий `posts.indexed`
3. Проверка обработки событий Trend Agents
4. Анализ логов на наличие ошибок публикации

