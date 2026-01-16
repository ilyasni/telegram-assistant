# Исправление логики вычисления since_date

**Дата**: 2025-12-03  
**Критичность**: КРИТИЧНО  
**Context7**: Исправлена логика вычисления since_date для предотвращения пропуска новых постов

---

## Проблема

Текущая логика использовала `last_post_date` (MAX(posted_at) из БД) как базу для `since_date`:

```python
last_post_date = await self._get_last_post_date(channel_id)
if last_post_date:
    base_utc = last_post_date  # ❌ Неправильно!
    since_date = base_utc - timedelta(seconds=overlap)
```

**Последствия**:
- `since_date` вычисляется от старого `last_post_date`
- `last_parsed_at` может быть обновлен даже без новых постов
- Новые посты, опубликованные между `last_post_date` и `now`, могут быть пропущены

---

## Исправление (Context7 Best Practices)

### Приоритетная логика

1. **Приоритет 1**: `last_parsed_at` (точка последнего парсинга) - самый актуальный источник истины
2. **Приоритет 2**: `last_post_date` (последний пост в БД) - fallback для проверки gap
3. **Приоритет 3**: Redis HWM - временное хранилище

### Новая логика

```python
if mode == "incremental":
    last_parsed_at_raw = channel.get('last_parsed_at')
    last_parsed_utc = ensure_dt_utc(last_parsed_at_raw) if last_parsed_at_raw else None
    last_post_date = await self._get_last_post_date(channel_id)
    
    # Приоритет 1: Используем last_parsed_at если он есть и не слишком старый (< 48 часов)
    if last_parsed_utc:
        age_hours = (now - last_parsed_utc).total_seconds() / 3600
        if age_hours < self.config.lpa_max_age_hours:
            base_utc = last_parsed_utc  # ✅ Используем last_parsed_at
        else:
            # last_parsed_at слишком старый - используем max(last_parsed_at, last_post_date)
            if last_post_date:
                base_utc = max(last_parsed_utc, last_post_date)
            else:
                base_utc = last_parsed_utc
    elif last_post_date:
        # Приоритет 2: Используем last_post_date если нет last_parsed_at
        base_utc = last_post_date
    elif redis_hwm:
        # Приоритет 3: Используем Redis HWM как fallback
        base_utc = redis_hwm
    else:
        # Fallback: используем incremental окно
        return now - timedelta(minutes=self.config.incremental_minutes)
    
    since_date = base_utc - timedelta(seconds=overlap)
```

---

## Преимущества

1. **Актуальность**: Используется `last_parsed_at` (точка последнего парсинга), а не старый `last_post_date`
2. **Надежность**: Fallback на `last_post_date` для проверки gap
3. **Безопасность**: Использование `max(last_parsed_at, last_post_date)` для старых дат
4. **Прозрачность**: Улучшенное логирование источника `since_date`

---

## Пример

**До исправления**:
- `last_post_date` = 2025-12-02 17:17:33 (вчера)
- `last_parsed_at` = 2025-12-03 07:31:11 (сегодня)
- `since_date` = 2025-12-02 17:07:33 (вчера - overlap)
- **Проблема**: Парсинг ищет посты после вчерашней даты, но новые посты могут быть пропущены

**После исправления**:
- `last_parsed_at` = 2025-12-03 07:31:11 (сегодня)
- `since_date` = 2025-12-03 07:21:11 (сегодня - overlap)
- **Результат**: Парсинг ищет посты после последнего парсинга, гарантируя полноту

---

## Тестирование

После применения исправления:
1. Перезапустить контейнер `telethon-ingest`
2. Проверить логи: искать "Using last_parsed_at as base for incremental mode"
3. Проверить метрики: `posts_parsed_total` должен увеличиться
4. Проверить Grafana: "Queue Depth: Pending & New Messages" должен показать новые посты

---

**Статус**: ✅ Исправлено

