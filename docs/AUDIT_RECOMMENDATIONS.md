# Рекомендации по исправлению: Vision + S3 + Crawl4ai Integration

**Дата**: 2025-01-30  
**Статус**: Готово к реализации  
**Приоритет**: См. таблицу ниже

## Приоритизированный список исправлений

### 🔴 КРИТИЧЕСКИЙ ПРИОРИТЕТ (блокируют работу)

#### 1. Добавить миграцию для поля `kind` в `post_enrichment`

**Файл**: `api/alembic/versions/YYYYMMDD_add_kind_to_post_enrichment.py`

**Действия**:
```python
def upgrade() -> None:
    # Добавляем колонку kind
    op.add_column('post_enrichment', sa.Column('kind', sa.Text(), 
        nullable=False, server_default='tags'))
    
    # Создаём уникальный индекс (post_id, kind)
    op.create_unique_constraint(
        'ux_post_enrichment_post_kind',
        'post_enrichment',
        ['post_id', 'kind']
    )
    
    # Обновляем существующие записи (если есть) - устанавливаем kind='general'
    op.execute("""
        UPDATE post_enrichment 
        SET kind = 'general' 
        WHERE kind IS NULL
    """)
    
    # Удаляем server_default после установки значений
    op.alter_column('post_enrichment', 'kind', server_default=None)
```

**Также обновить модели ORM**:
- `api/models/database.py` - добавить `kind = Column(String(50), nullable=False, default='tags')`
- `worker/shared/database.py` - аналогично
- Добавить в `__table_args__`: `UniqueConstraint('post_id', 'kind', name='ux_post_enrichment_post_kind')`

**Context7 best practices**:
- Использовать server_default для безопасной миграции
- Уникальный индекс для обеспечения целостности
- Обновить существующие записи перед удалением default

---

#### 2. Унифицировать конфликт-таргеты в `post_enrichment`

**Файлы для изменения**:

**a) `worker/tasks/vision_analysis_task.py:457`**

**Было**:
```python
ON CONFLICT (post_id) DO UPDATE SET
```

**Должно быть**:
```python
ON CONFLICT (post_id, kind) DO UPDATE SET
```

**И добавить**:
```python
# В VALUES добавить:
'vision',  # kind
...
```

**b) `worker/tasks/enrichment_task.py:763`**

**Было**:
```python
UPDATE post_enrichment 
SET crawl_md = :crawl_md,
    ...
WHERE post_id = :post_id
```

**Должно быть**:
```python
INSERT INTO post_enrichment (
    post_id, kind, crawl_md, ...
) VALUES (
    :post_id, 'crawl', :crawl_md, ...
)
ON CONFLICT (post_id, kind) DO UPDATE SET
    crawl_md = EXCLUDED.crawl_md,
    ...
```

**Context7 best practices**:
- Использовать UPSERT вместо UPDATE для идемпотентности
- Один конфликт-таргет для всех компонентов
- Модульное сохранение по видам обогащения

---

#### 3. Реализовать заполнение `post_forwards`, `post_reactions`, `post_replies`

**Файл**: `telethon-ingest/services/telegram_client.py` (или создать отдельный сервис)

**Добавить методы**:

```python
async def _save_forwards(
    self, 
    post_id: str, 
    message: Any, 
    db_connection
) -> None:
    """Сохранение деталей forwards в post_forwards."""
    # Извлечение forwards из message.fwd_from
    if not hasattr(message, 'fwd_from') or not message.fwd_from:
        return
    
    forwards_data = []
    # Обработка message.fwd_from (может быть один forward)
    # Telegram API: fwd_from может содержать from_id, date, etc.
    
    # Batch insert с идемпотентностью
    with db_connection.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO post_forwards (
                post_id, from_chat_id, from_message_id,
                from_chat_title, from_chat_username, forwarded_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, forwards_data)

async def _save_reactions(
    self,
    post_id: str,
    message: Any,
    db_connection
) -> None:
    """Сохранение деталей reactions в post_reactions."""
    if not hasattr(message, 'reactions') or not message.reactions:
        return
    
    reactions_data = []
    # Обработка message.reactions
    # Telegram API: reactions содержит список Reaction объектов
    
    with db_connection.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO post_reactions (
                post_id, reaction_type, reaction_value, user_tg_id, is_big
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (post_id, reaction_type, reaction_value, user_tg_id) 
            DO UPDATE SET updated_at = NOW()
        """, reactions_data)

async def _save_replies(
    self,
    post_id: str,
    message: Any,
    db_connection
) -> None:
    """Сохранение деталей replies в post_replies."""
    # Обработка message.replies (если есть)
    # Telegram API: replies содержит reply_to_msg_id, etc.
    
    # Извлечение из message.reply_to
    if hasattr(message, 'reply_to') and message.reply_to:
        # Сохранение в post_replies
        pass
```

**Интеграция**: Вызывать эти методы в `_save_message` после сохранения поста.

**Context7 best practices**:
- Batch insert для производительности
- Идемпотентность через ON CONFLICT
- Использование транзакций для атомарности

---

### 🟡 ВЫСОКИЙ ПРИОРИТЕТ (могут привести к потере данных)

#### 4. Реализовать заполнение `media_objects` и `post_media_map`

**Файл**: `telethon-ingest/services/media_processor.py`

