# Инструкция: Применение миграции 003 через Supabase Dashboard

## Шаг 1: Открыть SQL Editor в Supabase Dashboard

1. Войдите в [Supabase Dashboard](https://app.supabase.com)
2. Выберите ваш проект
3. В боковом меню выберите **SQL Editor** (или перейдите по ссылке: `https://app.supabase.com/project/{PROJECT_ID}/sql`)

## Шаг 2: Создать новый SQL запрос

1. Нажмите кнопку **New query** (или **+ New query**)
2. Вставьте содержимое миграции `003_add_media_groups_tables.sql`

## Шаг 3: Выполнить миграцию

### Вариант A: Выполнить целиком (рекомендуется для небольших таблиц)

Скопируйте весь SQL из файла и выполните одним запросом:

```sql
-- Создание таблиц для поддержки медиа-альбомов (MessageMediaGroup)
-- Context7 best practice: идемпотентность через UNIQUE constraint, порядок через position, смешанные типы медиа
-- Применяется для полной поддержки Telegram альбомов с дедупликацией

-- ============================================================================
-- 1. Добавление поля grouped_id в таблицу posts
-- ============================================================================

ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS grouped_id BIGINT;

-- ============================================================================
-- 2. Таблица media_groups (группы альбомов)
-- ============================================================================

CREATE TABLE IF NOT EXISTS media_groups (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    grouped_id BIGINT NOT NULL,
    album_kind TEXT,                     -- 'photo'|'video'|'mixed'
    items_count INT NOT NULL DEFAULT 0,
    content_hash TEXT,                   -- детерминированный hash по списку item_ids/bytes
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Уникальный ключ для предотвращения дубликатов на уровне БД
    UNIQUE (user_id, channel_id, grouped_id)
);

-- Индекс для быстрого поиска группы по grouped_id и channel_id
CREATE INDEX IF NOT EXISTS idx_media_groups_grouped_id 
    ON media_groups(channel_id, grouped_id);

-- Индекс для поиска по времени создания
CREATE INDEX IF NOT EXISTS idx_media_groups_created_at 
    ON media_groups(created_at DESC);

-- ============================================================================
-- 3. Таблица media_group_items (элементы альбома)
-- ============================================================================

CREATE TABLE IF NOT EXISTS media_group_items (
    id BIGSERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES media_groups(id) ON DELETE CASCADE,
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    position INT NOT NULL,               -- 0..N-1 для сохранения порядка
    media_type TEXT NOT NULL,            -- 'photo'|'video'|'document'|...
    media_bytes INT,                     -- размер в байтах (для метрик)
    media_sha256 TEXT,                   -- SHA256 медиа файла
    meta JSONB DEFAULT '{}',             -- дополнительные метаданные (width, height, duration, etc.)
    deleted_at TIMESTAMPTZ,              -- для отслеживания удаленных элементов
    
    -- Уникальность: один элемент на позицию в группе
    UNIQUE (group_id, position)
);

-- Индекс для быстрого поиска элементов по post_id
CREATE INDEX IF NOT EXISTS idx_media_group_items_post_id 
    ON media_group_items(post_id);

-- Индекс для поиска элементов группы с порядком
CREATE INDEX IF NOT EXISTS idx_media_group_items_group_position 
    ON media_group_items(group_id, position);

-- Индекс для поиска не удаленных элементов
CREATE INDEX IF NOT EXISTS idx_media_group_items_active 
    ON media_group_items(group_id, position) 
    WHERE deleted_at IS NULL;

-- Индекс для постов с grouped_id (активируем после добавления поля)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_with_grouped_id 
    ON posts(grouped_id) 
    WHERE grouped_id IS NOT NULL;

-- ============================================================================
-- 4. Функции и триггеры
-- ============================================================================

-- Функция для обновления updated_at
CREATE OR REPLACE FUNCTION update_media_groups_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Триггер для автоматического обновления updated_at
CREATE TRIGGER trigger_update_media_groups_updated_at
    BEFORE UPDATE ON media_groups
    FOR EACH ROW
    EXECUTE FUNCTION update_media_groups_updated_at();

-- Функция для обновления items_count в media_groups
CREATE OR REPLACE FUNCTION update_media_groups_items_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE media_groups
        SET items_count = items_count + 1
        WHERE id = NEW.group_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE media_groups
        SET items_count = GREATEST(0, items_count - 1)
        WHERE id = OLD.group_id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Триггеры для автоматического обновления items_count
CREATE TRIGGER trigger_media_group_items_insert
    AFTER INSERT ON media_group_items
    FOR EACH ROW
    EXECUTE FUNCTION update_media_groups_items_count();

CREATE TRIGGER trigger_media_group_items_delete
    AFTER DELETE ON media_group_items
    FOR EACH ROW
    EXECUTE FUNCTION update_media_groups_items_count();

-- ============================================================================
-- 5. Комментарии для документации
-- ============================================================================

COMMENT ON TABLE media_groups IS 'Группы медиа-альбомов из Telegram (MessageMediaGroup)';
COMMENT ON TABLE media_group_items IS 'Элементы медиа-альбомов с порядком';
COMMENT ON COLUMN media_groups.grouped_id IS 'Telegram grouped_id для связи сообщений в альбом';
COMMENT ON COLUMN media_group_items.position IS 'Позиция элемента в альбоме (0-based)';
COMMENT ON COLUMN posts.grouped_id IS 'Telegram grouped_id для связи поста с альбомом';
```

**⚠️ ВАЖНО**: Если вы видите ошибку `CREATE INDEX CONCURRENTLY cannot be executed inside a transaction block`, выполните этот индекс отдельно:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_with_grouped_id 
    ON posts(grouped_id) 
    WHERE grouped_id IS NOT NULL;
```

### Вариант B: Выполнить пошагово (для отладки)

Если возникли проблемы, выполните по частям:

**Шаг 1**: Добавить поле `grouped_id`:
```sql
ALTER TABLE posts 
    ADD COLUMN IF NOT EXISTS grouped_id BIGINT;
```

**Шаг 2**: Создать таблицу `media_groups`:
```sql
CREATE TABLE IF NOT EXISTS media_groups (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    grouped_id BIGINT NOT NULL,
    album_kind TEXT,
    items_count INT NOT NULL DEFAULT 0,
    content_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, channel_id, grouped_id)
);
```

**Шаг 3**: Создать индексы для `media_groups`:
```sql
CREATE INDEX IF NOT EXISTS idx_media_groups_grouped_id 
    ON media_groups(channel_id, grouped_id);

