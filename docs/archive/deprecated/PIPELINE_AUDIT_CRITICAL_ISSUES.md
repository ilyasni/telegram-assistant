# Полный аудит пайплайна - Критические проблемы

**Дата**: 2025-01-22  
**Context7**: Полный аудит цепочки обработки постов для выявления блокеров

---

## Context

Проведен полный аудит цепочки обработки постов. Обнаружены критические проблемы, которые блокируют обработку новых постов.

---

## Критические проблемы

### ❌ ПРОБЛЕМА 1: EnrichmentTask не обрабатывает события

**Симптомы**:
- `posts.tagged`: 5132 события
- `posts.enriched`: 2 события (!!!) 
- Consumer group `enrich_workers`: активна, но `idle=47` секунд, `inactive=1496160` секунд (17 дней!)
- Метрики: `posts_processed_total{stage="enrichment",success="true"}` = 27 (только 27 обработано!)

**Корневая причина**:
1. **Формат tags не соответствует ожиданиям**: `_check_enrichment_triggers` ожидает `tags` как список словарей с `name`, но в событиях `posts.tagged` `tags` - это список строк
2. **EnrichmentTask не обрабатывает события**: Consumer group активна, но события не обрабатываются
3. **TagPersistenceTask публикует posts.enriched**: Но только для постов, которые прошли через TagPersistenceTask

**Код проблемы**:
```556:560:api/worker/tasks/enrichment_task.py
        # Приоритет 2: trigger_tags
        trigger_tags_config = self.config.get('crawl4ai', {}).get('trigger_tags', [])
        if trigger_tags_config:
            post_tags = [tag.get('name', '').lower() for tag in tags]  # ❌ ОШИБКА: tags - это список строк, не словарей!
            if any(tag.lower() in post_tags for tag in trigger_tags_config):
```

**Фактический формат tags в posts.tagged**:
```json
"tags": ["кофейни", "Гонконг", "Бангкок", "геолокация", "фото"]
```

**Ожидаемый формат (неправильный)**:
```python
tags = [{"name": "кофейни"}, {"name": "Гонконг"}, ...]  # ❌ Неправильно!
```

---

### ❌ ПРОБЛЕМА 2: TagPersistenceTask публикует posts.enriched, но не все посты проходят через него

**Симптомы**:
- `posts.enriched`: только 2 события
- TagPersistenceTask публикует `posts.enriched` после сохранения тегов
- Но не все посты проходят через TagPersistenceTask

**Код**:
```508:521:api/worker/tasks/tag_persistence_task.py
                # КРИТИЧНО: Публикация в posts.enriched (даже если теги пустые!)
                # Context7: Используем tenant_id из БД, не хардкод 'default'
                enriched_event = {
                    "schema": "posts.enriched.v1",
                    "post_id": post_id,
                    "tenant_id": tenant_id,  # Context7: Используем tenant_id из БД
                    "tags": tags or [],
                    "enrichment": {},  # Будет заполнено crawl4ai
                    "trace_id": metadata.get("trace_id", str(uuid.uuid4())),
                    "ts": datetime.now(timezone.utc).isoformat()
                }
                
                await self.publisher.publish_event("posts.enriched", enriched_event)
                logger.info(f"Published posts.enriched event for post_id={post_id}, tenant_id={tenant_id}")
```

**Проблема**: TagPersistenceTask публикует `posts.enriched`, но это не должно быть его ответственностью. EnrichmentTask должен публиковать `posts.enriched` для всех постов.

---

### ❌ ПРОБЛЕМА 3: IndexingTask не обрабатывает новые события

**Симптомы**:
- `posts.indexed`: 6989 событий
- Последнее событие: 2025-11-17 09:24:04 (5 дней назад)
- Consumer group `trend_workers`: активна, но обработано только 51 событие

**Корневая причина**:
- IndexingTask подписан на `posts.enriched`, но новых событий нет (только 2!)
- TrendDetectionWorker подписан на `posts.indexed`, но новых событий нет

---

### ❌ ПРОБЛЕМА 4: crawl_trigger постоянно перезапускается

**Симптомы**:
```
worker-1  | 2025-11-17 12:45:01,716 [WARNING] supervisor: Task crawl_trigger completed unexpectedly, will be restarted
worker-1  | 2025-11-17 12:45:31,716 [WARNING] supervisor: Task crawl_trigger completed unexpectedly, will be restarted
```

**Корневая причина**: CrawlTriggerTask падает или завершается неожиданно.

---

## Детальный анализ

### Анализ стримов

