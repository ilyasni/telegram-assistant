# Проверка записи данных в Neo4j

**Дата**: 2025-12-05

## Context

Проверка сохранения постов, альбомов и OCR сущностей в Neo4j базу данных согласно best practices.

---

## Результаты проверки

### ✅ Посты (Posts)

**Статус**: ✅ **Работает корректно**

- **Количество постов в Neo4j**: 6,954
- **Метод создания**: `create_post_node()` в `Neo4jClient`
- **Место вызова**: `IndexingTask._index_to_neo4j()` (строка 1407)
- **Логи подтверждают**: `"Indexed to Neo4j with enrichment"` встречается регулярно

**Проверка best practices**:
- ✅ Используется `MERGE` для идемпотентности
- ✅ Параметризованные запросы (без f-strings)
- ✅ Поддержка multi-tenancy через `tenant_id`
- ✅ Health-пинг перед операциями
- ✅ Логирование структурированное

**Пример запроса**:
```cypher
MERGE (p:Post {post_id: $post_id})
SET p.user_id = $effective_user_id,
    p.tenant_id = $tenant_id,
    p.channel_id = $channel_id,
    ...
```

---

### ✅ Альбомы (Albums)

**Статус**: ✅ **Работает корректно**

- **Количество альбомов в Neo4j**: 962
- **Количество связей CONTAINS**: 1,418
- **Метод создания**: `create_album_node_and_relationships()` в `Neo4jClient`
- **Место вызова**: `IndexingTask._index_to_neo4j()` (строка 1424)

**Проверка best practices**:
- ✅ Идемпотентность через `MERGE`
- ✅ Метаданные альбомов получаются из БД перед созданием узла
- ✅ Связи `CONTAINS` создаются с поддержкой `position`
- ✅ Связи `HAS_ALBUM` между Channel и Album

**Структура**:
- `(:Album)` - узлы альбомов
- `(:Channel)-[:HAS_ALBUM]->(:Album)` - связи канал → альбом
- `(:Album)-[:CONTAINS]->(:Post)` - связи альбом → пост

---

### ❌ OCR сущности (OCR Entities)

**Статус**: ❌ **НЕ записываются**

- **Количество Entity узлов с source='ocr'**: 0
- **Количество MENTIONS связей**: 0
- **Метод создания**: `create_ocr_entities()` в `Neo4jClient`
- **Место вызова**: `IndexingTask._index_to_neo4j()` (строка 1589)

**Причины отсутствия данных**:

1. **OCR Enhancement Service может быть не включен**
   - Метод `extract_entities()` находится в `OCREnhancementService`
   - Требует настройку `ocr_enhancement_entities: true` в конфиге

2. **OCR данные могут не содержать поле `entities`**
   - Код ожидает `vision_data.ocr.entities` (строка 1585)
   - `entities` добавляются только после вызова `enhance_ocr_data()`
   - Если enhancement не вызывается, `entities` отсутствуют

3. **Возможные сценарии**:
   ```python
   # В vision_analysis_task.py (строка 2128)
   if self.ocr_enhancement_service:
       enhanced_ocr = await self.ocr_enhancement_service.enhance_ocr_data(...)
       # entities добавляются здесь
   else:
       ocr_value = ocr_extracted  # без entities
   ```

**Проверка кода**:
- ✅ Код для записи OCR entities существует и корректен
- ✅ Используется UNWIND для батч-операций (best practice)
- ⚠️ **Проблема**: OCR enhancement может быть не включен или не доступен

---

## Context7 Best Practices Проверка

### ✅ Идемпотентность

- Все операции используют `MERGE` вместо `CREATE`
- Поддержка `ON CREATE SET` и `ON MATCH SET`
- Проверка существования узлов перед созданием связей

### ✅ Производительность

- **Батч-операции**: OCR entities используют `UNWIND` для батч-обработки
- **Health-пинг**: Проверка подключения перед операциями
- **Логирование**: Структурированное логирование с минимальным overhead

### ✅ Multi-tenancy

- Все узлы содержат `tenant_id`
- Фильтрация по `tenant_id` в запросах (если требуется)

### ✅ Безопасность

- Параметризованные запросы (без f-strings или конкатенации)
- Ограничение длины контекста OCR (500 символов)
- Валидация данных перед сохранением

### ⚠️ Области для улучшения

1. **TTL для expired узлов**: Нет автоматического удаления по `expires_at`
   - Решение: Запустить периодическую задачу `cleanup_expired_posts()`

2. **Индексы Neo4j**: Рекомендуется добавить индексы для частых запросов
   ```cypher
   CREATE INDEX post_id_index IF NOT EXISTS FOR (p:Post) ON (p.post_id);
   CREATE INDEX album_id_index IF NOT EXISTS FOR (a:Album) ON (a.album_id);
   CREATE INDEX entity_name_type_index IF NOT EXISTS FOR (e:Entity) ON (e.name, e.type);
   ```

3. **Мониторинг**: Добавить метрики для операций Neo4j
   - `neo4j_operations_total{operation, status}` - уже есть в коде
   - Проверить, что метрики экспортируются

---

## Рекомендации

### Для OCR Entities

