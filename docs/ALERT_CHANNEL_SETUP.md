# Настройка AlertManager для канала Telegram

## Важно: Канал vs Группа

**@testgroupassistant** - это **канал**, а не группа. Для каналов требуются особые настройки.

## Требования для каналов

1. **Бот должен быть администратором канала**
   - Откройте канал @testgroupassistant
   - Перейдите в настройки канала → Администраторы
   - Добавьте бота как администратора
   - Убедитесь, что бот имеет права на отправку сообщений

2. **Использование chat_id**
   - Для каналов можно использовать username: `@testgroupassistant`
   - Или числовой chat_id: `-1003242606125` (отрицательное число)
   - Код автоматически разрешит username в chat_id

## Проверка настройки

### 1. Проверка статуса бота

```bash
docker exec telegram-assistant-api-1 python3 -c "
import sys, asyncio
sys.path.insert(0, '/app')
from bot.webhook import bot, init_bot

async def check():
    if not bot:
        init_bot()
        from bot.webhook import bot as b
        if not b: return
        bot_obj = b
    else:
        bot_obj = bot
    
    chat = await bot_obj.get_chat('@testgroupassistant')
    member = await bot_obj.get_chat_member(chat.id, bot_obj.id)
    print(f'Статус: {member.status}')
    print('✅ Администратор' if member.status in ['administrator', 'creator'] else '❌ Не администратор')

asyncio.run(check())
"
```

### 2. Тестовая отправка

```bash
./scripts/test_alert_notification.sh
```

Или вручную через webhook:

```bash
curl -X POST http://localhost:8000/api/monitoring/alertmanager/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "groupKey": "test",
    "status": "firing",
    "alerts": [{
      "status": "firing",
      "labels": {"alertname": "TestAlert", "severity": "critical"},
      "annotations": {"summary": "Тестовое уведомление"}
    }]
  }'
```

## Устранение проблем

### Ошибка: "Bot is not administrator"

**Проблема**: Бот не является администратором канала.

**Решение**:
1. Откройте канал @testgroupassistant в Telegram
2. Перейдите в настройки канала
3. Добавьте бота как администратора
4. Убедитесь, что бот имеет права на отправку сообщений

### Ошибка: "Chat not found"

**Проблема**: Бот не имеет доступа к каналу.

**Решение**:
1. Убедитесь, что бот добавлен в канал
2. Проверьте, что username правильный: `@testgroupassistant`
3. Попробуйте использовать числовой chat_id: `-1003242606125`

### Ошибка: "Failed to send message"

**Проблема**: Бот не может отправить сообщение в канал.

**Возможные причины**:
1. Бот не является администратором
2. Бот не имеет прав на отправку сообщений
3. Канал приватный и бот не имеет доступа

**Решение**:
1. Проверьте права бота в канале
2. Убедитесь, что бот имеет права на отправку сообщений
3. Для приватных каналов добавьте бота как администратора

## Текущая конфигурация

- **Канал**: @testgroupassistant
- **Chat ID**: -1003242606125
- **Тип**: channel
- **Статус бота**: administrator ✅
- **ALERT_TELEGRAM_CHAT_ID**: @testgroupassistant

## Готово!

После настройки все critical алерты будут автоматически отправляться в канал @testgroupassistant.

