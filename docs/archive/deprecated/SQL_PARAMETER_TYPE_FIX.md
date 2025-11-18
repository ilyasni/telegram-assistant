# Исправление SQL ошибки AmbiguousParameterError

**Дата**: 2025-01-22  
**Context7**: Критическая ошибка SQL при сохранении постов с forward_from_peer_id

---

## Проблема

**Ошибка**: `AmbiguousParameterError: could not determine data type of parameter $29`

**Причина**: asyncpg не может определить тип параметра `$29` (forward_from_peer_id) при использовании `CASE WHEN` в SQL запросе.

**SQL запрос**:
```sql
CASE WHEN :forward_from_peer_id IS NULL THEN NULL ELSE CAST(:forward_from_peer_id AS jsonb) END
```

---

## Исправление

### Изменение 1: Использование NULLIF вместо CASE WHEN

**Файл**: `telethon-ingest/services/atomic_db_saver.py`

**Было**:
```sql
CASE WHEN :forward_from_peer_id IS NULL THEN NULL ELSE CAST(:forward_from_peer_id AS jsonb) END
```

**Стало**:
```sql
NULLIF(:forward_from_peer_id, '')::jsonb
```

### Изменение 2: Использование пустой строки вместо None

**Было**:
```python
'forward_from_peer_id': json.dumps(post.get('forward_from_peer_id')) if post.get('forward_from_peer_id') else None,
```

**Стало**:
```python
'forward_from_peer_id': json.dumps(post.get('forward_from_peer_id')) if post.get('forward_from_peer_id') else '',
```

**Обоснование**: 
- `NULLIF(value, '')` возвращает `NULL` если значение равно пустой строке
- `::jsonb` применяется к результату `NULLIF`, что корректно обрабатывается PostgreSQL
- Пустая строка `''` вместо `None` позволяет asyncpg правильно определить тип параметра

---

## Проверка

### Команды для проверки

```bash
# Проверка логов на ошибки SQL
docker compose logs telethon-ingest --since 5m | grep -E "(AmbiguousParameterError|could not determine|Atomic batch save failed)"

# Проверка сохранения постов
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT COUNT(*) as total, 
       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '10 minutes') as last_10min
FROM posts;
"
```

### Ожидаемый результат

1. ✅ Нет ошибок `AmbiguousParameterError` в логах
2. ✅ Посты сохраняются в БД корректно
3. ✅ `forward_from_peer_id` сохраняется как `NULL` или корректный JSONB

---

## Impact / Rollback

### Влияние

- ✅ **Критично**: Исправляет ошибку сохранения постов
- ✅ **Безопасно**: Не влияет на существующие данные
- ✅ **Производительность**: Без изменений

### Rollback

Если потребуется откат:

1. Вернуть `CASE WHEN :forward_from_peer_id IS NULL THEN NULL ELSE CAST(:forward_from_peer_id AS jsonb) END`
2. Вернуть `None` вместо `''` для `forward_from_peer_id`
3. Перезапустить `telethon-ingest`: `docker compose restart telethon-ingest`

**Примечание**: Откат вернет ошибку, поэтому не рекомендуется.

---

## Context7 Best Practices

1. ✅ **Явное указание типов** - использование `NULLIF` и `::jsonb` для явного указания типа
2. ✅ **Обработка NULL** - использование пустой строки вместо `None` для asyncpg
3. ✅ **Логирование** - детальное логирование для диагностики проблем
4. ✅ **Тестирование** - проверка сохранения постов после исправления

---

## Статус

✅ **Исправлено** - SQL ошибка исправлена, посты должны сохраняться корректно.

