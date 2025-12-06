# Сброс UI кнопок Telegram бота

Инструкция по сбросу Menu Button и Main App кнопок в Telegram боте.

## Context

В Telegram боте есть два типа UI кнопок:
- **Menu Button** — кнопка меню в интерфейсе бота
- **Main App** — главное приложение Mini App

Эти кнопки можно настроить через интерфейс Telegram или через Bot API.

## Способы сброса

### 1. Через скрипт (частичный сброс)

Скрипт сбрасывает Menu Button на стандартную кнопку команд (не удаляет полностью):

```bash
# Через docker compose
docker compose exec api python scripts/reset_bot_menu_button.py

# Или напрямую через Python
docker compose exec api python -c "
import asyncio
import sys
sys.path.insert(0, '/app')
from aiogram import Bot
from aiogram.types import MenuButtonCommands
import os
from config import settings

async def main():
    bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN') or settings.telegram_bot_token)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    print('✅ Menu Button сброшен')
    await bot.session.close()

asyncio.run(main())
"
```

### 2. Через API endpoint

```bash
curl -X POST http://localhost:8000/api/monitoring/bot/reset-menu-button
```

### 3. Полное удаление через интерфейс Telegram

**Важно:** Через Bot API нельзя полностью удалить Menu Button, можно только изменить его тип. Для полного удаления используйте интерфейс:

#### Способ A: Через настройки бота в Telegram

1. Откройте бота в Telegram
2. Нажмите на название бота вверху (или три точки → Settings)
3. Перейдите в раздел **"Mini Apps"**
4. Нажмите на **"Menu Button"** → выберите **"Disabled"**
5. Нажмите на **"Main App"** → выберите **"Disabled"**

#### Способ B: Через BotFather

1. Откройте [@BotFather](https://t.me/BotFather)
2. Отправьте `/mybots`
3. Выберите вашего бота
4. Выберите **"Bot Settings"**
5. Выберите **"Menu Button"**
6. Выберите **"Disable"** или **"Remove"**

### 4. Полное удаление через Bot API (экспериментально)

Попробуйте установить `menu_button=None` (может не работать в некоторых версиях API):

```python
from aiogram import Bot

bot = Bot(token="YOUR_TOKEN")
# Попытка удалить кнопку (может не работать)
await bot.set_chat_menu_button(menu_button=None)
```

## Проверка результата

### Через скрипт

```bash
docker compose exec api python -c "
import asyncio
import sys
sys.path.insert(0, '/app')
from aiogram import Bot
import os
from config import settings

async def main():
    bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN') or settings.telegram_bot_token)
    button = await bot.get_chat_menu_button()
    print(f'Тип кнопки: {type(button).__name__}')
    await bot.session.close()

asyncio.run(main())
"
```

### Через Bot API напрямую

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getChatMenuButton"
```

## Типы Menu Button

- `MenuButtonCommands` — стандартная кнопка команд (дефолт)
- `MenuButtonWebApp` — кнопка с Mini App
- `MenuButtonDefault` — дефолтная кнопка (обычно то же, что Commands)

## Примечания

1. **Menu Button** можно сбросить через Bot API на стандартную кнопку команд
2. **Main App** можно отключить только через интерфейс Telegram
3. Полное удаление Menu Button возможно только через интерфейс Telegram или BotFather
4. После сброса кнопка будет показывать стандартное меню команд бота

## Troubleshooting

### Кнопка не сбрасывается

1. Проверьте токен бота: `echo $TELEGRAM_BOT_TOKEN`
2. Проверьте права бота (должен быть администратором или иметь права на изменение настроек)
3. Попробуйте через интерфейс Telegram напрямую

### Main App не отключается

Main App можно отключить только через интерфейс Telegram:
- Настройки бота → Mini Apps → Main App → Disabled

## Связанные файлы

- `scripts/reset_bot_menu_button.py` — скрипт для сброса
- `api/routers/monitoring.py` — API endpoint для сброса
- `api/bot/webhook.py` — настройка бота

