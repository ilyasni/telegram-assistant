# Context7 Actions: Telethon Improvements

## Планируемые изменения

### 1. FloodWait Handling
**Файл**: `telethon-ingest/services/qr_auth.py`
**Маркер**: `# [C7-ID: telethon-floodwait-001]`

**Изменения**:
```python
import random
import asyncio

async def handle_floodwait(e: FloodWaitError):
    """Context7 best practice: экспоненциальный backoff с джиттером"""
    # Максимальное ожидание: 60 секунд
    max_wait = min(e.seconds, 60)
    
    # Экспоненциальный backoff с джиттером
    base_delay = min(max_wait, 2 ** min(e.seconds // 10, 6))
    jitter = random.uniform(0.1, 0.3) * base_delay
    delay = base_delay + jitter
    
    logger.warning("FloodWait detected", seconds=e.seconds, delay=delay)
    await asyncio.sleep(delay)
```

### 2. QR Session TTL
**Файл**: `telethon-ingest/services/qr_auth.py`
**Маркер**: `# [C7-ID: telethon-qr-ttl-002]`

**Изменения**:
```python
# Context7 best practice: TTL для QR-сессий
QR_TTL_SECONDS = int(os.getenv("QR_TTL_SECONDS", "600"))  # 10 минут

# Обновление статусов в Redis с SETEX
redis_client.setex(f"tg:qr:session:{tenant_id}", QR_TTL_SECONDS, session_data)
```

### 3. Session Cleanup
**Файл**: `telethon-ingest/services/qr_auth.py`
**Маркер**: `# [C7-ID: telethon-cleanup-003]`

**Изменения**:
```python
# Context7 best practice: гарантированная остановка клиента
try:
    # ... основная логика ...
finally:
    try:
        if client.is_connected():
            await client.disconnect()
            logger.debug("Telethon client disconnected")
    except Exception as e:
        logger.warning("Error during client disconnect", error=str(e))
```

### 4. Request Throttling
**Файл**: `telethon-ingest/services/qr_auth.py`
**Маркер**: `# [C7-ID: telethon-throttle-004]`

**Изменения**:
```python
# Context7 best practice: пауза между запросами
async def throttled_request():
    """Запрос с throttling"""
    await asyncio.sleep(random.uniform(0.2, 0.5))  # 200-500ms
    # ... выполнение запроса ...
```

## Порядок внедрения

1. **Этап 1**: FloodWait handling (критично)
2. **Этап 2**: Session cleanup (стабильность)
3. **Этап 3**: QR TTL management (UX)
4. **Этап 4**: Request throttling (оптимизация)

## Тестирование

### Unit тесты
- Тест FloodWait backoff логики
- Тест session cleanup в различных сценариях
- Тест QR TTL expiration

### Integration тесты
- E2E тест QR flow с FloodWait симуляцией
- Тест cleanup при неожиданном завершении
- Тест rate limiting при высокой нагрузке

## Мониторинг

### Метрики
- `telethon_floodwait_total{reason}` - FloodWait события
- `telethon_session_cleanup_duration_seconds` - время cleanup
- `telethon_qr_expired_total` - истёкшие QR-сессии
- `telethon_rate_limit_hits_total` - rate limit срабатывания

### Алерты
- FloodWait > 10 событий в минуту
- Session cleanup > 5 секунд
- QR timeout rate > 20%
- Rate limit hits > 50 в час
