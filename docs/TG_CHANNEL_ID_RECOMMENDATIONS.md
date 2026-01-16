# Рекомендации по работе с tg_channel_id

## Context

После исправления логики получения и сохранения `tg_channel_id` необходимо:
1. Проверить текущее состояние системы
2. Заполнить существующие каналы без `tg_channel_id`
3. Настроить мониторинг
4. Предотвратить проблему в будущем

## План действий

### Шаг 1: Проверка текущего состояния

#### 1.1. Проверить каналы без `tg_channel_id`

```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    id, 
    username, 
    title, 
    is_active, 
    created_at,
    (SELECT COUNT(*) FROM user_channel WHERE channel_id = channels.id AND is_active = true) as subscribers_count
FROM channels 
WHERE tg_channel_id IS NULL 
  AND is_active = true
ORDER BY created_at DESC;
"
```

#### 1.2. Проверить наличие сессий в Redis

```bash
# Подключение к Redis
docker exec -it telegram-assistant-redis-1 redis-cli

# Поиск всех сессий
KEYS *session*

# Проверка конкретной сессии
HGETALL tg:qr:session:<key>
# Или
HGETALL ingest:session:<key>
```

#### 1.3. Проверить логи на ошибки

```bash
# Проверить логи API на ошибки получения tg_channel_id
docker logs telegram-assistant-api-1 --tail 100 | grep -i "tg_channel_id\|session"

# Проверить успешность фоновых задач
docker logs telegram-assistant-api-1 --tail 500 | grep -i "Updated tg_channel_id in background"
```

### Шаг 2: Заполнение существующих каналов

#### 2.1. Для конкретных каналов (beer_for_all, beer_by, prostopropivo)

Используйте готовый скрипт:

```bash
cd /opt/telegram-assistant
./scripts/update_beer_channels_manual.sh -1001234567890 -1001234567891 -1001234567892
```

**Где взять ID:**
- Бот @userinfobot — отправьте ссылку на канал
- Бот @getidsbot — отправьте ссылку на канал
- Telegram Desktop — View → Statistics → Channel ID

#### 2.2. Для всех каналов без `tg_channel_id`

**Вариант A: Использовать скрипт fetch_tg_channel_ids.py**

```bash
cd /opt/telegram-assistant
python telethon-ingest/scripts/fetch_tg_channel_ids.py
```

**Вариант B: Использовать скрипт backfill_tg_channel_id.py**

```bash
cd /opt/telegram-assistant
python telethon-ingest/scripts/backfill_tg_channel_id.py
```

**Вариант C: Ручное заполнение через SQL (если знаете ID)**

```sql
-- Для конкретного канала
UPDATE channels 
SET tg_channel_id = -1001234567890 
WHERE username = 'beer_for_all' 
  AND tg_channel_id IS NULL;

-- Проверка результата
SELECT id, username, tg_channel_id 
FROM channels 
WHERE username = 'beer_for_all';
```

### Шаг 3: Настройка мониторинга

#### 3.1. SQL запрос для мониторинга

Создайте запрос для регулярной проверки:

```sql
-- Количество каналов без tg_channel_id
SELECT 
    COUNT(*) as channels_without_tg_id,
    COUNT(*) FILTER (WHERE is_active = true) as active_without_tg_id
FROM channels 
WHERE tg_channel_id IS NULL;

-- Детальная информация
SELECT 
    id,
    username,
    title,
    is_active,
    created_at,
    (SELECT COUNT(*) FROM user_channel WHERE channel_id = channels.id AND is_active = true) as subscribers
FROM channels 
WHERE tg_channel_id IS NULL 
  AND is_active = true
ORDER BY created_at DESC;
```

#### 3.2. Скрипт для проверки (можно добавить в cron)

Создайте `scripts/check_missing_tg_channel_ids.sh`:

```bash
#!/bin/bash
# Проверка каналов без tg_channel_id

COUNT=$(docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -t -c "
SELECT COUNT(*) 
FROM channels 
WHERE tg_channel_id IS NULL 
  AND is_active = true;
")

if [ "$COUNT" -gt 0 ]; then
    echo "⚠️  Найдено $COUNT активных каналов без tg_channel_id"
    docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
    SELECT id, username, title, created_at
    FROM channels 
    WHERE tg_channel_id IS NULL 
      AND is_active = true
    ORDER BY created_at DESC
    LIMIT 10;
    "
    exit 1
else
    echo "✅ Все активные каналы имеют tg_channel_id"
    exit 0
fi
```

