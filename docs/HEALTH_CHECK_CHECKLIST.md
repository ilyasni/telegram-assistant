# Health Check Checklist для Telegram Assistant

## Обзор
Этот чеклист содержит все компоненты системы, которые необходимо проверить для обеспечения работоспособности и соответствия best practices.

---

## 🏗️ Инфраструктура

### Docker Compose
- [ ] **Все сервисы запущены** - `docker compose ps`
- [ ] **Health checks работают** - проверка health endpoints каждого сервиса
- [ ] **Сетевая связность** - сервисы могут общаться друг с другом
- [ ] **Volumes монтированы** - данные персистентны
- [ ] **Ресурсные ограничения** - memory/CPU limits установлены

### Сетевая инфраструктура
- [ ] **Caddy reverse proxy** - маршрутизация работает
- [ ] **Kong API Gateway** - API routing функционирует
- [ ] **SSL/TLS** - HTTPS работает корректно
- [ ] **Security headers** - HSTS, CSP, X-Frame-Options установлены

---

## 🗄️ База данных

### PostgreSQL (Supabase)
- [ ] **Подключение** - `psql` работает
- [ ] **Таблицы созданы** - все необходимые таблицы существуют
- [ ] **RLS политики** - Row Level Security настроен
- [ ] **Индексы** - производительные индексы созданы
- [ ] **Миграции** - схема актуальна

### Redis
- [ ] **Подключение** - `redis-cli ping` работает
- [ ] **Memory usage** - использование памяти в норме
- [ ] **Persistence** - данные сохраняются
- [ ] **Clustering** - если используется

### Qdrant
- [ ] **Подключение** - API доступен
- [ ] **Коллекции** - созданы per-tenant
- [ ] **Embeddings** - векторы сохраняются
- [ ] **Search** - поиск работает

### Neo4j
- [ ] **Подключение** - bolt:// работает
- [ ] **Аутентификация** - пользователь/пароль
- [ ] **Граф** - узлы и связи создаются
- [ ] **Cypher queries** - запросы выполняются

---

## 🚀 Сервисы

### API Service (FastAPI)
- [ ] **Health endpoint** - `/health` возвращает 200
- [ ] **Ready endpoint** - `/ready` возвращает 200
- [ ] **Metrics endpoint** - `/metrics` возвращает Prometheus метрики
- [ ] **CORS** - настроен корректно
- [ ] **Rate limiting** - middleware работает
- [ ] **Authentication** - JWT токены валидны
- [ ] **Error handling** - ошибки обрабатываются

### Worker Service
- [ ] **Health check** - worker отвечает
- [ ] **Event processing** - события обрабатываются
- [ ] **Task queues** - очереди работают
- [ ] **Retry logic** - повторные попытки
- [ ] **Dead letter queue** - неудачные задачи

### Telethon-ingest Service
- [ ] **Health endpoint** - порт 8011 отвечает
- [ ] **Telegram connection** - MTProto работает
- [ ] **QR auth** - авторизация через QR
- [ ] **Session management** - сессии сохраняются
- [ ] **Message parsing** - сообщения парсятся

### Crawl4AI Service
- [ ] **Health endpoint** - порт 8080 отвечает
- [ ] **Playwright** - браузер работает
- [ ] **Content extraction** - контент извлекается
- [ ] **Rate limiting** - ограничения соблюдаются

---

## 📊 Метрики

### HTTP Метрики
- [ ] **http_requests_total** - счетчик запросов
- [ ] **http_request_duration_seconds** - время выполнения
- [ ] **Labels** - method, endpoint, status присутствуют

### Бизнес Метрики
- [ ] **posts_processed_total** - обработанные посты
- [ ] **posts_in_queue_total** - посты в очереди
- [ ] **tagging_requests_total** - запросы тегирования
- [ ] **embedding_requests_total** - запросы эмбеддингов
- [ ] **enrichment_requests_total** - запросы обогащения

