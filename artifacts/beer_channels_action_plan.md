# План действий для каналов про пиво

## Context

Каналы существуют в Telegram:
- https://t.me/beer_for_all (2656 подписчиков)
- https://t.me/beer_by
- https://t.me/prostopropivo (31042 подписчиков)

Но сессия Telegram не имеет доступа к этим каналам (ошибка "The key is not registered in the system").

## Проблема

Сессия, используемая для получения `tg_channel_id`, не может получить доступ к этим каналам. Это может быть из-за:
1. Каналы требуют подписки для доступа
2. Сессия не имеет прав на доступ к этим каналам
3. Каналы приватные или ограниченные

## Решения

### Вариант 1: Получить telegram_id вручную

**Шаги:**
1. Открыть канал в Telegram (через ссылку)
2. Использовать бота для получения ID:
   - @userinfobot - отправить ссылку на канал
   - @getidsbot - отправить ссылку на канал
   - Или использовать Telegram Desktop: View -> Statistics -> Channel ID
3. Обновить каналы в БД через SQL:

```sql
UPDATE channels SET tg_channel_id = -100XXXXXXXXXX WHERE username = 'beer_for_all';
UPDATE channels SET tg_channel_id = -100XXXXXXXXXX WHERE username = 'beer_by';
UPDATE channels SET tg_channel_id = -100XXXXXXXXXX WHERE username = 'prostopropivo';
```

### Вариант 2: Использовать другую сессию

Если есть другая сессия Telegram с доступом к этим каналам:
1. Использовать эту сессию для получения `tg_channel_id`
2. Обновить каналы в БД

### Вариант 3: Попросить пользователя добавить каналы с telegram_id

Попросить пользователя 105957884:
1. Отписаться от текущих каналов (без tg_channel_id)
2. Добавить каналы заново через бота, указав `telegram_id` напрямую
3. Или использовать команду `/add_channel` и выбрать "По Telegram ID"

## Текущее состояние каналов

```sql
SELECT id, username, title, tg_channel_id 
FROM channels 
WHERE username IN ('beer_for_all', 'beer_by', 'prostopropivo')
ORDER BY username;
```

## Рекомендация

**Использовать Вариант 1** - получить `telegram_id` вручную через бота или Telegram Desktop, затем обновить через SQL.

## Следующие шаги

1. Получить `telegram_id` для каждого канала
2. Выполнить SQL запросы для обновления
3. Проверить, что каналы обновлены
4. Запустить парсинг для этих каналов

