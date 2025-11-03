# Проверка альбомов в Neo4j

**Дата**: 2025-11-03

## Текущее состояние

### Neo4j
- ✅ Neo4j работает нормально
- ⚠️ Альбомов в Neo4j: 0
- ⚠️ Связей CONTAINS: 0

### Индексация
- ✅ Indexing task работает (consumer group: `indexing_workers`, 2 consumers)
- ✅ Обработано событий: 4618
- ⚠️ Альбомы не создаются при индексации постов

## Проблема

Альбомы не создаются в Neo4j, хотя:
1. `indexing_task` поддерживает создание альбомов через `create_album_node_and_relationships`
2. Метод вызывается для постов из альбомов (см. код `indexing_task.py:909-915`)
3. Но альбомы не появляются в Neo4j

## Возможные причины

1. **Посты не индексируются** — посты из альбомов не проходят через `posts.enriched` → `indexing_task`
2. **Метод не вызывается** — `album_id` не определяется для постов
3. **Ошибки при создании** — альбомы не создаются из-за ошибок (нет в логах)

## Проверка

### 1. Наличие постов альбома в Neo4j

```bash
# Проверка постов альбома
docker exec telegram-assistant-worker-1 python3 -c "
from integrations.neo4j_client import Neo4jClient
# ... проверка постов
"
```

### 2. Создание альбома вручную

```python
# Создание альбома и связей
await neo4j_client.create_album_node(...)
await neo4j_client.create_album_item_relationships(...)
```

### 3. Проверка indexing_task

```bash
# Логи индексации
docker logs telegram-assistant-worker-1 | grep -i "album"
```

## Решение

1. ✅ Создать альбом вручную для теста
2. ⏭️ Проверить, почему посты альбомов не индексируются
3. ⏭️ Проверить эмиссию `posts.enriched` для постов из альбомов

