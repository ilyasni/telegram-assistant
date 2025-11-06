# Аудит хардкода tenant_id="default" в кодовой базе

**Дата**: 2025-11-05  
**Context7**: Полный аудит всех мест с хардкодом `tenant_id="default"` и исправления

---

## 🔍 Найденные проблемы

### 1. `worker/tasks/enrichment_task.py` (строка 889)

**Проблема**: Хардкод `tenant_id = post.get('tenant_id', 'default')` без проверки БД.

**Код**:
```python
# Получаем tenant_id если не передан
if not tenant_id:
    tenant_id = post.get('tenant_id', 'default')  # ❌ ХАРДКОД!
```

**Исправление**: Добавить запрос к БД перед использованием fallback на 'default'.

---

### 2. `worker/tasks/vision_analysis_task.py`

**Проблема**: Нужно проверить, как получается `tenant_id` из события `stream:posts:vision`.

**Статус**: ⏳ Требуется проверка

---

### 3. SQL запросы с COALESCE возвращают 'default'

**Проблема**: SQL запросы в `_get_tenant_id_from_post` и других местах могут возвращать строку `'default'` из COALESCE, даже когда есть реальный tenant_id в БД, но запрос не находит его из-за:
- Отсутствия `user_channel` связи
- Неправильного `channel_id` в запросе
- Пустых `channels.settings->>'tenant_id'`

**Решение**: Улучшить SQL запросы, чтобы они проверяли все возможные источники tenant_id.

---

## ✅ Исправления

### Исправление 1: `enrichment_task.py` строка 889

**Было**:
```python
# Получаем tenant_id если не передан
if not tenant_id:
    tenant_id = post.get('tenant_id', 'default')  # ❌
```

**Должно быть**:
```python
# Получаем tenant_id если не передан
if not tenant_id:
    tenant_id = post.get('tenant_id')
    # Context7: Если tenant_id отсутствует или равен 'default', пытаемся получить из БД
    if not tenant_id or tenant_id == 'default':
        try:
            # Используем тот же SQL запрос, что и в _handle_post_tagged
            tenant_id_result = await self.db_session.execute(
                text("""
                    SELECT COALESCE(
                        (SELECT u.tenant_id::text FROM users u 
                         JOIN user_channel uc ON uc.user_id = u.id 
                         WHERE uc.channel_id = c.id 
                         LIMIT 1),
                        CAST(pe_tags.data->>'tenant_id' AS text),
                        CAST(c.settings->>'tenant_id' AS text),
                        'default'
                    ) as tenant_id
                    FROM posts p
                    JOIN channels c ON c.id = p.channel_id
                    LEFT JOIN post_enrichment pe_tags 
                        ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
                    WHERE p.id = :post_id
                    LIMIT 1
                """),
                {"post_id": post_id}
            )
            row = tenant_id_result.fetchone()
            if row and row[0]:
                tenant_id_db = str(row[0]) if row[0] else None
                if tenant_id_db and tenant_id_db != "default":
                    tenant_id = tenant_id_db
        except Exception as e:
            logger.debug("Failed to get tenant_id from DB", post_id=post_id, error=str(e))
    
    # Fallback на 'default' только если все еще не найден
    if not tenant_id or tenant_id == 'default':
        tenant_id = 'default'
        logger.warning("tenant_id not found, using 'default'", post_id=post_id)
```

---

### Исправление 2: Улучшение SQL запросов

**Проблема**: SQL запросы могут не находить `tenant_id` из-за отсутствия `user_channel`.

**Решение**: Добавить альтернативный путь через прямой запрос к `users` по `channel_id` через `posts.user_id` (если есть).

**Улучшенный SQL**:
```sql
SELECT COALESCE(
    -- Приоритет 1: users.tenant_id через user_channel
    (SELECT u.tenant_id::text FROM users u 
     JOIN user_channel uc ON uc.user_id = u.id 
     WHERE uc.channel_id = c.id 
     LIMIT 1),
    -- Приоритет 2: users.tenant_id через posts.user_id (если есть прямая связь)
    (SELECT u.tenant_id::text FROM users u 
     JOIN posts p2 ON p2.user_id = u.id 
     WHERE p2.id = p.id 
     LIMIT 1),
    -- Приоритет 3: tenant_id из post_enrichment
    CAST(pe_tags.data->>'tenant_id' AS text),
    -- Приоритет 4: tenant_id из channels.settings
    CAST(c.settings->>'tenant_id' AS text),
    -- Fallback: 'default'
    'default'
) as tenant_id
FROM posts p
JOIN channels c ON c.id = p.channel_id
LEFT JOIN post_enrichment pe_tags 
    ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
WHERE p.id = :post_id
LIMIT 1
```

**Примечание**: Нужно проверить, есть ли поле `user_id` в таблице `posts`. Если нет, этот путь не будет работать.

---

## 📋 Чек-лист проверки

- [ ] `worker/tasks/enrichment_task.py` - строка 889
- [ ] `worker/tasks/vision_analysis_task.py` - проверка получения tenant_id
- [ ] `worker/tasks/indexing_task.py` - `_get_tenant_id_from_post` улучшить SQL
- [ ] `worker/tasks/album_assembler_task.py` - проверка SQL запроса
- [ ] `worker/tasks/tag_persistence_task.py` - проверка SQL запроса
- [ ] `telethon-ingest/services/` - проверка получения tenant_id

---

## 🎯 Context7 Best Practices

### Приоритет получения tenant_id:

1. **Приоритет 1**: Из контекста/события (если передан)
2. **Приоритет 2**: Запрос к БД через SQL с COALESCE:
   - `users.tenant_id` через `user_channel`
   - `users.tenant_id` через `posts.user_id` (если есть)
   - `post_enrichment.data->>'tenant_id'`
   - `channels.settings->>'tenant_id'`
3. **Fallback**: `'default'` только если все источники не дали результата

### Логирование:

- **DEBUG**: Когда используем tenant_id из БД
- **WARNING**: Когда используем fallback на 'default'
- **ERROR**: Когда ошибка получения tenant_id из БД (но не прерываем обработку)

### Обработка ошибок:

- Не прерывать обработку поста, если tenant_id не найден
- Логировать предупреждение для диагностики
- Использовать 'default' как последний fallback

---

## 🚀 Следующие шаги

1. Исправить `enrichment_task.py` строка 889
2. Улучшить SQL запросы для получения tenant_id
3. Проверить `vision_analysis_task.py`
4. Создать shared утилиту для получения tenant_id (опционально)
5. Протестировать на реальных данных

