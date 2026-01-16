# Быстрая настройка AlertManager для Telegram

## Шаг 1: Добавьте бота в группу

1. Откройте группу [@testgroupassistant](https://t.me/testgroupassistant)
2. Добавьте бота в группу как администратора
3. Убедитесь, что бот имеет права на отправку сообщений

## Шаг 2: Настройте ALERT_TELEGRAM_CHAT_ID

### Вариант A: Использовать username (самый простой)

Добавьте в файл `.env`:

```bash
ALERT_TELEGRAM_CHAT_ID=@testgroupassistant
```

Код автоматически разрешит username в числовой chat_id при первой отправке.

### Вариант B: Получить числовой chat_id

#### Способ 1: Через скрипт (требует .env с TELEGRAM_BOT_TOKEN)

```bash
# 1. Отправьте любое сообщение боту в группе (например: /start)
# 2. Запустите скрипт
./scripts/get_chat_id_from_bot.sh
```

#### Способ 2: Через Python утилиту (внутри контейнера)

```bash
# Войдите в контейнер API
docker exec -it telegram-assistant-api-1 bash

# Запустите утилиту
python scripts/get_alert_chat_id.py @testgroupassistant
```

#### Способ 3: Через Telegram API напрямую

```bash
# Получите обновления от бота
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" | jq '.result[] | select(.message.chat.type == "group" or .message.chat.type == "supergroup") | .message.chat.id'
```

Найдите chat_id группы и добавьте в `.env`:

```bash
ALERT_TELEGRAM_CHAT_ID=-1001234567890
```

## Шаг 3: Перезапустите сервисы

```bash
# Перезапустите API для применения изменений
docker compose restart api

# Убедитесь, что AlertManager запущен
docker compose up -d alertmanager
```

## Шаг 4: Проверка

### Проверка health check

```bash
curl http://localhost:8000/api/monitoring/alertmanager/health
```

Ожидаемый ответ:

```json
{
  "status": "ok",
  "chat_id_configured": true,
  "bot_available": false
}
```

### Тестовая отправка

Создайте тестовый алерт в Prometheus или дождитесь реального critical алерта. Уведомление должно прийти в группу @testgroupassistant.

### Проверка логов

```bash
# Логи API при отправке алерта
docker logs telegram-assistant-api-1 --tail 50 | grep -i alert

# Логи AlertManager
docker logs telegram-assistant-alertmanager-1 --tail 50
```

## Устранение проблем

### Бот не отправляет сообщения

1. Проверьте, что бот добавлен в группу:
   ```bash
   # Проверьте через API
   curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getChatMember?chat_id=@testgroupassistant&user_id=<BOT_USER_ID>"
   ```

2. Проверьте права бота в группе (должен быть администратором)

3. Проверьте логи:
   ```bash
   docker logs telegram-assistant-api-1 --tail 100 | grep -i "alert\|telegram"
   ```

### Ошибка "Failed to resolve username"

1. Убедитесь, что username правильный (без @ в начале, если используете ссылку)
2. Попробуйте использовать числовой chat_id вместо username
3. Проверьте, что бот имеет доступ к группе

### AlertManager не отправляет алерты

1. Проверьте статус AlertManager:
   ```bash
   docker ps | grep alertmanager
   curl http://localhost:9093/-/healthy
   ```

2. Проверьте конфигурацию Prometheus:
   ```bash
   curl http://localhost:9090/api/v1/alertmanagers
   ```

3. Проверьте активные алерты:
   ```bash
   curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.severity == "critical")'
   ```

## Готово!

После настройки все critical алерты из Prometheus будут автоматически отправляться в группу @testgroupassistant.

