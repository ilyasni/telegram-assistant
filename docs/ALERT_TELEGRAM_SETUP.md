# Настройка Telegram уведомлений для AlertManager

## Контекст

AlertManager настроен для отправки critical алертов в Telegram через webhook receiver. Для работы требуется настроить `ALERT_TELEGRAM_CHAT_ID`.

## Варианты настройки

### Вариант 1: Использование username (рекомендуется)

Можно использовать username группы/канала напрямую:

```bash
ALERT_TELEGRAM_CHAT_ID=@testgroupassistant
```

Или ссылку:

```bash
ALERT_TELEGRAM_CHAT_ID=https://t.me/testgroupassistant
```

Код автоматически разрешит username в числовой chat_id при первой отправке.

### Вариант 2: Использование числового chat_id

Для получения числового chat_id используйте утилиту:

```bash
python scripts/get_alert_chat_id.py @testgroupassistant
```

Или со ссылкой:

```bash
python scripts/get_alert_chat_id.py https://t.me/testgroupassistant
```

Утилита выведет числовой chat_id, который можно использовать:

```bash
ALERT_TELEGRAM_CHAT_ID=-1001234567890
```

## Требования

1. **Бот должен быть добавлен в группу/канал**
   - Добавьте бота в группу/канал как администратора
   - Убедитесь, что бот имеет права на отправку сообщений

2. **Переменная окружения TELEGRAM_BOT_TOKEN должна быть установлена**
   - Бот должен быть инициализирован и работать

## Настройка в .env

Добавьте в файл `.env`:

```bash
# Telegram уведомления для AlertManager
ALERT_TELEGRAM_CHAT_ID=@testgroupassistant
```

Или используйте числовой ID:

```bash
ALERT_TELEGRAM_CHAT_ID=-1001234567890
```

## Проверка настройки

1. Проверьте health check endpoint:

```bash
curl http://localhost:8000/api/monitoring/alertmanager/health
```

Должен вернуть:

```json
{
  "status": "ok",
  "chat_id_configured": true,
  "bot_available": false
}
```

2. Проверьте логи при отправке тестового алерта:

```bash
docker logs telegram-assistant-api-1 --tail 50 | grep -i alert
```

## Устранение проблем

### Ошибка: "Bot not initialized"

- Убедитесь, что `TELEGRAM_BOT_TOKEN` установлен
- Проверьте, что бот инициализирован при старте API
- Проверьте логи: `docker logs telegram-assistant-api-1 | grep -i bot`

### Ошибка: "Failed to resolve username to chat_id"

- Убедитесь, что бот добавлен в группу/канал
- Проверьте, что username правильный (без @ в начале, если используете ссылку)
- Попробуйте использовать числовой chat_id вместо username

### Ошибка: "Chat not found"

- Убедитесь, что бот имеет доступ к группе/каналу
- Проверьте права бота в группе/канале
- Попробуйте пересоздать приглашение для бота

## Примеры

### Для группы @testgroupassistant:

```bash
# В .env
ALERT_TELEGRAM_CHAT_ID=@testgroupassistant
```

### Для канала с числовым ID:

```bash
# Получить ID
python scripts/get_alert_chat_id.py @my_channel

# В .env
ALERT_TELEGRAM_CHAT_ID=-1001234567890
```

## Дополнительная информация

- AlertManager отправляет только critical алерты (настроено в `prometheus/alertmanager.yml`)
- Уведомления группируются по alertname и severity
- Повторные уведомления отправляются не чаще раза в час
- Формат сообщений: HTML с эмодзи и структурированной информацией

