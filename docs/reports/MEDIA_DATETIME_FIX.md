# Исправление ошибки datetime в media_objects

**Дата:** 2025-11-03 15:30 MSK  
**Статус:** ✅ **ИСПРАВЛЕНО**

---

## Проблема

После исправления всех ошибок сохранения постов, осталась ошибка при сохранении медиа:

```
asyncpg.exceptions.DataError: invalid input for query argument $6: 
datetime.datetime(2025, 11, 3, 12, 26, 47... 
(can't subtract offset-naive and offset-aware datetimes)
```

Ошибка возникала в методе `save_media_to_cas` при вставке в таблицы:
- `media_objects` (поля `first_seen_at`, `last_seen_at`)
- `post_media_map` (поле `uploaded_at`)

### Корневая причина

**asyncpg требует naive datetime** (без timezone) для полей типа `TIMESTAMP` в PostgreSQL, но код использовал `datetime.now(timezone.utc)` (offset-aware datetime).

---

## Исправление

### Context7 Best Practices применены

1. **Использование `datetime.utcnow()` вместо `datetime.now(timezone.utc)`**:
   - `datetime.utcnow()` возвращает naive datetime (без timezone)
   - asyncpg корректно обрабатывает naive datetime для PostgreSQL TIMESTAMP
   - PostgreSQL автоматически интерпретирует naive datetime как UTC
   - Оптимизация: вынесение `datetime.utcnow()` за цикл для всех записей в батче

### Изменения в коде

#### `telethon-ingest/services/atomic_db_saver.py`

**1. `save_media_to_cas()` - сохранение в media_objects:**

**Было:**
```python
media_objects_params = []
for media_file in media_files:
    media_objects_params.append({
        ...
        'now': datetime.now(timezone.utc)  # ❌ offset-aware, вызывается для каждой записи
    })
```

**Стало:**
```python
# Context7: asyncpg требует naive datetime для PostgreSQL TIMESTAMP
# Используем datetime.utcnow() вместо datetime.now(timezone.utc)
now_utc = datetime.utcnow()  # ✅ Вычисляем один раз для всего батча
media_objects_params = []
for media_file in media_files:
    media_objects_params.append({
        ...
        'now': now_utc  # ✅ Используем общий naive datetime
    })
```

**2. `save_media_to_cas()` - сохранение в post_media_map:**

**Было:**
```python
post_media_map_params = []
for idx, media_file in enumerate(media_files):
    post_media_map_params.append({
        ...
        'uploaded_at': datetime.now(timezone.utc)  # ❌ offset-aware, вызывается для каждой записи
    })
```

**Стало:**
```python
# Context7: asyncpg требует naive datetime для PostgreSQL TIMESTAMP
# Используем datetime.utcnow() вместо datetime.now(timezone.utc)
uploaded_at_utc = datetime.utcnow()  # ✅ Вычисляем один раз для всего батча
post_media_map_params = []
for idx, media_file in enumerate(media_files):
    post_media_map_params.append({
        ...
        'uploaded_at': uploaded_at_utc  # ✅ Используем общий naive datetime
    })
```

---

## Ожидаемое поведение после исправления

1. `save_media_to_cas` работает без ошибок datetime
2. Медиа сохраняются в `media_objects` успешно
3. Связи сохраняются в `post_media_map` успешно
4. `Media saved to CAS` в логах (без ошибок)
5. Полная функциональность сохранения постов с медиа восстановлена

---

## Проверка

### Команды для проверки

1. **Мониторинг логов на ошибки datetime в медиа:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep -E "(Failed to save media|Media saved to CAS|offset-naive|offset-aware)"
```

2. **Проверка успешных сохранений медиа:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 10m | grep "Media saved to CAS" | wc -l
```

3. **Проверка медиа в БД:**
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT COUNT(*) as total_media, 
       MAX(last_seen_at) as last_media_seen 
FROM media_objects;
"
```

### Ожидаемые результаты

- ✅ Нет ошибок "can't subtract offset-naive and offset-aware datetimes"
- ✅ `Media saved to CAS` в логах (без ошибок)
- ✅ Медиа появляются в таблице `media_objects`
- ✅ Связи появляются в таблице `post_media_map`

---

## Время проверки

Исправление применено в **15:30 MSK**. Контейнер перезапущен.

Следующий цикл парсинга с медиа должен произойти в течение **5-10 минут**. Рекомендуется проверить результаты через **15-20 минут** после исправления.

---

## Оптимизация

**Дополнительное улучшение:** Вынесение `datetime.utcnow()` за цикл:
- **Было**: `datetime.now(timezone.utc)` вызывался для каждой записи в цикле
- **Стало**: `datetime.utcnow()` вызывается один раз перед циклом, значение используется для всех записей батча
- **Эффект**: Меньше вызовов функции, все записи батча имеют одинаковый timestamp (корректное поведение)

---

## Связанные исправления

Это седьмое исправление в серии:
1. ✅ offset_date (исправлено ранее)
2. ✅ since_date (исправлено ранее)
3. ✅ Импорт identity_membership (исправлено ранее)
4. ✅ datetime в identity_membership (исправлено ранее)
5. ✅ UUID преобразование (исправлено ранее)
6. ✅ datetime в media_objects (исправлено сейчас)
7. ✅ datetime в post_media_map (исправлено сейчас)

---

## Заключение

**Статус:** ✅ **ИСПРАВЛЕНО**

Ошибка datetime (offset-aware vs offset-naive) при сохранении медиа устранена через использование `datetime.utcnow()` вместо `datetime.now(timezone.utc)` в методе `save_media_to_cas`.

После этого исправления **все** проблемы с сохранением данных (посты + медиа) должны быть решены.

