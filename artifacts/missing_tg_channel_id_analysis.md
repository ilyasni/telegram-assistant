# Анализ каналов без tg_channel_id

## Проблема

У пользователя `105957884` есть 3 канала без `tg_channel_id`:
- `beer_for_all` (ID: `827b661b-eb44-417e-beec-12c8fd51a454`)
- `beer_by` (ID: `d53aaa61-e88d-4d07-badb-12758b990603`)
- `prostopropivo` (ID: `5027bfe9-aa9f-45c7-96f8-53acf015384a`)

Все созданы 25 ноября 2025 в 15:24-15:28.

## Причина

В функции `_get_or_create_channel` (`api/routers/channels.py:514-525`) канал создается с `telegram_id=None`, если он не передан в запросе:

```python
db.execute(
    text("""
        INSERT INTO channels (id, tg_channel_id, username, title, is_active, created_at)
        VALUES (:id, :telegram_id, :username, :title, true, NOW())
    """),
    {
        "id": channel_id,
        "telegram_id": telegram_id,  # Может быть None!
        "username": normalized_username,
        "title": title or normalized_username or f"Channel {telegram_id}"
    }
)
```

**Проблемы:**
1. В модели `Channel` (`api/models/database.py:263`) указано `nullable=False`, но в реальной БД поле `nullable=True`
2. Нет логики для получения `tg_channel_id` из Telegram API по username при создании канала
3. Пользователь может добавить канал только по username (без telegram_id), и тогда канал создается без `tg_channel_id`

## Решение

### Вариант 1: Заполнить tg_channel_id через Telegram API

Использовать существующий скрипт `telethon-ingest/scripts/populate_channel_ids.py` или создать новый для заполнения `tg_channel_id` для каналов без него.

### Вариант 2: Улучшить логику создания канала

Добавить в `_get_or_create_channel` логику для получения `tg_channel_id` из Telegram API по username, если `telegram_id` не передан.

### Вариант 3: Валидация при создании

Запретить создание каналов без `tg_channel_id`, требуя обязательный `telegram_id` или получение его из Telegram API.

## Рекомендация

1. **Краткосрочно**: Заполнить `tg_channel_id` для существующих каналов через Telegram API
2. **Долгосрочно**: Улучшить логику `_get_or_create_channel` для автоматического получения `tg_channel_id` по username

## Проверка каналов

Нужно проверить, существуют ли эти каналы в Telegram:
- `beer_for_all`
- `beer_by`
- `prostopropivo`

Если каналы не существуют или недоступны, их нужно удалить или пометить как неактивные.

