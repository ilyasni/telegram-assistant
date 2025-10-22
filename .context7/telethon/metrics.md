# Context7 Metrics: Telethon Monitoring

## Prometheus метрики

### FloodWait метрики
```python
from prometheus_client import Counter, Histogram

# Счетчики FloodWait событий
FLOODWAIT_TOTAL = Counter(
    'telethon_floodwait_total', 
    'Total FloodWait events', 
    ['reason', 'seconds']
)

# Гистограмма времени ожидания
FLOODWAIT_DURATION = Histogram(
    'telethon_floodwait_duration_seconds',
    'FloodWait wait duration',
    ['reason']
)
```

### Session метрики
```python
# Счетчики сессий
SESSION_CLEANUP_TOTAL = Counter(
    'telethon_session_cleanup_total',
    'Total session cleanup operations',
    ['status']  # success, failed
)

# Время cleanup операций
SESSION_CLEANUP_DURATION = Histogram(
    'telethon_session_cleanup_duration_seconds',
    'Session cleanup duration'
)
```

### QR метрики
```python
# QR сессии
QR_SESSION_TOTAL = Counter(
    'telethon_qr_session_total',
    'Total QR sessions',
    ['status']  # created, expired, authorized, failed
)

# QR timeout
QR_TIMEOUT_TOTAL = Counter(
    'telethon_qr_timeout_total',
    'QR session timeouts'
)
```

### Rate Limiting метрики
```python
# Rate limit hits
RATE_LIMIT_HITS = Counter(
    'telethon_rate_limit_hits_total',
    'Rate limit hits',
    ['endpoint']
)

# Throttling delays
THROTTLING_DELAY = Histogram(
    'telethon_throttling_delay_seconds',
    'Request throttling delay'
)
```

## Grafana дашборд

### Панели
1. **FloodWait Events** - график FloodWait событий по времени
2. **Session Cleanup Duration** - гистограмма времени cleanup
3. **QR Session Funnel** - воронка QR сессий
4. **Rate Limiting** - график rate limit hits

### Алерты
```yaml
# FloodWait alert
- alert: HighFloodWaitRate
  expr: rate(telethon_floodwait_total[5m]) > 0.1
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "High FloodWait rate detected"

# Session cleanup alert
- alert: SlowSessionCleanup
  expr: histogram_quantile(0.95, telethon_session_cleanup_duration_seconds) > 5
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Session cleanup is taking too long"

# QR timeout alert
- alert: HighQRTimeoutRate
  expr: rate(telethon_qr_timeout_total[5m]) > 0.2
  for: 3m
  labels:
    severity: warning
  annotations:
    summary: "High QR timeout rate"
```

## SLO цели

### Доступность
- **Цель**: 99.9% uptime для QR авторизации
- **Метрика**: `rate(telethon_qr_session_total{status="authorized"}[5m]) / rate(telethon_qr_session_total[5m])`

### Производительность
- **Цель**: P95 session cleanup < 3 секунды
- **Метрика**: `histogram_quantile(0.95, telethon_session_cleanup_duration_seconds)`

### Надежность
- **Цель**: FloodWait rate < 5% от всех запросов
- **Метрика**: `rate(telethon_floodwait_total[5m]) / rate(telethon_requests_total[5m])`

## Логирование

### Структурированные логи
```python
# FloodWait событие
logger.warning(
    "FloodWait detected",
    seconds=e.seconds,
    delay=delay,
    tenant_id=tenant_id,
    operation="qr_login"
)

# Session cleanup
logger.info(
    "Session cleanup completed",
    duration=cleanup_duration,
    status="success",
    tenant_id=tenant_id
)

# QR timeout
logger.warning(
    "QR session timeout",
    tenant_id=tenant_id,
    session_id=session_id,
    duration=session_duration
)
```

### Лог-агрегация
- **Elasticsearch**: структурированные логи
- **Kibana**: поиск и анализ
- **Logstash**: парсинг и обогащение

## Трейсинг

### OpenTelemetry spans
```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@tracer.start_as_current_span("telethon_qr_login")
async def qr_login():
    with tracer.start_as_current_span("telethon_connect"):
        await client.connect()
    
    with tracer.start_as_current_span("telethon_qr_generate"):
        qr_login = await client.qr_login()
    
    with tracer.start_as_current_span("telethon_qr_wait"):
        await qr_login.wait()
```

### Трейс-атрибуты
- `telethon.operation` - тип операции
- `telethon.tenant_id` - ID арендатора
- `telethon.session_id` - ID сессии
- `telethon.floodwait_seconds` - время FloodWait
