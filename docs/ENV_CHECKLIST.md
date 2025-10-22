# Контрольный список окружения Telegram Assistant

## Требования к версиям

### Docker & Docker Compose
- **Docker Engine**: 24.0+ (рекомендуется 24.0.7+)
- **Docker Compose**: 2.20+ (рекомендуется 2.21.0+)
- **Проверка**: `docker --version && docker compose version`

### Python
- **Python**: 3.11+ (рекомендуется 3.11.7+)
- **Проверка**: `python --version`

### Node.js (для Mini App)
- **Node.js**: 18+ (рекомендуется 20.10.0+)
- **Проверка**: `node --version`

## Переменные окружения (.env)

### Обязательные переменные
```bash
# Telegram API
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash

# Шифрование (≥32 байт)
SESSION_CRYPTO_KEY=your_32_byte_encryption_key_here

# Базы данных
REDIS_URL=redis://redis:6379/0
POSTGRES_URL=postgresql://postgres:password@supabase-db:5432/telegram_assistant
QDRANT_URL=http://qdrant:6333

# Caddy
CADDY_DOMAINS=your-domain.com,api.your-domain.com
CADDY_EMAIL=admin@your-domain.com

# CORS
CORS_ALLOWED_ORIGINS=https://your-domain.com,https://api.your-domain.com

# Rate Limits
RATE_LIMIT_QR_INIT=5:60
RATE_LIMIT_QR_STATUS=30:60
```

### Опциональные переменные
```bash
# Логирование
LOG_LEVEL=INFO
ENVIRONMENT=production

# JWT
JWT_SECRET=your_jwt_secret_key
JWT_TTL_SECONDS=1800

# QR Session TTL
QR_TTL_SECONDS=1200

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token
BOT_WEBHOOK_SECRET=your_webhook_secret
BOT_PUBLIC_URL=https://your-domain.com

# Grafana
GF_SECURITY_ADMIN_PASSWORD=admin_password

# Tenant
DEFAULT_TENANT_ID=default-tenant-uuid
```

## Сети и Volumes

### Docker Networks
- **telegram-network**: bridge network для всех сервисов

### Volumes (персистентные данные)
- **telethon-ingest/sessions**: Telegram сессии Telethon
- **qdrant/storage**: Векторные данные Qdrant
- **redis/data**: Кэш и сессии Redis
- **grafana/provisioning**: Дашборды и конфигурация Grafana
- **supabase_data**: Данные PostgreSQL
- **caddy_data**: Сертификаты Caddy
- **caddy_config**: Конфигурация Caddy

## Порты

### Внешние порты (Caddy)
- **80**: HTTP (редирект на HTTPS)
- **443**: HTTPS (основной трафик)

### Внутренние порты сервисов
- **api**: 8000 (FastAPI)
- **telethon-ingest**: 8011 (health check)
- **redis**: 6379
- **supabase-db**: 5432
- **qdrant**: 6333
- **grafana**: 3000
- **kong**: 8000 (API Gateway)
- **rest**: 3000 (PostgREST)
- **auth**: 9999 (GoTrue)
- **meta**: 8080 (Postgres Meta)
- **realtime**: 4000
- **storage**: 5000

## Health Checks

### Проверка сервисов
```bash
# API
curl -f http://localhost:8000/health

# Telethon Ingest
curl -f http://localhost:8011/health

# Redis
redis-cli -h localhost -p 6379 ping

# PostgreSQL
psql -h localhost -p 5432 -U postgres -d telegram_assistant -c '\l'

# Qdrant
curl -f http://localhost:6333/health

# Grafana
curl -f http://localhost:3000/api/health
```

### Smoke-тесты
```bash
# Redis подключение
redis-cli -h localhost -p 6379 ping
# Ожидаемый результат: PONG

# PostgreSQL подключение
psql -h localhost -p 5432 -U postgres -d telegram_assistant -c '\l'
# Ожидаемый результат: список баз данных

# DNS резолв доменов Caddy
nslookup your-domain.com
# Ожидаемый результат: IP адрес сервера
```

## Профили Docker Compose

### Core (основные сервисы)
```bash
docker compose --profile core up -d
```
Включает: api, telethon-ingest, redis, supabase-db, qdrant, caddy

### Analytics (аналитика)
```bash
docker compose --profile analytics up -d
```
Дополнительно: grafana

### RAG (векторный поиск)
```bash
docker compose --profile rag up -d
```
Дополнительно: расширенная конфигурация Qdrant

## Диагностика

### Быстрая проверка
```bash
# Запуск диагностики
make diag
# или
bash scripts/diagnostic.sh
```

### Мониторинг логов
```bash
# Просмотр логов всех сервисов
make monitor
# или
bash scripts/monitor.sh
```

### Проверка метрик
```bash
# Prometheus метрики API
curl http://localhost:8000/metrics

# Prometheus метрики Telethon
curl http://localhost:8011/metrics
```

## Устранение неполадок

### Частые проблемы

1. **Порт уже используется**
   ```bash
   # Проверить занятые порты
   netstat -tulpn | grep :80
   netstat -tulpn | grep :443
   ```

2. **Redis недоступен**
   ```bash
   # Проверить статус Redis
   docker compose ps redis
   docker compose logs redis
   ```

3. **PostgreSQL подключение**
   ```bash
   # Проверить статус БД
   docker compose ps supabase-db
   docker compose logs supabase-db
   ```

4. **Telethon сессии**
   ```bash
   # Проверить volume сессий
   ls -la telethon-ingest/sessions/
   ```

5. **Caddy сертификаты**
   ```bash
   # Проверить сертификаты
   docker compose exec caddy cat /data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/your-domain.com/your-domain.com.crt
   ```

### Логи для диагностики
```bash
# Все сервисы
docker compose logs

# Конкретный сервис
docker compose logs api
docker compose logs telethon-ingest
docker compose logs redis
```

## Производительность

### Рекомендуемые ресурсы
- **CPU**: 2+ cores
- **RAM**: 4+ GB
- **Диск**: 20+ GB SSD
- **Сеть**: 100+ Mbps

### Мониторинг ресурсов
```bash
# Использование ресурсов контейнерами
docker stats

# Использование диска
df -h
du -sh telethon-ingest/sessions/
du -sh qdrant/storage/
```

## Безопасность

### Проверка безопасности
- Все сервисы работают в изолированной сети
- HTTPS принудительно через Caddy
- JWT токены с TTL
- Rate limiting включен
- Логи не содержат секретов

### Резервное копирование
```bash
# Бэкап PostgreSQL
docker compose exec supabase-db pg_dump -U postgres telegram_assistant > backup.sql

# Бэкап Redis
docker compose exec redis redis-cli BGSAVE
```

## Обновление

### Обновление образов
```bash
# Обновить все образы
docker compose pull

# Перезапустить с новыми образами
docker compose up -d
```

### Миграции БД
```bash
# Запуск миграций
docker compose exec api alembic upgrade head
```