### Системные Метрики
- [ ] **database_connections** - соединения с БД
- [ ] **memory_usage_bytes** - использование памяти
- [ ] **cpu_usage_percent** - использование CPU
- [ ] **queue_processing_time_seconds** - время обработки очередей

### AI Провайдеры Метрики
- [ ] **llm_requests_total** - запросы к LLM
- [ ] **llm_tokens_used_total** - использованные токены
- [ ] **llm_request_duration_seconds** - время ответа LLM
- [ ] **llm_errors_total** - ошибки LLM

---

## 🔒 Безопасность

### Rate Limiting
- [ ] **API endpoints** - лимиты установлены
- [ ] **Authentication** - 5 попыток в минуту
- [ ] **RAG queries** - 100 запросов в час
- [ ] **Channel management** - 10 операций в минуту

### Authentication & Authorization
- [ ] **QR-логин** - Telegram Login Widget
- [ ] **JWT токены** - валидация работает
- [ ] **Redis сессии** - сессии сохраняются
- [ ] **RBAC** - роли и права доступа

### Security Headers
- [ ] **HSTS** - Strict-Transport-Security
- [ ] **CSP** - Content-Security-Policy
- [ ] **X-Frame-Options** - DENY
- [ ] **X-Content-Type-Options** - nosniff
- [ ] **Referrer-Policy** - no-referrer

### Data Protection
- [ ] **RLS** - Row Level Security в Supabase
- [ ] **Шифрование** - чувствительные данные зашифрованы
- [ ] **Secrets** - секреты в environment variables
- [ ] **Tenant isolation** - изоляция данных по tenant_id

---

## 📈 Observability

### Логирование
- [ ] **Structured logs** - JSON формат
- [ ] **Trace ID** - в каждом логе
- [ ] **Log levels** - DEBUG, INFO, WARNING, ERROR
- [ ] **Context fields** - user_id, tenant_id, request_id

### Трассировка
- [ ] **OpenTelemetry** - интеграция настроена
- [ ] **Span correlation** - связи между операциями
- [ ] **Trace ID propagation** - передача между сервисами

### Мониторинг
- [ ] **Grafana dashboards** - дашборды настроены
- [ ] **Prometheus alerts** - алерты сконфигурированы
- [ ] **Health checks matrix** - матрица проверок

---

## 🏛️ Архитектура

### Event-driven
- [ ] **Redis Streams** - event bus работает
- [ ] **Event publishing** - события публикуются
- [ ] **Event consuming** - события обрабатываются
- [ ] **Idempotency** - идемпотентность обработки

### Мульти-тенантность
- [ ] **Tenant isolation** - изоляция по tenant_id
- [ ] **RLS policies** - политики безопасности
- [ ] **Qdrant collections** - отдельные коллекции per-tenant
- [ ] **S3 buckets** - отдельные bucket'ы per-tenant

### Масштабируемость
- [ ] **Stateless services** - сервисы без состояния
- [ ] **Horizontal scaling** - горизонтальное масштабирование
- [ ] **Connection pooling** - пулы соединений
- [ ] **Load balancing** - балансировка нагрузки

---

## 🧪 Тестирование

### Smoke Tests
- [ ] **Все endpoints** - базовые проверки
- [ ] **Health checks** - все сервисы здоровы
- [ ] **Database connectivity** - подключения работают
- [ ] **External services** - внешние сервисы доступны

### Load Testing
- [ ] **API endpoints** - под нагрузкой
- [ ] **Database queries** - производительность БД
- [ ] **Rate limiting** - лимиты под нагрузкой
- [ ] **Queue processing** - обработка очередей

### Stress Testing
- [ ] **Memory usage** - использование памяти
- [ ] **CPU usage** - использование CPU
- [ ] **Disk I/O** - операции с диском
- [ ] **Network I/O** - сетевой трафик

---

## 📋 Конфигурация

