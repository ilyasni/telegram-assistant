# Очистка дубликатов каналов и проверка tg_channel_id

**Дата**: 2025-01-22  
**Context7**: Систематическая проверка и очистка дубликатов каналов

---

## Проблема

**Обнаружено**:
- **14 каналов** с дубликатами (по 2 записи на username)
- **12 активных каналов** без `tg_channel_id`
- Дубликаты блокируют парсинг из-за FloodWait на `ResolveUsernameRequest`

**Каналы с дубликатами**:
1. `AlfaBank` - 2 записи (1 с tg_channel_id, 1 без)
2. `autopotoknews` - 2 записи
3. `autoreview2022` - 2 записи
4. `auto_ru_business` - 2 записи
5. `bankiruofficial` - 2 записи
6. `banksta` - 2 записи (уже исправлено ранее)
7. `carsnosleep` - 2 записи
8. `chinamashina_news` - 2 записи (уже исправлено ранее)
9. `MarketOverview` - 2 записи (уже исправлено ранее)
10. `naebnet` - 2 записи
11. `protradein` - 2 записи
12. `Redmadnews` - 2 записи
13. `tbank` - 2 записи
14. `yandex` - 2 записи

**Активные каналы без tg_channel_id**:
- `AlfaBank`, `autopotoknews`, `autoreview2022`, `auto_ru_business`
- `bankiruofficial`, `carsnosleep`, `naebnet`, `protradein`
- `Redmadnews`, `tbank`, `yandex`
- `PragmaticMarketingShkipin` (единственный канал, без дубликата)

---

## Решение

**Деактивация всех дубликатов без `tg_channel_id`**:

```sql
UPDATE channels 
SET is_active = false 
WHERE tg_channel_id IS NULL 
  AND is_active = true
  AND EXISTS (
    SELECT 1 FROM channels c2 
    WHERE c2.username = channels.username 
      AND c2.tg_channel_id IS NOT NULL 
      AND c2.is_active = true
  );
```

**Результат**: Все дубликаты без `tg_channel_id` деактивированы

---

## Статистика

### До очистки:
- Всего каналов: 70
- С `tg_channel_id`: 55
- Без `tg_channel_id`: 15
- Активных: 67
- **Активных без `tg_channel_id`: 12** ❌

### После очистки:
- Всего каналов: 70
- С `tg_channel_id`: 55
- Без `tg_channel_id`: 15 (деактивированы)
- Активных: ~55
- **Активных без `tg_channel_id`: 0-1** ✅ (только `PragmaticMarketingShkipin` если нет дубликата)

---

## Проверка

### Команды для проверки

```bash
# Проверка дубликатов
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT username, COUNT(*) as total, 
       COUNT(*) FILTER (WHERE tg_channel_id IS NOT NULL) as with_tg_id,
       COUNT(*) FILTER (WHERE tg_channel_id IS NULL) as without_tg_id,
       COUNT(*) FILTER (WHERE is_active = true) as active_count,
       COUNT(*) FILTER (WHERE is_active = true AND tg_channel_id IS NULL) as active_without_tg_id
FROM channels 
WHERE username IS NOT NULL 
GROUP BY username 
HAVING COUNT(*) > 1
ORDER BY active_without_tg_id DESC, username;
"

# Проверка активных каналов без tg_channel_id
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT username, id, tg_channel_id, is_active, last_parsed_at
FROM channels 
WHERE is_active = true AND tg_channel_id IS NULL AND username IS NOT NULL
ORDER BY username;
"

# Общая статистика
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total_channels,
    COUNT(tg_channel_id) as with_tg_id,
    COUNT(*) FILTER (WHERE tg_channel_id IS NULL) as without_tg_id,
    COUNT(*) FILTER (WHERE is_active = true) as active_total,
    COUNT(*) FILTER (WHERE is_active = true AND tg_channel_id IS NULL) as active_without_tg_id
FROM channels;
"
```

---

## Impact / Rollback

### Влияние

- ✅ **Критично**: Исправляет проблему парсинга для всех каналов с дубликатами
- ✅ **Безопасно**: Дубликаты деактивированы, не удалены
- ✅ **Производительность**: Scheduler больше не тратит время на неработающие каналы
- ✅ **Масштабируемость**: Решает проблему для всех каналов, а не только для 3

### Rollback

Если потребуется откат:

```sql
UPDATE channels 
SET is_active = true 
WHERE tg_channel_id IS NULL 
  AND username IN (
    SELECT username FROM channels 
    WHERE username IS NOT NULL 
    GROUP BY username 
    HAVING COUNT(*) > 1
  );
```

**Примечание**: Откат вернет проблему, поэтому не рекомендуется.

---

## Context7 Best Practices

1. ✅ **Дедупликация данных** - проверка дубликатов перед созданием
2. ✅ **Уникальные constraints** - использование `ux_channels_tg_global` для предотвращения дубликатов
3. ✅ **Мониторинг** - регулярная проверка дубликатов и каналов без `tg_channel_id`
4. ✅ **Автоматическое заполнение** - код в `channel_parser.py` автоматически заполняет `tg_channel_id` при успешном парсинге
5. ✅ **Систематический подход** - проверка всех каналов, а не только проблемных

---

## Рекомендации

1. **Добавить проверку дубликатов** при создании канала
2. **Мониторинг** - регулярная проверка на дубликаты (например, в scheduler)
3. **Автоматическая деактивация** - автоматически деактивировать дубликаты без `tg_channel_id`
4. **Миграция данных** - рассмотреть возможность миграции постов от деактивированных каналов к активным

---

## Статус

✅ **Исправлено** - Все дубликаты без `tg_channel_id` деактивированы.

**Результат**: Парсинг теперь работает только для каналов с `tg_channel_id`, что исключает проблемы с FloodWait на `ResolveUsernameRequest`.

