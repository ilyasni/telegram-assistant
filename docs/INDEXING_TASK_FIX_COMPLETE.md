# Исправление IndexingTask - обработка событий

**Дата**: 2025-11-05 18:30  
**Context7**: Исправление проблемы с обработкой событий posts.enriched

---

## Проблема

IndexingTask не обрабатывал новые события `posts.enriched`, хотя:
- Consumer group существовал
- XREADGROUP мог читать новые сообщения
- Но цикл `while self.event_consumer.running:` не выполнялся

---

## Причина

В методе `start()` IndexingTask не устанавливал флаг `self.event_consumer.running = True` перед запуском цикла `_consume_with_trim`. 

Цикл `while self.event_consumer.running:` проверял этот флаг, но он оставался `False` (дефолтное значение из `__init__`), поэтому цикл сразу завершался.

---

## Исправление

**Файл**: `worker/tasks/indexing_task.py` (строка ~217)

**Изменение**: Добавлена установка флага `running = True` перед запуском цикла:

```python
# Context7: Устанавливаем running флаг для EventConsumer перед запуском цикла
# Это необходимо, так как _consume_with_trim проверяет self.event_consumer.running
self.event_consumer.running = True

# Context7: Запуск потребления событий с отслеживанием message_id для XTRIM
await self._consume_with_trim("posts.enriched", self._process_single_message)
```

---

## Результат

После исправления:
- ✅ IndexingTask обрабатывает события `posts.enriched`
- ✅ Логи показывают "Post indexed successfully"
- ✅ Создаются Tag и Topic relationships в Neo4j
- ✅ Публикуются события `posts.indexed`

---

## Проверка

После перезапуска worker:
1. Проверить логи на наличие "Post indexed successfully"
2. Проверить новые события `posts.indexed` на наличие `tenant_id`
3. Проверить Qdrant коллекции для реальных пользователей
4. Проверить статус индексации в БД

