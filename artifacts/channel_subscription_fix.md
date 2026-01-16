# Исправление проблемы автоматического создания подписок

**Дата:** 2025-11-25  
**Статус:** ✅ **ИСПРАВЛЕНО - Автоматическое создание подписок удалено**

---

## Context

Пользователь 105957884 (shanskiy) сообщил, что добавил намного меньше каналов, чем показывает БД:
- **Показывает БД:** 28 каналов (29 подписок)
- **Уникальных каналов:** 26 с `tg_channel_id` + 3 без `tg_channel_id`

**Проблема:** Парсер автоматически создавал подписки при парсинге каналов!

---

## Наблюдения

### 1. Автоматическое создание подписок

**Найдено в коде:**
- `telethon-ingest/services/channel_parser.py:1754` - создание подписки при парсинге (когда нет новых постов)
- `telethon-ingest/services/channel_parser.py:2123` - создание подписки при обработке альбомов
- `telethon-ingest/services/atomic_db_saver.py:206` - создание подписки при сохранении постов

**Подтверждение проблемы:**
- **26 каналов** имеют посты, созданные ДО подписки
- Например, `autopotoknews` - посты от 3 ноября, подписка от 25 ноября (разница 22 дня!)

### 2. Логика создания подписок

**Проблема:**
- Функция `_ensure_user_channel` автоматически создавала подписки при парсинге
- Это происходило без явного запроса пользователя
- Подписки должны создаваться ТОЛЬКО через API при явном запросе

---

## Исправления

### 1. Удалено автоматическое создание подписок при парсинге (когда нет новых постов)

**Файл:** `telethon-ingest/services/channel_parser.py:1747-1770`

**Было:**
```python
# Создаём user и channel, чтобы user_channel мог быть создан
await self.atomic_saver._upsert_user(self.db_session, user_data)
channel_id_uuid = await self.atomic_saver._upsert_channel(self.db_session, channel_data)
await self.atomic_saver._ensure_user_channel(...)  # ❌ Автоматическое создание!
```

**Стало:**
```python
# Context7: НЕ создаем user_channel автоматически при парсинге!
# Проверяем, подписан ли пользователь на канал
check_subscription = await self.db_session.execute(...)
if not check_subscription.fetchone():
    # Пользователь не подписан - НЕ создаем подписку автоматически
    logger.warning("User not subscribed to channel, skipping parsing")
    return {"status": "skipped", "reason": "not_subscribed", ...}
```

### 2. Исправлена логика для альбомов

**Файл:** `telethon-ingest/services/channel_parser.py:2120-2140`

**Было:**
```python
# Создаём user и channel
await self.atomic_saver._upsert_user(...)
channel_id_uuid = await self.atomic_saver._upsert_channel(...)
await self.atomic_saver._ensure_user_channel(...)  # ❌ Автоматическое создание!
```

**Стало:**
```python
# Context7: НЕ создаем user_channel автоматически при парсинге!
# Проверяем, подписан ли пользователь на канал
check_subscription = await self.db_session.execute(...)
if not check_subscription.fetchone():
    # Пользователь не подписан - НЕ создаем подписку автоматически
    logger.warning("User not subscribed to channel, cannot process albums")
    user_uuid = None
else:
    # Пользователь подписан - создаем user если нужно
    await self.atomic_saver._upsert_user(...)
    channel_id_uuid = await self.atomic_saver._upsert_channel(...)
```

### 3. Исправлена логика в `save_batch_atomic`

**Файл:** `telethon-ingest/services/atomic_db_saver.py:202-211`

**Было:**
```python
# Создаём user_channel связь если её нет
await self._ensure_user_channel(...)  # ❌ Автоматическое создание!
```

**Стало:**
```python
# Context7: НЕ создаем user_channel автоматически при парсинге!
# Проверяем, подписан ли пользователь на канал перед сохранением постов
check_subscription = await db_session.execute(...)
if not check_subscription.fetchone():
    # Пользователь не подписан - НЕ сохраняем посты и НЕ создаем подписку
    return False, "user_not_subscribed", 0
```

---

## Context7 Best Practices

1. **Разделение ответственности:** Парсер не должен управлять подписками
2. **Явное создание подписок:** Подписки создаются только через API при явном запросе
3. **Проверка перед парсингом:** Парсер проверяет подписку перед обработкой постов

---

## Checks

### Проверка исправлений

```bash
# Проверка, что автоматические подписки больше не создаются
docker logs telegram-assistant-telethon-ingest-1 --tail 100 | grep -i "not subscribed"

# Проверка подписок пользователя
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT COUNT(*) as total_subscriptions, COUNT(DISTINCT channel_id) as unique_channels
FROM user_channel uc
JOIN users u ON uc.user_id = u.id
WHERE u.telegram_id = 105957884;
"
```

### После исправления

1. Парсер не будет создавать подписки автоматически
2. Посты будут сохраняться только для каналов, на которые пользователь подписан
3. Новые подписки создаются только через API при явном запросе

---

## Impact / Rollback

### Impact

- **Автоматические подписки:** Больше не создаются при парсинге
- **Правильная логика:** Подписки создаются только при явном запросе пользователя
- **Обратная совместимость:** Существующие подписки остаются без изменений

### Rollback

Если нужно откатить изменения:
1. Изменения в парсере - можно откатить через git
2. Изменения в `atomic_db_saver` - можно откатить через git
3. Существующие автоматически созданные подписки - остаются в БД (можно удалить вручную)

---

## Рекомендации

1. **Немедленно:** Исправления применены, парсер больше не создает подписки автоматически
2. **Краткосрочно:** Очистить автоматически созданные подписки (если нужно)
3. **Долгосрочно:** Добавить мониторинг автоматических подписок (alert при создании)

