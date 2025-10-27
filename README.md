# Telegram Assistant

Telegram bot с QR-авторизацией для управления каналами и контентом.

## 🚀 Быстрый старт

### Требования
- Docker & Docker Compose
- Telegram Bot Token
- PostgreSQL
- Redis

### Установка
```bash
git clone <repository>
cd telegram-assistant
cp .env.example .env
# Настройте переменные в .env
docker compose up -d
```

### Переменные окружения
```bash
# Telegram Bot
BOT_TOKEN=your_bot_token
BOT_WEBHOOK_SECRET=your_webhook_secret
BOT_PUBLIC_URL=https://your-domain.com

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/telegram_assistant

# Redis
REDIS_URL=redis://localhost:6379

# JWT
JWT_SECRET=your_jwt_secret
```

## 🏗️ Архитектура

### Сервисы
- **api** - FastAPI приложение с ботом и API
- **telethon-ingest** - QR-авторизация через Telethon
- **worker** - Обработка постов и тегирование
- **redis** - Кеширование и сессии
- **postgres** - Основная база данных
- **caddy** - Reverse proxy

### Компоненты
- **Telegram Bot** - обработка команд и webhook
- **Mini App** - QR-авторизация в Telegram
- **Worker Tasks** - тегирование, обогащение, индексация постов

## Retention & Cleanup

Проект использует автоматические политики очистки данных:

- **telegram_auth_events**: 90 дней (псевдонимизация через 30 дней)
- **outbox_events**: 7 дней
- **posts**: 90 дней (настраивается)

Управление через pg_cron в Supabase. Подробности в `docs/RETENTION_POLICY.md`.

Проверка статистики:
```sql
SELECT * FROM get_telegram_auth_events_stats();
```
- **QR Auth Service** - Telethon интеграция
- **API Endpoints** - REST API для фронтенда

## 📱 Mini App

### Функциональность
- ✅ **QR-авторизация** - вход через QR-код
- ✅ **Theme support** - темная/светлая тема
- ✅ **Responsive design** - адаптивный дизайн
- ✅ **Error handling** - обработка ошибок
- ✅ **Session management** - управление сессиями

### Технологии
- **HTML5** - семантическая разметка
- **CSS3** - современные стили и анимации
- **Vanilla JS** - без фреймворков
- **Telegram WebApp SDK** - интеграция с Telegram

## 🔐 QR Authentication

### Flow
1. **Пользователь** нажимает /start в боте
2. **Бот** открывает Mini App
3. **Mini App** создает QR-сессию
4. **Пользователь** сканирует QR-код
5. **Telethon** обрабатывает авторизацию
6. **Система** сохраняет сессию

### Безопасность
- **JWT токены** для сессий
- **Шифрование** StringSession
- **Ownership check** - проверка владельца
- **Rate limiting** - защита от спама

## 🏷️ Тегирование постов

### Пайплайн тегирования

Пайплайн тегирования использует строгий промпт для извлечения тегов-подстрок из текста:

1. **tagging_task** — генерация тегов через GigaChat с TTL-LRU кешем
2. **tag_persistence_task** — идемпотентное сохранение в `post_enrichment`
3. **Анти-зацикливание** — локальный кеш (10K записей, TTL 24ч) + Redis-дедупликация

**Правила тегирования:**
- Теги должны быть подстроками текста (без синонимов)
- Запрещены мета-теги ("экономика", "инвестиции")
- Формат: JSON-массив строк `["тег1", "тег2"]`

**Схема пайплайна:**
```
telethon-ingest → posts.parsed
         ↓
tagging_task (GigaChat) — строгий промпт, TTL-LRU + Redis дедуп
         ↓
posts.tagged (v1: {post_id, tags[], tags_hash})
         ↓
tag_persistence_task → UPSERT post_enrichment(post_id, kind='tags')
```

**Мониторинг:**
```bash
# Проверить метрики
curl -s localhost:8000/metrics | grep -E "tags_persisted_total|tagging_cache_size"

# Проверить теги в БД
psql -U postgres -d telegram_assistant -c \
  "SELECT post_id, kind, tags, enrichment_provider, updated_at FROM post_enrichment WHERE kind='tags' ORDER BY updated_at DESC LIMIT 10;"

# Проверить события в Redis
redis-cli XINFO STREAM posts.tagged
redis-cli XLEN posts.tagged
```

## 🛠️ Разработка

