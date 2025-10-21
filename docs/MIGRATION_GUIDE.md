# Руководство по миграции

## Обзор

Этот документ описывает переход от старой версии Telegram Channel Parser Bot к новой архитектуре.

## Что изменилось

### Архитектурные изменения
- **От монолита к микросервисам** — четкое разделение доменов
- **От n8n/Flowise к LangChain** — все пайплайны описаны как LangChain chains/graphs
- **От SQLite к Postgres** — расширенная схема с мульти-тенантностью
- **Добавление векторных БД** — Qdrant для RAG, Neo4j для графов

### Технологические изменения
- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.x, LangChain
- **Базы данных**: Postgres (Supabase), Redis, Qdrant, Neo4j
- **Messaging**: Kafka/Redpanda, Redis Streams
- **AI/ML**: GigaChat через `gigachain`, OpenRouter, Ollama

## План миграции

### Этап 1: Инициализация ядра
- [ ] Настройка базовой инфраструктуры (Postgres, Redis)
- [ ] Реализация Telegram Ingestion Service
- [ ] Создание FastAPI Gateway
- [ ] Базовая аутентификация через Telegram Login Widget

### Этап 2: Добавление orchestrator
- [ ] Настройка Redpanda/Redis Streams
- [ ] Реализация LangChain Orchestrator
- [ ] Создание Worker Service
- [ ] Event-driven обработка сообщений

### Этап 3: RAG модуль
- [ ] Настройка Qdrant
- [ ] Реализация LangChain retrieval pipeline
- [ ] Создание RAG API
- [ ] Интеграция с GigaChat

### Этап 4: Graph Intelligence
- [ ] Настройка Neo4j
- [ ] Реализация Graph Intelligence Service
- [ ] Синхронизация Postgres/Qdrant → Neo4j
- [ ] GraphRAG и рекомендации

### Этап 5: Quality Evaluation
- [ ] Настройка Ragas evaluator
- [ ] Создание контрольных датасетов
- [ ] Интеграция метрик в мониторинг

### Этап 6: Observability & Security
- [ ] Настройка OpenTelemetry
- [ ] Реализация RBAC
- [ ] Настройка Grafana dashboards
- [ ] Аудит и мониторинг

## Совместимость

### API endpoints
- **Совместимые**: `/api/v1/channels`, `/api/v1/posts`, `/api/v1/search`
- **Измененные**: `/api/v2/` — новая версия с расширенным функционалом
- **Новые**: `/api/v2/rag/`, `/api/v2/graph/`, `/api/v2/analytics/`

### Схема БД
- **Сохранены**: все пользовательские данные
- **Расширены**: добавлены поля `tenant_id`, `embedding_status`, `graph_status`
- **Новые таблицы**: `IndexingStatus`, `DigestSettings`, `GraphRelations`

### Конфигурация
- **Мигрируется автоматически** через скрипты миграции
- **Сохранены**: все пользовательские настройки
- **Новые**: настройки RAG, графов, аналитики

## Миграция данных

### Пользователи
```sql
-- Все пользователи сохраняются
-- Добавляется tenant_id для мульти-тенантности
ALTER TABLE users ADD COLUMN tenant_id UUID DEFAULT gen_random_uuid();
```

### Каналы
```sql
-- Все каналы сохраняются
-- Добавляются настройки парсинга
ALTER TABLE channels ADD COLUMN parsing_settings JSONB;
ALTER TABLE channels ADD COLUMN digest_frequency VARCHAR(20);
```

### Посты
```sql
-- Все посты сохраняются
-- Добавляются статусы обработки
ALTER TABLE posts ADD COLUMN embedding_status VARCHAR(20);
ALTER TABLE posts ADD COLUMN graph_status VARCHAR(20);
```

## Проверка миграции

### Команды проверки
```bash
# Проверка состояния сервисов
docker-compose ps

# Проверка подключения к БД
docker-compose exec postgres psql -U postgres -d telegram_assistant -c "SELECT COUNT(*) FROM users;"

# Проверка Redis
docker-compose exec redis redis-cli ping

# Проверка Qdrant
curl http://localhost:6333/collections

# Проверка Neo4j
curl -u neo4j:password http://localhost:7474/db/data/
```

### Тесты миграции
```bash
# Запуск тестов
docker-compose exec api python -m pytest tests/migration/

# Проверка API
curl -X GET http://localhost:8000/api/v2/health

# Проверка RAG
curl -X POST http://localhost:8000/api/v2/rag/search -d '{"query": "test"}'
```

## Откат

### План отката
1. **Остановка новых сервисов**
2. **Восстановление из бэкапа**
3. **Проверка целостности данных**
4. **Запуск старой версии**

### Команды отката
```bash
# Остановка новых сервисов
docker-compose down

# Восстановление из бэкапа
docker-compose exec postgres pg_restore -U postgres -d telegram_assistant /backup/backup.sql

# Запуск старой версии
git checkout old-version
docker-compose up -d
```

## Поддержка

### Документация
- **Старая версия**: `docs/OLD_*` файлы
- **Новая версия**: `ARCHITECTURE_PRINCIPLES.md`
- **Миграция**: `docs/MIGRATION_GUIDE.md`

### Контакты
- **Техническая поддержка**: через GitHub Issues
- **Документация**: в репозитории проекта
- **Статус миграции**: в README.md

## Заключение

Миграция спроектирована как поэтапный процесс с сохранением всех пользовательских данных и обратной совместимостью API. Каждый этап может быть протестирован и откачен при необходимости.
