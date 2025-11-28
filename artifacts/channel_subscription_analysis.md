# Анализ проблемы с добавлением и привязкой каналов

**Дата:** 2025-11-25  
**Статус:** ⚠️ **Обнаружена проблема с дубликатами подписок**

---

## Context

Пользователь 105957884 (shanskiy) сообщает, что добавил намного меньше каналов, чем показывает БД:
- **Показывает БД:** 28 каналов (29 подписок)
- **Уникальных каналов:** 26 с `tg_channel_id` + 3 без `tg_channel_id` = 29 подписок

---

## Наблюдения

### 1. Статистика подписок

- **Всего подписок:** 29
- **Уникальных каналов:** 26 (с `tg_channel_id`) + 3 (без `tg_channel_id`) = 29
- **Проблема:** Нет дубликатов подписок на один канал, но есть проблема с каналами без `tg_channel_id`

### 2. Каналы без `tg_channel_id`

- `beer_for_all` - добавлен 1 раз
- `beer_by` - добавлен 1 раз  
- `prostopropivo` - добавлен 1 раз

**Проблема:** Эти каналы могут создаваться заново при каждом добавлении, т.к. нет уникального индекса на `username`

### 3. Структура таблицы `channels`

```sql
-- Есть UNIQUE индекс на tg_channel_id
"ux_channels_tg_global" UNIQUE, btree (tg_channel_id)
"ux_channels_tg_id" UNIQUE, btree (tg_channel_id)

-- НО НЕТ уникального индекса на username!
"ix_channels_username" btree (username)  -- только обычный индекс
```

**Проблема:** Каналы без `tg_channel_id` могут дублироваться, если добавляются несколько раз

### 4. Логика создания каналов

В `api/routers/channels.py:_get_or_create_channel`:

```python
# Поиск существующего канала
if telegram_id:
    # Ищет по tg_channel_id ✅
    existing_result = db.execute(...)
    
if normalized_username:
    # Ищет по username ✅
    existing_result = db.execute(...)
    
# Создание нового канала
db.execute(
    text("""
        INSERT INTO channels (id, tg_channel_id, username, title, is_active, created_at)
        VALUES (:id, :telegram_id, :username, :title, true, NOW())
    """)
)
```

**Проблема:** 
- НЕТ `ON CONFLICT` для предотвращения дубликатов
- Если два запроса одновременно создают канал без `tg_channel_id`, может создаться два канала с одинаковым `username`

---

## Причины

### 1. Race condition при создании каналов

**Сценарий:**
1. Пользователь добавляет канал `@beer_for_all` (без `tg_channel_id`)
2. Два запроса одновременно проверяют существование канала
3. Оба не находят канал (race condition)
4. Оба создают новый канал с одинаковым `username`
5. Результат: дубликаты каналов

### 2. Отсутствие уникального индекса на `username`

**Проблема:**
- В БД есть UNIQUE индекс на `tg_channel_id`, но НЕТ на `username`
- Каналы без `tg_channel_id` могут дублироваться

### 3. Логика поиска канала не идемпотентна

**Проблема:**
- `_get_or_create_channel` использует SELECT + INSERT вместо UPSERT
- Нет защиты от race condition

---

## Решение

### 1. Добавить уникальный индекс на `username` (частичный)

```sql
-- Создать уникальный индекс только для каналов без tg_channel_id
CREATE UNIQUE INDEX ux_channels_username_no_id 
ON channels(username) 
WHERE tg_channel_id IS NULL AND username IS NOT NULL;
```

### 2. Исправить `_get_or_create_channel` для использования UPSERT

```python
def _get_or_create_channel(
    tenant_id: str,
    username: Optional[str],
    telegram_id: Optional[int],
    title: Optional[str],
    db: Session
) -> Optional[Dict[str, Any]]:
    """Получение или создание канала с защитой от дубликатов."""
    try:
        normalized_username = username.lstrip('@') if username else None
        
        # Context7: Используем UPSERT для предотвращения дубликатов
        if telegram_id:
            # UPSERT по tg_channel_id
            result = db.execute(
                text("""
                    INSERT INTO channels (id, tg_channel_id, username, title, is_active, created_at)
                    VALUES (gen_random_uuid(), :telegram_id, :username, :title, true, NOW())
                    ON CONFLICT (tg_channel_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        title = EXCLUDED.title
                    RETURNING id
                """),
                {
                    "telegram_id": telegram_id,
                    "username": normalized_username,
                    "title": title or normalized_username or f"Channel {telegram_id}"
                }
            )
        elif normalized_username:
            # UPSERT по username (только если нет tg_channel_id)
            result = db.execute(
                text("""
                    INSERT INTO channels (id, tg_channel_id, username, title, is_active, created_at)
                    VALUES (gen_random_uuid(), NULL, :username, :title, true, NOW())
                    ON CONFLICT (username) WHERE tg_channel_id IS NULL DO UPDATE SET
                        title = EXCLUDED.title
                    RETURNING id
                """),
                {
                    "username": normalized_username,
                    "title": title or normalized_username
                }
            )
        else:
            raise ValueError("Either telegram_id or username must be provided")
        
        row = result.fetchone()
        if row:
            db.commit()
            return {"id": str(row.id)}
        else:
            db.rollback()
            return None
            
    except Exception as e:
        logger.error(f"Failed to get or create channel: {e}")
        db.rollback()
        return None
```

### 3. Проверка и очистка существующих дубликатов

```sql
-- Найти дубликаты каналов по username (без tg_channel_id)
SELECT username, COUNT(*) as count, array_agg(id) as channel_ids
FROM channels
WHERE tg_channel_id IS NULL AND username IS NOT NULL
GROUP BY username
HAVING COUNT(*) > 1;

-- Объединить дубликаты (оставить один канал, перенести подписки)
-- TODO: Написать скрипт для объединения дубликатов
```

---

## Checks

### Проверка текущего состояния

```bash
# Проверка дубликатов каналов
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT username, COUNT(*) as count, array_agg(id) as channel_ids
FROM channels
WHERE tg_channel_id IS NULL AND username IS NOT NULL
GROUP BY username
HAVING COUNT(*) > 1;
"

# Проверка подписок пользователя
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT COUNT(*) as total_subscriptions, COUNT(DISTINCT channel_id) as unique_channels
FROM user_channel uc
JOIN users u ON uc.user_id = u.id
WHERE u.telegram_id = 105957884;
"
```

---

## Impact / Rollback

### Impact

- **Дубликаты каналов:** Пользователь видит больше каналов, чем добавил
- **Race condition:** При одновременном добавлении могут создаваться дубликаты
- **Отсутствие уникального индекса:** Каналы без `tg_channel_id` могут дублироваться

### Rollback

Если нужно откатить изменения:
1. Удаление уникального индекса - безопасно
2. Изменения в `_get_or_create_channel` - можно откатить через git
3. Очистка дубликатов - нужно сделать вручную

---

## Рекомендации

1. **Немедленно:** Добавить уникальный индекс на `username` для каналов без `tg_channel_id`
2. **Краткосрочно:** Исправить `_get_or_create_channel` для использования UPSERT
3. **Долгосрочно:** Добавить мониторинг дубликатов каналов