CREATE INDEX IF NOT EXISTS idx_media_groups_created_at 
    ON media_groups(created_at DESC);
```

**Шаг 4**: Создать таблицу `media_group_items`:
```sql
CREATE TABLE IF NOT EXISTS media_group_items (
    id BIGSERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES media_groups(id) ON DELETE CASCADE,
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    position INT NOT NULL,
    media_type TEXT NOT NULL,
    media_bytes INT,
    media_sha256 TEXT,
    meta JSONB DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    UNIQUE (group_id, position)
);
```

**Шаг 5**: Создать индексы для `media_group_items`:
```sql
CREATE INDEX IF NOT EXISTS idx_media_group_items_post_id 
    ON media_group_items(post_id);

CREATE INDEX IF NOT EXISTS idx_media_group_items_group_position 
    ON media_group_items(group_id, position);

CREATE INDEX IF NOT EXISTS idx_media_group_items_active 
    ON media_group_items(group_id, position) 
    WHERE deleted_at IS NULL;
```

**Шаг 6**: Создать функции и триггеры (скопируйте блок "4. Функции и триггеры" из полного SQL)

**Шаг 7**: Создать индекс для `posts` (отдельно, так как CONCURRENTLY):
```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_with_grouped_id 
    ON posts(grouped_id) 
    WHERE grouped_id IS NOT NULL;
```

## Шаг 4: Проверить результат

После выполнения миграции проверьте, что всё создано корректно:

```sql
-- Проверка таблиц
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('media_groups', 'media_group_items');

-- Проверка поля grouped_id
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'posts' 
AND column_name = 'grouped_id';

-- Проверка индексов
SELECT indexname 
FROM pg_indexes 
WHERE tablename IN ('media_groups', 'media_group_items', 'posts')
AND indexname LIKE 'idx_%'
ORDER BY tablename, indexname;
```

## Шаг 5: Отметить миграцию как примененную (опционально)

Если у вас есть таблица `schema_migrations`:

```sql
INSERT INTO schema_migrations (version, applied_at) 
VALUES ('003_add_media_groups_tables', NOW())
ON CONFLICT (version) DO NOTHING;
```

## Troubleshooting

### Ошибка: "relation already exists"
Если таблица уже существует, используйте `CREATE TABLE IF NOT EXISTS` — ошибки не будет.

### Ошибка: "column already exists"
Если поле `grouped_id` уже существует, команда `ADD COLUMN IF NOT EXISTS` просто пропустит его.

### Ошибка: "CREATE INDEX CONCURRENTLY cannot be executed inside a transaction block"
Выполните индекс отдельным запросом (без транзакции):

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_with_grouped_id 
    ON posts(grouped_id) 
    WHERE grouped_id IS NOT NULL;
```

### Ошибка: "foreign key constraint"
Убедитесь, что таблицы `channels` и `posts` существуют и имеют правильные типы полей (`id UUID` для `channels`, `id UUID` для `posts`).

## Дополнительная информация

- **Файл миграции**: `telethon-ingest/migrations/003_add_media_groups_tables.sql`
- **Статус миграций**: `telethon-ingest/MIGRATIONS_STATUS.md`