**Добавить метод**:

```python
async def _save_media_to_db(
    self,
    post_id: str,
    media_files: List[MediaFile],
    db_pool: asyncpg.Pool
) -> None:
    """
    Сохранение медиа в БД: media_objects и post_media_map.
    
    Context7: Используем транзакции для атомарности.
    """
    if not media_files:
        return
    
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for idx, media_file in enumerate(media_files):
                # 1. UPSERT в media_objects
                await conn.execute("""
                    INSERT INTO media_objects (
                        file_sha256, mime, size_bytes, s3_key, s3_bucket,
                        first_seen_at, last_seen_at, refs_count
                    ) VALUES (
                        $1, $2, $3, $4, $5, NOW(), NOW(), 1
                    )
                    ON CONFLICT (file_sha256) DO UPDATE SET
                        last_seen_at = NOW(),
                        refs_count = media_objects.refs_count + 1
                """,
                    media_file.sha256,
                    media_file.mime_type,
                    media_file.size_bytes,
                    media_file.s3_key,
                    self.s3_service.bucket_name
                )
                
                # 2. INSERT в post_media_map
                await conn.execute("""
                    INSERT INTO post_media_map (
                        post_id, file_sha256, position, role
                    ) VALUES ($1, $2, $3, 'primary')
                    ON CONFLICT (post_id, file_sha256) DO NOTHING
                """,
                    post_id,
                    media_file.sha256,
                    idx
                )
```

**Интеграция**: Вызывать после успешной загрузки в S3 в `process_message_media`.

**Context7 best practices**:
- Транзакции для атомарности
- Инкремент refs_count при конфликтах
- ON CONFLICT для идемпотентности

---

#### 5. Определить migration path для legacy полей

**План миграции**:

1. **OCR поля**:
   - `ocr_text` → `vision_ocr_text` (если источник Vision)
   - Оставить `ocr_text` для crawl-based OCR

2. **Vision labels**:
   - `vision_labels` → deprecated
   - Использовать только `vision_classification`

**SQL скрипт миграции**:
```sql
-- Копирование ocr_text в vision_ocr_text (если vision_ocr_text пусто)
UPDATE post_enrichment
SET vision_ocr_text = ocr_text
WHERE ocr_text IS NOT NULL 
AND vision_ocr_text IS NULL
AND vision_provider IS NOT NULL;

-- Копирование vision_labels в vision_classification (если vision_classification пусто)
UPDATE post_enrichment
SET vision_classification = vision_labels::jsonb
WHERE vision_labels IS NOT NULL 
AND vision_classification IS NULL;
```

---

### 🟢 СРЕДНИЙ ПРИОРИТЕТ (влияют на производительность)

#### 6. Добавить недостающие индексы

```sql
-- Если kind добавлен
CREATE INDEX IF NOT EXISTS idx_post_enrichment_kind 
ON post_enrichment(kind) 
WHERE kind IS NOT NULL;

-- Для post_forwards
CREATE INDEX IF NOT EXISTS idx_post_forwards_post 
ON post_forwards(post_id);

-- Для post_reactions
CREATE INDEX IF NOT EXISTS idx_post_reactions_post 
ON post_reactions(post_id);

-- Для post_replies
CREATE INDEX IF NOT EXISTS idx_post_replies_post 
ON post_replies(post_id);
CREATE INDEX IF NOT EXISTS idx_post_replies_reply_to 
ON post_replies(reply_to_post_id) 
WHERE reply_to_post_id IS NOT NULL;
```

---

#### 7. Проверить foreign key constraints

**Проверка**:
```sql
-- Проверить существующие FK
SELECT 
    tc.table_name,
    tc.constraint_name,
    rc.delete_rule,
    rc.update_rule
FROM information_schema.table_constraints tc
JOIN information_schema.referential_constraints rc 
    ON tc.constraint_name = rc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
AND tc.table_name IN (
    'post_forwards', 'post_reactions', 'post_replies',
    'post_media_map'
);
```

**Рекомендация**: Убедиться, что все FK имеют `ON DELETE CASCADE` для связанных записей.

---

## Контрольный список реализации

- [ ] Миграция для `kind` создана и протестирована
- [ ] Модели ORM обновлены
- [ ] `vision_analysis_task.py` обновлён для использования `kind='vision'`
- [ ] `enrichment_task.py` обновлён для использования UPSERT с `kind='crawl'`
- [ ] Реализовано заполнение `post_forwards`
- [ ] Реализовано заполнение `post_reactions`
- [ ] Реализовано заполнение `post_replies`
- [ ] Реализовано заполнение `media_objects` и `post_media_map`
- [ ] Миграция legacy полей выполнена
- [ ] Индексы добавлены
- [ ] FK constraints проверены
- [ ] Тесты написаны и пройдены
- [ ] Документация обновлена

---

## Использование Context7

Все исправления должны следовать Context7 best practices:
- **PostgreSQL**: Использовать транзакции, правильные индексы, constraints
- **Идемпотентность**: ON CONFLICT для всех INSERT операций
- **Обработка ошибок**: Правильная обработка Cloud.ru S3 специфичных ошибок
- **Нормализация**: Избегать дублирования данных между таблицами
- **Метрики**: Добавить Prometheus метрики для новых операций

