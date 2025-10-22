# QR-авторизация — типовые проблемы

## Проблема: QR не генерируется (статус всегда pending)

**Симптомы:**
- `/qr/status` возвращает `status: pending` без `qr_url`
- Логи telethon-ingest: `QR session key created` но нет `QR published`

**Причины:**
1. telethon-ingest не запущен или не подключён к Redis
2. MASTER_API_ID/MASTER_API_HASH неверные
3. FloodWait от Telegram API

**Решение:**
```bash
# Проверка telethon-ingest
docker logs telegram-assistant-telethon-ingest-1 | grep "QR auth loop started"

# Проверка переменных
docker exec telegram-assistant-telethon-ingest-1 env | grep MASTER_API

# FloodWait — подождать время из логов
grep -i "floodwait" logs/telethon-ingest.log
```

## Проблема: Ownership mismatch после скана

**Симптомы:**
- QR отсканирован, но статус `failed` с `reason: ownership_mismatch`

**Причина:**
- Пользователь сканирует QR с другого Telegram-аккаунта

**Решение:**
- Проверить, что `tenant_id` соответствует `telegram_id` пользователя
- Логи покажут: `expected=123, actual=456`

## Проблема: 2FA required

**Симптомы:**
- QR отсканирован, но статус `failed` с `reason: password_required`

**Причина:**
- У пользователя включена двухфакторная аутентификация

**Решение:**
- Пользователь должен отключить 2FA или использовать другой метод входа
- В будущем: добавить поддержку 2FA через Mini App

## Проблема: FloodWait

**Симптомы:**
- Логи: `FloodWait during QR login, seconds=3600`
- Статус: `failed` с `reason: flood_wait_3600s`

**Причина:**
- Превышение лимитов Telegram API

**Решение:**
- Подождать указанное время (обычно 1 час)
- Уменьшить частоту запросов QR-логина
- Проверить rate limiting настройки

## Проблема: Session expired

**Симптомы:**
- QR не отсканирован в течение 10 минут
- Статус: `expired`

**Причина:**
- Пользователь не успел отсканировать QR

**Решение:**
- Запустить новую QR-сессию
- Увеличить TTL в настройках (если нужно)

## Диагностические команды

```bash
# Полная диагностика
./scripts/diagnose_qr_sessions.sh

# Проверка Redis
docker exec telegram-assistant-redis-1 redis-cli ping

# Активные QR-сессии
docker exec telegram-assistant-redis-1 redis-cli --scan --pattern "tg:qr:session:*"

# Health check
curl -s http://localhost:8010/health/auth | jq .

# Логи telethon-ingest
docker logs telegram-assistant-telethon-ingest-1 | grep -i qr

# Логи API
docker logs telegram-assistant-api-1 | grep -i qr
```

## Мониторинг

**Метрики Prometheus:**
- `auth_qr_start_total` — количество стартов QR-сессий
- `auth_qr_success_total` — успешные авторизации
- `auth_qr_fail_total` — провалы (по причинам)
- `auth_qr_expired_total` — истёкшие сессии
- `auth_qr_ownership_fail_total` — провалы ownership check
- `auth_qr_2fa_required_total` — требующие 2FA

**SLO:**
- Успех QR-логина ≥ 95%
- P95 длительность ≤ 30 сек

**Алерты:**
- Success rate < 95% в течение 5 минут
- P95 duration > 30 секунд в течение 3 минут
- FloodWait > 1 часа