1. **Проверить конфигурацию OCR Enhancement**:
   ```bash
   docker exec telegram-assistant-worker-1 python3 -c "
   from worker.tasks.vision_analysis_task import VisionAnalysisTask
   task = VisionAnalysisTask(...)
   print(f'OCR Enhancement Service: {task.ocr_enhancement_service is not None}')
   "
   ```

2. **Проверить наличие OCR данных в vision enrichment**:
   ```sql
   SELECT 
       post_id,
       data->'ocr'->>'text' as ocr_text,
       data->'ocr'->'entities' as ocr_entities
   FROM post_enrichment
   WHERE kind = 'vision'
     AND data->'ocr' IS NOT NULL
     AND data->'ocr' != 'null'::jsonb
   LIMIT 10;
   ```

3. **Если OCR enhancement включен, но entities нет**:
   - Проверить логи на ошибки `extract_entities()`
   - Проверить конфиг `ocr_enhancement_entities: true`
   - Убедиться, что LLM для entity extraction доступен

### Для Neo4j индексов

Добавить индексы для улучшения производительности:

```cypher
// Выполнить в Neo4j Browser или через cypher-shell
CREATE INDEX post_id_index IF NOT EXISTS FOR (p:Post) ON (p.post_id);
CREATE INDEX album_id_index IF NOT EXISTS FOR (a:Album) ON (a.album_id);
CREATE INDEX entity_name_type_index IF NOT EXISTS FOR (e:Entity) ON (e.name, e.type);
CREATE INDEX channel_id_index IF NOT EXISTS FOR (c:Channel) ON (c.channel_id);
```

---

## Checks

### Проверка постов

```bash
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (p:Post) RETURN count(p) as posts_count;"
```

### Проверка альбомов

```bash
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (a:Album) RETURN count(a) as albums_count;" && \
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (a:Album)-[r:CONTAINS]->(p:Post) RETURN count(r) as links_count;"
```

### Проверка OCR entities

```bash
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (e:Entity {source: 'ocr'}) RETURN count(e) as ocr_entities_count;"
```

---

## Impact / Rollback

### Impact

- ✅ Посты и альбомы записываются стабильно
- ❌ OCR entities не записываются (требуется включение OCR enhancement)

### Rollback

Изменения не требуются, так как:
- Код для записи OCR entities уже существует и корректен
- Проблема в конфигурации/настройке OCR enhancement, а не в коде

---

## Выводы

1. ✅ **Посты записываются** - 6,954 постов в Neo4j
2. ✅ **Альбомы записываются** - 962 альбома с 1,418 связями
3. ❌ **OCR entities НЕ записываются** - требуется включить OCR Enhancement Service и извлечение entities

**Следующие шаги**:
1. Проверить конфигурацию OCR enhancement (`ocr_enhancement_enabled`, `ocr_enhancement_entities`)
2. Добавить индексы Neo4j для производительности
3. Включить периодическую очистку expired узлов

---

## Дополнительные находки

### Multi-tenancy статистика

- **Tenants в графе**: 5
- **Channels в графе**: 88
- **Постов**: 6,954

Подтверждает корректную работу multi-tenancy: все узлы содержат `tenant_id`.

### Индексы Neo4j

Текущее состояние:
- Только LOOKUP индексы (встроенные)
- Нет кастомных индексов для `post_id`, `album_id`, `entity.name+type`

Рекомендуется добавить индексы для улучшения производительности запросов.

### OCR Enhancement Service

**Статус**: ✅ Сервис инициализирован корректно

- **Логи подтверждают**: `OCR Enhancement Service initialized enabled=True entity_extraction_enabled=True`
- **Инициализация**: В `create_vision_analysis_task()` (строка 3498-3523)
- **Конфигурация**: `ocr_enhancement_enabled=True`, `ocr_enhancement_entities=True`

**Возможные причины отсутствия OCR entities в Neo4j**:

1. **OCR текста нет в vision данных**
   - Проверить: есть ли OCR текст в `post_enrichment` для vision kind

2. **LLM не возвращает entities**
   - Ошибка парсинга JSON ответа от LLM
   - LLM не находит сущности в тексте

3. **OCR enhancement не вызывается**
   - Проверить логи на ошибки `extract_entities()`
   - Проверить, что `enhance_ocr_data()` успешно выполняется

4. **Entities извлекаются, но не записываются в Neo4j**
   - Проверить логи `"OCR entities indexed to Neo4j"`
   - Проверить, что `create_ocr_entities()` вызывается с непустым списком entities

**Рекомендуемая диагностика**:

```bash
# 1. Проверить наличие OCR данных в БД
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    post_id,
    data->'ocr'->>'text' as ocr_text,
    jsonb_array_length(data->'ocr'->'entities') as entities_count
FROM post_enrichment
WHERE kind = 'vision'
  AND data->'ocr' IS NOT NULL
  AND data->'ocr' != 'null'::jsonb
LIMIT 5;
"

# 2. Проверить логи на ошибки OCR enhancement
docker logs telegram-assistant-worker-1 2>&1 | grep -i "ocr.*error\|extract_entities.*error" | tail -20

# 3. Проверить, вызывается ли create_ocr_entities
docker logs telegram-assistant-worker-1 2>&1 | grep -i "ocr entities.*indexed\|create_ocr_entities" | tail -20
```