Сделайте исполняемым:
```bash
chmod +x scripts/check_missing_tg_channel_ids.sh
```

#### 3.3. Настройка алертов (опционально)

Если используете Prometheus/Grafana, добавьте метрику:

```python
# В api/routers/channels.py или отдельный endpoint
channels_without_tg_id = Gauge(
    'channels_without_tg_channel_id_total',
    'Number of channels without tg_channel_id'
)

# Обновление метрики
async def update_metrics():
    count = db.execute(
        text("SELECT COUNT(*) FROM channels WHERE tg_channel_id IS NULL AND is_active = true")
    ).scalar()
    channels_without_tg_id.set(count)
```

### Шаг 4: Предотвращение проблемы

#### 4.1. Обеспечить наличие сессии в Redis

**Проверка:**
```bash
docker exec telegram-assistant-redis-1 redis-cli KEYS "*session*"
```

**Если сессий нет:**
- Проверить, что Telegram бот авторизован
- Запустить процесс авторизации через QR-код
- Проверить настройки Redis

#### 4.2. Мониторинг фоновых задач

Проверяйте логи на успешность фоновых задач:

```bash
# Успешные обновления
docker logs telegram-assistant-api-1 | grep -c "Updated tg_channel_id in background"

# Ошибки
docker logs telegram-assistant-api-1 | grep -c "Failed to get tg_channel_id from Telegram API"
```

#### 4.3. Валидация при создании канала

Текущая логика:
- Пытается получить `tg_channel_id` синхронно (3 попытки)
- Если не удалось — создает канал и запускает фоновую задачу (5 попыток)

**Рекомендация:** Если критично, можно добавить проверку перед созданием:

```python
# В subscribe_to_channel, перед созданием канала
if not telegram_id and request.username:
    raise HTTPException(
        status_code=400,
        detail={
            "error": "tg_channel_id_required",
            "message": "Cannot create channel without tg_channel_id. Please provide telegram_id or ensure Telegram session is authorized."
        }
    )
```

**Но это может быть слишком строго** — лучше оставить текущую логику с фоновой задачей.

### Шаг 5: Регулярное обслуживание

#### 5.1. Еженедельная проверка

```bash
# Запускать раз в неделю
./scripts/check_missing_tg_channel_ids.sh

# Если найдены каналы без tg_channel_id
python telethon-ingest/scripts/fetch_tg_channel_ids.py
```

#### 5.2. После массового создания каналов

Если создано много каналов, проверьте:

```bash
# Проверить количество
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT COUNT(*) 
FROM channels 
WHERE tg_channel_id IS NULL 
  AND created_at > NOW() - INTERVAL '1 day';
"

# Заполнить автоматически
python telethon-ingest/scripts/fetch_tg_channel_ids.py
```

## Чеклист для проверки

- [ ] Проверены каналы без `tg_channel_id`
- [ ] Проверено наличие сессий в Redis
- [ ] Заполнены `tg_channel_id` для существующих каналов
- [ ] Настроен мониторинг (скрипт или метрики)
- [ ] Проверены логи на ошибки
- [ ] Настроено регулярное обслуживание (cron или ручная проверка)

## Troubleshooting

### Проблема: Сессия не найдена в Redis

**Решение:**
1. Проверить, что Telegram бот авторизован
2. Перезапустить процесс авторизации
3. Проверить настройки Redis (подключение, ключи)

### Проблема: Фоновая задача не заполняет `tg_channel_id`

**Диагностика:**
```bash
# Проверить логи фоновой задачи
docker logs telegram-assistant-api-1 | grep -A 5 "background.*tg_channel_id"

# Проверить наличие сессии
docker exec telegram-assistant-redis-1 redis-cli KEYS "*session*"
```

**Решение:**
- Убедиться, что сессия есть в Redis
- Запустить скрипт заполнения вручную
- Проверить доступность Telegram API

### Проблема: Канал не найден в Telegram

**Решение:**
- Проверить правильность username
- Убедиться, что канал существует и доступен
- Проверить права доступа бота к каналу

## Дополнительные ресурсы

- Документация по исправлению: `docs/TG_CHANNEL_ID_FIX.md`
- Скрипты для заполнения: `telethon-ingest/scripts/fetch_tg_channel_ids.py`
- Ручное обновление: `scripts/update_beer_channels_manual.sh`

