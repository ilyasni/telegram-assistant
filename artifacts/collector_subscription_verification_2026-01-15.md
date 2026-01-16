# Проверка автоматической подписки collector на каналы

**Дата**: 2026-01-15 00:18  
**Context7**: Критическая проверка - только collector (8124731874) должен подписываться автоматически

---

## Проверка кода

### 1. Функция `_ensure_collector_subscription`

**Файл**: `telethon-ingest/services/channel_parser.py:1613`

**Логика проверки** (строки 1635-1639):
```python
collector_id = int(os.getenv("COLLECTOR_TELEGRAM_ID", "8124731874"))

# Проверяем, что это collector сессия
if account_id != collector_id:
    return True  # Не collector - подписка не требуется
```

✅ **Проверка корректна**: Если `account_id != collector_id`, функция возвращает `True` без подписки.

### 2. Вызовы `_ensure_collector_subscription`

**Места вызова**:
1. `channel_parser.py:1105` - после успешного резолва по tg_channel_id
2. `channel_parser.py:1263` - после успешного резолва по username

**Условие вызова** (строки 1104, 1262):
```python
# Context7: Если это collector сессия - обеспечиваем подписку
if account_id:
    await self._ensure_collector_subscription(
        client, entity, channel_id, account_id
    )
```

⚠️ **Проблема**: Проверка `if account_id:` недостаточна - нужно проверять `if account_id == collector_id`

---

## Проблема

**Текущий код**:
```python
if account_id:
    await self._ensure_collector_subscription(...)
```

**Проблема**: Вызывается для любой сессии, не только для collector. Хотя внутри функции есть проверка, но вызов происходит для всех сессий.

**Риск**: Если проверка внутри функции будет изменена или удалена, подписка может произойти для других сессий.

---

## Исправление

Нужно добавить проверку `account_id == collector_id` перед вызовом `_ensure_collector_subscription`.
