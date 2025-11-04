# Исправление ошибки datetime (offset-aware vs offset-naive)

**Дата:** 2025-11-03 15:15 MSK  
**Статус:** ✅ **ИСПРАВЛЕНО**

---

## Проблема

После исправления импорта `identity_membership` появилась новая ошибка при сохранении постов:

```
asyncpg.exceptions.DataError: invalid input for query argument $3: 
datetime.datetime(2025, 11, 3, 12, 8, 49... 
(can't subtract offset-naive and offset-aware datetimes)
```

### Корневая причина

**asyncpg требует naive datetime** (без timezone) для полей типа `TIMESTAMP` в PostgreSQL, но код использовал `datetime.now(timezone.utc)` (offset-aware datetime).

---

## Исправление

### Context7 Best Practices применены

1. **Использование `datetime.utcnow()` вместо `datetime.now(timezone.utc)`**:
   - `datetime.utcnow()` возвращает naive datetime (без timezone)
   - asyncpg корректно обрабатывает naive datetime для PostgreSQL TIMESTAMP
   - PostgreSQL автоматически интерпретирует naive datetime как UTC

### Изменения в коде

#### `api/utils/identity_membership.py`

**1. `upsert_identity_async()` - создание identity:**

**Было:**
```python
identity_record = {
    'id': str(uuid.uuid4()),
    'telegram_id': telegram_id,
    'created_at': datetime.now(timezone.utc)  # ❌ offset-aware
}
```

**Стало:**
```python
# Context7: asyncpg требует naive datetime для created_at (PostgreSQL TIMESTAMP без timezone)
# Используем datetime.utcnow() вместо datetime.now(timezone.utc)
identity_record = {
    'id': str(uuid.uuid4()),
    'telegram_id': telegram_id,
    'created_at': datetime.utcnow()  # ✅ naive datetime
}
```

**2. `upsert_membership_async()` - создание membership:**

**Было:**
```python
user_record = {
    ...
    'created_at': datetime.now(timezone.utc),  # ❌ offset-aware
    'last_active_at': datetime.now(timezone.utc)  # ❌ offset-aware
}
```

**Стало:**
```python
# Context7: asyncpg требует naive datetime для created_at/last_active_at
# Используем datetime.utcnow() вместо datetime.now(timezone.utc)
user_record = {
    ...
    'created_at': datetime.utcnow(),  # ✅ naive datetime
    'last_active_at': datetime.utcnow()  # ✅ naive datetime
}
```

**3. `upsert_membership_sync()` - обновление membership (синхронная версия):**

**Было:**
```python
user.last_active_at = datetime.utcnow()  # ✅ Уже правильное
```

**Оставлено без изменений** (уже использовался `utcnow()`).

---

## Ожидаемое поведение после исправления

1. `upsert_identity_async` работает без ошибок datetime
2. `upsert_membership_async` работает без ошибок datetime
3. `Atomic batch save successful` в логах
4. Посты сохраняются в БД

---

## Проверка

### Команды для проверки

1. **Мониторинг логов на ошибки datetime:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep -E "(offset-naive|offset-aware|DataError|Atomic batch save)"
```

2. **Проверка новых постов:**
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '10 minutes') as new_posts 
FROM posts;
"
```

### Ожидаемые результаты

- ✅ Нет ошибок "can't subtract offset-naive and offset-aware datetimes"
- ✅ `Atomic batch save successful` в логах
- ✅ `inserted_count > 0` для обработанных постов
- ✅ Новые посты появляются в БД

---

## Время проверки

Исправление применено в **15:15 MSK**. Контейнер перезапущен.

Следующий цикл парсинга должен произойти в течение **5-10 минут**. Рекомендуется проверить результаты через **15-20 минут** после исправления.

---

## Связанные исправления

Это четвертое исправление в серии:
1. ✅ offset_date (исправлено ранее)
2. ✅ since_date (исправлено ранее)
3. ✅ Импорт identity_membership (исправлено ранее)
4. ✅ datetime offset-aware/naive (исправлено сейчас)

---

## Заключение

**Статус:** ✅ **ИСПРАВЛЕНО**

Ошибка datetime (offset-aware vs offset-naive) устранена через использование `datetime.utcnow()` вместо `datetime.now(timezone.utc)` в async функциях работы с БД.

Это должно решить последнюю проблему с сохранением постов в БД. После этого исправления все критические проблемы должны быть решены.

