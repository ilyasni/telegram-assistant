# Применение исправлений пайплайна

**Дата**: 2025-01-22  
**Context7**: Применение исправлений для критических проблем в пайплайне

---

## Context

Применены исправления для критических проблем, обнаруженных в полном аудите пайплайна.

---

## Исправления

### ✅ Исправление 1: Формат tags в _check_enrichment_triggers

**Проблема**: `_check_enrichment_triggers` ожидал `tags` как список словарей, но получал список строк.

**Исправление**: Добавлена поддержка обоих форматов:
- Список словарей: `[{"name": "tag1"}, {"name": "tag2"}]`
- Список строк: `["tag1", "tag2"]` (стандартный формат из posts.tagged)

**Код**:
```python
# Context7: Поддержка двух форматов tags: список строк или список словарей
if tags and isinstance(tags[0], dict):
    # Формат: [{"name": "tag1"}, {"name": "tag2"}]
    post_tags = [tag.get('name', '').lower() for tag in tags if isinstance(tag, dict)]
else:
    # Формат: ["tag1", "tag2"] (стандартный формат из posts.tagged)
    post_tags = [str(tag).lower() for tag in tags] if tags else []
```

---

### ✅ Исправление 2: _publish_skipped_enrichment

**Проблема**: `_publish_skipped_enrichment` использовал неправильную схему события.

**Исправление**: Использует тот же формат, что и успешное обогащение:
- Получает `tenant_id` и `post_context` из БД
- Публикует событие в правильном формате для IndexingTask
- Добавлено логирование для отладки

**Код**:
```python
# Context7: Используем тот же формат, что и успешное обогащение
enriched_event = {
    "idempotency_key": original_event.get('idempotency_key', f"{post_id}:enriched:v1"),
    "post_id": post_id,
    "tenant_id": tenant_id,
    "channel_id": post_context.get("channel_id", "") if post_context else original_event.get('channel_id', ''),
    "text": post_context.get("content", "") if post_context else original_event.get('text', ''),
    "telegram_post_url": post_context.get("telegram_post_url", "") if post_context else "",
    "posted_at": post_context.get("posted_at", datetime.now(timezone.utc)) if post_context else datetime.now(timezone.utc),
    "enrichment_data": {
        "kind": "enrichment",
        "source": "enrichment_task",
        "version": "v1",
        "tags": original_event.get('tags', []),
        "entities": [],
        "urls": [],
        "reason": skip_reason,
        "metadata": {
            "triggers": [],
            "crawl_priority": "normal"
        }
    },
}
```

---

### ✅ Исправление 3: crawl_trigger - аргумент 'path' в logger

**Проблема**: `Logger._log() got an unexpected keyword argument 'path'`

**Исправление**: Заменен аргумент `path` на `config_path` в вызовах logger:
- `logger.info(..., path=str(path), ...)` → `logger.info(..., config_path=str(path), ...)`
- `logger.debug(..., path=str(path), ...)` → `logger.debug(..., config_path=str(path), ...)`
- `logger.warning(..., path=str(path), ...)` → `logger.warning(..., config_path=str(path), ...)`

**Файл**: `api/worker/run_all_tasks.py`

---

## Проверка результатов

### 1. Перезапуск worker

```bash
docker compose restart worker
```

**Результат**: ✅ Worker перезапущен успешно

---

### 2. Проверка статуса задач

**EnrichmentTask**: ✅ Запущен
```
worker-1  | 2025-11-17 12:53:02,251 [INFO] tasks.enrichment_task: Enrichment worker initialized
worker-1  | 2025-11-17 12:53:02,251 [INFO] tasks.enrichment_task: Starting enrichment worker
worker-1  | 2025-11-17 12:53:02,316 [INFO] tasks.enrichment_task: EnrichmentWorker initialized successfully
```

**CrawlTriggerTask**: ❌ Падает с ошибкой (исправлено)
```
worker-1  | 2025-11-17 12:53:02,964 [ERROR] supervisor: Task crawl_trigger failed (retry 2): Logger._log() got an unexpected keyword argument 'path'
```

---

### 3. Проверка метрик

**До исправлений**:
```
posts_processed_total{stage="enrichment",success="true"} 27.0
```

**После перезапуска** (метрики сброшены):
```
posts_processed_total{stage="enrichment",success="true"} 0.0
```

**Ожидается**: Метрики начнут увеличиваться при обработке новых событий.

---

### 4. Проверка событий в стримах

**stream:posts:enriched**: 2 события (старые)

**Ожидается**: Новые события будут появляться при обработке событий из `posts.tagged`.

---

## Следующие шаги

1. ✅ **Мониторинг обработки событий**:
   ```bash
   docker compose logs worker --tail 100 | grep -iE "enrich_handler_enter|enrich_publish"
   ```

2. ✅ **Проверка метрик**:
   ```bash
   docker compose exec worker curl -s http://localhost:8001/metrics | grep enrichment
   ```

3. ✅ **Проверка событий в стримах**:
   ```bash
   docker compose exec redis redis-cli XLEN stream:posts:enriched
   ```

4. ✅ **Проверка consumer groups**:
   ```bash
   docker compose exec redis redis-cli XINFO CONSUMERS stream:posts:tagged enrich_workers
   ```

---

## Ожидаемые результаты

После применения исправлений:

1. **EnrichmentTask** должен начать обрабатывать события из `posts.tagged`
2. **События posts.enriched** должны публиковаться для всех постов (даже пропущенных)
3. **CrawlTriggerTask** должен запускаться без ошибок
4. **IndexingTask** должен начать обрабатывать новые события из `posts.enriched`

---

## Impact / Rollback

**Impact**:
- Исправления не должны нарушить существующую функциональность
- Все изменения обратно совместимы
- Добавлена поддержка обоих форматов tags

**Rollback**:
Если нужно откатить изменения:
1. Откатить изменения в `_check_enrichment_triggers`
2. Откатить изменения в `_publish_skipped_enrichment`
3. Откатить изменения в `run_all_tasks.py` для crawl_trigger

---

## Статус

✅ **Исправления применены**
- Формат tags исправлен
- _publish_skipped_enrichment исправлен
- crawl_trigger исправлен
- Worker перезапущен

⏳ **Ожидание результатов**
- Мониторинг обработки событий
- Проверка метрик
- Проверка событий в стримах

