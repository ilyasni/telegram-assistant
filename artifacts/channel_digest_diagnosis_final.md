# Финальная диагностика проблемы доступа к дайджесту канала

**Дата**: 2026-01-12  
**Context7**: Детальное логирование для диагностики проблемы доступа

## Проблема

При запросе дайджеста канала через Telegram бота возвращается ошибка:
```
❌ Канал не найден или нет доступа.
Проверьте, что канал добавлен в ваши подписки.
```

API endpoint возвращает `404 Not Found` при запросе:
```
POST /api/users/cc1e70c9-9058-4fd0-9b52-94012623f0e0/channels/abf7d27b-234b-4ed0-a33d-4a94931d0c2c/digest?period=7
```

## Наблюдения

### 1. Запрос доходит до API
- Webhook обрабатывается: `POST /tg/bot/webhook` → `200 OK`
- API запрос выполняется: `POST /api/users/{user_id}/channels/{channel_id}/digest`
- Возвращается `404 Not Found`

### 2. Нет логов проверки доступа
- **КРИТИЧНО**: В логах нет записей "Channel digest - checking access"
- Это означает, что либо:
  - Логирование не срабатывает (но код есть)
  - Запрос не доходит до проверки доступа (но 404 возвращается)
  - Логирование на уровне `info` не видно из-за настроек логирования

### 3. Параметры запроса
- `user_id`: `cc1e70c9-9058-4fd0-9b52-94012623f0e0` (UUID)
- `channel_id`: `abf7d27b-234b-4ed0-a33d-4a94931d0c2c` (UUID)
- `period`: 7

## Исправления

### 1. Изменен уровень логирования на `warning`

**Файл**: `api/routers/channels.py`

- Изменено с `logger.info` на `logger.warning` для гарантированной видимости
- Добавлено логирование в начале функции для подтверждения выполнения кода

```python
# В начале функции
logger.warning(
    "Channel digest endpoint called",
    user_id=user_id,
    channel_id=channel_id,
    period=period
)

# Перед проверкой доступа
logger.warning(
    "Channel digest - checking access",
    user_id=str(user_uuid),
    channel_id=str(channel_uuid),
    ...
)
```

### 2. Улучшено логирование в боте

**Файл**: `api/bot/handlers/base.py`

- Добавлено логирование при получении callback для дайджеста
- Добавлено логирование параметров перед вызовом API

## Следующие шаги

1. **Попросить пользователя запросить дайджест снова**
2. **Проверить логи** на наличие:
   - `Channel digest endpoint called` - функция вызвана
   - `Channel digest - checking access` - проверка доступа начата
   - `Channel digest - access check result` - результат проверки (успешной или нет)
   - `Channel digest access denied` - детальная диагностика при отказе

## Команды для проверки

```bash
# Проверить логи после нового запроса
docker compose logs api --tail 500 | grep -E "(Channel digest|access check|access denied)" -A 15

# Проверить логи бота
docker compose logs api --tail 500 | grep -E "(Channel digest callback|Channel digest API)" -A 10
```

## Возможные причины

1. **`user_channel` не существует** - пользователь не подписан на канал
2. **`user_channel.is_active = false`** - подписка деактивирована
3. **Канал не существует** - канал удален или не найден
4. **Канал неактивен** - `channel.is_active = false`
5. **Несоответствие `tenant_id`** - канал принадлежит другому tenant

## Рекомендации

1. Проверить логи после нового запроса дайджеста
2. Если логи "Channel digest endpoint called" не появляются - проблема в маршрутизации FastAPI
3. Если логи появляются, но нет "Channel digest - checking access" - проблема в выполнении кода до проверки доступа
4. Если логи "Channel digest - checking access" появляются, но нет результата - проблема в SQL запросе
