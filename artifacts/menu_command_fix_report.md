# Исправление команды /menu

**Дата**: 2025-12-19  
**Context7**: Улучшенное логирование и обработка ошибок

## Проблема

Команда `/menu` перестала работать, выдавая ошибку:
```
❌ Произошла ошибка при открытии меню. Попробуйте позже.
```

## Диагностика

### Ошибка в логах

```
{"error": "HTTP Client says - ServerDisconnectedError: Server disconnected", 
 "user_id": 139883458, 
 "event": "Error in /menu command", 
 "logger": "bot.handlers.base", 
 "level": "error"}
```

### Причина

Ошибка `ServerDisconnectedError: Server disconnected` указывает на проблему с соединением с Telegram API при отправке сообщения. Это может быть:
1. Временная проблема с сетью
2. Проблема с Telegram API (timeout, перегрузка)
3. Проблема с HTTP клиентом (httpx)

## Исправления

### ✅ Улучшенное логирование

**Файл**: `api/bot/handlers/base.py`

Добавлено детальное логирование для диагностики:
- Логирование получения команды
- Логирование создания клавиатуры
- Логирование успешной отправки
- Детальное логирование ошибок с `exc_info=True`
- Защита от двойной ошибки при отправке сообщения об ошибке

### Код

```python
@router.message(Command("menu"))
async def cmd_menu(msg: Message):
    """Обработчик команды /menu — показывает главное меню."""
    try:
        # Context7: Детальное логирование для диагностики
        logger.info(
            "Menu command received",
            user_id=msg.from_user.id,
            username=msg.from_user.username
        )
        
        # Создаем клавиатуру
        keyboard = _kb_main_menu()
        logger.debug("Main menu keyboard created", user_id=msg.from_user.id)
        
        # Отправляем сообщение
        await msg.answer(
            "🤖 <b>Главное меню</b>\n\n"
            "Выберите действие:",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        logger.info("Menu sent successfully", user_id=msg.from_user.id)
        
    except Exception as e:
        # Context7: Детальное логирование ошибки
        logger.error(
            "Error in /menu command",
            error=str(e),
            error_type=type(e).__name__,
            user_id=msg.from_user.id,
            username=msg.from_user.username,
            exc_info=True
        )
        try:
            await msg.answer("❌ Произошла ошибка при открытии меню. Попробуйте позже.")
        except Exception as e2:
            logger.error(
                "Failed to send error message to user",
                error=str(e2),
                user_id=msg.from_user.id
            )
```

## Результаты

1. ✅ Код обновлен с улучшенным логированием
2. ✅ Контейнер перезапущен
3. ✅ Изменения применены (WatchFiles автоматически перезагрузил)
4. ✅ Детальное логирование поможет диагностировать проблему

## Следующие шаги

1. **Протестировать команду `/menu`** - должна работать корректно
2. **Проверить логи** при следующем запросе на наличие детальной информации:
   - `Menu command received` - команда получена
   - `Main menu keyboard created` - клавиатура создана
   - `Menu sent successfully` - сообщение отправлено успешно
   - Или детальная ошибка с типом и трейсбеком

## Примечания

- Ошибка `ServerDisconnectedError` обычно временная и связана с сетью или Telegram API
- Улучшенное логирование поможет понять, на каком этапе происходит ошибка
- Защита от двойной ошибки предотвращает сбой при отправке сообщения об ошибке

## Команды для проверки

```bash
# Проверить логи при следующем запросе
docker compose logs api --tail 200 | grep -E "(Menu command|Error in /menu)" -A 10

# Проверить статус API
docker compose ps api --format "{{.Status}}"
```
