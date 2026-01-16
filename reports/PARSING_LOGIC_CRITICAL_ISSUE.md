# Критическая проблема в логике парсинга

**Дата**: 2025-12-03  
**Критичность**: КРИТИЧНО  
**Context7**: Проблема с вычислением since_date приводит к пропуску новых постов

---

## Проблема

### Текущая логика

В `channel_parser.py`, метод `_get_since_date()` для incremental режима:

```python
last_post_date = await self._get_last_post_date(channel_id)  # MAX(posted_at) из БД
if last_post_date:
    base_utc = last_post_date  # Используем last_post_date как базу
    since_date = base_utc - timedelta(seconds=overlap)  # Вычитаем overlap
```

### Что происходит

1. **`last_post_date`** = последний пост в БД (например, вчера 17:17)
2. **`since_date`** = `last_post_date` - overlap (например, вчера 17:07)
3. **`last_parsed_at`** = обновляется ДО парсинга (например, сегодня 07:31)
4. **Парсинг** ищет посты после `since_date` (вчера 17:07)
5. **НО**: Новых постов может не быть, если они уже были обработаны

### Результат

- Парсинг ищет посты после **СТАРОЙ** даты (`last_post_date`)
- Но `last_parsed_at` уже обновлен до **НОВОЙ** даты
- Новые посты, опубликованные между `last_post_date` и `now`, могут быть пропущены

---

## Пример проблемы

Для канала "Нейро":
- `last_post_date` = 2025-12-02 17:17:33 (вчера)
- `last_parsed_at` = 2025-12-03 07:31:11 (сегодня)
- `since_date` = 2025-12-02 17:07:33 (вчера - 10 минут overlap)

**Проблема**: Парсинг ищет посты после вчерашней даты, но `last_parsed_at` уже сегодня. Это означает, что парсинг уже запускался, но новых постов не нашел.

---

## Правильная логика (Context7)

### Использовать `last_parsed_at` как базу

**Приоритеты**:
1. **Приоритет 1**: Использовать `last_parsed_at` (если есть и не слишком старый)
2. **Приоритет 2**: Использовать `last_post_date` (только как fallback)
3. **Приоритет 3**: Использовать Redis HWM (если доступен)

### Логика

```python
if mode == "incremental":
    # Приоритет 1: last_parsed_at (точка последнего парсинга)
    last_parsed_at = channel.get('last_parsed_at')
    if last_parsed_at:
        last_parsed_utc = ensure_dt_utc(last_parsed_at)
        if last_parsed_utc:
            # Проверяем, не слишком ли старый last_parsed_at
            age_hours = (now - last_parsed_utc).total_seconds() / 3600
            if age_hours < 48:  # Не старше 48 часов
                base_utc = last_parsed_utc  # Используем last_parsed_at
            else:
                # Слишком старый - используем last_post_date
                base_utc = await self._get_last_post_date(channel_id) or last_parsed_utc
        else:
            base_utc = await self._get_last_post_date(channel_id)
    else:
        # Приоритет 2: last_post_date (fallback)
        base_utc = await self._get_last_post_date(channel_id)
    
    # Приоритет 3: Redis HWM (fallback)
    if not base_utc:
        base_utc = redis_hwm
    
    # Вычисляем since_date с overlap
    since_date = base_utc - timedelta(seconds=overlap)
```

---

## Рекомендации (Context7 Best Practices)

### 1. Исправить логику вычисления since_date

**Приоритеты**:
1. `last_parsed_at` - точка последнего парсинга (самая актуальная)
2. `last_post_date` - последний пост в БД (fallback)
3. Redis HWM - временное хранилище (fallback)

### 2. Добавить проверку наличия новых постов

**Перед парсингом**:
- Проверять, есть ли посты между `last_post_date` и `now`
- Если есть - приоритизировать канал для парсинга
- Если нет - можно пропустить или использовать более длинный интервал

### 3. Улучшить логирование

**Метрики**:
- `parsing_since_date_source` - источник since_date (last_parsed_at / last_post_date / hwm)
- `parsing_gap_seconds` - разница между last_post_date и now
- `parsing_new_posts_expected` - ожидаемое количество новых постов

---

**Статус**: Требуется исправление

