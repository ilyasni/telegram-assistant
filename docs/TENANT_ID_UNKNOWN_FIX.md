# Исправление tenant_id='unknown' в posts.indexed

**Дата**: 2025-11-05  
**Context7**: Устранение проблемы с отсутствием tenant_id в событиях posts.indexed

---

## Проблема

В событиях `posts.indexed` отсутствовало поле `tenant_id`, что приводило к тому, что при парсинге оно получало значение `'unknown'` (fallback в парсере).

**Raw данные из Redis Stream**:
```json
{
  "post_id": "26171c96-7843-490a-bd8e-24c8ea389bd6",
  "vector_id": "26171c96-7843-490a-bd8e-24c8ea389bd6",
  "indexed_at": "2025-11-04T09:57:50.158701+00:00"
  // tenant_id отсутствует!
}
```

---

## Причина

`EventPublisher._to_json_bytes` в `worker/event_bus.py` фильтрует `None` значения из JSON:

```python
def norm(o):
    if o is None:
        return None  # отфильтруем на уровне словаря/списка
    # ...
```

Если `tenant_id` был `None`, он удалялся из JSON перед публикацией в Redis Stream.

---

## Исправления

### 1. Гарантия строкового значения tenant_id

**Файл**: `worker/tasks/indexing_task.py` (строка ~570)

**Изменение**: Добавлена явная конвертация `tenant_id` в строку перед публикацией:

```python
# Context7: Гарантируем, что tenant_id всегда строка (не None), иначе EventPublisher._to_json_bytes удалит его
tenant_id_str = str(tenant_id) if tenant_id else 'default'

await self.publisher.publish_event("posts.indexed", {
    "post_id": post_id,
    "tenant_id": tenant_id_str,  # Context7: Обязательно строка, не None
    "vector_id": vector_id,
    "indexed_at": datetime.now(timezone.utc).isoformat()
})
```

### 2. Добавлен 'default' в COALESCE запрос

**Файл**: `worker/tasks/indexing_task.py` (`_get_tenant_id_from_post`)

**Изменение**: Добавлен `'default'` как последний fallback в `COALESCE`:

```sql
SELECT COALESCE(
    (SELECT u.tenant_id::text FROM users u ...),
    CAST(pe.data->>'tenant_id' AS text),
    CAST(c.settings->>'tenant_id' AS text),
    'default'  -- Context7: Добавлен fallback
) as tenant_id
```

### 3. Упрощена логика возврата tenant_id

**Файл**: `worker/tasks/indexing_task.py` (`_get_tenant_id_from_post`)

**Изменение**: Убрана логика, которая возвращала `None` для `'default'` значений:

```python
# Было:
if tenant_id_value and tenant_id_value != 'default':
    return tenant_id_value
return None

# Стало:
return tenant_id_value  # Возвращаем значение, даже если 'default'
```

---

## Ожидаемый результат

После исправлений:
1. Все события `posts.indexed` будут содержать поле `tenant_id` (либо реальный UUID, либо `'default'`)
2. Поле `tenant_id` не будет удаляться из JSON при сериализации
3. Посты с реальным `tenant_id` будут индексироваться в правильные Qdrant коллекции

---

## Проверка

После перезапуска `worker`:
1. Проверить последние события `posts.indexed` в Redis Stream
2. Убедиться, что все события содержат поле `tenant_id`
3. Проверить, что новые посты индексируются в правильные коллекции

