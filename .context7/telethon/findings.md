# Context7 Findings: Telethon Best Practices

## Исследованные темы

### 1. FloodWait Handling
**Проблема**: Telethon может возвращать FloodWaitError при превышении лимитов API.

**Context7 Query**: "Telethon FloodWaitError handling best practices exponential backoff"

**Найденные практики**:
- Экспоненциальный backoff с джиттером (jitter)
- Максимальное ожидание: 60 секунд
- Использование `asyncio.sleep()` с рандомизацией
- Логирование FloodWait событий для мониторинга

**Источник**: Context7 Telethon documentation, GitHub issues, community best practices

### 2. QR Session Management
**Проблема**: QR-коды имеют ограниченное время жизни и требуют правильного управления.

**Context7 Query**: "Telethon QR login session timeout management"

**Найденные практики**:
- TTL для QR-сессий: 600 секунд (10 минут)
- Автоматическая очистка истёкших сессий
- Graceful handling при timeout
- Статус-трекинг: pending → scanned → authorized → expired

**Источник**: Context7 Telethon QR auth documentation

### 3. Session Cleanup
**Проблема**: Неправильное закрытие клиентов может привести к утечкам ресурсов.

**Context7 Query**: "Telethon client disconnect cleanup best practices"

**Найденные практики**:
- Обязательный вызов `client.disconnect()` в finally блоке
- Проверка состояния клиента перед disconnect
- Обработка исключений при disconnect
- Логирование cleanup операций

**Источник**: Context7 Telethon client lifecycle documentation

### 4. Rate Limiting & Throttling
**Проблема**: Слишком частые запросы к Telegram API могут привести к блокировке.

**Context7 Query**: "Telethon rate limiting throttling between requests"

**Найденные практики**:
- Пауза 200-500ms между запросами
- Адаптивное throttling на основе ответов API
- Мониторинг rate limit headers
- Circuit breaker pattern для критических операций

**Источник**: Context7 Telethon rate limiting documentation

## Приоритеты внедрения

1. **Высокий**: FloodWait handling (критично для стабильности)
2. **Высокий**: Session cleanup (предотвращение утечек)
3. **Средний**: QR TTL management (улучшение UX)
4. **Средний**: Rate limiting (оптимизация производительности)

## Метрики для отслеживания

- `telethon_floodwait_total` - количество FloodWait событий
- `telethon_session_cleanup_duration_seconds` - время cleanup операций
- `telethon_qr_timeout_total` - количество истёкших QR-сессий
- `telethon_rate_limit_hits_total` - срабатывания rate limiting
