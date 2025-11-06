# Анализ пайплайна tenant_id

**Дата**: 2025-11-05  
**Context7**: Анализ передачи tenant_id через пайплайн

---

## Текущая ситуация

### 1. Qdrant коллекции

- **`t6bf3422f-456f-4c41-a7f8-c86861c328c9_posts`** (User 8124731874): **0 точек**
- **`te70c43b0-e11d-45a8-8e51-f0ead91fb126_posts`** (User 139883458): **0 точек**
- **`tNone_posts`**: **103 точки**

### 2. Статус индексации

- **Всего постов**: 371
- **Embedding**: success=0, **pending=301**, failed=0
- **Graph**: success=0, **pending=301**, failed=0

### 3. События

- **posts.enriched**: 2 из 10 последних с реальным tenant_id
- **posts.indexed**: 0 из 10 последних с реальным tenant_id, все с `tenant_id='unknown'`

---

## Проблема

`posts.indexed` события содержат `tenant_id='unknown'` вместо реального `tenant_id` или `'default'`.

**Возможные причины**:
1. `_get_tenant_id_from_post` возвращает `None`, и где-то `None` преобразуется в `'unknown'`
2. EventPublisher сериализует `None` как `'unknown'`
3. EventConsumer парсит события и заменяет `None` на `'unknown'`

---

## Исправления

### 1. `_get_tenant_id_from_post` в `indexing_task.py`

**Проблема**: Запрос не возвращал `'default'` как fallback, что могло приводить к `None`.

**Исправление**: Добавлен `'default'` в `COALESCE` запрос, аналогично другим методам.

### 2. Логика обработки `None` в `_get_tenant_id_from_post`

**Проблема**: Если `tenant_id` из БД был `'default'`, метод возвращал `None`, что могло приводить к потере значения.

**Исправление**: Метод теперь возвращает `None` только если `tenant_id` действительно не найден, позволяя использовать fallback на `'default'` в вызывающем коде.

---

## Ожидаемый результат

После исправлений:
1. `_get_tenant_id_from_post` будет возвращать `'default'` если tenant_id не найден
2. `posts.indexed` будут содержать `'default'` вместо `'unknown'`
3. Посты с реальным `tenant_id` будут индексироваться в правильные коллекции

---

## Документация

- `docs/TENANT_ID_HARDCODE_FIXES_COMPLETE.md` - Полный отчет об исправлениях
- `docs/QDRANT_CHECK_REPORT.md` - Отчет о проверке Qdrant