### Environment Variables
- [ ] **JWT_SECRET** - секрет для JWT
- [ ] **ANON_KEY** - анонимный ключ Supabase
- [ ] **SERVICE_KEY** - сервисный ключ Supabase
- [ ] **DATABASE_URL** - URL базы данных
- [ ] **REDIS_URL** - URL Redis
- [ ] **QDRANT_URL** - URL Qdrant
- [ ] **NEO4J_URL** - URL Neo4j

### Feature Flags
- [ ] **FEATURE_NEO4J_ENABLED** - Neo4j включен
- [ ] **FEATURE_GIGACHAT_ENABLED** - GigaChat включен
- [ ] **FEATURE_OPENROUTER_ENABLED** - OpenRouter включен
- [ ] **FEATURE_CRAWL4AI_ENABLED** - Crawl4AI включен

### AI Providers
- [ ] **GIGACHAT_API_KEY** - ключ GigaChat
- [ ] **OPENROUTER_API_KEY** - ключ OpenRouter
- [ ] **AI_HTTP_TIMEOUT_SEC** - таймаут AI запросов
- [ ] **AI_MAX_RETRIES** - количество повторов

---

## 🚨 Критичные проблемы

### Блокеры (Blocker)
- [ ] **Сервисы не запускаются** - Docker Compose
- [ ] **База данных недоступна** - PostgreSQL
- [ ] **Redis недоступен** - кэширование
- [ ] **API не отвечает** - FastAPI
- [ ] **Health checks падают** - мониторинг

### Важные (Major)
- [ ] **Метрики отсутствуют** - Prometheus
- [ ] **Логи не структурированы** - observability
- [ ] **Rate limiting не работает** - безопасность
- [ ] **RLS не настроен** - безопасность данных
- [ ] **Трассировка отсутствует** - debugging

### Незначительные (Minor)
- [ ] **Документация устарела** - поддержка
- [ ] **Конфигурация неоптимальна** - производительность
- [ ] **Алерты не настроены** - мониторинг
- [ ] **Тесты отсутствуют** - качество

---

## 📊 Статистика проверки

| Категория | Всего | ✅ Pass | ❌ Fail | ⚠️ Warn | ⏭️ Skip |
|-----------|-------|---------|---------|---------|---------|
| Инфраструктура | 0 | 0 | 0 | 0 | 0 |
| База данных | 0 | 0 | 0 | 0 | 0 |
| Сервисы | 0 | 0 | 0 | 0 | 0 |
| Метрики | 0 | 0 | 0 | 0 | 0 |
| Безопасность | 0 | 0 | 0 | 0 | 0 |
| Observability | 0 | 0 | 0 | 0 | 0 |
| Архитектура | 0 | 0 | 0 | 0 | 0 |
| Тестирование | 0 | 0 | 0 | 0 | 0 |
| Конфигурация | 0 | 0 | 0 | 0 | 0 |
| **ИТОГО** | **0** | **0** | **0** | **0** | **0** |

---

## 🔧 Рекомендации

### Краткосрочные (Quick Wins)
1. **Добавить недостающие health checks**
2. **Настроить структурированное логирование**
3. **Добавить недостающие метрики**
4. **Настроить rate limiting**

### Среднесрочные (Improvements)
1. **Настроить OpenTelemetry трассировку**
2. **Создать Grafana dashboards**
3. **Настроить алерты**
4. **Добавить тесты**

### Долгосрочные (Architectural)
1. **Миграция на Kafka/Redpanda**
2. **Настройка кластеризации**
3. **Автоматическое масштабирование**
4. **Disaster recovery**

---

## 📝 Заметки

### Выполненные проверки
- [ ] Дата: ___________
- [ ] Выполнил: ___________
- [ ] Версия системы: ___________

### Обнаруженные проблемы
1. ___________
2. ___________
3. ___________

### Планы по улучшению
1. ___________
2. ___________
3. ___________

---

*Последнее обновление: 2024-12-19*
*Версия чеклиста: 1.0*
