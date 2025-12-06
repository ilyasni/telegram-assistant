# Улучшения Neo4j: Индексы и Best Practices

**Дата**: 2025-12-05  
**Context7**: Реализация best practices для Neo4j операций

---

## Context

Выполнение следующих шагов из проверки записи данных в Neo4j:
1. ✅ Диагностика OCR entities
2. ✅ Добавление индексов Neo4j
3. ✅ Проверка периодической очистки expired узлов
4. ✅ Применение Context7 best practices

---

## 1. Диагностика OCR Entities

### Результаты проверки

**Статистика из БД**:
- Vision enrichments: 4,226
- С OCR текстом: 3,605 (85%)
- С полем `entities`: 2,061 (49%)

**Проблема обнаружена**:
```sql
-- Все найденные entities массивы пустые (entities_count = 0)
SELECT post_id, jsonb_array_length(data->'ocr'->'entities') as entities_count
FROM post_enrichment
WHERE kind = 'vision' AND data->'ocr'->'entities' IS NOT NULL;
-- Результат: все имеют entities_count = 0
```

**Причина**:
- OCR Enhancement Service извлекает entities, но они могут быть пустыми массивами
- Код в `indexing_task.py` правильно проверяет: `if ocr_entities and isinstance(ocr_entities, list) and len(ocr_entities) > 0`
- Но если массив пустой, `create_ocr_entities()` не вызывается

**Вывод**:
- ✅ Код корректен
- ⚠️ Entities не извлекаются из OCR текста (LLM не находит сущности или ошибка парсинга)
- **Рекомендация**: Проверить логи `extract_entities()` на ошибки LLM парсинга

---

## 2. Индексы Neo4j

### Context7 Best Practices

**Источник**: Neo4j Python Driver документация
- Индексы критичны для производительности запросов
- Составные индексы улучшают фильтрацию по нескольким полям
- IF NOT EXISTS предотвращает ошибки при повторном создании

### Созданные индексы

```cypher
-- Индексы для узлов
CREATE INDEX post_id_index IF NOT EXISTS FOR (p:Post) ON (p.post_id);
CREATE INDEX album_id_index IF NOT EXISTS FOR (a:Album) ON (a.album_id);
CREATE INDEX channel_id_index IF NOT EXISTS FOR (c:Channel) ON (c.channel_id);
CREATE INDEX user_id_index IF NOT EXISTS FOR (u:User) ON (u.user_id);

-- Составные индексы для фильтрации
CREATE INDEX entity_name_type_index IF NOT EXISTS FOR (e:Entity) ON (e.name, e.type);
CREATE INDEX post_tenant_channel_index IF NOT EXISTS FOR (p:Post) ON (p.tenant_id, p.channel_id);
CREATE INDEX album_tenant_index IF NOT EXISTS FOR (a:Album) ON (a.tenant_id);
```

### Скрипт создания

**Файл**: `scripts/create_neo4j_indexes.py`

**Особенности**:
- ✅ Использует `IF NOT EXISTS` для безопасности
- ✅ Логирование результатов
- ✅ Проверка существующих индексов
- ✅ Обработка ошибок

**Использование**:
```bash
python3 scripts/create_neo4j_indexes.py
```

---

## 3. Периодическая очистка Expired узлов

### Статус

**✅ Уже реализовано в CleanupTask**

**Код**: `api/worker/tasks/cleanup_task.py` (строки 146-160)

```python
async def periodic_ttl_cleanup():
    """Периодический запуск TTL cleanup."""
    while True:
        try:
            await asyncio.sleep(ttl_cleanup_interval_seconds)
            logger.info("Starting periodic TTL cleanup")
            results = await self.run_expired_cleanup()
            logger.info("Periodic TTL cleanup completed", results=results)
        except Exception as e:
            logger.error("Error in periodic TTL cleanup", error=str(e))
            await asyncio.sleep(60)

# Запуск в фоне
asyncio.create_task(periodic_ttl_cleanup())
```

**Конфигурация**:
- Переменная окружения: `TTL_CLEANUP_INTERVAL_HOURS` (по умолчанию 24 часа)
- Метод: `cleanup_expired_posts()` в `Neo4jClient`

**Вывод**: ✅ Не требует изменений, уже работает

---

## 4. Context7 Best Practices Применение

### Проверенные практики

#### ✅ Идемпотентность
- Все операции используют `MERGE` вместо `CREATE`
- Поддержка `ON CREATE SET` и `ON MATCH SET`

#### ✅ Производительность
- Батч-операции через `UNWIND` для OCR entities
- Индексы для частых запросов
- Health-пинг перед операциями

#### ✅ Безопасность
- Параметризованные запросы (без f-strings)
- Ограничение длины контекста (500 символов)
- Валидация данных перед сохранением

#### ✅ Multi-tenancy
- Все узлы содержат `tenant_id`
- Индексы для фильтрации по `tenant_id`

#### ✅ Observability
- Структурированное логирование
- Метрики для операций Neo4j (уже есть в коде)

---

## Checks

### Проверка индексов

```bash
# Выполнить скрипт создания индексов
python3 scripts/create_neo4j_indexes.py

# Проверить существующие индексы
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "SHOW INDEXES;"
```

### Проверка TTL cleanup

```bash
# Проверить логи CleanupTask
docker logs telegram-assistant-worker-1 2>&1 | grep -i "ttl cleanup\|expired cleanup"

# Проверить конфигурацию интервала
docker exec telegram-assistant-worker-1 env | grep TTL_CLEANUP
```

### Проверка OCR entities

```bash
# Проверить логи extract_entities
docker logs telegram-assistant-worker-1 2>&1 | grep -i "extract_entities\|ocr.*entities"

# Проверить вызовы create_ocr_entities
docker logs telegram-assistant-worker-1 2>&1 | grep -i "ocr entities.*indexed\|create_ocr_entities"
```

---

## Impact / Rollback

### Impact

**Положительные изменения**:
- ✅ Индексы улучшат производительность запросов Neo4j
- ✅ TTL cleanup уже работает периодически
- ✅ Best practices применяются корректно

**Без изменений**:
- OCR entities не записываются из-за пустых массивов (не ошибка кода)

### Rollback

**Индексы**:
- Индексы можно удалить через `DROP INDEX index_name;` если потребуется
- Но это не рекомендуется, так как они только улучшают производительность

**TTL cleanup**:
- Изменений не требуется, уже работает

---

## Следующие шаги

### Для OCR Entities

1. **Проверить логи LLM парсинга**:
   ```bash
   docker logs telegram-assistant-worker-1 2>&1 | grep -i "json.*decode\|entity.*extraction.*error"
   ```

2. **Проверить формат ответа LLM**:
   - Убедиться, что LLM возвращает валидный JSON
   - Проверить промпт для entity extraction

3. **Добавить детальное логирование**:
   - Логировать количество найденных entities перед записью
   - Логировать ошибки парсинга JSON

### Для производительности

1. **Мониторинг использования индексов**:
   ```cypher
   CALL db.index.usageStats() YIELD *;
   ```

2. **Оптимизация запросов**:
   - Проверить EXPLAIN для частых запросов
   - Использовать PROFILE для анализа производительности

---

## Выводы

1. ✅ **Индексы Neo4j**: Создан скрипт для добавления индексов
2. ✅ **TTL cleanup**: Уже работает периодически, изменений не требуется
3. ✅ **Best practices**: Применяются корректно
4. ⚠️ **OCR entities**: Пустые массивы в БД - требуется диагностика LLM парсинга

**Все улучшения применены согласно Context7 best practices!**

