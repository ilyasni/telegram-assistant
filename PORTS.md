# Порты, используемые в сборке Telegram Assistant

## Публичные порты (пробрасываются на хост)

### Основные сервисы (Core)
- **80** (HTTP) - Caddy Reverse Proxy
- **443** (HTTPS) - Caddy Reverse Proxy (TLS)
- **6379** (Redis) - Redis Cache (только в dev режиме)
- **8008** (PaddleOCR) - Локальный OCR сервис (переменная `LOCAL_OCR_PORT`, по умолчанию 8008)

### API Gateway и сервисы
- **8080** (SearXNG) - Поисковая система для RAG
- **8090** (gpt2giga-proxy) - GigaChat Proxy (OpenAI-compatible API)

### Мониторинг (Monitoring profile)
- **9090** (Prometheus) - Метрики и мониторинг
- **9093** (AlertManager) - Управление алертами

### Neo4j Health
- **7475** (neo4j-health) - Health check endpoint для Neo4j

## Внутренние порты (только внутри Docker сети)

### База данных
- **5432** (PostgreSQL) - Supabase Database
- **6333-6334** (Qdrant) - Vector Database
- **7687** (Neo4j) - Graph Database (Bolt protocol)
- **7473-7474** (Neo4j) - Neo4j HTTP/HTTPS

### API сервисы
- **8000-8001** (Kong) - API Gateway
- **8443-8444** (Kong) - API Gateway (TLS)
- **3000** (Supabase Studio) - Web UI для управления БД
- **3000** (Grafana) - Дашборды мониторинга
- **8080** (Crawl4AI) - Enrichment сервис
- **8080** (Postgres Meta) - Metadata API

### Служебные
- **2019** (Caddy) - Caddy management API

## Конфигурация портов

### Переменные окружения
- `LOCAL_OCR_PORT` - порт для PaddleOCR (по умолчанию: 8008)

### Профили Docker Compose
- `core` - основные сервисы (всегда активны)
- `monitoring` - Prometheus, Grafana, AlertManager
- `analytics` - Grafana (включен в monitoring)
- `rag` - SearXNG

## Сводная таблица

| Порт | Сервис | Профиль | Назначение |
|------|--------|---------|------------|
| 80 | Caddy | core | HTTP Reverse Proxy |
| 443 | Caddy | core | HTTPS Reverse Proxy |
| 6379 | Redis | core (dev) | Cache (только в dev) |
| 8008 | PaddleOCR | - | OCR сервис |
| 8080 | SearXNG | rag | Поисковая система |
| 8090 | gpt2giga-proxy | core | GigaChat Proxy |
| 9090 | Prometheus | monitoring | Метрики |
| 9093 | AlertManager | monitoring | Алерты |
| 7475 | neo4j-health | core | Neo4j Health |

## Примечания

1. **Redis (6379)** - пробрасывается только в dev режиме (`docker-compose.dev.yml`)
2. **PaddleOCR (8008)** - настраивается через переменную `LOCAL_OCR_PORT`
3. **Caddy (80/443)** - основной entry point для всех внешних запросов
4. Все остальные сервисы доступны только через внутреннюю Docker сеть `telegram-network`

