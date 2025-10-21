# 🌐 Subdomain-based маршрутизация

## ✅ Конфигурация

Переключились на **subdomain-based маршрутизацию** как в прошлом проекте:

### 🏗️ Архитектура

**Внешний доступ:**
- **Supabase:** `supabase.produman.studio` (Studio + API через Kong)
- **Grafana:** `grafana.produman.studio`

**Внутренний доступ (только для Docker сети):**
- **API Gateway:** `api:8000`
- **Neo4j:** `neo4j:7474`
- **Qdrant:** `qdrant:6333`
- **RAG:** `rag:8000`

### 🔧 Caddyfile

```caddyfile
# Supabase: студия по корню, API-пути — через Kong
supabase.produman.studio {
    # API/WS/SSE на Kong (внутри docker-сети supabase)
    @api path /rest/v1/* /auth/v1/* /realtime/v1/* /storage/v1/*
    reverse_proxy @api kong:8000

    # Всё остальное — в Studio (Next.js на 3000)
    handle_path /* {
        reverse_proxy supabase-studio:3000
    }
}

# Grafana Dashboard (внешний доступ)
grafana.produman.studio {
    reverse_proxy grafana:3000
}

# Внутренние сервисы (только для Docker сети)
# API Gateway
api:8000 {
    reverse_proxy api:8000
}

# Neo4j Browser
neo4j:7474 {
    reverse_proxy neo4j:7474
}

# Qdrant Dashboard
qdrant:6333 {
    reverse_proxy qdrant:6333
}

# RAG Service
rag:8000 {
    reverse_proxy api:8000
}
```

### 🎯 Преимущества

- ✅ **Автоматический TLS** — Caddy сам получает сертификаты
- ✅ **Чистые URL** — каждый сервис на своём поддомене
- ✅ **WebSockets/SSE** — работают из коробки
- ✅ **Production-ready** — как в прошлом проекте

### 🔒 Безопасность

- **Внешний доступ:** `supabase.produman.studio`, `grafana.produman.studio`
- **Внутренний доступ:** `api:8000`, `neo4j:7474`, `qdrant:6333`, `rag:8000` (только внутри Docker сети)

### 📋 DNS записи

Нужны A-записи только для внешних поддоменов:

```
supabase.produman.studio → 192.168.31.64
grafana.produman.studio → 192.168.31.64
```

**Внутренние сервисы** доступны только внутри Docker сети через имена контейнеров.

### 🧪 Тестирование

```bash
# Проверка внешних поддоменов
curl -k https://supabase.produman.studio
curl -k https://grafana.produman.studio

# Проверка внутренних сервисов (только внутри Docker сети)
curl http://api:8000/health
curl http://neo4j:7474
curl http://qdrant:6333
curl http://rag:8000/health
```

### 🚀 Статус

Caddy запущен и пытается получить SSL сертификаты для внешних поддоменов (`supabase.produman.studio`, `grafana.produman.studio`). После настройки DNS внешние сервисы будут доступны по HTTPS, а внутренние — только внутри Docker сети.
