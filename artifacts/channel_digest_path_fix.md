# Исправление пути endpoint'а для дайджеста канала

**Дата**: 2026-01-12  
**Context7**: Исправление несоответствия пути endpoint'а

## Проблема

Endpoint возвращает `404 Not Found`, хотя данные в БД корректны:
- ✅ Пользователь найден
- ✅ Канал найден и активен
- ✅ Подписка найдена и активна
- ✅ SQL запрос возвращает доступ разрешен

## Причина

**Несоответствие пути endpoint'а**:

1. **Router prefix**: `/channels` (в `api/routers/channels.py`)
2. **Endpoint path**: `/users/{user_id}/channels/{channel_id}/digest`
3. **Main prefix**: `/api` (в `api/main.py`)
4. **Итоговый путь endpoint'а**: `/api/channels/users/{user_id}/channels/{channel_id}/digest`

5. **Запрос из бота идет на**: `/api/users/{user_id}/channels/{channel_id}/digest`

**Результат**: FastAPI не находит endpoint и возвращает 404.

## Исправление

### Изменен путь в боте

**Файл**: `api/bot/handlers/base.py`

```python
# Было:
url = f"{API_BASE}/api/users/{user['id']}/channels/{channel_id}/digest?period={period}"

# Стало:
url = f"{API_BASE}/api/channels/users/{user['id']}/channels/{channel_id}/digest?period={period}"
```

## Проверка

После исправления запрос должен идти на правильный путь:
- `/api/channels/users/{user_id}/channels/{channel_id}/digest`
- Endpoint должен быть найден
- Должны появиться логи "Channel digest endpoint called" и "Channel digest - checking access"

## Альтернативное решение (не применено)

Можно было бы изменить путь endpoint'а, убрав `/channels` из router prefix для этого конкретного endpoint'а, но это нарушило бы консистентность с другими endpoints в роутере.

## Вывод

✅ **Проблема найдена и исправлена**: несоответствие пути endpoint'а
✅ **Данные в БД корректны**: подписка существует и активна
✅ **Endpoint архитектурно корректен**: каналы глобальные, доступ через подписки
