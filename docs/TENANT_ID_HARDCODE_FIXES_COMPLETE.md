# Полный отчет об исправлении хардкода tenant_id

**Дата**: 2025-11-05  
**Context7**: Устранение всех хардкодов tenant_id='default' и 'unknown' в кодовой базе

---

## Найденные проблемы

### 1. `worker/tasks/enrichment_task.py` (строка 306)

**Проблема**: Хардкод `"default"` использовался, если `post_context` не содержал `tenant_id`:
```python
"tenant_id": post_context.get("tenant_id", "default") if post_context else "default"
```

**Исправление**: Добавлен запрос к БД для извлечения `tenant_id` через `COALESCE` (users -> tags_data -> channels.settings) как fallback перед использованием `'default'`.

**Логика**:
1. Приоритет 1: `tenant_id` из `post_context`
2. Приоритет 2: Запрос к БД через `COALESCE`
3. Fallback: `'default'` с предупреждением в логах

### 2. `worker/tasks/indexing_task.py` (строки 1188, 1201)

**Проблема**: Использовался `post_data.get('tenant_id', 'default')`, что возвращало `'default'` даже если `tenant_id` был `None`.

**Исправление**: Изменено на `post_data.get('tenant_id') or 'default'`, чтобы корректно обрабатывать `None` значения.

### 3. `worker/tasks/album_assembler_task.py` (строка 725)

**Статус**: ✅ Нормально - `state.get('tenant_id', 'default')` используется для получения `tenant_id` из состояния альбома, которое уже заполнено из события `albums.parsed` с правильным `tenant_id`.

### 4. `telethon-ingest/services/telegram_client.py` (строки 362, 561, 576, 625, 663)

**Статус**: ✅ Нормально - `message_data.get('tenant_id', 'default')` используется для получения `tenant_id` из `message_data`, которое уже заполнено из парсинга канала с правильным `tenant_id`.

### 5. `api/main.py` (строка 263)

**Статус**: ✅ Нормально - `tenant_id = extract_tenant_id_from_jwt(request) or "unknown"` используется для метрик и логирования, где `"unknown"` является допустимым значением для некорректных запросов.

---

## Исправления

### Файл: `worker/tasks/enrichment_task.py`

**Изменения**:
1. Добавлен запрос к БД для извлечения `tenant_id` через `COALESCE` перед использованием `'default'`
2. Улучшено логирование источника `tenant_id` для диагностики
3. Добавлено предупреждение, если `tenant_id` не найден

**Код**:
```python
# Context7: Если tenant_id отсутствует или равен 'default', пытаемся получить из БД
if not tenant_id or tenant_id == "default":
    try:
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
        if row:
            tenant_id_db = str(row[0]) if row[0] else None
            if tenant_id_db and tenant_id_db != "default":
                tenant_id = tenant_id_db
```

### Файл: `worker/tasks/indexing_task.py`

**Изменения**:
1. Изменено `post_data.get('tenant_id', 'default')` на `post_data.get('tenant_id') or 'default'` для корректной обработки `None` значений

---

## Ожидаемый результат

После применения исправлений:
1. `enrichment_task` будет корректно извлекать `tenant_id` из БД перед использованием `'default'`
2. `indexing_task` будет корректно обрабатывать `None` значения `tenant_id`
3. События `posts.enriched` и `posts.indexed` будут содержать правильный `tenant_id`
4. Qdrant коллекции для реальных пользователей начнут заполняться

---

## Документация

- `docs/TENANT_ID_HARDCODE_FIX.md` - Исправление хардкода tenant_id в tag_persistence_task
- `docs/INDEXING_TASK_FIXES.md` - Исправления IndexingTask
- `docs/PIPELINE_FIXES.md` - Исправления пайплайна
- `docs/QDRANT_CHECK_REPORT.md` - Отчет о проверке Qdrant

