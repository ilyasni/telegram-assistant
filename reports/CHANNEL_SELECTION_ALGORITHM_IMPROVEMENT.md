# Улучшение алгоритма выбора каналов для парсинга

**Дата**: 2025-12-03  
**Проблема**: Парсинг выбирает каналы без учета наличия новых постов  
**Context7**: Улучшение алгоритма приоритизации каналов

---

## Текущая проблема

### Алгоритм выбора каналов

**Текущий SQL-запрос** (строки 1302-1308 в `parse_all_channels_task.py`):

```sql
ORDER BY
  (c.last_parsed_at IS NULL) DESC,  -- Явный приоритет NULL (TRUE идет первым)
  c.last_parsed_at ASC NULLS FIRST,  -- Старые каналы в приоритете
  COALESCE(u.tenant_id::text, '00000000-0000-0000-0000-000000000000'),  -- Fairness
  COALESCE(uc.user_id::text, '0'),  -- Fairness
  c.created_at DESC
LIMIT %s
```

### Проблемы текущего алгоритма

1. **Не учитывает наличие новых постов**:
   - Выбирает каналы по `last_parsed_at ASC` (старые первыми)
   - Но не проверяет, есть ли в канале новые посты (`posted_at > last_parsed_at`)

2. **Нет приоритизации активных каналов**:
   - Каналы с высокой активностью обрабатываются так же, как и неактивные
   - Нет учета частоты публикаций

3. **Неэффективное использование ресурсов**:
   - Парсинг каналов, в которых нет новых постов
   - Пропуск каналов, в которых есть новые посты

---

## Рекомендации (Context7 Best Practices)

### 1. Приоритизация каналов с новыми постами

**Context7 Best Practice**: Приоритизировать каналы на основе реальной активности.

**Предлагаемый алгоритм**:

1. **Приоритет 1**: Каналы с новыми постами (`posted_at > last_parsed_at`)
   - Подзапрос: `EXISTS (SELECT 1 FROM posts WHERE channel_id = c.id AND posted_at > c.last_parsed_at)`
   - Сортировка: по количеству новых постов DESC

2. **Приоритет 2**: Каналы с активностью за последние 24 часа
   - Сортировка: по `MAX(posted_at)` DESC

3. **Приоритет 3**: Каналы, которые давно не парсились
   - Сортировка: по `last_parsed_at ASC` (старые первыми)

4. **Приоритет 4**: Fairness между tenant'ами и пользователями

### 2. Добавить метрики для observability

**Context7 Best Practice**: Метрики для мониторинга эффективности выбора каналов.

**Метрики для добавления**:

```python
# Метрики выбора каналов
channel_selection_total = Counter(
    'channel_selection_total',
    'Total channels selected for parsing',
    ['priority', 'has_new_posts']
)

channel_selection_effectiveness = Gauge(
    'channel_selection_effectiveness',
    'Effectiveness of channel selection (new posts found / channels parsed)',
    ['priority']
)

channel_parsing_activity_score = Gauge(
    'channel_parsing_activity_score',
    'Activity score of parsed channel (new posts / time since last parse)',
    ['channel_id']
)
```

### 3. Улучшить SQL-запрос

**Предлагаемый улучшенный SQL**:

```sql
WITH channel_activity AS (
  SELECT 
    c.id,
    c.last_parsed_at,
    COUNT(p.id) FILTER (WHERE p.posted_at > COALESCE(c.last_parsed_at, '1970-01-01'::timestamp)) as new_posts_count,
    MAX(p.posted_at) as last_post_time,
    COUNT(p.id) FILTER (WHERE p.posted_at > NOW() - INTERVAL '24 hours') as posts_24h
  FROM channels c
  LEFT JOIN posts p ON p.channel_id = c.id
  WHERE c.is_active = true
    AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
  GROUP BY c.id, c.last_parsed_at
)
SELECT 
  c.id,
  c.tg_channel_id,
  c.username,
  c.title,
  c.last_parsed_at,
  c.is_active,
  c.blocked_until,
  COALESCE(u.tenant_id::text, '00000000-0000-0000-0000-000000000000') as tenant_id,
  COALESCE(uc.user_id::text, '0') as user_id,
  ca.new_posts_count,
  ca.last_post_time,
  ca.posts_24h
FROM channels c
LEFT JOIN user_channel uc ON c.id = uc.channel_id AND uc.is_active = true
LEFT JOIN users u ON uc.user_id = u.id
LEFT JOIN channel_activity ca ON c.id = ca.id
WHERE c.is_active = true
  AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
ORDER BY
  -- Приоритет 1: Каналы с новыми постами
  (ca.new_posts_count > 0) DESC,
  ca.new_posts_count DESC NULLS LAST,
  -- Приоритет 2: Активные каналы
  ca.posts_24h DESC NULLS LAST,
  ca.last_post_time DESC NULLS LAST,
  -- Приоритет 3: Давно не парсились
  (c.last_parsed_at IS NULL) DESC,
  c.last_parsed_at ASC NULLS FIRST,
  -- Приоритет 4: Fairness
  COALESCE(u.tenant_id::text, '00000000-0000-0000-0000-000000000000'),
  COALESCE(uc.user_id::text, '0'),
  c.created_at DESC
LIMIT %s
```

### 4. Добавить индексы для производительности

**Context7 Best Practice**: Индексы для оптимизации запросов.

**Индексы для добавления**:

```sql
-- Индекс для быстрого поиска новых постов
CREATE INDEX IF NOT EXISTS idx_posts_channel_posted_at 
ON posts(channel_id, posted_at DESC) 
WHERE posted_at > NOW() - INTERVAL '7 days';

-- Индекс для быстрого подсчета постов за 24 часа
CREATE INDEX IF NOT EXISTS idx_posts_channel_created_at 
ON posts(channel_id, created_at DESC) 
WHERE created_at > NOW() - INTERVAL '7 days';
```

---

## План реализации

### Этап 1: Добавить CTE для активности каналов

1. Добавить CTE `channel_activity` для подсчета новых постов
2. Использовать CTE в основном запросе
3. Добавить сортировку по приоритету

### Этап 2: Добавить метрики

1. Добавить метрики `channel_selection_total`
2. Добавить метрики `channel_selection_effectiveness`
3. Логировать выбор каналов с приоритетом

### Этап 3: Добавить индексы

1. Создать индексы для оптимизации запросов
2. Проверить производительность запроса

### Этап 4: Тестирование

1. Проверить, что каналы с новыми постами выбираются первыми
2. Проверить метрики выбора каналов
3. Проверить производительность запроса

---

## Ожидаемые результаты

### До улучшения

- Каналы выбираются по `last_parsed_at ASC`
- Нет учета наличия новых постов
- Нет приоритизации активных каналов
- Метрики не показывают эффективность выбора

### После улучшения

- Каналы с новыми постами выбираются первыми
- Активные каналы получают приоритет
- Метрики показывают эффективность выбора
- Лучшее использование ресурсов парсинга

---

## Context7 Best Practices

1. **Observability**: Метрики для мониторинга эффективности
2. **Priority-based scheduling**: Приоритизация на основе реальной активности
3. **Performance optimization**: Индексы для оптимизации запросов
4. **Structured logging**: Логирование выбора каналов с приоритетом

---

**Статус**: Требуется реализация

