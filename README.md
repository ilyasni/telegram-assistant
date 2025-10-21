# 🤖 Telegram Assistant

Архитектурный ко-пилот для Telegram Channel Parser Bot с event-driven микросервисной архитектурой.

## 🚀 Быстрый старт

```bash
# Запуск системы
docker compose up -d

# Проверка статуса
curl http://localhost:80/health
```

## 📚 Документация

### 🛠️ Настройка
- [**Быстрый старт**](docs/setup/QUICKSTART.md) — основные команды для запуска
- [**Полная настройка**](docs/setup/SETUP.md) — детальная инструкция
- [**DNS настройка**](docs/setup/DNS_SETUP.md) — настройка доменов
- [**Локальная DNS**](docs/setup/LOCAL_DNS_SETUP.md) — для разработки

### 🚀 Развертывание
- [**Продакшн настройка**](docs/deployment/PRODUCTION_SETUP.md) — полная настройка для production
- [**Внешний доступ**](docs/deployment/EXTERNAL_ACCESS_SETUP.md) — настройка внешнего доступа
- [**Subdomain маршрутизация**](docs/deployment/SUBDOMAIN_SETUP.md) — настройка поддоменов
- [**Простая доменная настройка**](docs/deployment/SIMPLE_DOMAIN_SETUP.md) — единый домен
- [**Безопасный доступ**](docs/deployment/SECURE_ACCESS.md) — настройка безопасности
- [**Supabase доступ**](docs/deployment/SUPABASE_ACCESS.md) — настройка Supabase

### 🔧 Устранение неполадок
- [**DNS проблемы**](docs/troubleshooting/TROUBLESHOOTING_DNS.md) — решение проблем с DNS
- [**Альтернативная DNS**](docs/troubleshooting/ALTERNATIVE_DNS.md) — альтернативные решения
- [**Финальное решение DNS**](docs/troubleshooting/FINAL_DNS_SOLUTION.md) — итоговое решение

### 📊 Статус системы
- [**Система готова**](docs/status/SYSTEM_READY.md) — текущий статус
- [**Порты исправлены**](docs/status/PORTS_FIXED.md) — решение проблем с портами
- [**Caddy готов**](docs/status/CADDY_READY.md) — статус Caddy
- [**DNS готов**](docs/status/DNS_READY.md) — статус DNS

## 🌐 Доступные сервисы

- **API Gateway:** http://localhost:80/api/
- **Supabase Studio:** http://localhost:80/supabase/
- **Grafana Dashboard:** http://localhost:80/grafana/
- **Neo4j Browser:** http://localhost:80/neo4j/
- **Qdrant Dashboard:** http://localhost:80/qdrant/
- **RAG Service:** http://localhost:80/rag/

## 🏗️ Архитектура

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Caddy Proxy   │    │   API Gateway   │    │   Worker        │
│   (Port 80/443) │────│   (FastAPI)     │────│   (Redis)       │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Supabase DB    │    │   Redis Streams  │    │   Qdrant        │
│  (PostgreSQL)   │    │   (Event Bus)    │    │   (Vector DB)   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 🛠️ Технологический стек

- **Caddy** — reverse proxy с автоматическим HTTPS
- **FastAPI** — API Gateway
- **PostgreSQL** — основная база данных (Supabase)
- **Redis** — кэш и event bus
- **Qdrant** — векторная база данных
- **Neo4j** — графовая база данных
- **Grafana** — мониторинг и дашборды

## 📋 Основные команды

```bash
# Запуск всех сервисов
docker compose up -d

# Запуск только core сервисов
docker compose --profile core up -d

# Запуск с аналитикой
docker compose --profile analytics up -d

# Проверка логов
docker compose logs -f

# Остановка
docker compose down
```

## 🔧 Разработка

```bash
# Пересборка сервисов
docker compose build

# Перезапуск конкретного сервиса
docker compose restart api

# Просмотр статуса
docker compose ps
```

## 📞 Поддержка

При возникновении проблем:
1. Проверьте [документацию по устранению неполадок](docs/troubleshooting/)
2. Посмотрите [статус системы](docs/status/)
3. Обратитесь к [настройке](docs/setup/)

---

**Система готова к работе!** 🚀