# Telegram Channel Parser Bot

## Статус проекта: Переходная фаза

Проект находится в процессе миграции от старой монолитной архитектуры к новой event-driven микросервисной архитектуре.

### 🏗️ Текущее состояние

- **Старая версия**: Документирована в `docs/OLD_*` файлах
  - Монолитная архитектура с n8n/Flowise
  - Репозиторий: https://github.com/ilyasni/t-bot-for-channels/tree/test-cleanup-fresh
- **Новая архитектура**: Описана в `docs/ARCHITECTURE_PRINCIPLES.md`
  - Event-driven микросервисы с LangChain
  - Минимально достаточная инфраструктура
- **Реализация**: В процессе (папки `api/`, `worker/`, `telethon-ingest/` пустые)

### 🎯 Миссия

Архитектурный ко-пилот для Telegram Channel Parser Bot — помощь в проектировании, рефакторинге и поддержке новой архитектуры.

## Архитектура

### Основные принципы
- **Event-driven** — обмен через Kafka/Redpanda или Redis Streams
- **Stateless** — горизонтально масштабируемые воркеры
- **LLM-first** — GigaChat через `gigachain` как основной провайдер
- **Мульти-тенантность** — изоляция данных по tenant_id

### Сервисная топология
- **Telegram Ingestion Service** — парсинг каналов через Telethon
- **LangChain Orchestrator** — обработка событий и запуск пайплайнов
- **FastAPI Gateway** — REST/WebSocket API
- **Graph Intelligence Service** — синхронизация с Neo4j
- **Worker Service** — асинхронная обработка

## Технологический стек

### Backend
- **Python 3.11+**, FastAPI, SQLAlchemy 2.x
- **LangChain**, `gigachain` (GigaChat), Pydantic v2
- **Celery/FastStream** для асинхронных задач

### Базы данных
- **Postgres** (Supabase) — основное хранилище
- **Redis/Valkey** — кэш, сессии, очереди
- **Qdrant** — векторное хранилище
- **Neo4j** — граф знаний
- **Cloud.ru S3** — файлы и медиа

### Messaging
- **Kafka/Redpanda** — event bus
- **Schema Registry** — контракты событий

## Быстрый старт

### Разработка
```bash
# Клонирование репозитория
git clone <repository-url>
cd telegram-assistant

# Запуск базовой инфраструктуры
docker-compose up -d postgres redis qdrant neo4j

# Запуск сервисов (когда будут реализованы)
docker-compose up -d api worker telethon-ingest
```

### Проверка состояния
```bash
# Проверка сервисов
docker-compose ps

# Проверка логов
docker-compose logs api worker telethon-ingest

# Проверка здоровья
curl http://localhost:8000/api/v2/health
```

## Документация

### Основная документация
- **Архитектурные принципы**: `docs/ARCHITECTURE_PRINCIPLES.md`
- **Руководство по миграции**: `docs/MIGRATION_GUIDE.md`
- **Правила Cursor**: `.cursor/rules/`

### Старая версия (для справки)
- **Спецификация**: `docs/OLD_SYSTEM_SPECIFICATION.md`
- **Диагностика**: `docs/OLD_CURRENT_STATE_DIAGNOSTICS.md`
- **Пайплайны**: `docs/OLD_SYSTEM_PIPELINE.md`

## Roadmap

1. **Инициализация ядра** — Telethon + FastAPI + Postgres + Redis
2. **Добавление orchestrator** — Redpanda/Redis Streams + LangChain
3. **RAG модуль** — Qdrant + LangChain retrieval pipeline
4. **Graph Intelligence** — Neo4j + GraphRAG
5. **Quality Evaluation** — Ragas evaluator
6. **Observability & Security** — OTel + RBAC + аудит

## Разработка

### Правила Cursor
Проект использует модульные правила Cursor в `.cursor/rules/`:
- `00-principles.mdc` — миссия, приоритеты, архитектурные ограничения
- `01-project-context.mdc` — контекст проекта, сущности, технологический стек
- `02-services.mdc` — правила для сервисов, формат PR, тестирование
- `03-database.mdc` — правила БД, шаблоны таблиц, миграции
- `04-rag-graph.mdc` — RAG, GraphRAG, Qdrant, Neo4j
- `05-auth-security.mdc` — аутентификация, безопасность, RBAC
- `06-evaluation-llmops.mdc` — оценка качества, A/B тесты, метрики
- `07-devops-observability.mdc` — мониторинг, health checks, Grafana
- `99-troubleshooting.mdc` — диагностика, частые проблемы, фиксы

### Структура ответов
- **Context** — кратко (< 5 строк) о файле, сервисе или задаче
- **Plan** — пошаговый план изменений
- **Patch** — дифф или полный фрагмент изменённого кода
- **Checks** — как проверить результат (команды, сценарий, тесты, логи)
- **Impact / Rollback** — что может быть затронуто и как откатить

## Лицензия

[Указать лицензию]

## Поддержка

- **GitHub Issues**: для багов и feature requests
- **Документация**: в репозитории проекта
- **Статус**: в README.md
