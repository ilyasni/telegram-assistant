# Улучшение алгоритма выбора каналов - Реализация

**Дата**: 2025-12-03  
**Статус**: ✅ Реализовано  
**Context7**: Приоритизация каналов с новыми постами

---

## Проблема

Алгоритм выбора каналов для парсинга не учитывал наличие новых постов:
- Выбирал каналы по `last_parsed_at ASC` (старые первыми)
- Не проверял, есть ли в канале новые посты (`posted_at > last_parsed_at`)
- Не учитывал активность каналов

Результат: Парсинг тратил ресурсы на каналы без новых постов, пропуская каналы с новыми постами.

---

## Решение (Context7 Best Practices)

### Улучшенный алгоритм выбора

**Приоритеты**:
1. **Приоритет 1**: Каналы с новыми постами (`posted_at > last_parsed_at`)
   - Сортировка: по количеству новых постов DESC
   
2. **Приоритет 2**: Активные каналы (много постов за 24 часа)
   - Сортировка: по количеству постов за 24 часа DESC
   
3. **Приоритет 3**: Новые каналы (`last_parsed_at IS NULL`)
   - Сортировка: NULL первыми
   
4. **Приоритет 4**: Давно не парсились (старые `last_parsed_at`)
   - Сортировка: по `last_parsed_at ASC`
   
5. **Приоритет 5**: Fairness между tenant'ами и пользователями

### Техническая реализация

**Использование CTE** для подсчета активности каналов:

```sql
WITH channel_activity AS (
    SELECT 
        c.id as channel_id,
        COUNT(p.id) FILTER (
            WHERE p.posted_at > COALESCE(c.last_parsed_at, '1970-01-01'::timestamp)
        ) as new_posts_count,
        MAX(p.posted_at) as last_post_time,
        COUNT(p.id) FILTER (
            WHERE p.posted_at > NOW() - INTERVAL '24 hours'
        ) as posts_24h
    FROM channels c
    LEFT JOIN posts p ON p.channel_id = c.id
    WHERE c.is_active = true
      AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
    GROUP BY c.id
)
```

**ORDER BY** с приоритетами:

```sql
ORDER BY
  -- Приоритет 1: Каналы с новыми постами
  (COALESCE(ca.new_posts_count, 0) > 0) DESC,
  COALESCE(ca.new_posts_count, 0) DESC NULLS LAST,
  -- Приоритет 2: Активные каналы
  COALESCE(ca.posts_24h, 0) DESC NULLS LAST,
  ca.last_post_time DESC NULLS LAST,
  -- Приоритет 3: Новые каналы
  (c.last_parsed_at IS NULL) DESC,
  -- Приоритет 4: Давно не парсились
  c.last_parsed_at ASC NULLS FIRST,
  -- Приоритет 5: Fairness
  COALESCE(u.tenant_id::text, '00000000-0000-0000-0000-000000000000'),
  COALESCE(uc.user_id::text, '0'),
  c.created_at DESC
```

### Улучшенное логирование

**Добавлены метрики** для observability:

- `channels_with_new_posts` - количество каналов с новыми постами
- `total_new_posts` - общее количество новых постов
- Сохранены существующие метрики (`new_channels`)

---

## Ожидаемые результаты

### До улучшения

- Каналы выбираются по `last_parsed_at ASC`
- Нет учета наличия новых постов
- Нет приоритизации активных каналов
- Неэффективное использование ресурсов

### После улучшения

- ✅ Каналы с новыми постами выбираются первыми
- ✅ Активные каналы получают приоритет
- ✅ Лучшее использование ресурсов парсинга
- ✅ Логирование для диагностики

---

## Context7 Best Practices

1. **Observability**: Логирование выбора каналов с метриками
2. **Priority-based scheduling**: Приоритизация на основе реальной активности
3. **Performance optimization**: Использование CTE для эффективных запросов
4. **Structured logging**: Структурированное логирование с контекстом

---

## Файлы изменены

- `telethon-ingest/tasks/parse_all_channels_task.py`:
  - Метод `_get_active_channels()`: улучшен SQL-запрос с CTE
  - Добавлено логирование новых метрик

---

**Статус**: ✅ Реализовано и готово к тестированию

