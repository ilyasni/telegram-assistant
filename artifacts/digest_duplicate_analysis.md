# Анализ проблемы дубликатов дайджестов

**Дата:** 2025-11-25  
**Статус:** ⚠️ **Обнаружена проблема с дубликатами запросов**

---

## Context

На скриншоте показаны два сообщения с одинаковым ID дайджеста `dac58a5d-522f-4e94-b803-caee803b53c6`, но разными временными метками "Поставлен" (18:36:57 и 18:39:09). Оба в статусе "pending".

---

## Наблюдения

### Статус в БД

- **ID дайджеста:** `dac58a5d-522f-4e94-b803-caee803b53c6`
- **Статус в БД:** `failed` (не `pending`!)
- **Создан:** 2025-11-25 15:36:57 MSK
- **User ID:** `6af5e7d3-e736-46da-bfc1-42786f2ab1c0`
- **Digest date:** 2025-11-25

### Проблема

1. **Дубликаты запросов:** Пользователь дважды нажал кнопку генерации дайджеста
2. **Одинаковый ID:** Оба запроса вернули один и тот же `digest_id`
3. **Разные временные метки:** Разница ~2 минуты (18:36:57 и 18:39:09)
4. **Статус в БД:** `failed` (дайджест упал при обработке)

---

## Анализ кода

### 1. Логика проверки существующего дайджеста

В `api/tasks/scheduler_tasks.py:generate_digest_for_user`:

```python
existing = db.query(DigestHistory).filter(
    and_(
        DigestHistory.user_id == user_uuid,
        DigestHistory.digest_date == today
    )
).order_by(DigestHistory.created_at.desc()).first()

force_new = trigger == "manual"

if existing:
    if existing.status in {"scheduled", "pending", "processing"}:
        if force_new:
            # Manual trigger: перезаписываем существующий
            existing.status = "failed"
            existing = None
        else:
            # Возвращаем существующий дайджест
            return existing
```

**Проблема:**
- При `trigger="manual"` и существующем дайджесте со статусом "pending" → создается новый дайджест
- Но если пользователь быстро дважды нажмет кнопку, может возникнуть race condition:
  - Первый запрос: создает дайджест со статусом "pending"
  - Второй запрос (через 2 минуты): находит существующий "pending", но `force_new=True` → перезаписывает его в "failed" и создает новый
  - Результат: два сообщения с одинаковым ID, но разными временными метками

### 2. Защита от двойного нажатия

В `api/bot/handlers/digest_handlers.py:callback_digest_generate`:

```python
async def callback_digest_generate(callback: CallbackQuery):
    # Показываем индикатор загрузки
    await callback.answer("⏳ Генерирую дайджест...")
    
    # Нет защиты от двойного нажатия!
    r = await client.post(f"{API_BASE}/api/digest/generate/{user_id}")
```

**Проблема:**
- Нет проверки, не обрабатывается ли уже запрос
- Нет блокировки кнопки после первого нажатия
- Пользователь может дважды нажать кнопку

### 3. Идемпотентность событий

В `api/worker/event_bus.py:EventPublisher`:

```python
async def publish_event(self, stream_name: str, event: BaseEvent) -> str:
    # Нет проверки идемпотентности по idempotency_key!
    # События всегда публикуются в Redis Streams
```

**Проблема:**
- `idempotency_key` используется только для логирования
- Нет проверки, не было ли уже опубликовано событие с таким ключом
- Дубликаты событий попадают в очередь

---

## Context7 Best Practices

### 1. Идемпотентность на уровне API

**Рекомендация:** Использовать `idempotency_key` для предотвращения дубликатов:

```python
# В generate_digest_for_user
idempotency_key = f"digest:{user_id}:{today.isoformat()}:{trigger}"

# Проверка в Redis перед созданием
if await redis_client.exists(f"idempotency:{idempotency_key}"):
    # Возвращаем существующий дайджест
    return existing
```

### 2. Защита от двойного нажатия

**Рекомендация:** Использовать FSM (Finite State Machine) или Redis lock:

```python
# В callback_digest_generate
lock_key = f"digest:lock:{user_id}"
if await redis_client.set(lock_key, "1", nx=True, ex=60):
    try:
        # Обработка запроса
    finally:
        await redis_client.delete(lock_key)
else:
    await callback.answer("⏳ Дайджест уже генерируется, подождите...")
```

### 3. Уникальный индекс в БД

**Рекомендация:** Добавить уникальный индекс для предотвращения дубликатов:

```sql
CREATE UNIQUE INDEX ux_digest_history_user_date 
ON digest_history(user_id, digest_date) 
WHERE status IN ('pending', 'processing');
```

---

## Решение

### 1. Добавить защиту от двойного нажатия

Изменить `api/bot/handlers/digest_handlers.py`:

