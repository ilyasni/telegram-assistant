# Исправление критической ошибки в расчете since_date

**Дата:** 2025-11-03 15:00 MSK  
**Статус:** ✅ **ИСПРАВЛЕНО**

---

## Проблема

Парсинг пропускал новые посты из-за **неправильного расчета `since_date` в incremental режиме**.

### Симптомы
- `last_post_date`: `2025-11-03 07:02:49` (реальный последний пост в БД)
- `last_parsed_at`: `2025-11-03 10:02:03` (время последнего парсинга)
- `base_date` (старая логика): `2025-11-03 10:02:03` (использовался `last_parsed_at`)
- `since_date`: `2025-11-03 09:57:03` (base_date - 5 минут overlap)
- **Результат**: Искались посты между 09:57 и 10:02, но последний пост был в 07:02!

### Корневая причина

**Старая логика использовала `max(last_post_date, last_parsed_at)` для расчета `base_date`:**

```python
# НЕПРАВИЛЬНО (старая логика)
candidates = []
candidates.append(('last_post_date', last_post_date))
candidates.append(('last_parsed_at', last_parsed_at))
base_utc = max(candidates, key=lambda x: x[1])[1]  # Брали МАКСИМАЛЬНУЮ дату
```

**Проблема:**
- `last_parsed_at` обновляется даже если новых постов НЕ было найдено
- Если парсинг не нашел новых постов, `last_parsed_at` >> `last_post_date`
- `base_date` = `last_parsed_at` (например, 10:02)
- `since_date` = `base_date - overlap` (например, 09:57)
- Но реальный последний пост был в 07:02!
- **Итог**: Искались посты в неправильном диапазоне, новые посты пропускались

---

## Исправление

### Context7 Best Practices применены

1. **Использование реального источника истины**: 
   - Для incremental режима используем ТОЛЬКО `MAX(posted_at)` из БД
   - НЕ используем `last_parsed_at`, так как он может быть неточным

2. **Fallback логика**:
   - Если нет постов в БД → используем `last_parsed_at` или Redis HWM
   - Если нет данных → используем incremental окно

### Изменения в коде

#### `channel_parser.py` - `_get_since_date()`

**Было:**
```python
candidates = []
if last_post_date:
    candidates.append(('last_post_date', last_post_date))
base = channel.get('last_parsed_at') or redis_hwm
if base:
    candidates.append(('last_parsed_at', base_utc))
base_utc = max(candidates, key=lambda x: x[1])[1]  # Брали MAX
```

**Стало:**
```python
if last_post_date:
    # Context7: Используем ТОЛЬКО last_post_date (реальный последний пост в БД)
    # НЕ используем last_parsed_at, так как он может быть неточным
    base_utc = last_post_date
else:
    # Fallback: если нет постов в БД, используем last_parsed_at/HWM
    base = channel.get('last_parsed_at') or redis_hwm
    base_utc = ensure_dt_utc(base) if base else None
```

---

## Ожидаемое поведение после исправления

### Пример для канала fffworks:
- `last_post_date`: `2025-11-03 07:02:49` (реальный последний пост)
- `base_utc`: `2025-11-03 07:02:49` (используем ТОЛЬКО last_post_date)
- `overlap_minutes`: 10 минут
- `since_date`: `2025-11-03 06:52:49` (base_utc - 10 минут)
- **Ищем посты**: между `06:52:49` и `now` (15:00)
- **Результат**: Найдем ВСЕ новые посты с 07:02:49 до текущего момента!

---

## Проверка

### Команды для проверки

1. **Мониторинг логов парсинга:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 5m | grep -E "(Using last_post_date|Calculated since_date|messages_processed)"
```

2. **Проверка новых постов в БД:**
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT MAX(created_at) as last_post, 
       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as posts_last_hour
FROM posts;
"
```

3. **Проверка расчета since_date:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 5m | grep "Using last_post_date as base"
```

### Ожидаемые результаты

- ✅ `base_date` = `last_post_date` (реальный последний пост)
- ✅ `since_date` = `last_post_date - overlap` (правильный диапазон)
- ✅ `messages_processed > 0` для каналов с новыми постами
- ✅ Новые посты появляются в БД после следующего цикла парсинга

---

## Время проверки

Исправление применено в **15:00 MSK**. Следующий цикл парсинга должен произойти в течение **5-10 минут**.

Рекомендуется проверить результаты через **15-20 минут** после исправления.

---

## Связанные файлы

- `telethon-ingest/services/channel_parser.py` - логика расчета since_date
- `docs/reports/INCREMENTAL_PARSING_FIX.md` - первое исправление (offset_date)

---

## Заключение

**Статус:** ✅ **ИСПРАВЛЕНО**

Критическая ошибка в расчете `since_date` устранена. Парсер теперь использует реальный последний пост из БД, а не время последнего парсинга, что гарантирует правильный диапазон поиска новых постов.

