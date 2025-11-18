# P3 — Sideloading Implementation

## Context

**Context7 P3**: Импорт личных диалогов и групп через Telethon для persona-based RAG.

## Реализованные компоненты

### 1. Поле `source` в таблицах БД

**Файлы**:
- `api/models/database.py`: Добавлено поле `source` в `Post` и `GroupMessage`
- `api/alembic/versions/20250121_add_source_field_p3.py`: Миграция для добавления поля

**Значения**:
- `Post.source`: `'channel'` (по умолчанию), `'group'`, `'dm'`, `'persona'`
- `GroupMessage.source`: `'group'` (по умолчанию), `'dm'`, `'persona'`

**Использование**:
```sql
-- Фильтрация по источнику
SELECT * FROM posts WHERE source = 'dm';
SELECT * FROM group_messages WHERE source = 'group';
```

### 2. SideloadService

**Файл**: `telethon-ingest/services/sideload_service.py`

**Основные методы**:
- `import_user_dialogs()`: Импорт всех диалогов пользователя
- `_import_dialog_messages()`: Импорт сообщений из диалога
- `_classify_dialog()`: Классификация типа диалога (DM/group/channel)
- `_save_messages_batch()`: Сохранение сообщений в БД
- `_publish_persona_events()`: Публикация событий в Redis Streams

**Использование**:
```python
from services.sideload_service import SideloadService
from services.telegram_client_manager import TelegramClientManager

# Инициализация
sideload_service = SideloadService(
    telegram_client_manager=client_manager,
    db_session=db_session,
    redis_client=redis_client,
    event_publisher=event_publisher
)

# Импорт диалогов пользователя
result = await sideload_service.import_user_dialogs(
    user_id="123456789",
    tenant_id="550e8400-e29b-41d4-a716-446655440000",
    dialog_types=['dm', 'group'],  # Или None для всех
    limit_per_dialog=100,
    since_date=datetime.now(timezone.utc) - timedelta(days=30)
)
```

### 3. Схемы событий

**Файл**: `api/worker/events/schemas/persona_messages_v1.py`

**События**:
- `PersonaMessageIngestedEventV1`: Событие импорта сообщения из личного диалога/группы
- `PersonaGraphUpdatedEventV1`: Событие обновления графа persona в Neo4j

**Redis Stream**: `stream:persona:messages:ingested`

### 4. Хранение DM в Post

**Context7 P3**: Личные диалоги (DM) сохраняются в таблицу `posts` с `source='dm'`.

**Особенности**:
- Используются виртуальные "каналы" с отрицательным `tg_channel_id` (`-abs(peer_id)`)
- Каждый DM диалог = отдельный виртуальный канал
- Автоматическое создание канала при первом сообщении

**Пример**:
```sql
-- DM сообщения в таблице posts
SELECT * FROM posts WHERE source = 'dm';
```

## Архитектура

### Поток данных

```
Telethon (iter_dialogs) 
  → SideloadService.import_user_dialogs()
    → Классификация диалогов (DM/group)
      → Итерация по сообщениям (iter_messages)
        → Извлечение данных (_extract_message_data)
          → Сохранение в БД (_save_messages_batch)
            → Публикация событий (_publish_persona_events)
              → Redis Streams (stream:persona:messages:ingested)
                → Graph-RAG Writer (Neo4j)
```

### Использование существующих таблиц

**Context7 P3**: Используем существующие таблицы с флагом `source`:
- `Post` для DM (`source='dm'`) и каналов (`source='channel'`)
- `GroupMessage` для групп (`source='group'`) и persona-групп (`source='persona'`)

## Применение миграции

```bash
# Применить миграцию для добавления поля source
# Через Docker контейнер:
docker-compose exec api alembic upgrade head

# Или напрямую в контейнере:
docker-compose exec api bash -c "cd /app && alembic upgrade head"
```

**Проверка миграции**:
```sql
-- Проверка поля source в posts
SELECT column_name, data_type, column_default 
FROM information_schema.columns 
WHERE table_name = 'posts' AND column_name = 'source';

-- Проверка поля source в group_messages
SELECT column_name, data_type, column_default 
FROM information_schema.columns 
WHERE table_name = 'group_messages' AND column_name = 'source';
```

## Интеграция с Graph-RAG (Реализовано)

**Статус**: ✅ Реализовано