### Структура проекта
```
telegram-assistant/
├── api/                    # FastAPI приложение
│   ├── routers/           # API endpoints
│   ├── bot/               # Telegram bot
│   ├── models/            # Database models
│   └── main.py            # FastAPI app
├── telethon-ingest/       # QR auth service
│   ├── services/          # Business logic
│   └── main.py            # Service entry point
├── worker/                # Worker tasks
│   ├── tasks/             # Task implementations
│   │   ├── tagging_task.py
│   │   ├── tag_persistence_task.py
│   │   └── enrichment_task.py
│   ├── ai_providers/      # AI integrations
│   └── prompts/           # Centralized prompts
├── webapp/                # Mini App
│   └── index.html         # Mini App UI
├── docs/                  # Документация
│   ├── ENV_CHECKLIST.md   # Контрольный список окружения
│   ├── API_CONTRACTS.md   # API контракты
│   └── TESTING_GUIDE.md   # Руководство по тестированию
├── scripts/               # Утилиты
│   ├── diagnostic.sh      # Диагностика системы
│   ├── monitor.sh         # Мониторинг логов
│   └── invites_cli.py     # CLI для инвайт-кодов
├── docker-compose.yml     # Docker services
├── Makefile              # Автоматизация задач
└── Caddyfile             # Reverse proxy config
```

### Команды разработки
```bash
# Диагностика системы
make diag
# или
bash scripts/diagnostic.sh

# Мониторинг логов
make monitor
# или
bash scripts/monitor.sh

# Запуск в dev режиме
make dev
# или
docker compose --profile core up -d

# Просмотр логов конкретного сервиса
make logs-api
make logs-telethon
make logs-redis

# Пересборка сервиса
docker compose build api
docker compose up -d api

# Остановка
make down
# или
docker compose down
```

### Управление инвайт-кодами
```bash
# Создание инвайта
python scripts/invites_cli.py create --tenant <uuid> --role user --limit 10 --expires 2025-12-31T23:59:59Z

# Отзыв инвайта
python scripts/invites_cli.py revoke --code ABC123XYZ456

# Список инвайтов
python scripts/invites_cli.py list --tenant <uuid> --status active

# Информация об инвайте
python scripts/invites_cli.py get --code ABC123XYZ456
```

## 🔌 API

### Документация
- **Swagger UI**: `https://your-domain.com/docs`
- **ReDoc**: `https://your-domain.com/redoc`
- **API Контракты**: [docs/API_CONTRACTS.md](docs/API_CONTRACTS.md)

### Основные endpoints
- **QR Авторизация**: `/tg/qr/*` (алиасы `/qr-auth/*`)
- **Пользователи**: `/api/users/*`
- **Каналы**: `/api/channels/*`
- **RAG Поиск**: `/api/rag/query`
- **Администрирование**: `/api/admin/*`

### Примеры использования
```bash
# Создание QR-сессии
curl -X POST "https://your-domain.com/api/tg/qr/start" \
  -H "Content-Type: application/json" \
  -d '{"telegram_user_id": "123456789", "invite_code": "ABC123XYZ456"}'

# Проверка статуса QR
curl "https://your-domain.com/api/tg/qr/status/550e8400-e29b-41d4-a716-446655440000"

# RAG-поиск
curl -X POST "https://your-domain.com/api/rag/query" \
  -H "Authorization: Bearer <jwt_token>" \
  -d '{"query": "Что нового в ИИ?", "user_id": "uuid"}'
```

## 📊 Мониторинг

### Health Checks
- `/health` - общее состояние API
- `/health/auth` - состояние QR-авторизации
- `/health/bot` - состояние Telegram бота

### Метрики
- **Prometheus** - сбор метрик
- **Grafana** - визуализация
- **Redis** - кеш и сессии
- **PostgreSQL** - база данных

### Логирование
- **Structured logs** - JSON формат
- **Log levels** - DEBUG, INFO, WARNING, ERROR
- **Correlation IDs** - трассировка запросов
- **Performance metrics** - время выполнения

## 🔧 Конфигурация

### Docker Compose
```yaml
services:
  api:
    build: ./api
    ports: ["8000:8000"]
    environment:
      - DATABASE_URL=postgresql://...
      - REDIS_URL=redis://...
  
  telethon-ingest:
    build: ./telethon-ingest
    volumes:
      - ./telethon-ingest/sessions:/app/sessions
    environment:
      - DATABASE_URL=postgresql://...
      - REDIS_URL=redis://...
```

### Caddy Reverse Proxy
```caddyfile
produman.studio {
    handle /tg/bot/* {
        reverse_proxy api:8000
    }
    
    handle /tg/app/* {
        root * /app/webapp
        file_server
    }
    
    handle /tg/qr/* {
        reverse_proxy api:8000
    }
}
```

