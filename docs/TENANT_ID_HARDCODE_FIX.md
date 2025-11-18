# Исправление хардкода tenant_id='default' в tag_persistence_task

**Дата**: 2025-11-05  
**Context7**: Устранение хардкода tenant_id='default' в tag_persistence_task

---

## 🔧 Проблема

**Файл**: `worker/tasks/tag_persistence_task.py`

**Строки 434 и 479**:
```python
"tenant_id": metadata.get("tenant_id", "default"),
```

**Причина**: Хардкод `"default"` использовался, если `metadata` не содержал `tenant_id`. Это приводило к тому, что все новые посты получали `tenant_id='default'` в событиях `posts.enriched`, даже если у них был реальный `tenant_id` в БД.

---

## ✅ Исправление

**Изменения**:
1. Добавлен запрос к БД для извлечения `tenant_id` через COALESCE (users -> tags_data -> channels.settings)
2. Используется приоритет: `tenant_id` из БД > `tenant_id` из metadata > 'default'
3. Добавлено логирование источника `tenant_id` для диагностики

**Код**:
```python
# Context7: Извлекаем tenant_id из БД (как в enrichment_task и indexing_task)
tenant_id_result = await conn.fetchrow(
    """
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
    JOIN channels c ON p.channel_id = c.id
    LEFT JOIN post_enrichment pe_tags 
        ON pe_tags.post_id = p.id AND pe_tags.kind = 'tags'
    WHERE p.id = $1
    LIMIT 1
    """,
    post_id
)

# Context7: Используем tenant_id из БД, fallback на metadata, затем 'default'
tenant_id_from_db = tenant_id_result["tenant_id"] if tenant_id_result else None
tenant_id = metadata.get("tenant_id") or tenant_id_from_db or "default"
```

**Использование**:
- В `tags_data` (строка 434): `"tenant_id": tenant_id`
- В `enriched_event` (строка 479): `"tenant_id": tenant_id`

---

## 📊 Ожидаемый результат

После исправления:
1. ✅ Новые посты получают реальный `tenant_id` из БД
2. ✅ События `posts.enriched` содержат правильный `tenant_id`
3. ✅ IndexingTask индексирует посты в правильные Qdrant коллекции
4. ✅ Коллекции для реальных пользователей начинают заполняться

---

## 🔍 Проверка

После применения исправлений:
- Новые события в `posts.enriched` должны содержать реальный `tenant_id` (не 'default')
- Qdrant коллекции для реальных пользователей должны начать заполняться
- Логи должны показывать источник `tenant_id` (db/metadata/default)

---

## 📄 Документация

- `docs/PIPELINE_CHECK_REPORT.md` - Отчет о проверке пайплайна
- `docs/PIPELINE_FIXES.md` - Исправления пайплайна
- `docs/INDEXING_TASK_FIXES.md` - Исправления IndexingTask

