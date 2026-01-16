# Финальный отчет: Диагностика и исправление проблемы отсутствия постов

**Дата**: 2026-01-12 20:15 UTC  
**Проблема**: За последние 3 часа не было добавлено ни одного поста  
**Context7**: Диагностика и исправление с использованием best practices

---

## Context

Пользователь сообщил, что за последние 3 часа не было добавлено ни одного поста, хотя посты были в каналах. Scheduler работает, но посты не сохраняются.

---

## 1. Проблема подтверждена

### Анализ логов

**Ключевые ошибки**:

1. **FloodWait от Telegram API**:
   ```
   "A wait of 11472 seconds is required (caused by ResolveUsernameRequest)"
   ```
   - Время: 2026-01-12 17:07:59 UTC
   - Длительность: 11472 секунд (~3.2 часа)
   - Причина: `ResolveUsernameRequest` (попытка получить entity канала по username)

2. **Entity not found**:
   ```
   "Could not find the input entity for PeerChannel(channel_id=...)"
   ```
   - После FloodWait парсер пытается использовать `tg_channel_id`
   - Entity не найден (канал недоступен или `tg_channel_id` неверен)

3. **messages_processed = 0**:
   ```
   "messages_processed": 0, "status": "ok"
   ```
   - Парсер запускается, но не обрабатывает сообщения
   - Посты не сохраняются в БД

### Проблемные каналы

- `naebnet` (1a81433d-351a-43bb-9974-f64e1c3bc1a6)
- `designsniper` (35d8421a-ebe8-4a64-8912-f299a565b287)
- `ilyabirman_channel` (843237db-1f4f-47df-8608-f979c66b57fb)
- `How2AI` (b309b2ae-5401-4301-a809-008009a02bfd)

---

## 2. Причина проблемы

### 2.1. FloodWait при ResolveUsernameRequest

**Проблема**: 
- Парсер пытается получить entity канала по `username` через `client.get_entity(username)`
- Telegram API возвращает FloodWait: 11472 секунд
- Текущий код обрабатывает это как общий `Exception`, не как `FloodWaitError`
- В результате FloodWait не обрабатывается правильно

**Текущий код** (было):
```python
try:
    entity = await client.get_entity(clean_username)
except Exception as e:  # ❌ Не обрабатывает FloodWaitError специально
    logger.warning("Failed to get entity by username, trying tg_channel_id", ...)
    # Пытается использовать tg_channel_id, но не устанавливает cooldown
```

### 2.2. Entity not found после FloodWait

**Проблема**:
- После FloodWait парсер пытается использовать `tg_channel_id` как fallback
- Но entity не найден (канал недоступен или `tg_channel_id` неверен)
- В результате парсер не может получить доступ к каналу

### 2.3. MAX_FLOOD_WAIT слишком мал

**Проблема**:
- `MAX_FLOOD_WAIT = 60` секунд
- Но FloodWait может быть больше (11472 секунд)
- Для больших FloodWait cooldown не работает правильно

---

## 3. Исправления применены

### ✅ 3.1. Улучшена обработка FloodWait для ResolveUsernameRequest

**Файл**: `telethon-ingest/services/channel_parser.py`

**Изменения**:
- Добавлена специальная обработка `errors.FloodWaitError` при `get_entity(username)`
- Установка cooldown для канала при FloodWait
- Fallback на `tg_channel_id` после FloodWait
- Детальное логирование FloodWait

**Код** (строки ~825-863):
```python
except errors.FloodWaitError as e:
    # Context7: Специальная обработка FloodWait при ResolveUsernameRequest
    wait_seconds = min(e.seconds, 300)  # Cap at 5 minutes
    
    # Устанавливаем cooldown для канала
    if self.redis_client:
        from .telethon_retry import set_channel_cooldown
        await set_channel_cooldown(self.redis_client, channel_id, wait_seconds)
    
    # Пытаемся использовать tg_channel_id как fallback
    if tg_channel_id_db is not None:
        entity = await client.get_entity(int(tg_channel_id_db))
        # Успешно получили entity
    else:
        return None  # Пропускаем канал
```

**Статус**: ✅ Применено

### ✅ 3.2. Увеличен MAX_FLOOD_WAIT

**Файл**: `telethon-ingest/services/telethon_retry.py`

**Изменение** (строка ~59):
```python
MAX_FLOOD_WAIT = 300  # Context7: Увеличено до 5 минут (было 60)
```

**Статус**: ✅ Применено

### ✅ 3.3. Улучшена обработка больших FloodWait

**Файл**: `telethon-ingest/services/telethon_retry.py`

**Изменения** (строки ~165-172):
- Улучшено логирование больших FloodWait
- Cooldown устанавливается на MAX_FLOOD_WAIT, но логируется реальное время
- Более детальная информация для диагностики