## 🚀 Деплой

### Production
```bash
# Настройка production переменных
export BOT_PUBLIC_URL=https://produman.studio
export DATABASE_URL=postgresql://user:pass@db:5432/telegram_assistant

# Запуск production
docker compose -f docker-compose.yml up -d
```

### SSL/TLS
- **Let's Encrypt** - автоматические сертификаты
- **Caddy** - автоматическое обновление
- **HSTS** - безопасные заголовки
- **CSP** - защита от XSS

## 🧪 Тестирование

### Unit Tests
```bash
# Запуск тестов
pytest tests/

# Покрытие кода
pytest --cov=api tests/
```

### Integration Tests
```bash
# Тестирование API
curl -X POST https://produman.studio/tg/qr/start \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "123456789"}'

# Тестирование Mini App
open https://produman.studio/tg/app/
```

### Load Testing
```bash
# Нагрузочное тестирование
wrk -t12 -c400 -d30s https://produman.studio/health
```

## 📚 Документация

### API Documentation
- **OpenAPI/Swagger** - `/docs` endpoint
- **Postman Collection** - экспорт API
- **cURL Examples** - примеры запросов
- **Error Codes** - коды ошибок

### Architecture Docs
- **Mini App Architecture** - `docs/MINIAPP_ARCHITECTURE.md`
- **QR Auth Flow** - `docs/QR_AUTH_FLOW.md`
- **Troubleshooting** - `docs/TROUBLESHOOTING.md`
- **Deployment Guide** - `docs/DEPLOYMENT.md`

## 🤝 Contributing

### Development Workflow
1. **Fork** репозиторий
2. **Create** feature branch
3. **Commit** изменения
4. **Push** в fork
5. **Create** Pull Request

### Code Style
- **Black** для Python форматирования
- **ESLint** для JavaScript
- **Prettier** для HTML/CSS
- **TypeScript** для типизации

### Git Hooks
```bash
# Pre-commit hooks
pre-commit install

# Pre-push hooks
pre-push install
```

## 📄 Лицензия

MIT License - см. [LICENSE](LICENSE) файл.

## 🆘 Поддержка

### Issues
- **Bug Reports** - GitHub Issues
- **Feature Requests** - GitHub Discussions
- **Security Issues** - приватные сообщения

### Community
- **Telegram** - @telegram_assistant
- **Discord** - сервер сообщества
- **Email** - support@produman.studio

## 📋 Статус системы

### Последний аудит: 26 октября 2025

**Общая оценка**: 🟡 **ТРЕБУЕТ ДОРАБОТКИ** (критические проблемы безопасности)

#### ✅ Работающие компоненты
- **API**: Healthy, версия 2.0.0
- **Worker**: Healthy, обрабатывает события
- **Telethon**: Healthy, готов к парсингу
- **PostgreSQL**: Healthy, 200 соединений
- **Redis**: Healthy, пул настроен
- **Neo4j**: Healthy, готов к работе
- **Qdrant**: Работает, коллекции очищены
- **GigaChat Proxy**: Healthy, модели доступны

#### 🔴 Критические проблемы (требуют немедленного исправления)
1. **Row Level Security отключен** - полная утечка данных между tenant'ами
2. **Отсутствует tenant_id в таблице posts** - нарушение мульти-тенантности
3. **Слабые пароли** - POSTGRES_PASSWORD=postgres, NEO4J_PASSWORD=neo4j123

#### 🟡 Проблемы средней критичности
4. **Невалидная .env.schema.json** - пустой ключ и неправильные паттерны
5. **Отсутствуют активные Telegram сессии** - парсинг не запускается

**Подробный отчёт**: [docs/AUDIT_REPORT_20251026.md](docs/AUDIT_REPORT_20251026.md)

## 🎯 Roadmap

### v1.1.0 (Приоритет: КРИТИЧЕСКИЙ)
- [x] **Аудит системы** - полная проверка безопасности и архитектуры
- [ ] **Исправление RLS** - включение Row Level Security на всех таблицах
- [ ] **Добавление tenant_id** - исправление мульти-тенантности
- [ ] **Усиление паролей** - генерация сильных паролей для всех сервисов

### v1.2.0
- [ ] **Multi-language** поддержка
- [ ] **Push notifications** для статуса
- [ ] **Biometric auth** для быстрого входа
- [ ] **Offline support** для базовой функциональности

### v2.0.0
- [ ] **Admin panel** для управления
- [ ] **Analytics dashboard** для метрик
- [ ] **A/B testing** для экспериментов
- [ ] **Feature flags** для постепенного внедрения