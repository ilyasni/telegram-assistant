# Исправление проблемы с сохранением tg_channel_id

## Context

`tg_channel_id` может не сохраняться при создании канала, что приводит к ошибкам при парсинге через `AtomicDBSaver`.

## Причины проблемы

### 1. Отсутствие сессии в Redis
- `get_tg_channel_id_by_username` требует авторизованную сессию Telegram в Redis
- Если сессии нет, функция возвращает `None`
- Канал создается без `tg_channel_id`

### 2. Канал создается даже без `tg_channel_id`
- В `_get_or_create_channel` нет проверки на обязательность `tg_channel_id`
- БД позволяет `NULL` значения (хотя модель указывает `nullable=False`)

### 3. Фоновая задача может не сработать
- Фоновая задача `_fill_tg_channel_id_background` может не найти сессию
- Нет retry логики для повторных попыток

## Реализованные исправления

### 1. Retry логика в `subscribe_to_channel`
- Добавлено 3 попытки получения `tg_channel_id` с exponential backoff
- Улучшено логирование для диагностики

### 2. Улучшенная фоновая задача
- Добавлено 5 попыток с задержками: 2, 5, 10, 30, 60 секунд
- Проверка результата обновления в БД
- Детальное логирование каждой попытки

### 3. Улучшенное логирование в `telegram_channel_resolver`
- Детальное логирование поиска сессии в Redis
- Логирование всех найденных ключей для диагностики
- Предупреждения при отсутствии сессии

### 4. Предупреждение при создании канала без `tg_channel_id`
- Логируется предупреждение при создании канала без `tg_channel_id`
- Фоновая задача запускается автоматически

## Диагностика

### Проверка каналов без `tg_channel_id`

```sql
SELECT id, username, title, is_active, created_at
FROM channels 
WHERE tg_channel_id IS NULL 
  AND is_active = true
ORDER BY created_at DESC;
```

### Проверка сессий в Redis

```bash
# Подключение к Redis
docker exec -it telegram-assistant-redis-1 redis-cli

# Поиск сессий
KEYS *session*

# Проверка статуса сессии
HGETALL tg:qr:session:<key>
HGETALL ingest:session:<key>
GET telegram:session:<key>
```

### Проверка логов

```bash
# Логи API сервиса
docker logs telegram-assistant-api-1 | grep -i "tg_channel_id\|session"

# Логи фоновой задачи
docker logs telegram-assistant-api-1 | grep -i "background.*tg_channel_id"
```

## Ручное исправление

### Для конкретных каналов

Используйте скрипт `scripts/update_beer_channels_manual.sh`:

```bash
./scripts/update_beer_channels_manual.sh -1001234567890 -1001234567891 -1001234567892
```

### Через SQL

```sql
UPDATE channels 
SET tg_channel_id = -1001234567890 
WHERE username = 'beer_for_all' 
  AND tg_channel_id IS NULL;
```

### Через скрипт заполнения

```bash
# Использовать telethon-ingest скрипт
python telethon-ingest/scripts/fetch_tg_channel_ids.py
```

## Мониторинг

### Метрики для отслеживания

1. Количество каналов без `tg_channel_id`:
```sql
SELECT COUNT(*) 
FROM channels 
WHERE tg_channel_id IS NULL 
  AND is_active = true;
```

2. Успешность фоновой задачи:
```bash
docker logs telegram-assistant-api-1 | grep -c "Updated tg_channel_id in background"
```

3. Ошибки получения сессии:
```bash
docker logs telegram-assistant-api-1 | grep -c "No Telegram session found in Redis"
```

## Предотвращение проблемы

### 1. Обеспечить наличие сессии в Redis
- Проверить, что Telegram сессия авторизована
- Настроить автоматическое обновление сессии при истечении

### 2. Валидация при создании канала
- Требовать `telegram_id` или `username` для получения `tg_channel_id`
- Не создавать канал, если не удалось получить `tg_channel_id` (опционально)

### 3. Мониторинг
- Настроить алерты на каналы без `tg_channel_id`
- Отслеживать успешность фоновых задач

## Impact / Rollback

### Изменения
- `api/routers/channels.py`: добавлена retry логика и улучшена фоновая задача
- `api/services/telegram_channel_resolver.py`: улучшено логирование

### Откат
- Изменения обратно совместимы
- Можно откатить через git, если возникнут проблемы
- Каналы без `tg_channel_id` продолжат работать (с ограничениями)

### Риски
- Увеличение времени ответа при создании канала (retry логика)
- Дополнительная нагрузка на Redis при поиске сессии
- Фоновая задача может потреблять ресурсы при множественных retry

