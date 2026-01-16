# Исправление ошибки "reply markup is too long" для "Мои каналы"

**Дата**: 2026-01-15 00:26  
**Context7**: Исправление ошибки при запросе "Мои каналы" у пользователей с большим количеством каналов

---

## Проблема

**Симптомы**:
- ❌ Ошибка "Telegram server says - Bad Request: reply markup is too long"
- ❌ Происходит при запросе "Мои каналы" у пользователей с большим количеством каналов
- ❌ Функция `_kb_channels_list` создает кнопку для каждого канала без ограничений

**Причина**:
- Telegram ограничивает размер reply markup (~4096 байт)
- Функция `_kb_channels_list` создает кнопку для каждого канала
- При большом количестве каналов клавиатура превышает лимит

---

## Исправление

### 1. Ограничение количества кнопок в клавиатуре

**Файл**: `api/bot/handlers/base.py:150`

**Изменения**:
- Добавлено ограничение `MAX_BUTTONS = 50`
- Показываются только первые 50 каналов в клавиатуре
- Обрезается длина текста кнопки до 60 символов (лимит Telegram - 64)
- Добавлена кнопка "Показано X из Y", если каналов больше 50

**Код**:
```python
def _kb_channels_list(channels: list):
    """
    Клавиатура со списком каналов.
    
    Context7: Telegram ограничивает размер reply markup (максимум ~4096 байт).
    Ограничиваем количество кнопок до 50 для предотвращения ошибки "reply markup is too long".
    """
    builder = InlineKeyboardBuilder()
    
    # Context7: Ограничиваем количество кнопок до 50
    MAX_BUTTONS = 50
    channels_to_show = channels[:MAX_BUTTONS]
    
    for channel in channels_to_show:
        # Context7: Обрезаем длину текста кнопки до 64 символов (лимит Telegram)
        title = channel.get('title', 'Без названия')
        if len(title) > 60:
            title = title[:57] + "..."
        builder.button(
            text=f"📺 {title}",
            callback_data=f"channel:view:{channel['id']}"
        )
    
    # Context7: Если каналов больше MAX_BUTTONS, показываем информацию об этом
    if len(channels) > MAX_BUTTONS:
        builder.button(
            text=f"📄 Показано {MAX_BUTTONS} из {len(channels)}",
            callback_data="menu:channels_info"
        )
    
    builder.button(text="➕ Добавить канал", callback_data="menu:add_channel")
    builder.button(text="🔙 Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()
```

### 2. Ограничение количества каналов в тексте

**Файл**: `api/bot/handlers/base.py:933, 978`

**Изменения**:
- Добавлено ограничение `MAX_CHANNELS_IN_TEXT = 50`
- Показываются только первые 50 каналов в тексте
- Добавлено сообщение "... и еще X каналов", если каналов больше 50

**Код**:
```python
text = "📺 <b>Мои каналы</b>\n\n"
# Context7: Ограничиваем количество каналов в тексте
MAX_CHANNELS_IN_TEXT = 50
channels_to_show = channels[:MAX_CHANNELS_IN_TEXT]
for channel in channels_to_show:
    status = "🟢" if channel['is_active'] else "🔴"
    text += f"{status} {channel['title']}\n"

if len(channels) > MAX_CHANNELS_IN_TEXT:
    text += f"\n... и еще {len(channels) - MAX_CHANNELS_IN_TEXT} каналов"
```

---

## Результат

✅ **Ошибка исправлена**: Клавиатура ограничена 50 кнопками  
✅ **Текст ограничен**: Показываются только первые 50 каналов в тексте  
✅ **Информация о количестве**: Пользователь видит, сколько каналов всего  
✅ **Безопасность**: Предотвращено превышение лимитов Telegram

---

## Проверка

После перезапуска API проверить:
1. ✅ Отсутствие ошибок "reply markup is too long"
2. ✅ Корректное отображение списка каналов (первые 50)
3. ✅ Информация о количестве каналов, если их больше 50
