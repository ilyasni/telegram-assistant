# Реализация исправления проблемы отсутствия tg_channel_id

## Context

У пользователей есть каналы без `tg_channel_id`, которые создаются при добавлении канала только по username (без `telegram_id`). Это приводит к проблемам с парсингом и идентификацией каналов.

## Реализованные изменения

### 1. Создана утилита для получения tg_channel_id

**Файл**: `api/services/telegram_channel_resolver.py`

- Функция `get_tg_channel_id_by_username()` получает `tg_channel_id` из Telegram API по username
- Использует существующую сессию из Redis для доступа к Telegram API
- Обрабатывает ошибки (UsernameNotOccupiedError, FloodWaitError)

### 2. Улучшена логика создания канала

**Файл**: `api/routers/channels.py`

- В функции `subscribe_to_channel()` добавлена попытка получить `tg_channel_id` из Telegram API, если он не передан
- Если не удалось получить синхронно - канал создается без `tg_channel_id`, запускается фоновая задача для заполнения

### 3. Добавлена фоновая задача для заполнения tg_channel_id

**Файл**: `api/routers/channels.py`

- Функция `_fill_tg_channel_id_background()` заполняет `tg_channel_id` для каналов, созданных без него
- Запускается автоматически при создании канала без `tg_channel_id`
- Обновляет канал в БД после получения `tg_channel_id` из Telegram API

## Как это работает

1. **При создании канала с username (без telegram_id)**:
   - Система пытается получить `tg_channel_id` из Telegram API синхронно
   - Если успешно - канал создается с `tg_channel_id`
   - Если не удалось - канал создается без `tg_channel_id`, запускается фоновая задача

2. **Фоновая задача**:
   - Получает `tg_channel_id` из Telegram API по username
   - Обновляет канал в БД, если `tg_channel_id` был получен

## Заполнение существующих каналов

Для заполнения `tg_channel_id` для существующих каналов можно использовать скрипт:

```bash
cd telethon-ingest
python scripts/populate_channel_ids.py
```

Этот скрипт:
- Находит все каналы без `tg_channel_id` с username
- Получает `tg_channel_id` из Telegram API
- Обновляет каналы в БД

## Проверка

После внедрения изменений:

1. **Новые каналы**: При добавлении канала по username система автоматически получит `tg_channel_id`
2. **Существующие каналы**: Запустить скрипт `populate_channel_ids.py` для заполнения

## Impact / Rollback

### Impact
- Улучшение качества данных: новые каналы будут создаваться с `tg_channel_id`
- Автоматическое заполнение: фоновая задача заполнит `tg_channel_id` для каналов, созданных без него
- Обратная совместимость: каналы без `tg_channel_id` по-прежнему поддерживаются

### Rollback
- Откатить изменения в `api/routers/channels.py` (убрать вызов `get_tg_channel_id_by_username` и фоновую задачу)
- Удалить файл `api/services/telegram_channel_resolver.py`
- Существующие каналы без `tg_channel_id` продолжат работать

