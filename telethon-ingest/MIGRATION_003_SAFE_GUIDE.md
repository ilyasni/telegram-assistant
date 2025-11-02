# Безопасное применение миграции 003: Media Groups

## 🚨 Важно: Используйте прямое подключение к Postgres

Миграции с `CREATE INDEX CONCURRENTLY` и некоторые DDL операции требуют **прямого подключения к Postgres**, минуя PgBouncer.

### В Supabase Dashboard:

1. Откройте **Project Settings** → **Database**
2. Найдите **Connection string** с пометкой **"Direct connection"** или **"Session mode"**
3. Используйте этот connection string для выполнения миграций

Или добавьте параметр `?pgbouncer=false` к вашему connection string.

---

## 📋 Пошаговый план применения

### Шаг 0: Диагностика (опционально, но рекомендуется)

Выполните диагностические запросы из `scripts/diagnose_migration_locks.sql` для проверки:
- Активных блокировок
- Размеров таблиц
- Долгих запросов

**Если обнаружены блокировки:**
1. Определите blocking_pid
2. Попробуйте мягкую отмену: `SELECT pg_cancel_backend(<blocking_pid>);`
3. При необходимости: `SELECT pg_terminate_backend(<blocking_pid>);` (осторожно!)

---

### Шаг 1: Основная миграция (без CONCURRENTLY)

**Файл:** `003_add_media_groups_tables_safe.sql`

Выполните в SQL Editor Supabase:

```sql
-- Копируйте содержимое файла 003_add_media_groups_tables_safe.sql
```

**Что делает:**
- ✅ Добавляет поле `grouped_id` в таблицу `posts`
- ✅ Создает таблицы `media_groups` и `media_group_items`
- ✅ Создает обычные индексы (без CONCURRENTLY) - безопасно для новых таблиц
- ✅ Создает функции и триггеры

**Время выполнения:** Обычно < 1 секунды (для новых пустых таблиц)

**Проверка успешности:**
```sql
-- Проверка таблиц
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('media_groups', 'media_group_items');

-- Должно вернуть 2 строки

-- Проверка поля grouped_id
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'posts' 
AND column_name = 'grouped_id';

-- Должно вернуть 1 строку с data_type = 'bigint'
```

---

### Шаг 2: Индекс CONCURRENTLY (отдельно, для существующей таблицы posts)

**Файл:** `003_add_media_groups_index_concurrent.sql`

**⚠️ ВАЖНО:** Выполните это **ОТДЕЛЬНЫМ запросом**, **ВНЕ транзакции**

Если таблица `posts` уже содержит данные (>10K записей), этот шаг может занять время:

- **10K записей:** ~5-10 секунд
- **100K записей:** ~30-60 секунд
- **1M+ записей:** ~5-15 минут

**Выполнение:**

1. Откройте **новый SQL запрос** в Supabase Dashboard
2. Скопируйте содержимое `003_add_media_groups_index_concurrent.sql`
3. **НЕ заворачивайте в транзакцию** (не используйте BEGIN/COMMIT)
4. Выполните запрос

**Проверка прогресса (если долго выполняется):**

```sql
-- Проверка статуса индекса
SELECT 
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE indexname = 'idx_posts_with_grouped_id';

-- Если индекс создается, он будет виден, но может быть в состоянии "IN PROGRESS"
-- Проверьте также:
SELECT * FROM pg_stat_progress_create_index;
```

---

## 🔍 Troubleshooting

### Проблема: "CREATE INDEX CONCURRENTLY cannot be executed inside a transaction block"

**Решение:** Выполните индекс отдельным запросом (без BEGIN/COMMIT). В Supabase SQL Editor просто выполните SQL без оборачивания в транзакцию.

---

### Проблема: "lock timeout" или миграция "зависает"

**Диагностика:**
```sql
-- См. файл scripts/diagnose_migration_locks.sql
-- Проверьте блокировки и долгие запросы
```

**Действия:**
1. Установите таймауты:
   ```sql
   SET lock_timeout = '10s';
   SET statement_timeout = '10min';
   ```

2. Найдите blocking запрос:
   ```sql
   SELECT pid, query, now() - query_start AS duration
   FROM pg_stat_activity
   WHERE state = 'active'
     AND now() - query_start > interval '5 seconds'
   ORDER BY duration DESC;
   ```

3. Отмените блокирующий запрос (если безопасно):
   ```sql
   SELECT pg_cancel_backend(<blocking_pid>);
   ```

4. Перезапустите миграцию

---

### Проблема: "relation already exists"

**Решение:** Это нормально, миграция использует `IF NOT EXISTS`. Продолжайте дальше.

---

### Проблема: "foreign key constraint" ошибка

**Проверка:**
```sql
-- Убедитесь, что таблицы channels и posts существуют
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('channels', 'posts');

-- Проверьте типы полей id
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('channels', 'posts')
AND column_name = 'id';
```

**Должно быть:** `channels.id = UUID` и `posts.id = UUID`

---

## ✅ Финальная проверка

После применения обеих частей миграции:

```sql
-- 1. Проверка таблиц
SELECT 
    'media_groups' AS table_name,
    COUNT(*) AS exists_check
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name = 'media_groups'
UNION ALL
SELECT 
    'media_group_items',
    COUNT(*)
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name = 'media_group_items';

-- Должно вернуть 2 строки с exists_check = 1

-- 2. Проверка индексов
SELECT 
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename IN ('media_groups', 'media_group_items', 'posts')
AND indexname LIKE 'idx_%'
ORDER BY tablename, indexname;

-- Должно быть:
-- media_groups: idx_media_groups_created_at, idx_media_groups_grouped_id
-- media_group_items: idx_media_group_items_active, idx_media_group_items_group_position, idx_media_group_items_post_id
-- posts: idx_posts_with_grouped_id

-- 3. Проверка функций и триггеров
SELECT 
    routine_name,
    routine_type
FROM information_schema.routines
WHERE routine_schema = 'public'
AND routine_name LIKE '%media_group%';

-- Должно быть 2 функции: update_media_groups_updated_at, update_media_groups_items_count

SELECT 
    trigger_name,
    event_object_table
FROM information_schema.triggers
WHERE trigger_schema = 'public'
AND trigger_name LIKE '%media_group%';

-- Должно быть 2 триггера на media_groups и media_group_items
```

---

## 📝 Отметка миграции как примененной

После успешного применения:

```sql
-- Создайте таблицу отслеживания миграций (если её нет)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Отметьте миграцию как примененную
INSERT INTO schema_migrations (version) 
VALUES ('003_add_media_groups_tables')
ON CONFLICT (version) DO NOTHING;
```

---

## 🔗 Связанные файлы

- `migrations/003_add_media_groups_tables_safe.sql` - основная миграция
- `migrations/003_add_media_groups_index_concurrent.sql` - индекс CONCURRENTLY
- `scripts/diagnose_migration_locks.sql` - диагностика блокировок
- `MIGRATIONS_STATUS.md` - общий статус миграций

