# P3 — Sideloading: Implementation Complete

## Context

**Context7 P3**: Полная реализация импорта личных диалогов и групп через Telethon для persona-based RAG.

## Реализованные компоненты

### ✅ 1. Поле `source` в таблицах БД

**Файлы**:
- `api/models/database.py`: Добавлено поле `source` в `Post` и `GroupMessage`
- `api/alembic/versions/20250121_add_source_field_p3.py`: Миграция

**Статус**: ✅ Готово к применению миграции

**Значения**:
- `Post.source`: `'channel'` (по умолчанию), `'group'`, `'dm'`, `'persona'`
- `GroupMessage.source`: `'group'` (по умолчанию), `'dm'`, `'persona'`

### ✅ 2. SideloadService

**Файл**: `telethon-ingest/services/sideload_service.py`

**Функционал**:
- ✅ Импорт диалогов через `iter_dialogs()`
- ✅ Импорт сообщений из DM и групп через `iter_messages()`
- ✅ Классификация диалогов (DM/group/channel)
- ✅ Сохранение в PostgreSQL с флагом `source`
- ✅ Публикация событий в Redis Streams
- ✅ Виртуальные каналы для DM (отрицательный `tg_channel_id`)

**Основные методы**:
- `import_user_dialogs()`: Импорт всех диалогов пользователя
- `_import_dialog_messages()`: Импорт сообщений из диалога
- `_classify_dialog()`: Классификация типа диалога
- `_extract_message_data()`: Извлечение данных сообщения
- `_save_messages_batch()`: Сохранение сообщений в БД
- `_publish_persona_events()`: Публикация событий

### ✅ 3. Схемы событий

**Файл**: `api/worker/events/schemas/persona_messages_v1.py`

**События**:
- ✅ `PersonaMessageIngestedEventV1`: Событие импорта сообщения из личного диалога/группы
- ✅ `PersonaGraphUpdatedEventV1`: Событие обновления графа persona в Neo4j (подготовлено)

**Redis Stream**: `stream:persona:messages:ingested`

### ✅ 4. Интеграция с Graph-RAG (Neo4j)

**Файлы**:
- `api/worker/integrations/neo4j_client.py`: ✅ Добавлены методы для Persona и Dialogue узлов
- `api/worker/services/graph_writer.py`: ✅ Добавлена обработка persona событий

**Методы Neo4jClient**:
- ✅ `create_persona_node()`: Создание узла `:Persona` для пользователя
- ✅ `create_dialogue_node()`: Создание узла `:Dialogue` для диалога
- ✅ `create_persona_message_relationship()`: Создание связей между Post и Dialogue

**Методы GraphWriter**:
- ✅ `_process_persona_batch()`: Обработка батча persona событий
- ✅ `_process_persona_message_event()`: Обработка отдельного persona события
- ✅ `start_consuming_persona()`: Запуск consumption persona stream

**Графовые связи**:
- `(:User)-[:HAS_PERSONA]->(:Persona)`
- `(:Persona)-[:HAS_DIALOGUE]->(:Dialogue)`
- `(:Post)-[:IN_DIALOGUE]->(:Dialogue)`
- `(:Persona)-[:SENT_MESSAGE]->(:Post)`

## Архитектура

### Поток данных для Sideloading

```
Telethon (iter_dialogs) 
  → SideloadService.import_user_dialogs()
    → Классификация диалогов (DM/group)
      → Итерация по сообщениям (iter_messages)
        → Извлечение данных (_extract_message_data)
          → Сохранение в БД (_save_messages_batch)
            → Публикация событий (_publish_persona_events)
              → Redis Streams (stream:persona:messages:ingested)
                → GraphWriter._process_persona_message_event()
                  → Neo4j (создание Persona, Dialogue узлов и связей)
```

### Использование существующих таблиц

**Context7 P3**: Используем существующие таблицы с флагом `source`:
- `Post` для DM (`source='dm'`) и каналов (`source='channel'`)
- `GroupMessage` для групп (`source='group'`) и persona-групп (`source='persona'`)

**Особенности DM**:
- DM диалоги используют виртуальные "каналы" с отрицательным `tg_channel_id` (`-abs(peer_id)`)
- Каждый DM диалог = отдельный виртуальный канал
- Автоматическое создание канала при первом сообщении

## Применение миграции

```bash
# Применить миграцию для добавления поля source
docker-compose exec api alembic upgrade head

# Проверка миграции
docker-compose exec supabase-db psql -U postgres -d telegram_assistant -c "
SELECT column_name, data_type, column_default 
FROM information_schema.columns 
WHERE table_name = 'posts' AND column_name = 'source';
"
```

## Использование

### Пример: Импорт диалогов пользователя

```python
from services.sideload_service import SideloadService
from services.telegram_client_manager import TelegramClientManager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Инициализация
client_manager = TelegramClientManager(redis_client, db_connection)
db_session = async_session(bind=async_engine)()

sideload_service = SideloadService(
    telegram_client_manager=client_manager,
    db_session=db_session,
    redis_client=redis_client,
    event_publisher=event_publisher
)

# Импорт последних 100 сообщений из всех DM
result = await sideload_service.import_user_dialogs(
    user_id="123456789",
    tenant_id="550e8400-e29b-41d4-a716-446655440000",
    dialog_types=['dm'],
    limit_per_dialog=100
)

print(f"Импортировано: {result['stats']['messages_imported']} сообщений")
```

