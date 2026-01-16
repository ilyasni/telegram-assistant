# Исправление проблемы доступа к дайджесту канала

**Дата**: 2026-01-12  
**Context7**: Детальное логирование и диагностика проблемы доступа

## Проблема

При запросе дайджеста канала через Telegram бота возвращается ошибка:
```
❌ Канал не найден или нет доступа.
Проверьте, что канал добавлен в ваши подписки.
```

API endpoint возвращает `404 Not Found` при запросе:
```
POST /api/users/{user_id}/channels/{channel_id}/digest?period=7
```

## Диагностика

### Наблюдения из логов

1. **Запрос доходит до API**: 
   - `POST /api/users/cc1e70c9-9058-4fd0-9b52-94012623f0e0/channels/abf7d27b-234b-4ed0-a33d-4a94931d0c2c/digest`
   - Возвращается `404 Not Found`

2. **Нет логов проверки доступа**:
   - Логи "Channel digest - checking access" не появляются
   - Это означает, что либо логирование не срабатывает, либо запрос не доходит до проверки

3. **Параметры запроса**:
   - `user_id`: `cc1e70c9-9058-4fd0-9b52-94012623f0e0` (UUID)
   - `channel_id`: `abf7d27b-234b-4ed0-a33d-4a94931d0c2c` (UUID)
   - `period`: 7

## Исправления

### 1. Улучшено логирование в API endpoint

**Файл**: `api/routers/channels.py`

- Добавлено логирование результата проверки доступа (успешной и неуспешной)
- Добавлена детальная диагностика при отказе в доступе:
  - Проверка существования `user_channel` (даже неактивной)
  - Проверка существования канала
  - Проверка статуса активности

```python
# Логирование успешной проверки доступа
if access_row:
    access_dict = dict(access_row._mapping) if hasattr(access_row, '_mapping') else dict(access_row)
    logger.info(
        "Channel digest - access check result",
        user_id=str(user_uuid),
        channel_id=str(channel_uuid),
        access_granted=True,
        user_channel_is_active=access_dict.get('user_channel_is_active'),
        channel_exists=access_dict.get('channel_exists') is not None,
        channel_is_active=access_dict.get('channel_is_active'),
        tenant_id=str(access_dict.get('tenant_id')) if access_dict.get('tenant_id') else None
    )
```

### 2. Улучшено логирование в боте

**Файл**: `api/bot/handlers/base.py`

- Добавлено логирование при получении callback для дайджеста
- Добавлено логирование параметров перед вызовом API

```python
# Логирование callback
logger.info(
    "Channel digest callback received",
    callback_data=cb.data,
    channel_id=channel_id,
    parts_count=len(parts),
    user_id=cb.from_user.id
)

# Логирование перед API вызовом
logger.info(
    "Channel digest API call",
    user_id=user['id'],
    user_id_type=type(user['id']).__name__,
    channel_id=channel_id,
    channel_id_type=type(channel_id).__name__,
    period=period
)
```

## Следующие шаги

1. **Попросить пользователя запросить дайджест снова**
2. **Проверить логи** на наличие:
   - `Channel digest callback received` - callback получен в боте
   - `Channel digest API call` - параметры перед API вызовом
   - `Channel digest - checking access` - проверка доступа в API
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
2. Если проблема сохраняется - проверить данные в БД:
   ```sql
   -- Проверить user_channel
   SELECT * FROM user_channel 
   WHERE user_id = 'cc1e70c9-9058-4fd0-9b52-94012623f0e0' 
     AND channel_id = 'abf7d27b-234b-4ed0-a33d-4a94931d0c2c';
   
   -- Проверить канал
   SELECT * FROM channels 
   WHERE id = 'abf7d27b-234b-4ed0-a33d-4a94931d0c2c';
   ```
