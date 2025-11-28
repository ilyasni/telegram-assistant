# Проблема автоматического создания подписок при парсинге

**Дата:** 2025-11-25  
**Статус:** ⚠️ **КРИТИЧЕСКАЯ ПРОБЛЕМА - Автоматическое создание подписок**

---

## Context

Пользователь 105957884 (shanskiy) сообщает, что добавил намного меньше каналов, чем показывает БД:
- **Показывает БД:** 28 каналов (29 подписок)
- **Уникальных каналов:** 26 с `tg_channel_id` + 3 без `tg_channel_id`

**Проблема:** Парсер автоматически создает подписки при парсинге каналов!

---

## Наблюдения

### 1. Автоматическое создание подписок

В `telethon-ingest/services/channel_parser.py`:

**Строка 1754:**
```python
await self.atomic_saver._ensure_user_channel(self.db_session, user_data, channel_data, channel_id_uuid)
```

**Строка 2123:**
```python
await self.atomic_saver._ensure_user_channel(self.db_session, user_data, channel_data, channel_id_uuid)
```

**Проблема:** При парсинге каналов автоматически создается `user_channel` связь, даже если пользователь не подписывался вручную!

### 2. Функция `_ensure_user_channel`

В `telethon-ingest/services/atomic_db_saver.py:707`:

```python
async def _ensure_user_channel(...):
    """
    Context7 best practice: Создание user_channel связи если её нет.
    Это необходимо для корректной работы сохранения альбомов и других операций,
    требующих связь пользователя с каналом.
    """
    # Создаём user_channel связь
    INSERT INTO user_channel (user_id, channel_id, is_active, subscribed_at, settings)
    VALUES (:user_id, :channel_id, true, NOW(), '{}'::jsonb)
    ON CONFLICT (user_id, channel_id) DO NOTHING
```

**Проблема:** Функция автоматически создает подписку при парсинге, без явного запроса пользователя!

### 3. Подтверждение проблемы

- **26 каналов** имеют посты, созданные ДО подписки (`first_post_msk < subscribed_msk`)
- Это означает, что каналы парсились до того, как пользователь подписался
- Парсер автоматически создал подписки при обработке постов

---

## Причины

### 1. Неправильная логика парсера

**Проблема:**
- Парсер создает `user_channel` при парсинге каналов
- Это должно происходить ТОЛЬКО при явной подписке пользователя
- Парсер не должен автоматически подписывать пользователей на каналы

### 2. Отсутствие проверки существующей подписки

**Проблема:**
- Парсер не проверяет, подписан ли пользователь на канал перед парсингом
- Автоматически создает подписку при обработке постов

### 3. Неправильное использование `_ensure_user_channel`

**Проблема:**
- `_ensure_user_channel` используется для "обеспечения" связи при парсинге
- Но это создает подписки без явного запроса пользователя

---

## Решение

### 1. Удалить автоматическое создание подписок из парсера

**Изменить `telethon-ingest/services/channel_parser.py`:**

```python
# УДАЛИТЬ эти строки:
# await self.atomic_saver._ensure_user_channel(self.db_session, user_data, channel_data, channel_id_uuid)

# ИЛИ добавить проверку:
# Проверяем, подписан ли пользователь на канал
check_subscription = await self.db_session.execute(
    text("SELECT user_id FROM user_channel WHERE user_id = :user_id AND channel_id = :channel_id"),
    {"user_id": user_id, "channel_id": channel_id}
)
if not check_subscription.fetchone():
    # Пользователь не подписан - НЕ создаем подписку автоматически
    logger.warning("User not subscribed to channel, skipping auto-subscription",
                  user_id=user_id, channel_id=channel_id)
    return
```

### 2. Изменить логику `_ensure_user_channel`

**Вариант 1:** Удалить функцию или сделать её опциональной

**Вариант 2:** Добавить параметр `auto_subscribe=False`:

```python
async def _ensure_user_channel(
    self, 
    db_session: AsyncSession, 
    user_data: Dict[str, Any], 
    channel_data: Dict[str, Any], 
    channel_id: str,
    auto_subscribe: bool = False  # НОВЫЙ ПАРАМЕТР
) -> None:
    """
    Создание user_channel связи если её нет.
    
    Args:
        auto_subscribe: Если False, создает подписку только если она уже существует
    """
    # Проверяем существование подписки
    check_result = await db_session.execute(...)
    
    if check_result.fetchone():
        return  # Подписка уже существует
    
    if not auto_subscribe:
        # НЕ создаем подписку автоматически
        logger.warning("User not subscribed, skipping auto-subscription",
                      user_id=user_id, channel_id=channel_id)
        return
    
    # Создаём подписку только если auto_subscribe=True
    await db_session.execute(insert_sql, ...)
```

### 3. Проверка подписки перед парсингом

**Изменить `telethon-ingest/tasks/parse_all_channels_task.py`:**

```python
async def _parse_channel_with_retry(self, channel: Dict[str, Any], mode: str):
    # Context7: Проверяем, подписан ли пользователь на канал
    # Парсим ТОЛЬКО каналы, на которые пользователь подписан
    user_subscriptions = await self._get_user_subscriptions(channel['id'])
    if not user_subscriptions:
        logger.debug("Channel has no active subscriptions, skipping parsing",
                    channel_id=channel['id'])
        return {"status": "skipped", "reason": "no_subscriptions"}
    
    # Продолжаем парсинг...
```

---

## Checks

### Проверка текущего состояния

```bash
# Каналы с постами до подписки
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT c.username, c.title, 
       uc.subscribed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow' as subscribed_msk,
       MIN(p.posted_at) AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow' as first_post_msk
FROM channels c
JOIN user_channel uc ON c.id = uc.channel_id
JOIN users u ON uc.user_id = u.id
LEFT JOIN posts p ON p.channel_id = c.id
WHERE u.telegram_id = 105957884
GROUP BY c.id, c.username, c.title, uc.subscribed_at
HAVING COUNT(p.id) > 0 AND MIN(p.posted_at) < uc.subscribed_at
ORDER BY (MIN(p.posted_at) - uc.subscribed_at) ASC;
"
```

---

## Impact / Rollback

### Impact

- **Автоматические подписки:** Пользователи видят каналы, на которые не подписывались
- **Неправильная статистика:** Количество каналов не соответствует реальным подпискам
- **Нарушение логики:** Подписки должны создаваться только при явном запросе пользователя

### Rollback

Если нужно откатить изменения:
1. Удаление автоматического создания подписок - безопасно (только исправляет логику)
2. Изменения в парсере - можно откатить через git
3. Очистка автоматически созданных подписок - нужно сделать вручную

---

## Рекомендации

1. **Немедленно:** Удалить автоматическое создание подписок из парсера
2. **Краткосрочно:** Добавить проверку подписки перед парсингом
3. **Долгосрочно:** Разделить логику парсинга и подписок (парсер не должен управлять подписками)