| Стрим | Событий | Последнее событие | Consumer Groups | Pending |
|-------|---------|-------------------|-----------------|---------|
| `posts.parsed` | 5134 | 2025-11-17 09:22:41 | ✅ Активны | 0 |
| `posts.tagged` | 5132 | 2025-11-17 09:22:41 | ✅ Активны (3 groups) | 0 |
| `posts.enriched` | 2 | 2025-11-17 09:22:42 | ✅ Активна (1 group) | 0 |
| `posts.indexed` | 6989 | 2025-11-17 09:24:04 | ✅ Активна (1 group) | 0 |

**Вывод**: События доходят до `posts.tagged`, но не доходят до `posts.enriched`.

---

### Анализ Consumer Groups

**stream:posts:tagged**:
- `enrich_workers`: `idle=47`, `inactive=1496160` (17 дней!) - **НЕ ОБРАБАТЫВАЕТ!**
- `crawl_trigger_workers`: `lag=1562` - отстает
- `tag_persist_workers`: `lag=0` - работает

**Вывод**: `enrich_workers` не обрабатывает события уже 17 дней!

---

### Анализ метрик

```
posts_processed_total{stage="enrichment",success="true"} 27.0
posts_processed_total{stage="enrichment",success="skip"} 0.0
posts_processed_total{stage="enrichment",success="error"} 0.0
posts_processed_total{stage="enrichment",success="attempt"} 27.0
```

**Вывод**: Только 27 постов обработано через EnrichmentTask, хотя событий `posts.tagged` 5132!

---

## Исправления

### Исправление 1: Формат tags в _check_enrichment_triggers

**Проблема**: `_check_enrichment_triggers` ожидает `tags` как список словарей, но получает список строк.

**Исправление**:
```python
# Было:
post_tags = [tag.get('name', '').lower() for tag in tags]

# Должно быть:
if tags and isinstance(tags[0], dict):
    post_tags = [tag.get('name', '').lower() for tag in tags]
else:
    post_tags = [str(tag).lower() for tag in tags] if tags else []
```

---

### Исправление 2: _publish_skipped_enrichment использует неправильную схему

**Проблема**: `_publish_skipped_enrichment` использует `PostEnrichedEvent`, но должен использовать `PostEnrichedEventV1` или правильный формат.

**Исправление**: Использовать тот же формат, что и в `_handle_post_tagged` для успешного обогащения.

---

### Исправление 3: Проверка почему EnrichmentTask не обрабатывает события

**Проблема**: Consumer group активна, но события не обрабатываются.

**Проверка**:
1. Проверить логи на ошибки
2. Проверить, запущен ли EnrichmentTask
3. Проверить consumer group на наличие проблем

---

## Checks

### 1. Проверка формата tags

```bash
# Проверка формата tags в последнем событии posts.tagged
docker compose exec redis redis-cli XREVRANGE stream:posts:tagged + - COUNT 1 | grep -o '"tags":\[[^]]*\]'
```

### 2. Проверка логов EnrichmentTask

```bash
# Проверка логов на ошибки
docker compose logs worker --tail 1000 | grep -iE "enrich.*error|enrich.*exception|enrich.*fail"

# Проверка обработки событий
docker compose logs worker --tail 1000 | grep -iE "enrich_handler_enter|enrich_build_start"
```

### 3. Проверка consumer groups

```bash
# Проверка consumer groups для posts.tagged
docker compose exec redis redis-cli XINFO GROUPS stream:posts:tagged

# Проверка consumers
docker compose exec redis redis-cli XINFO CONSUMERS stream:posts:tagged enrich_workers
```

### 4. Проверка метрик

```bash
# Метрики enrichment
docker compose exec worker curl -s http://localhost:8001/metrics | grep enrichment
```

---

## Impact / Rollback

**Impact**:
- Исправление формата tags позволит EnrichmentTask обрабатывать события
- Исправление _publish_skipped_enrichment позволит публиковать события для пропущенных постов
- Проверка EnrichmentTask позволит выявить другие проблемы

**Rollback**:
Если нужно откатить изменения:
1. Откатить изменения в `_check_enrichment_triggers`
2. Откатить изменения в `_publish_skipped_enrichment`

---

## Следующие шаги

1. ✅ Исправить формат tags в `_check_enrichment_triggers`
2. ✅ Исправить `_publish_skipped_enrichment` для использования правильной схемы
3. ✅ Проверить почему EnrichmentTask не обрабатывает события
4. ✅ Исправить проблему с crawl_trigger
5. ✅ Проверить работу IndexingTask

