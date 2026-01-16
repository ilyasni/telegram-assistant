# Диагностика проблемы доступа к дайджесту канала

**Дата**: 2025-12-19  
**Context7**: Детальное логирование для диагностики

## Проблема

При запросе дайджеста по каналу возвращается ошибка:
```
❌ Канал не найден или нет доступа.
Проверьте, что канал добавлен в ваши подписки.
```

## Анализ потока данных

### 1. Бот → API

**Файл**: `api/bot/handlers/base.py`

1. Пользователь нажимает кнопку "📰 Дайджест" в канале
2. Callback data: `channel:digest:{channel_id}` где `channel_id` берется из `channel['id']`
3. `channel['id']` приходит из API `/api/channels/users/{user_id}/list`
4. В API `channel['id']` конвертируется в строку: `channel_data['id'] = str(channel_data['id'])`
5. Бот вызывает: `POST /api/users/{user_id}/channels/{channel_id}/digest?period={period}`

### 2. API Endpoint

**Файл**: `api/routers/channels.py`

1. `channel_id` приходит как строка из URL параметра
2. Валидация: `channel_uuid = UUID(channel_id)` (преобразует строку в UUID)
3. SQL запрос проверяет доступ:
   ```sql
   SELECT uc.channel_id, c.id, c.is_active, u.tenant_id
   FROM user_channel uc
   JOIN channels c ON c.id = uc.channel_id
   JOIN users u ON u.id = uc.user_id
   WHERE uc.user_id = :user_id 
       AND uc.channel_id = :channel_id 
       AND uc.is_active = true
   ```
4. Если запись не найдена → 404

## Добавленное логирование

### Перед SQL запросом

```python
logger.info(
    "Channel digest - checking access",
    user_id=str(user_uuid),
    user_id_type=type(user_uuid).__name__,
    channel_id=str(channel_uuid),
    channel_id_type=type(channel_uuid).__name__,
    channel_id_raw=channel_id,
    tenant_id=str(tenant_id) if tenant_id else None
)
```

### При ошибке доступа

```python
# Проверяем, существует ли user_channel вообще (даже неактивный)
check_user_channel = db.execute(...)
user_channel_row = check_user_channel.fetchone()

# Проверяем, существует ли канал вообще
check_channel = db.execute(...)
channel_row = check_channel.fetchone()

logger.warning(
    "Channel digest access denied - user_channel not found",
    tenant_id=str(tenant_id) if tenant_id else None,
    user_id=str(user_uuid),
    channel_id=str(channel_uuid),
    channel_id_raw=channel_id,
    user_channel_exists=(user_channel_row is not None),
    user_channel_is_active=(user_channel_row.is_active if user_channel_row else None),
    channel_exists=(channel_row is not None),
    channel_is_active=(channel_row.is_active if channel_row else None)
)
```

## Возможные причины проблемы

### 1. Запись user_channel неактивна

- `user_channel.is_active = false`
- SQL запрос ищет только активные записи: `uc.is_active = true`

### 2. Канал неактивен

- `channels.is_active = false`
- Хотя JOIN не проверяет это напрямую, но логирование покажет

### 3. Несоответствие UUID

- `channel_id` из callback_data не совпадает с UUID в БД
- Логирование покажет `channel_id_raw` и `channel_id` (преобразованный)

### 4. Пользователь не подписан на канал

- Записи в `user_channel` вообще нет
- Логирование покажет `user_channel_exists = false`

## Следующие шаги

1. **Дождаться нового запроса** от пользователя
2. **Проверить логи** на наличие сообщений:
   - `Channel digest - checking access` - параметры запроса
   - `Channel digest access denied - user_channel not found` - детали ошибки
3. **Проверить БД** (если доступна):
   ```sql
   SELECT uc.*, c.is_active as channel_active
   FROM user_channel uc
   JOIN channels c ON c.id = uc.channel_id
   WHERE uc.user_id = 'cc1e70c9-9058-4fd0-9b52-94012623f0e0'
     AND uc.channel_id = 'abf7d27b-234b-4ed0-a33d-4a94931d0c2c';
   ```

## Исправления

1. ✅ Добавлена валидация `channel_id` как UUID
2. ✅ Добавлено детальное логирование перед SQL запросом
3. ✅ Добавлена проверка существования записей при ошибке доступа
4. ✅ Логирование включает все необходимые поля для диагностики

## Команды для проверки

```bash
# Проверить логи API
docker compose logs api --tail 500 | grep -E "(Channel digest|channel.*digest)"

# Проверить последние запросы
docker compose logs api --tail 200 | grep "POST.*channels.*digest"

# Проверить статус API
docker compose ps api --format "{{.Status}}"
```
