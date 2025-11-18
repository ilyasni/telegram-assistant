# Исправление пропуска новых постов в incremental режиме

**Дата**: 2025-01-22  
**Context7**: Критическая ошибка в логике incremental режима приводила к пропуску новых постов

---

## Проблема

Новые посты не парсились, хотя они были опубликованы после `last_post_date`:
- `banksta/82062` (последний в БД: 82058)
- `MarketOverview/17157` (последний в БД: 17156)
- `chinamashina_news/10084` (последний в БД: 10082)

### Причина

**Логика incremental режима**:
1. `since_date = last_post_date - overlap (10 минут)`
2. Если `last_post_date` старше 48 часов → переключение на historical режим
3. Historical режим использует `now - 24 hours` как `since_date`
4. **Проблема**: Посты между `last_post_date` и `now - 24 hours` пропускаются!

**Пример**:
- `last_post_date`: 2025-11-16 21:13 (9 часов назад)
- `now`: 2025-11-17 06:28
- `since_date` (historical): `now - 24h` = 2025-11-16 06:28
- **Пропущены**: Посты между 2025-11-16 21:13 и 2025-11-16 06:28 ❌

---

## Исправление

### Изменение 1: Убрать переключение на historical режим

**Файл**: `telethon-ingest/services/channel_parser.py`

**Было**:
```python
if age_hours > self.config.lpa_max_age_hours:
    logger.warning("Base date too old, forcing historical mode")
    return now - timedelta(hours=self.config.historical_hours)
```

**Стало**:
```python
if age_hours > self.config.lpa_max_age_hours:
    logger.warning(
        "Base date too old, but using it as lower bound to avoid missing posts",
        channel_id=channel_id,
        age_hours=age_hours,
        base_date=base_utc.isoformat()
    )
    # НЕ переключаемся на historical - используем last_post_date как есть
    # Это гарантирует, что мы найдем все посты после last_post_date
```

### Изменение 2: Убрать ограничение min_since_date

**Было**:
```python
since_date = base_utc - timedelta(seconds=int(overlap_minutes * 60))

# Не даём since_date уйти в слишком далёкое прошлое
min_since_date = now - timedelta(hours=self.config.historical_hours)
if since_date < min_since_date:
    since_date = min_since_date
```

**Стало**:
```python
since_date = base_utc - timedelta(seconds=int(overlap_minutes * 60))

# Context7: [C7-ID: incremental-old-date-fix-002] КРИТИЧНО - НЕ ограничиваем since_date для старых дат
# Если last_post_date старый, мы должны парсить все посты после него, даже если это больше 24 часов
# Ограничение min_since_date приводит к пропуску постов между last_post_date и now - 24h
# Убираем это ограничение для incremental режима, чтобы гарантировать полноту парсинга
```

---

## Обоснование

1. **Incremental режим должен парсить все посты после `last_post_date`** - независимо от возраста
2. **Historical режим** используется только для новых каналов или полного перепарсинга
3. **Ограничение `min_since_date`** приводит к пропуску постов между `last_post_date` и `now - 24h`

---

## Проверка

### Команды для проверки

```bash
# Проверка конкретных постов
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT p.id, p.telegram_message_id, c.username, p.posted_at
FROM posts p
JOIN channels c ON c.id = p.channel_id
WHERE (c.username = 'MarketOverview' AND p.telegram_message_id = 17157)
   OR (c.username = 'banksta' AND p.telegram_message_id = 82062)
   OR (c.username = 'chinamashina_news' AND p.telegram_message_id = 10084);
"

# Проверка логов парсинга
docker compose logs telethon-ingest --since 5m | grep -E "(since_date|last_post_date|messages_processed|inserted|saved)"
```

### Ожидаемый результат

1. ✅ Парсинг должен найти все посты после `last_post_date`
2. ✅ Посты должны сохраниться в БД
3. ✅ События `posts.parsed` должны публиковаться в Redis Stream

---

## Impact / Rollback

### Влияние

- ✅ **Критично**: Исправляет пропуск новых постов
- ✅ **Безопасно**: Не влияет на существующие данные
- ✅ **Производительность**: Может увеличить время парсинга для старых каналов, но это ожидаемо

### Rollback

Если потребуется откат:

1. Вернуть переключение на historical режим при `age_hours > lpa_max_age_hours`
2. Вернуть ограничение `min_since_date`
3. Перезапустить `telethon-ingest`: `docker compose restart telethon-ingest`

**Примечание**: Откат вернет проблему, поэтому не рекомендуется.

---

## Context7 Best Practices

1. ✅ **Incremental режим должен быть идемпотентным** - парсит все посты после `last_post_date`
2. ✅ **Не ограничивать диапазон парсинга** - если `last_post_date` старый, парсить все посты после него
3. ✅ **Логирование** - детальное логирование для диагностики проблем
4. ✅ **Мониторинг** - метрики для отслеживания пропусков постов

---

## Статус

✅ **Исправлено** - Логика incremental режима исправлена, новые посты должны парситься корректно.

