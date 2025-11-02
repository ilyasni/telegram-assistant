# Результат применения миграции 003

## ✅ Успешно применено

1. **Таблица `media_groups`** - ✓ создана
   - Индексы: `idx_media_groups_grouped_id`, `idx_media_groups_created_at`
   - Функции: `update_media_groups_updated_at()`
   - Триггеры: `trigger_update_media_groups_updated_at`

2. **Таблица `media_group_items`** - ✓ создана
   - Индексы: `idx_media_group_items_post_id`, `idx_media_group_items_group_position`, `idx_media_group_items_active`
   - Триггеры: `trigger_update_media_groups_items_count`

## ⚠️ Требует ручного применения

### 1. Поле `grouped_id` в таблице `posts`

**Проблема:** `ALTER TABLE posts ADD COLUMN grouped_id` зависает из-за блокировок в production окружении.

**Решение:** Применить через **Supabase Dashboard** в окне низкой нагрузки:

```sql
-- Выполнить в Supabase SQL Editor (прямое подключение)
SET lock_timeout = '120s';
SET statement_timeout = '15min';

ALTER TABLE posts ADD COLUMN IF NOT EXISTS grouped_id BIGINT;

COMMENT ON COLUMN posts.grouped_id IS 'Telegram grouped_id для связи поста с альбомом';
```

**Примечание:** Если операция всё ещё зависает:
1. Проверьте блокировки: `scripts/diagnose_migration_locks.sql`
2. Отмените блокирующие запросы
3. Примените в окно низкой нагрузки (ночью/в выходные)

### 2. Индекс CONCURRENTLY для `posts.grouped_id`

**Применить после добавления поля:**

```sql
-- ВАЖНО: Отдельным запросом, БЕЗ транзакции, через прямое подключение
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_with_grouped_id 
    ON posts(grouped_id) 
    WHERE grouped_id IS NOT NULL;
```

## 📊 Текущий статус

```sql
-- Проверка статуса
SELECT 
    'media_groups' AS component,
    CASE WHEN EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'media_groups') 
         THEN '✓ OK' ELSE '✗ MISSING' END AS status
UNION ALL
SELECT 
    'media_group_items',
    CASE WHEN EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'media_group_items') 
         THEN '✓ OK' ELSE '✗ MISSING' END
UNION ALL
SELECT 
    'posts.grouped_id',
    CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'posts' AND column_name = 'grouped_id') 
         THEN '✓ OK' ELSE '✗ MISSING' END
UNION ALL
SELECT 
    'idx_posts_grouped_id',
    CASE WHEN EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'posts' AND indexname = 'idx_posts_with_grouped_id') 
         THEN '✓ OK' ELSE '⚠ NOT CREATED' END;
```

**Ожидаемый результат после полного применения:**
```
component          | status
-------------------+---------------
media_groups       | ✓ OK
media_group_items  | ✓ OK  
posts.grouped_id   | ✓ OK
idx_posts_grouped_id | ✓ OK
```

## 🔧 Рекомендации

1. **Применить оставшиеся шаги через Supabase Dashboard:**
   - Используйте **Direct connection** (не через PgBouncer)
   - Применяйте в окно низкой нагрузки
   - Мониторьте блокировки через диагностические запросы

2. **Если блокировки сохраняются:**
   - Найдите долгие транзакции через `scripts/diagnose_migration_locks.sql`
   - Отмените блокирующие запросы: `SELECT pg_cancel_backend(<pid>);`
   - При необходимости: `SELECT pg_terminate_backend(<pid>);` (осторожно!)

3. **После полного применения:**
   - Отметьте миграцию в `schema_migrations`
   - Проверьте работу приложения с новыми таблицами

## 📝 Связанные файлы

- `MIGRATION_003_SAFE_GUIDE.md` - подробное руководство
- `scripts/diagnose_migration_locks.sql` - диагностика блокировок
- `migrations/003_add_media_groups_tables_safe.sql` - основная миграция
- `migrations/003_add_media_groups_index_concurrent.sql` - индекс CONCURRENTLY

