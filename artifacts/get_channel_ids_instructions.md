# Инструкция по получению telegram_id для каналов про пиво

## Каналы для обновления

1. **beer_for_all** - https://t.me/beer_for_all (2656 подписчиков)
2. **beer_by** - https://t.me/beer_by
3. **prostopropivo** - https://t.me/prostopropivo (31042 подписчиков)

## Способы получения telegram_id

### Способ 1: Через бота @userinfobot (самый простой)

1. Откройте Telegram
2. Найдите бота @userinfobot
3. Отправьте ссылку на канал (например: `https://t.me/beer_for_all`)
4. Бот вернет информацию о канале, включая **Channel ID** (отрицательное число, например: `-1001234567890`)

### Способ 2: Через бота @getidsbot

1. Откройте Telegram
2. Найдите бота @getidsbot
3. Отправьте ссылку на канал
4. Бот вернет **Channel ID**

### Способ 3: Через Telegram Desktop

1. Откройте канал в Telegram Desktop
2. Перейдите в **View** → **Statistics** (если доступно)
3. Найдите **Channel ID** (отрицательное число)

### Способ 4: Через веб-интерфейс Telegram

1. Откройте канал в веб-версии Telegram (web.telegram.org)
2. Откройте Developer Tools (F12)
3. Перейдите на вкладку Network
4. Найдите запросы к API Telegram
5. В ответах найдите `channel_id` или `peer_id`

## После получения ID

### Вариант A: Использовать скрипт

```bash
cd /opt/telegram-assistant
./scripts/update_beer_channels_manual.sh <beer_for_all_id> <beer_by_id> <prostopropivo_id>
```

Пример:
```bash
./scripts/update_beer_channels_manual.sh -1001234567890 -1001234567891 -1001234567892
```

### Вариант B: Выполнить SQL напрямую

```sql
UPDATE channels SET tg_channel_id = -1001234567890 WHERE username = 'beer_for_all';
UPDATE channels SET tg_channel_id = -1001234567891 WHERE username = 'beer_by';
UPDATE channels SET tg_channel_id = -1001234567892 WHERE username = 'prostopropivo';
```

Выполнить через:
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "UPDATE channels SET tg_channel_id = -1001234567890 WHERE username = 'beer_for_all';"
```

## Проверка результатов

После обновления проверьте:

```sql
SELECT id, username, title, tg_channel_id 
FROM channels 
WHERE username IN ('beer_for_all', 'beer_by', 'prostopropivo')
ORDER BY username;
```

Или через команду:
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "SELECT id, username, title, tg_channel_id FROM channels WHERE username IN ('beer_for_all', 'beer_by', 'prostopropivo') ORDER BY username;"
```

## Важно

- `telegram_id` для каналов всегда **отрицательное число** (например: `-1001234567890`)
- Убедитесь, что используете правильный ID для каждого канала
- После обновления можно запустить парсинг для этих каналов

