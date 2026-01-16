# Анализ каналов в БД и создание тестовых данных

**Дата:** 2025-11-25  
**Статус:** ⚠️ **Обнаружена проблема с тестовыми данными**

---

## Context

Проверка каналов в БД `channels` и анализ источников создания каналов, включая тестовые данные.

---

## Наблюдения

### Статистика каналов

- **Всего каналов:** 118
- **Активных каналов:** 104
- **Неактивных каналов:** 14
- **Тестовых каналов:** 45 (38% от общего числа!)
- **Реальных активных каналов:** 59

### Тестовые каналы

- **Название:** "Test E2E Channel"
- **Username:** "test_e2e_channel"
- **Созданы:** 2025-11-22 16:26:08 - 16:29:18 MSK (за 3 минуты)
- **Все активны:** `is_active = true`
- **Уникальные `tg_channel_id`:** 45 (каждый тест создаёт свой канал)

---

## Проблема

### 1. Тесты создают дубликаты

В `tests/e2e/test_trend_refinement_pipeline.py`:

```python
# ❌ ПРОБЛЕМА: ON CONFLICT (id) DO NOTHING
# Но используется случайный tg_channel_id, который может конфликтовать
tg_channel_id = -1000000000000 - random.randint(1000000, 9999999)

await db_session.execute(text("""
    INSERT INTO channels (id, tg_channel_id, username, title, created_at)
    VALUES (:channel_id, :tg_channel_id, 'test_e2e_channel', 'Test E2E Channel', NOW())
    ON CONFLICT (id) DO NOTHING  # ❌ Конфликт по id, а не по tg_channel_id!
"""), {
    "channel_id": channel_id,
    "tg_channel_id": tg_channel_id,
})
```

**Проблема:**
- Тесты используют `ON CONFLICT (id) DO NOTHING`, но проверяют конфликт по `id` (UUID)
- В production коде используется `ON CONFLICT (tg_channel_id)` - правильно
- Уникальный индекс: `ux_channels_tg_global UNIQUE (tg_channel_id)`
- Результат: каждый тест создаёт новый канал, даже если `tg_channel_id` совпадает

### 2. Нет очистки тестовых данных

- Тесты не очищают созданные каналы после выполнения
- Скрипт `cleanup_all_test_data.py` существует, но не очищает тестовые каналы
- Тестовые каналы остаются в production БД

---

## Production код (правильный)

### `atomic_db_saver.py` - правильный UPSERT

```python
# ✅ ПРАВИЛЬНО: ON CONFLICT (tg_channel_id)
sql = """
INSERT INTO channels (id, tg_channel_id, title, username, is_active, created_at)
VALUES (:id, :tg_channel_id, :title, :username, :is_active, :created_at)
ON CONFLICT (tg_channel_id)  # ✅ Конфликт по tg_channel_id
DO UPDATE SET
    title = EXCLUDED.title,
    username = EXCLUDED.username,
    is_active = EXCLUDED.is_active
RETURNING id
"""
```

### `api/routers/channels.py` - создание через API

```python
# ✅ ПРАВИЛЬНО: Проверка существования перед созданием
if telegram_id:
    existing_result = db.execute(
        text("SELECT id FROM channels WHERE tg_channel_id = :telegram_id"),
        {"telegram_id": telegram_id}
    )
    if existing_row:
        return {"id": str(existing_row.id)}

# Создание нового канала
db.execute(
    text("""
        INSERT INTO channels (id, tg_channel_id, username, title, is_active, created_at)
        VALUES (:id, :telegram_id, :username, :title, true, NOW())
    """),
    {...}
)
```

---

## Context7 Best Practices

### 1. UPSERT с ON CONFLICT

**Рекомендация:** Использовать `ON CONFLICT (tg_channel_id)` для каналов, так как:
- `tg_channel_id` - уникальный идентификатор Telegram канала
- Есть уникальный индекс: `ux_channels_tg_global UNIQUE (tg_channel_id)`
- Это предотвращает дубликаты каналов

### 2. Управление тестовыми данными

