# Руководство по миграции к UnifiedSessionManager

## Обзор

Данное руководство описывает процесс миграции от legacy системы управления сессиями к `UnifiedSessionManager` с Context7 best practices.

## Предварительные требования

### 1. Environment Variables

Убедитесь, что установлены следующие переменные окружения:

```bash
# Supabase
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_ANON_KEY="your-anon-key"
export SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"

# Telegram API
export TELEGRAM_API_ID_MASTER="your-api-id"
export TELEGRAM_API_HASH_MASTER="your-api-hash"

# Redis
export REDIS_URL="redis://localhost:6379"

# Feature flags
export FEATURE_UNIFIED_SESSION_MANAGER="true"

# Session storage
export SESSIONS_BASE_PATH="/app/sessions"
```

### 2. Зависимости

Установите необходимые зависимости:

```bash
pip install -r requirements.txt
```

## Пошаговая миграция

### Шаг 1: Применение миграций БД

Создайте таблицу `telegram_sessions` в Supabase:

```bash
# Применение миграции
python scripts/apply_migrations.py --project-id your-project-id

# Или вручную через Supabase Dashboard
# Выполните SQL из migrations/001_create_telegram_sessions_table.sql
```

### Шаг 2: Создание каталога сессий

```bash
# Создайте каталог для сессий
sudo mkdir -p /app/sessions
sudo chmod 700 /app/sessions
sudo chown $USER:$USER /app/sessions
```

### Шаг 3: Миграция существующих сессий

```bash
# Dry run - посмотреть что будет мигрировано
python scripts/migrate_sessions.py \
  --sessions-dir /app/sessions \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  --dry-run

# Реальная миграция
python scripts/migrate_sessions.py \
  --sessions-dir /app/sessions \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379"
```

### Шаг 4: Включение фичефлага

```bash
# Включите UnifiedSessionManager
export FEATURE_UNIFIED_SESSION_MANAGER="true"

# Перезапустите сервис
docker compose restart telethon-ingest
```

### Шаг 5: Проверка работоспособности

```bash
# Проверьте статус системы
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  health

# Список сессий
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  list

# Статус конкретной сессии
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  status --tenant-id tenant1 --app-id app1
```

## Тестирование

### Unit тесты

```bash
# Запуск unit тестов
pytest tests/test_unified_session_manager.py -v

# С покрытием
pytest tests/test_unified_session_manager.py --cov=services.session --cov-report=html
```

### Integration тесты

```bash
# Запуск integration тестов
pytest tests/integration/test_miniapp_api.py -v
```

### E2E тестирование

```bash
# Тест QR авторизации
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  qr-login --tenant-id tenant1 --app-id app1

# После сканирования QR
python scripts/session_cli.py \
  --database-url "postgresql://user:pass@localhost/db" \
  --redis-url "redis://localhost:6379" \
  finalize --ticket your-ticket
```

## Мониторинг

### Prometheus метрики

```bash
# Проверьте метрики
curl http://localhost:9090/metrics | grep session_
```

### Grafana Dashboard

```bash
# Импортируйте dashboard
curl -X POST http://grafana:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @grafana/dashboards/session_manager.json
```

### Логи

```bash
# Проверьте логи
docker logs telethon-ingest | grep "UnifiedSessionManager"
```

## Troubleshooting

### Проблема: Сессии не мигрируются

**Решение:**
1. Проверьте права доступа к каталогу `/app/sessions`
2. Убедитесь, что Redis и БД доступны
3. Проверьте логи миграции

```bash
# Проверка прав
ls -la /app/sessions

# Проверка подключений
python -c "import redis; r=redis.Redis.from_url('redis://localhost:6379'); print(r.ping())"
python -c "import psycopg2; conn=psycopg2.connect('postgresql://user:pass@localhost/db'); print('OK')"
```

### Проблема: RLS политики блокируют доступ

**Решение:**
1. Убедитесь, что используется service role для внутренних операций
2. Проверьте JWT claims в RLS политиках

```sql
-- Проверка RLS
SELECT * FROM pg_policies WHERE tablename = 'telegram_sessions';

-- Тест с service role
SET ROLE service_role;
SELECT * FROM telegram_sessions LIMIT 1;
```

### Проблема: QR авторизация не работает

**Решение:**
1. Проверьте HMAC secret в конфигурации
2. Убедитесь, что timestamp в пределах tolerance
3. Проверьте Redis для QR tickets

```bash
# Проверка QR tickets в Redis
redis-cli KEYS "qr:*"

# Проверка конфигурации
python -c "from config import settings; print(settings.miniapp_hmac_secret)"
```

### Проблема: Fingerprint mismatch

**Решение:**
1. Проверьте целостность файлов сессий
2. Пересчитайте fingerprint

```bash
# Проверка fingerprint
python -c "
from services.session.session_fingerprint import SessionFingerprint
fp = SessionFingerprint.compute_fingerprint('/app/sessions/tenant1/app1.session')
print(fp.to_string() if fp else 'Failed')
"
```

## Rollback

### Откат к legacy системе

```bash
# 1. Отключите фичефлаг
export FEATURE_UNIFIED_SESSION_MANAGER="false"

# 2. Перезапустите сервис
docker compose restart telethon-ingest

# 3. Восстановите сессии из бэкапа (если нужно)
# TODO: Реализовать восстановление из Supabase Storage
```

### Откат миграций БД

```sql
-- Удаление таблицы (ОСТОРОЖНО!)
DROP TABLE IF EXISTS public.telegram_sessions CASCADE;

-- Удаление политик
DROP POLICY IF EXISTS tenant_isolation_anon ON public.telegram_sessions;
DROP POLICY IF EXISTS tenant_isolation_authenticated ON public.telegram_sessions;
DROP POLICY IF EXISTS service_role_bypass ON public.telegram_sessions;
```

## Проверочный список

- [ ] Environment variables настроены
- [ ] Миграции БД применены
- [ ] Каталог сессий создан с правильными правами
- [ ] Существующие сессии мигрированы
- [ ] Фичефлаг включен
- [ ] Unit тесты проходят
- [ ] Integration тесты проходят
- [ ] E2E тестирование выполнено
- [ ] Мониторинг настроен
- [ ] Логи проверены
- [ ] Rollback план готов

## Поддержка

При возникновении проблем:

1. Проверьте логи: `docker logs telethon-ingest`
2. Проверьте метрики: `curl http://localhost:9090/metrics`
3. Проверьте статус: `python scripts/session_cli.py health`
4. Создайте issue с логами и метриками

## Дополнительные ресурсы

- [Context7 Best Practices](docs/CONTEXT7_BEST_PRACTICES.md)
- [API Reference](docs/API_REFERENCE.md)
- [State Machine Diagram](docs/state_machine_diagram.md)
- [Grafana Dashboard](grafana/dashboards/session_manager.json)
- [Prometheus Alerts](grafana/alerts/session_manager.yml)