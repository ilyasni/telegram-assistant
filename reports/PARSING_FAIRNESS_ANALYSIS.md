# Анализ справедливости и полноты парсинга каналов

**Дата**: 2025-12-03  
**Context7**: Анализ fairness распределения парсинга и рекомендации по улучшению

---

## Текущая ситуация

### Статистика

- **Всего активных каналов**: 82
- **Каналов за тик (CHANNELS_PER_TICK)**: 50
- **Тиков для обхода всех каналов**: 2
- **Теоретическое время обхода**: ~10 минут (2 тика × 5 минут)

### Распределение по времени последнего парсинга

- **Парсились 5min-1h назад**: 5 каналов (6.10%)
- **Парсились 1h-6h назад**: 77 каналов (93.90%)
- **Не парсились > 6 часов**: 0 каналов ✅

### Проблема

**77 из 82 каналов** (93.90%) не парсились более 1 часа, хотя теоретически все каналы должны обходиться за ~10 минут.

---

## Причины проблемы

### 1. Приоритизация каналов с новыми постами

**Текущий алгоритм** приоритизирует:
1. Каналы с новыми постами (`posted_at > last_parsed_at`)
2. Активные каналы (много постов за 24 часа)
3. Новые каналы (`last_parsed_at IS NULL`)
4. Давно не парсились (старые `last_parsed_at`)
5. Fairness между tenant'ами и пользователями

**Проблема**: Каналы с новыми постами постоянно выбираются первыми, что может приводить к "starvation" других каналов.

### 2. Отсутствие гарантии минимальной частоты парсинга

**Текущая логика**: Нет гарантии, что каждый канал будет распарсен хотя бы раз в N часов.

**Результат**: Каналы без новых постов могут не парситься долгое время.

---

## Рекомендации (Context7 Best Practices)

### 1. Реализовать Weighted Round-Robin с гарантией минимальной частоты

**Идея**: Комбинировать приоритизацию с гарантией минимальной частоты парсинга.

**Алгоритм**:
1. Выделить слоты для каналов с гарантией (например, 20% от `channels_per_tick`)
2. Остальные слоты использовать для приоритизированных каналов
3. Каналы без парсинга > 6 часов получают высокий приоритет

### 2. Добавить метрики fairness

**Метрики**:
- `channel_max_hours_since_parse` (Gauge) - максимальное время без парсинга
- `channels_starved_total` (Counter) - каналы, которые не парсились > N часов
- `channel_parse_fairness_score` (Gauge) - оценка справедливости (0-1)

### 3. Реализовать анти-starvation механизм

**Механизм**:
- Если канал не парсился > 6 часов → высокий приоритет
- Если канал не парсился > 24 часа → критический приоритет
- Ограничить количество каналов с новыми постами за тик (например, макс. 70%)

### 4. Улучшить алгоритм выбора

**Предложение**:
1. **Гарантия** (20% слотов): Каналы без парсинга > 6 часов
2. **Приоритет** (50% слотов): Каналы с новыми постами
3. **Fairness** (30% слотов): Остальные каналы по round-robin

---

## Предлагаемые изменения

### 1. Модифицировать SQL-запрос выбора каналов

```sql
WITH channel_priority AS (
    SELECT 
        c.id,
        c.username,
        c.title,
        c.last_parsed_at,
        COALESCE(ca.new_posts_count, 0) as new_posts_count,
        COALESCE(ca.posts_24h, 0) as posts_24h,
        CASE 
            WHEN c.last_parsed_at IS NULL THEN 'new_channel'
            WHEN c.last_parsed_at < NOW() - INTERVAL '24 hours' THEN 'critical_starved'
            WHEN c.last_parsed_at < NOW() - INTERVAL '6 hours' THEN 'starved'
            WHEN COALESCE(ca.new_posts_count, 0) > 0 THEN 'has_new_posts'
            ELSE 'normal'
        END as priority_level
    FROM channels c
    LEFT JOIN channel_activity ca ON c.id = ca.channel_id
    WHERE c.is_active = true
      AND (c.blocked_until IS NULL OR c.blocked_until < NOW())
)
SELECT 
    -- Гарантия: каналы без парсинга > 6 часов
    SELECT * FROM channel_priority
    WHERE priority_level IN ('critical_starved', 'starved')
    ORDER BY priority_level, last_parsed_at ASC NULLS FIRST
    LIMIT (channels_per_tick * 0.2)
    
    UNION ALL
    
    -- Приоритет: каналы с новыми постами
    SELECT * FROM channel_priority
    WHERE priority_level = 'has_new_posts'
    ORDER BY new_posts_count DESC
    LIMIT (channels_per_tick * 0.5)
    
    UNION ALL
    
    -- Fairness: остальные каналы
    SELECT * FROM channel_priority
    WHERE priority_level IN ('new_channel', 'normal')
    ORDER BY last_parsed_at ASC NULLS FIRST
    LIMIT (channels_per_tick * 0.3)
```

### 2. Добавить метрики

```python
# Максимальное время без парсинга
channel_max_hours_since_parse = Gauge(
    'channel_max_hours_since_parse',
    'Maximum hours since last parse across all channels'
)

# Количество "голодающих" каналов
channels_starved_total = Gauge(
    'channels_starved_total',
    'Number of channels not parsed for > 6 hours',
    ['threshold_hours']  # 6, 12, 24
)
```

---

## Ожидаемые результаты

### До улучшения:
- 77 из 82 каналов (93.90%) не парсились > 1 часа
- Нет гарантии минимальной частоты парсинга
- Возможна starvation каналов без новых постов

### После улучшения:
- Все каналы парсятся минимум раз в 6 часов ✅
- Справедливое распределение между каналами ✅
- Приоритизация активных каналов сохраняется ✅

---

**Статус**: ⚠️ Требуется улучшение алгоритма для гарантии справедливости

