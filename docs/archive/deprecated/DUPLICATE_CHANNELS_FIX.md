# Исправление проблемы дубликатов каналов

**Дата**: 2025-01-22  
**Context7**: Критическая проблема - дубликаты каналов без `tg_channel_id` блокировали парсинг

---

## Проблема

**Симптомы**:
- Новые посты не появлялись в БД
- В логах: `ERROR: No username and no tg_channel_id`
- `FloodWait` на `ResolveUsernameRequest` (7000+ секунд)

**Причина**:
В БД были **дубликаты каналов**:
- Один канал с `tg_channel_id` (активный, парсится успешно)
- Другой канал без `tg_channel_id` (активный, не может быть распознан)

Scheduler парсил каналы **без** `tg_channel_id`, которые не могли быть распознаны из-за FloodWait на `ResolveUsernameRequest`.

**Пример**:
```
banksta:
  - id: 3e68034c-59b5-429d-911d-62ab3b8e5c95, tg_channel_id: -1001136626166, is_active: true ✅
  - id: d55671d2-90e9-43ba-9bad-95e984b6523d, tg_channel_id: NULL, is_active: true ❌
```

---

## Решение

**Деактивация дубликатов без `tg_channel_id`**:

```sql
UPDATE channels 
SET is_active = false 
WHERE tg_channel_id IS NULL 
  AND username IN ('banksta', 'MarketOverview', 'chinamashina_news')
  AND EXISTS (
    SELECT 1 FROM channels c2 
    WHERE c2.username = channels.username 
      AND c2.tg_channel_id IS NOT NULL 
      AND c2.is_active = true
  );
```

**Результат**: 3 дубликата деактивированы

---

## Проверка

### Команды для проверки

```bash
# Проверка дубликатов
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT id, username, tg_channel_id, is_active 
FROM channels 
WHERE username IN ('banksta', 'MarketOverview', 'chinamashina_news')
ORDER BY username, is_active DESC, tg_channel_id NULLS LAST;
"

# Проверка новых постов
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT p.id, p.telegram_message_id, c.username, p.posted_at, p.created_at
FROM posts p
JOIN channels c ON c.id = p.channel_id
WHERE (c.username = 'MarketOverview' AND p.telegram_message_id = 17157)
   OR (c.username = 'banksta' AND p.telegram_message_id = 82062)
   OR (c.username = 'chinamashina_news' AND p.telegram_message_id = 10084)
ORDER BY p.created_at DESC;
"
```

### Ожидаемый результат

1. ✅ Только один активный канал на username (с `tg_channel_id`)
2. ✅ Дубликаты деактивированы (`is_active = false`)
3. ✅ Новые посты появляются в БД

---

## Impact / Rollback

### Влияние

- ✅ **Критично**: Исправляет проблему парсинга новых постов
- ✅ **Безопасно**: Дубликаты деактивированы, не удалены
- ✅ **Производительность**: Scheduler больше не тратит время на неработающие каналы

### Rollback

Если потребуется откат:

```sql
UPDATE channels 
SET is_active = true 
WHERE username IN ('banksta', 'MarketOverview', 'chinamashina_news')
  AND tg_channel_id IS NULL;
```

**Примечание**: Откат вернет проблему, поэтому не рекомендуется.

---

## Context7 Best Practices

1. ✅ **Дедупликация данных** - проверка дубликатов перед созданием
2. ✅ **Уникальные constraints** - использование `ux_channels_tg_global` для предотвращения дубликатов
3. ✅ **Мониторинг** - логирование каналов без `tg_channel_id`
4. ✅ **Автоматическое заполнение** - код в `channel_parser.py` автоматически заполняет `tg_channel_id` при успешном парсинге

---

## Статус

✅ **Исправлено** - Дубликаты деактивированы, парсинг работает для каналов с `tg_channel_id`.

**Результат**: Пост `banksta/82062` появился в БД после исправления.