**Реализовано**:
- ✅ Создание узлов `:Persona` в Neo4j через `create_persona_node()`
- ✅ Создание узлов `:Dialogue` для диалогов через `create_dialogue_node()`
- ✅ Создание связей `[:HAS_PERSONA]`, `[:HAS_DIALOGUE]`, `[:IN_DIALOGUE]`, `[:SENT_MESSAGE]`
- ✅ Обработка событий `persona_message_ingested` в GraphWriter
- ✅ Публикация событий `persona_graph_updated` (подготовлено)

**Файлы**:
- `api/worker/integrations/neo4j_client.py`: ✅ Добавлены методы для Persona и Dialogue узлов
- `api/worker/services/graph_writer.py`: ✅ Добавлена обработка persona событий

**Методы Neo4jClient**:
- `create_persona_node()`: Создание узла `:Persona` для пользователя
- `create_dialogue_node()`: Создание узла `:Dialogue` для диалога
- `create_persona_message_relationship()`: Создание связей между Post и Dialogue

**Методы GraphWriter**:
- `_process_persona_batch()`: Обработка батча persona событий
- `_process_persona_message_event()`: Обработка отдельного persona события
- `start_consuming_persona()`: Запуск consumption persona stream

**Использование**:
```python
# GraphWriter автоматически обрабатывает persona события из stream:persona:messages:ingested
# Или запустить отдельно:
await graph_writer.start_consuming_persona()
```

## Примеры использования

### Импорт последних 100 сообщений из всех DM

```python
result = await sideload_service.import_user_dialogs(
    user_id="123456789",
    tenant_id="550e8400-e29b-41d4-a716-446655440000",
    dialog_types=['dm'],
    limit_per_dialog=100
)
```

### Импорт сообщений из групп за последние 30 дней

```python
from datetime import datetime, timezone, timedelta

result = await sideload_service.import_user_dialogs(
    user_id="123456789",
    tenant_id="550e8400-e29b-41d4-a716-446655440000",
    dialog_types=['group'],
    limit_per_dialog=500,
    since_date=datetime.now(timezone.utc) - timedelta(days=30)
)
```

### Запрос DM сообщений из БД

```sql
-- Все DM сообщения пользователя
SELECT p.*, c.title as dialog_title
FROM posts p
JOIN channels c ON p.channel_id = c.id
WHERE p.source = 'dm'
  AND p.posted_at >= NOW() - INTERVAL '30 days'
ORDER BY p.posted_at DESC;
```

## Ограничения и будущие улучшения

### Текущие ограничения

1. **Медиа**: Медиа-вложения не обрабатываются (флаг `has_media` устанавливается, но файлы не скачиваются)
2. **Forward/Reply**: Информация о пересылках и ответах не извлекается для DM/групп
3. **Neo4j интеграция**: Базовый функционал, полная интеграция с Graph-RAG pending

### Планируемые улучшения

1. **Медиа-обработка**: Интеграция с `MediaProcessor` для обработки медиа из DM/групп
2. **Deep Extraction**: Извлечение forwards/replies для persona сообщений
3. **Neo4j Persona Nodes**: Создание узлов `:Persona` и `:Dialogue` в Neo4j
4. **RAG индексация**: Индексация persona сообщений в Qdrant для персонализированного RAG

## Безопасность и приватность

**Context7 P3**: Sideloading работает с личными данными пользователя.

**Важно**:
- Все сообщения сохраняются с `tenant_id` для RLS изоляции
- DM диалоги используют виртуальные каналы с отрицательными ID для изоляции
- События публикуются в tenant-scoped Redis Streams
- Доступ к persona данным контролируется через RLS policies

## Метрики

**Статистика импорта**:
- `dialogs_processed`: Количество обработанных диалогов
- `messages_imported`: Количество импортированных сообщений
- `dm_messages`: Количество DM сообщений
- `group_messages`: Количество сообщений из групп
- `errors`: Количество ошибок
- `skipped`: Количество пропущенных сообщений

## Проверка работы

```bash
# Проверка миграции
docker-compose exec supabase-db psql -U postgres -d telegram_assistant -c "SELECT column_name, data_type, column_default FROM information_schema.columns WHERE table_name = 'posts' AND column_name = 'source';"

# Проверка DM сообщений
docker-compose exec supabase-db psql -U postgres -d telegram_assistant -c "SELECT COUNT(*) FROM posts WHERE source = 'dm';"

# Проверка событий в Redis
docker-compose exec redis redis-cli XLEN stream:persona:messages:ingested
```