### Пример: Импорт сообщений из групп за последние 30 дней

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

### Пример: Запуск GraphWriter для persona событий

```python
from worker.services.graph_writer import GraphWriter
from worker.integrations.neo4j_client import Neo4jClient

# Инициализация
neo4j_client = Neo4jClient()
await neo4j_client.connect()

graph_writer = GraphWriter(
    neo4j_client=neo4j_client,
    redis_client=redis_client,
    db_session=db_session
)

# Запуск consumption persona событий
await graph_writer.start_consuming_persona()
```

## Проверка работы

### Проверка миграции

```bash
# Проверка поля source в posts
docker-compose exec supabase-db psql -U postgres -d telegram_assistant -c "
SELECT column_name, data_type, column_default 
FROM information_schema.columns 
WHERE table_name = 'posts' AND column_name = 'source';
"

# Проверка поля source в group_messages
docker-compose exec supabase-db psql -U postgres -d telegram_assistant -c "
SELECT column_name, data_type, column_default 
FROM information_schema.columns 
WHERE table_name = 'group_messages' AND column_name = 'source';
"
```

### Проверка импортированных сообщений

```sql
-- DM сообщения
SELECT COUNT(*) FROM posts WHERE source = 'dm';

-- Групповые сообщения
SELECT COUNT(*) FROM group_messages WHERE source = 'group';

-- Виртуальные каналы для DM (отрицательные ID)
SELECT COUNT(*) FROM channels WHERE tg_channel_id < 0;
```

### Проверка событий в Redis

```bash
# Проверка событий persona в Redis Streams
docker-compose exec redis redis-cli XLEN stream:persona:messages:ingested

# Просмотр последних событий
docker-compose exec redis redis-cli XREVRANGE stream:persona:messages:ingested + - COUNT 5
```

### Проверка графа в Neo4j

```cypher
// Проверка Persona узлов
MATCH (p:Persona)
RETURN p.user_id, p.telegram_id, p.tenant_id
LIMIT 10;

// Проверка Dialogue узлов
MATCH (d:Dialogue)
RETURN d.dialogue_id, d.dialogue_type, d.peer_id, d.peer_name
LIMIT 10;

// Проверка связей Persona -> Dialogue
MATCH (pers:Persona)-[:HAS_DIALOGUE]->(d:Dialogue)
RETURN pers.user_id, d.dialogue_id, d.dialogue_type
LIMIT 10;

// Проверка связей Post -> Dialogue
MATCH (p:Post)-[:IN_DIALOGUE]->(d:Dialogue)
RETURN p.post_id, d.dialogue_id, d.dialogue_type
LIMIT 10;
```

## Ограничения и будущие улучшения

### Текущие ограничения

1. **Медиа**: Медиа-вложения не обрабатываются (флаг `has_media` устанавливается, но файлы не скачиваются)
2. **Forward/Reply**: Информация о пересылках и ответах не извлекается для DM/групп
3. **Группы в Neo4j**: Связи для групповых сообщений в Neo4j пока не реализованы (только DM)

### Планируемые улучшения

1. **Медиа-обработка**: Интеграция с `MediaProcessor` для обработки медиа из DM/групп
2. **Deep Extraction**: Извлечение forwards/replies для persona сообщений
3. **Группы в Neo4j**: Создание связей для групповых сообщений в Neo4j
4. **RAG индексация**: Индексация persona сообщений в Qdrant для персонализированного RAG

## Безопасность и приватность

**Context7 P3**: Sideloading работает с личными данными пользователя.

**Важно**:
- ✅ Все сообщения сохраняются с `tenant_id` для RLS изоляции
- ✅ DM диалоги используют виртуальные каналы с отрицательными ID для изоляции
- ✅ События публикуются в tenant-scoped Redis Streams
- ✅ Доступ к persona данным контролируется через RLS policies

## Метрики

**Статистика импорта**:
- `dialogs_processed`: Количество обработанных диалогов
- `messages_imported`: Количество импортированных сообщений
- `dm_messages`: Количество DM сообщений
- `group_messages`: Количество сообщений из групп
- `errors`: Количество ошибок
- `skipped`: Количество пропущенных сообщений

**Prometheus метрики** (GraphWriter):
- `graph_writer_processed_total{operation_type="persona", status="ok"}`
- `graph_writer_operation_duration_seconds{operation_type="persona"}`
- `graph_writer_errors_total{error_type="processing_error"}`

## Статус реализации

- ✅ Поле `source` в таблицах БД
- ✅ Миграция для добавления поля
- ✅ SideloadService для импорта диалогов/групп
- ✅ Схемы событий для persona messages
- ✅ Интеграция с Graph-RAG (Neo4j)
- ✅ GraphWriter обработка persona событий
- ✅ Документация

**Готово к применению миграции и тестированию!**

