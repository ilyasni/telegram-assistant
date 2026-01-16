# Сброс кнопок ответа (Reply Keyboard) в Telegram боте

Инструкция по сбросу клавиатуры ответа у пользователей Telegram бота.

## Context

В Telegram боте могут использоваться два типа кнопок:
- **Inline Keyboard** — кнопки под сообщениями (не требуют сброса, удаляются автоматически)
- **Reply Keyboard** — кнопки, заменяющие стандартную клавиатуру (требуют явного сброса)

Если у пользователя установлена кастомная Reply Keyboard, её нужно сбросить через `ReplyKeyboardRemove`.

## Способы сброса

### 1. Через команду бота (для пользователя)

Пользователь может сбросить свою клавиатуру командой:

```
/remove_keyboard
```

Бот отправит сообщение с подтверждением и удалит кастомную клавиатуру.

### 2. Через скрипт (для администратора)

Сброс клавиатуры конкретного пользователя:

```bash
# Через docker compose
docker compose exec api python scripts/reset_user_keyboard.py <telegram_user_id>

# Пример
docker compose exec api python scripts/reset_user_keyboard.py 123456789
```

### 3. Через API endpoint

```bash
# Сброс клавиатуры пользователя
curl -X POST http://localhost:8000/api/monitoring/bot/reset-user-keyboard/<telegram_user_id>

# Пример
curl -X POST http://localhost:8000/api/monitoring/bot/reset-user-keyboard/123456789
```

### 4. Программно в коде

```python
from aiogram.types import ReplyKeyboardRemove
from aiogram import Bot

bot = Bot(token="YOUR_TOKEN")

# Отправка сообщения с удалением клавиатуры
await bot.send_message(
    chat_id=user_id,
    text="Клавиатура сброшена",
    reply_markup=ReplyKeyboardRemove(remove_keyboard=True)
)
```

## Проверка результата

### Через команду бота

1. Пользователь отправляет `/remove_keyboard`
2. Бот отвечает: "⌨️ Клавиатура сброшена. Стандартная клавиатура восстановлена."
3. Кастомная клавиатура исчезает, возвращается стандартная

### Через API

```bash
# Проверка через endpoint
curl -X POST http://localhost:8000/api/monitoring/bot/reset-user-keyboard/123456789

# Ответ:
# {
#   "success": true,
#   "message": "Keyboard reset for user 123456789",
#   "telegram_user_id": 123456789
# }
```

## Типы клавиатур

### Reply Keyboard (требует сброса)

```python
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Кнопка 1"), KeyboardButton(text="Кнопка 2")],
        [KeyboardButton(text="Кнопка 3")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

await bot.send_message(chat_id, "Выберите действие:", reply_markup=keyboard)
```

### Inline Keyboard (не требует сброса)

```python
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Кнопка 1", callback_data="action1")],
        [InlineKeyboardButton(text="Кнопка 2", callback_data="action2")]
    ]
)

await bot.send_message(chat_id, "Выберите действие:", reply_markup=keyboard)
```

## Примечания

1. **Reply Keyboard** остаётся видимой до явного удаления
2. **Inline Keyboard** удаляется автоматически при редактировании/удалении сообщения
3. Команда `/remove_keyboard` доступна всем пользователям
4. Скрипт и API endpoint требуют знания `telegram_user_id`

## Troubleshooting

### Клавиатура не сбрасывается

1. Проверьте, что пользователь запускал бота (`/start`)
2. Убедитесь, что используется правильный `telegram_user_id`
3. Проверьте, что у пользователя действительно установлена Reply Keyboard
4. Убедитесь, что пользователь не заблокировал бота

### Ошибка "Chat not found"

- Пользователь не запускал бота
- Неверный `telegram_user_id`
- Пользователь заблокировал бота

### Клавиатура сбрасывается, но появляется снова

- Проверьте код бота на наличие мест, где устанавливается Reply Keyboard
- Убедитесь, что после сброса клавиатура не устанавливается заново

## Связанные файлы

- `scripts/reset_user_keyboard.py` — скрипт для сброса клавиатуры
- `api/routers/monitoring.py` — API endpoint для сброса
- `api/bot/handlers/base.py` — команда `/remove_keyboard`
- `api/bot/webhook.py` — список команд бота