**Статус**: ✅ Применено

---

## 4. Текущая ситуация

### 4.1. FloodWait статус

**Последний FloodWait**: 2026-01-12 17:07:59 UTC  
**Время ожидания**: 11472 секунд (~3.2 часа)  
**Текущее время**: 2026-01-12 20:15 UTC  
**Прошло времени**: ~3.1 часа  
**Осталось ждать**: ~3.1 часа (FloodWait еще активен)

**Вывод**: FloodWait еще активен, нужно дождаться окончания (~через 3 часа от времени FloodWait).

### 4.2. Что произойдет после окончания FloodWait

1. **Каналы выйдут из cooldown**: После окончания FloodWait каналы будут доступны для парсинга
2. **Парсер попытается снова**: При следующем тике scheduler'а (каждые 5 минут)
3. **Улучшенная обработка**: Новый код правильно обработает FloodWait и установит cooldown

---

## 5. Рекомендации

### Немедленные действия

1. **Дождаться окончания FloodWait**:
   - FloodWait активен еще ~3 часа
   - После окончания парсер должен снова работать

2. **Проверить доступность каналов**:
   - Проверить каналы вручную через Telegram
   - Убедиться, что каналы не удалены и доступны

3. **Обновить tg_channel_id** (если нужно):
   ```bash
   python3 telethon-ingest/scripts/backfill_tg_channel_id.py
   ```

### После окончания FloodWait

4. **Проверить работу парсера**:
   ```bash
   docker compose logs telethon-ingest --tail=100 | grep -E "CHANNEL_PARSE|messages_processed"
   ```

5. **Проверить посты в БД**:
   ```sql
   SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '1 hour';
   ```

6. **Проверить метрики**:
   ```bash
   curl "http://localhost:9090/api/v1/query?query=posts_parsed_total"
   ```

---

## 6. Context7 Best Practices - Применено

### ✅ Обработка ошибок

1. **Специальная обработка FloodWaitError**: Добавлена для `ResolveUsernameRequest`
2. **Cooldown механизм**: Устанавливается при FloodWait
3. **Fallback логика**: Использует `tg_channel_id` после FloodWait
4. **Детальное логирование**: Вся информация о FloodWait логируется

### ✅ Observability

1. **Метрики**: `parser_floodwait_seconds_total`, `parser_retries_total`
2. **Логирование**: Детальные логи всех ошибок
3. **Мониторинг**: Cooldown статус в Redis

### ✅ Resilience

1. **Retry logic**: Есть retry с экспоненциальным backoff
2. **Graceful degradation**: Пропуск каналов при недоступности
3. **Cooldown**: Предотвращение повторных запросов при FloodWait

---

## 7. Checks

1. ✅ Проблема подтверждена: нет постов за последние 3 часа
2. ✅ Причина найдена: FloodWait + Entity not found
3. ✅ Исправления применены: обработка FloodWait улучшена
4. ⏳ Ожидание: FloodWait активен еще ~3 часа
5. ⏳ Проверка: требуется проверить работу после окончания FloodWait

**Статус**: ✅ **ИСПРАВЛЕНИЯ ПРИМЕНЕНЫ, ОЖИДАЕТСЯ ВОССТАНОВЛЕНИЕ РАБОТЫ ПОСЛЕ ОКОНЧАНИЯ FLOODWAIT**

---

## 8. Выводы

### ✅ Исправлено

1. **Обработка FloodWait для ResolveUsernameRequest**: Добавлена специальная обработка
2. **MAX_FLOOD_WAIT**: Увеличен до 300 секунд
3. **Логирование**: Улучшено для диагностики
4. **Cooldown**: Правильно устанавливается при FloodWait

### ⏳ Ожидается

1. **Окончание FloodWait**: Через ~3 часа от времени FloodWait (17:07 UTC)
2. **Восстановление работы**: Парсер должен снова работать после окончания FloodWait
3. **Проверка**: Требуется проверить работу после окончания FloodWait

### ⚠️ Требует внимания

1. **Доступность каналов**: Проверить, что каналы доступны в Telegram
2. **tg_channel_id**: Обновить, если неверен
3. **Мониторинг**: Следить за метриками и логами после окончания FloodWait

**Статус**: ✅ **ИСПРАВЛЕНИЯ ПРИМЕНЕНЫ, ОЖИДАЕТСЯ ВОССТАНОВЛЕНИЕ РАБОТЫ**

---

## 9. Следующие шаги

1. ✅ Исправления применены
2. ⏳ Дождаться окончания FloodWait (~3 часа)
3. ⏳ Проверить работу парсера после окончания FloodWait
4. ⏳ Проверить посты в БД
5. ⏳ Мониторить метрики и логи

**Рекомендация**: Проверить работу парсера через ~3 часа после окончания FloodWait.
