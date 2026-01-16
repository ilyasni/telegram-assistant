# Анализ архитектуры endpoint'а `/api/users/{user_id}/channels/{channel_id}/digest`

**Дата**: 2026-01-12  
**Context7**: Проверка корректности архитектуры endpoint'а с учетом глобальных каналов

## Архитектура каналов

### 1. Каналы глобальные (общие для всех пользователей)

**Модель `Channel`** (`api/models/database.py`):
```python
class Channel(Base):
    """Модель канала (глобальный)."""
    __tablename__ = "channels"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # tenant_id УДАЛЁН - каналы теперь глобальные
    tg_channel_id = Column(BigInteger, nullable=False, unique=True)
    username = Column(String(255))
    title = Column(String(500), nullable=False)
    is_active = Column(Boolean, default=True)
    ...
```

**Ключевые моменты**:
- ✅ Каналы **не привязаны к tenant_id** - они глобальные
- ✅ Один канал парсится один раз для всех пользователей
- ✅ Нет дублирования постов при парсинге

### 2. Подписки пользователей (many-to-many)

**Модель `UserChannel`** (`api/models/database.py`):
```python
class UserChannel(Base):
    """Many-to-many связь пользователей и каналов."""
    __tablename__ = "user_channel"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id"), primary_key=True)
    subscribed_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    settings = Column(JSON, default={})
```

**Ключевые моменты**:
- ✅ Пользователи подписываются на каналы через `user_channel`
- ✅ Подписка может быть активной (`is_active = true`) или неактивной
- ✅ Каждый пользователь видит только те каналы, на которые подписан

### 3. Парсинг каналов (глобальный процесс)

**Из `telethon-ingest/tasks/parse_all_channels_task.py`**:
```python
# Context7: Парсинг каналов - глобальный процесс, не привязан к конкретному tenant_id
# Посты сохраняются глобально, изоляция происходит через user_channel при запросах пользователя
```

**Ключевые моменты**:
- ✅ Каналы парсятся один раз для всех пользователей
- ✅ Посты сохраняются глобально (не привязаны к tenant_id)
- ✅ Изоляция данных происходит через `user_channel` при запросах пользователя

## Корректность endpoint'а `/api/users/{user_id}/channels/{channel_id}/digest`

### ✅ Endpoint архитектурно корректен

**Причины**:

1. **Каналы общие, доступ контролируется через подписки**:
   - Канал существует глобально (один раз)
   - Пользователь должен быть подписан на канал через `user_channel`
   - Дайджест генерируется для конкретного пользователя из общих постов канала

2. **Проверка доступа через `user_channel`**:
   ```python
   access_check = db.execute(
       text("""
           SELECT 
               uc.channel_id,
               uc.user_id,
               uc.is_active as user_channel_is_active,
               c.id as channel_exists,
               c.is_active as channel_is_active,
               u.tenant_id
           FROM user_channel uc
           JOIN channels c ON c.id = uc.channel_id
           JOIN users u ON u.id = uc.user_id
           WHERE uc.user_id = :user_id 
               AND uc.channel_id = :channel_id 
               AND uc.is_active = true
           LIMIT 1
       """),
       {"user_id": str(user_uuid), "channel_id": str(channel_uuid)}
   )
   ```

3. **Дайджест генерируется из общих постов**:
   - Посты канала общие для всех пользователей
   - Дайджест фильтруется по периоду и генерируется для конкретного пользователя
   - Каждый пользователь получает свой дайджест из общих данных

### Проблема: проверка доступа не находит запись

**Возможные причины**:

1. **Пользователь не подписан на канал**:
   - Запись в `user_channel` отсутствует
   - Или `is_active = false`

2. **Канал не существует**:
   - Канал с таким `channel_id` не найден в таблице `channels`

3. **Канал неактивен**:
   - `channel.is_active = false`

## Рекомендации

### 1. Проверить данные в БД

```sql
-- Проверить подписку пользователя на канал
SELECT uc.user_id, uc.channel_id, uc.is_active, c.id as channel_exists, c.is_active as channel_is_active
FROM user_channel uc
LEFT JOIN channels c ON c.id = uc.channel_id
WHERE uc.user_id = 'cc1e70c9-9058-4fd0-9b52-94012623f0e0' 
  AND uc.channel_id = 'abf7d27b-234b-4ed0-a33d-4a94931d0c2c';

-- Проверить существование канала
SELECT id, title, is_active FROM channels 
WHERE id = 'abf7d27b-234b-4ed0-a33d-4a94931d0c2c';

-- Проверить все подписки пользователя
SELECT uc.channel_id, c.title, uc.is_active 
FROM user_channel uc
JOIN channels c ON c.id = uc.channel_id
WHERE uc.user_id = 'cc1e70c9-9058-4fd0-9b52-94012623f0e0';
```

### 2. Улучшить диагностику

- Добавить логирование результата проверки доступа
- Добавить проверку существования канала отдельно
- Добавить проверку подписки пользователя отдельно

### 3. Улучшить UX

- Если канал не найден - предложить подписаться
- Если подписка неактивна - предложить активировать
- Если канал неактивен - сообщить об этом

## Вывод

✅ **Endpoint архитектурно корректен**:
- Каналы глобальные (нет дублирования при парсинге)
- Доступ контролируется через `user_channel` (пользователь должен быть подписан)
- Дайджест генерируется для конкретного пользователя из общих постов

❌ **Проблема в данных**:
- Пользователь не подписан на канал
- Или подписка неактивна
- Или канал не существует/неактивен

**Следующий шаг**: Проверить данные в БД и улучшить диагностику в endpoint'е.
