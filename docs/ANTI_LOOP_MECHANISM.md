# Механизм анти-петли для RetaggingTask

## Проблема

RetaggingTask подписан на `posts.vision.analyzed` и публикует `posts.tagged` с `trigger=vision_retag`. Необходимо предотвратить циклы, когда RetaggingTask обрабатывает свои же события.

## Решение

### 1. RetaggingTask подписан только на `posts.vision.analyzed`

RetaggingTask НЕ подписан на `posts.tagged`, поэтому он не обрабатывает свои же события с `trigger=vision_retag`.

```python
# worker/tasks/retagging_task.py
await self.event_consumer.start_consuming("posts.vision.analyzed", self._process_single_message)
```

### 2. Публикация с `trigger=vision_retag`

RetaggingTask публикует события с явным флагом:

```python
tagged_event = PostTaggedEventV1(
    ...
    trigger="vision_retag",  # Явный флаг
    vision_version=vision_version
)
```

### 3. Другие tasks обрабатывают все события

**TagPersistenceTask** — сохраняет теги в БД, обрабатывает все события (включая retagging):
- ✅ Обрабатывает `trigger=vision_retag` (теги изменились, нужно обновить БД)
- 📊 Логирует `is_retagging` для observability

**CrawlTriggerTask** — проверяет триггерные теги, обрабатывает все события:
- ✅ Обрабатывает `trigger=vision_retag` (новые теги могут быть триггерными)
- 📊 Логирует для observability

**EnrichmentTask** — обогащает посты, обрабатывает все события:
- ✅ Обрабатывает `trigger=vision_retag` (enrichment не зависит от источника тегов)
- 📊 Логирует для observability

**IndexingTask** — индексирует посты, подписан на `posts.enriched`:
- Не обрабатывает `posts.tagged` напрямую
- Работает через `posts.enriched` → `posts.parsed`

## Event Flow

```
1. VisionAnalyzedEventV1 (posts.vision.analyzed)
   ↓
2. RetaggingTask.process()
   - Проверка версий (vision_version > tags_version)
   - Ретеггинг через GigaChain
   ↓
3. PostTaggedEventV1 (posts.tagged)
   - trigger="vision_retag"
   - vision_version="vision@2025-01-29#p3"
   ↓
4. TagPersistenceTask ✅ (обновляет теги в БД)
5. CrawlTriggerTask ✅ (проверяет триггерные теги)
6. EnrichmentTask ✅ (обогащает пост)
   ↓
7. RetaggingTask ❌ (НЕ обрабатывает, так как подписан только на posts.vision.analyzed)
```

## Валидация

Схема события поддерживает `trigger`:

```python
# worker/events/schemas/posts_tagged_v1.py
trigger: Optional[str] = Field(
    default="initial",
    description="Триггер тегирования: initial, vision_retag, manual"
)
```

## Метрики

Все tasks логируют `trigger` и `is_retagging` для observability:
- TagPersistenceTask: логирует `trigger` и `is_retagging`
- CrawlTriggerTask: логирует `trigger` при retagging
- EnrichmentTask: логирует `trigger` при retagging

## Тестирование

E2E тесты проверяют:
- ✅ RetaggingTask не обрабатывает события с `trigger=vision_retag`
- ✅ Другие tasks корректно обрабатывают retagging события
- ✅ Версионирование предотвращает повторный ретеггинг

## Best Practices

1. **Явные флаги**: Использование `trigger` для идентификации источника события
2. **Разделение стримов**: RetaggingTask подписан на другой стрим, не на свои события
3. **Observability**: Логирование `trigger` во всех tasks для трассировки
4. **Версионирование**: Контроль версий предотвращает повторную обработку