```python
async def callback_digest_generate(callback: CallbackQuery):
    """Сгенерировать дайджест немедленно."""
    user_id = await _get_user_id(callback.from_user.id)
    if not user_id:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Context7: Защита от двойного нажатия через Redis lock
    import redis.asyncio as redis
    redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
    lock_key = f"digest:lock:{user_id}"
    
    # Пытаемся получить lock (TTL 5 минут)
    lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=300)
    if not lock_acquired:
        await callback.answer("⏳ Дайджест уже генерируется, подождите...", show_alert=True)
        await redis_client.close()
        return
    
    try:
        # Показываем индикатор загрузки
        await callback.answer("⏳ Генерирую дайджест...")
        
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{API_BASE}/api/digest/generate/{user_id}")
            
            # ... остальная логика ...
            
    finally:
        # Освобождаем lock
        await redis_client.delete(lock_key)
        await redis_client.close()
```

### 2. Улучшить проверку существующего дайджеста

Изменить `api/tasks/scheduler_tasks.py:generate_digest_for_user`:

```python
if existing:
    if existing.status in {"scheduled", "pending", "processing"}:
        if force_new:
            # Context7: При manual trigger перезаписываем только если прошло достаточно времени
            # Предотвращаем дубликаты при быстром двойном нажатии
            age_seconds = (datetime.now(timezone.utc) - existing.created_at).total_seconds()
            if age_seconds < 30:  # Меньше 30 секунд - игнорируем второй запрос
                logger.warning(
                    "Duplicate digest request ignored (too recent)",
                    user_id=user_id,
                    digest_id=str(existing.id),
                    age_seconds=age_seconds
                )
                return existing
            
            logger.warning(
                "Manual trigger overriding in-flight digest",
                user_id=user_id,
                digest_id=str(existing.id),
                status=existing.status,
            )
            existing.status = "failed"
            existing.sent_at = None
            db.commit()
            existing = None
        else:
            logger.debug(
                "Digest already scheduled or in progress",
                user_id=user_id,
                digest_id=str(existing.id),
                status=existing.status
            )
            return existing
```

### 3. Добавить идемпотентность на уровне событий

Изменить `api/worker/event_bus.py:EventPublisher`:

```python
async def publish_event(self, stream_name: str, event: BaseEvent) -> str:
    """Публикация события с проверкой идемпотентности."""
    
    # Context7: Проверка идемпотентности через Redis
    if hasattr(event, 'idempotency_key') and event.idempotency_key:
        idempotency_key = f"event:idempotency:{stream_name}:{event.idempotency_key}"
        
        # Проверяем, не было ли уже опубликовано событие
        existing_msg_id = await self.client.client.get(idempotency_key)
        if existing_msg_id:
            logger.debug(
                "Duplicate event prevented by idempotency",
                stream_name=stream_name,
                idempotency_key=event.idempotency_key,
                existing_msg_id=existing_msg_id
            )
            return existing_msg_id.decode() if isinstance(existing_msg_id, bytes) else existing_msg_id
    
    # Публикация события
    msg_id = await self.publish_json(stream_name, event)
    
    # Сохраняем idempotency_key для предотвращения дубликатов (TTL 24 часа)
    if hasattr(event, 'idempotency_key') and event.idempotency_key:
        await self.client.client.setex(
            idempotency_key,
            86400,  # 24 часа
            msg_id
        )
    
    return msg_id
```

---

## Checks

### Проверка текущего состояния

```bash
# Проверка дайджестов пользователя
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT id, status, created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow' as created_msk
FROM digest_history 
WHERE user_id = '6af5e7d3-e736-46da-bfc1-42786f2ab1c0'::uuid 
AND digest_date = '2025-11-25'
ORDER BY created_at DESC;
"

# Проверка событий в Redis Streams
docker exec telegram-assistant-redis-1 redis-cli XINFO STREAM digests.generate
```

### После исправления

1. Проверить, что двойное нажатие блокируется
2. Проверить, что дубликаты событий не попадают в очередь
3. Проверить, что дайджесты обрабатываются корректно

---

## Impact / Rollback

### Impact

- **Дубликаты запросов:** Пользователь видит два сообщения с одинаковым ID
- **Статус в БД:** Дайджест упал в статус "failed" (нужно проверить причину)
- **Обработка:** Воркер может обрабатывать дубликаты событий

### Rollback

Если нужно откатить изменения:
1. Изменения в обработчике кнопки не критичны
2. Изменения в логике проверки можно откатить через git
3. Идемпотентность на уровне событий - опциональная оптимизация

---

## Рекомендации

1. **Немедленно:** Добавить защиту от двойного нажатия в `callback_digest_generate`
2. **Краткосрочно:** Улучшить проверку существующего дайджеста с учетом времени
3. **Долгосрочно:** Добавить идемпотентность на уровне событий через Redis

