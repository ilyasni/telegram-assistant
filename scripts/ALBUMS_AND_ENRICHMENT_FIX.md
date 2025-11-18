# Исправление проблем с альбомами и обогащением Crawl4AI

## Дата
2025-11-06 06:35 MSK

## Обнаруженные проблемы

### 1. Альбомы не сохраняются в БД
**Проблема:**
- Посты с `grouped_id` есть (215 постов, 33 уникальных альбома)
- Но альбомы не сохраняются в `media_groups` (только 1 альбом за последние 24 часа)
- Ошибка: "A transaction is already begun on this Session" при сохранении альбомов

**Причина:**
- В `channel_parser.py` при сохранении альбомов не проверялось состояние транзакции перед началом новой

**Исправление:**
- Добавлена проверка состояния транзакции перед началом новой транзакции для сохранения альбомов
- Файл: `telethon-ingest/services/channel_parser.py`, строка 1551

```python
# Context7: Проверяем состояние транзакции перед началом новой
if self.db_session.in_transaction():
    await self.db_session.rollback()
    logger.debug("Rolled back active transaction before saving albums",
               channel_id=channel_id)
async with self.db_session.begin():
    # ... сохранение альбомов ...
```

### 2. Crawl4AI обогащения не выполняются
**Проблема:**
- Обогащения не сохраняются в БД (0 crawl4ai обогащений за последние 24 часа)
- В логах видно `has_crawl=False` во всех обогащениях
- Ошибка: "Error processing post" в enrichment_task, но без детального traceback

**Причина:**
- Недостаточно логирования для диагностики проблемы
- Возможные причины:
  1. Политика обогащения не триггерит crawl4ai (теги не совпадают)
  2. Ошибка при выполнении crawl
  3. Ошибка при сохранении в БД

**Исправления:**
1. Добавлено детальное логирование в `_enrich_post_urls`:
   - `enrich_crawl_start` - начало crawl
   - `enrich_crawl_done` - завершение crawl с результатами
   - `enrich_crawl_error` - ошибка при crawl

2. Добавлено логирование при сохранении enrichment:
   - `enrich_save_start` - начало сохранения
   - `enrich_save_ok` - успешное сохранение
   - `enrich_save_fail` - ошибка при сохранении

3. Добавлена обработка ошибок:
   - Ошибки при crawl не прерывают обработку
   - Ошибки при сохранении в БД не прерывают публикацию события

**Файлы:**
- `worker/tasks/enrichment_task.py`:
  - Строка 355-379: добавлено логирование и обработка ошибок для crawl
  - Строка 431-442: добавлено логирование и обработка ошибок для сохранения

## Context7 Best Practices применены

### ✅ Asyncpg Connection Pool
- Правильное управление транзакциями с проверкой состояния
- Rollback зависших транзакций перед началом новых

### ✅ Error Handling
- Обработка ошибок с логированием
- Graceful degradation при ошибках (не прерываем обработку)

### ✅ Observability
- Детальное логирование для диагностики
- Метрики для отслеживания проблем

## Следующие шаги

1. ✅ Мониторить логи после исправлений
2. ⚠️ Проверить, что альбомы сохраняются корректно
3. ⚠️ Проверить, что crawl4ai обогащения выполняются
4. ⚠️ Проверить политику обогащения (триггеры, теги)

## Проверка результатов

### Альбомы
```sql
SELECT COUNT(*) as total_albums, 
       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as last_24h
FROM media_groups;
```

### Обогащения Crawl4AI
```sql
SELECT COUNT(*) as total_enrichments,
       COUNT(*) FILTER (WHERE updated_at > NOW() - INTERVAL '24 hours') as last_24h,
       COUNT(*) FILTER (WHERE kind = 'crawl4ai') as crawl4ai_total,
       COUNT(*) FILTER (WHERE kind = 'crawl4ai' AND updated_at > NOW() - INTERVAL '24 hours') as crawl4ai_24h
FROM post_enrichment;
```

### Логи
```bash
# Альбомы
docker compose logs --since=30m telethon-ingest | grep -E "(Media group saved|Failed to save albums|A transaction is already begun)"

# Обогащения
docker compose logs --since=30m worker | grep -E "(enrich_crawl_start|enrich_crawl_done|enrich_crawl_error|enrich_save_start|enrich_save_ok|enrich_save_fail)"
```

## Рекомендации

1. **Мониторинг**: Настроить алерты на ошибки сохранения альбомов и обогащений
2. **Политика обогащения**: Проверить триггеры обогащения (теги, URLs, word count)
3. **Логирование**: Использовать структурированное логирование для анализа проблем
4. **Тестирование**: Добавить тесты для проверки сохранения альбомов и обогащений