**Рекомендация:** 
- Использовать фикстуры pytest для автоматической очистки
- Или использовать транзакции с rollback после тестов
- Или добавить cleanup в `cleanup_all_test_data.py`

### 3. Нормализация данных

**Рекомендация:**
- Нормализовать `username` (убирать `@` из начала)
- Использовать единый формат для всех операций

---

## Решение

### 1. Исправить тесты

Изменить `tests/e2e/test_trend_refinement_pipeline.py`:

```python
# ✅ ПРАВИЛЬНО: ON CONFLICT (tg_channel_id)
await db_session.execute(text("""
    INSERT INTO channels (id, tg_channel_id, username, title, created_at)
    VALUES (:channel_id, :tg_channel_id, 'test_e2e_channel', 'Test E2E Channel', NOW())
    ON CONFLICT (tg_channel_id) DO UPDATE SET
        title = EXCLUDED.title,
        username = EXCLUDED.username
    RETURNING id
"""), {
    "channel_id": channel_id,
    "tg_channel_id": tg_channel_id,
})
```

### 2. Добавить очистку тестовых каналов

Добавить в `cleanup_all_test_data.py`:

```python
# Очистка тестовых каналов
await conn.execute(text("""
    DELETE FROM channels 
    WHERE title = 'Test E2E Channel' 
    OR username = 'test_e2e_channel'
"""))
```

### 3. Использовать фикстуры pytest

Добавить cleanup в фикстуры тестов:

```python
@pytest.fixture
async def test_channel(test_db):
    """Создание тестового канала с автоматической очисткой."""
    channel_id = str(uuid.uuid4())
    tg_channel_id = -1000000000000 - random.randint(1000000, 9999999)
    
    async with AsyncSession(test_db) as session:
        await session.execute(text("""
            INSERT INTO channels (id, tg_channel_id, username, title, created_at)
            VALUES (:channel_id, :tg_channel_id, 'test_e2e_channel', 'Test E2E Channel', NOW())
            ON CONFLICT (tg_channel_id) DO UPDATE SET
                title = EXCLUDED.title
        """), {
            "channel_id": channel_id,
            "tg_channel_id": tg_channel_id,
        })
        await session.commit()
    
    yield {"id": channel_id, "tg_channel_id": tg_channel_id}
    
    # Cleanup
    async with AsyncSession(test_db) as session:
        await session.execute(text("""
            DELETE FROM channels WHERE id = :channel_id
        """), {"channel_id": channel_id})
        await session.commit()
```

---

## Checks

### Проверка текущего состояния

```bash
# Количество тестовых каналов
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT COUNT(*) as test_channels 
FROM channels 
WHERE title = 'Test E2E Channel' OR username = 'test_e2e_channel';
"

# Проверка дубликатов по tg_channel_id
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT tg_channel_id, COUNT(*) as count 
FROM channels 
WHERE title = 'Test E2E Channel' 
GROUP BY tg_channel_id 
HAVING COUNT(*) > 1;
"
```

### После исправления

1. Запустить тесты и проверить, что не создаются дубликаты
2. Проверить, что тестовые каналы очищаются после тестов
3. Запустить `cleanup_all_test_data.py` для очистки существующих тестовых каналов

---

## Impact / Rollback

### Impact

- **Тестовые каналы:** 45 каналов (38% от общего числа) - это много для production БД
- **Производительность:** Тестовые каналы попадают в scheduler и парсятся каждые 5 минут
- **Данные:** Тестовые каналы могут влиять на статистику и отчёты

### Rollback

Если нужно откатить изменения:
1. Тестовые каналы можно удалить вручную через SQL
2. Изменения в тестах не влияют на production код
3. Скрипт cleanup можно запустить в любое время

---

## Рекомендации

1. **Немедленно:** Запустить cleanup для удаления существующих тестовых каналов
2. **Краткосрочно:** Исправить тесты для использования правильного ON CONFLICT
3. **Долгосрочно:** Добавить автоматическую очистку тестовых данных в CI/CD

